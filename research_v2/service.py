from __future__ import annotations

import asyncio
from collections import OrderedDict, deque
from datetime import datetime, timedelta
from typing import Protocol
from uuid import uuid4

from .classification import classify, deduplicate
from .models import BatchResult, ProviderOutcome, Report, TargetInput, utc_now
from .providers import ApolloProvider, PublicHTMLProvider, TheirStackProvider
from .resolution import InvalidTarget, resolve_target, target_key


class ResearchStore(Protocol):
    """Durable implementations must enforce workspace isolation, TTL and retention too."""
    async def cached(self, workspace: str, key: tuple, now: datetime) -> Report | None: ...
    async def save(self, workspace: str, key: tuple, report: Report) -> None: ...
    async def report(self, workspace: str, report_id: str, now: datetime) -> Report | None: ...


class MemoryStore:
    """Bounded, process-local storage; intentionally not durable across restarts/workers."""
    def __init__(self, max_entries=128):
        if not 1 <= max_entries <= 1024:
            raise ValueError("Storage bound required")
        self.max_entries = max_entries
        self._cache, self._reports = OrderedDict(), OrderedDict()

    def _get(self, collection, key, now):
        item = collection.get(key)
        if item is None:
            return None
        if item.expires_at <= now:
            del collection[key]
            return None
        collection.move_to_end(key)
        return item.model_copy(deep=True)

    async def cached(self, workspace, key, now):
        return self._get(self._cache, (workspace, key), now)

    async def save(self, workspace, key, report):
        for collection, index in ((self._cache, (workspace, key)), (self._reports, (workspace, report.id))):
            collection[index] = report.model_copy(deep=True)
            collection.move_to_end(index)
            while len(collection) > self.max_entries:
                collection.popitem(last=False)

    async def report(self, workspace, report_id, now):
        return self._get(self._reports, (workspace, report_id), now)


class ResearchLimit(ValueError):
    pass


class ResearchUnavailable(RuntimeError):
    def __init__(self):
        super().__init__("O armazenamento privado está temporariamente indisponível.")


class ResearchQuota(Protocol):
    async def charge(self, workspace: str, count: int) -> bool: ...


class ResearchService:
    def __init__(self, providers=None, store: ResearchStore | None = None, *, quota: ResearchQuota | None = None, clock=utc_now, allow_synthetic=False):
        self.providers = tuple(providers if providers is not None else (PublicHTMLProvider(), TheirStackProvider(), ApolloProvider()))
        if len(self.providers) > 3 or len({p.name for p in self.providers}) != len(self.providers):
            raise ValueError("At most three distinct collectors are allowed")
        self.store = store or MemoryStore()
        self.quota = quota
        self.clock, self.allow_synthetic = clock, allow_synthetic
        self._concurrency = asyncio.Semaphore(2)
        self._active = {}
        self._rates = OrderedDict()

    def _charge(self, workspace, count):
        now = self.clock()
        requests = self._rates.setdefault(workspace, deque())
        while requests and requests[0] <= now - timedelta(minutes=1):
            requests.popleft()
        if len(requests) + count > 10:
            raise ResearchLimit("Máximo de 10 alvos por minuto por workspace.")
        requests.extend([now] * count)
        self._rates.move_to_end(workspace)
        # Never evict an active rate bucket and accidentally reset its allowance.
        for key in list(self._rates):
            bucket = self._rates[key]
            if bucket and bucket[-1] <= now - timedelta(minutes=1):
                del self._rates[key]
        if len(self._rates) > 256:
            del self._rates[workspace]
            raise ResearchLimit("Capacidade temporariamente esgotada.")

    async def research(self, workspace: str, value: TargetInput) -> Report:
        result = await self.batch(workspace, [value])
        return result.reports[0]

    async def batch(self, workspace: str, values: list[TargetInput]) -> BatchResult:
        if not 1 <= len(values) <= 5:
            raise ResearchLimit("Lotes precisam conter de 1 a 5 alvos.")
        targets = [resolve_target(value) for value in values]
        names = {}
        for target in targets:
            key = target_key(target)
            if key in names and names[key] != target.company_name:
                raise InvalidTarget("Um mesmo domínio/escopo recebeu nomes conflitantes no lote.")
            names[key] = target.company_name
        unique = {target_key(target): target for target in targets}
        if self.quota is None:
            self._charge(workspace, len(unique))
        elif not await self.quota.charge(workspace, len(unique)):
            raise ResearchLimit("Máximo de 10 alvos por minuto por workspace.")
        reports = {}
        for key, target in unique.items():
            reports[key] = await self._research(workspace, target)
        return BatchResult(reports=[reports[target_key(target)] for target in targets], requested=len(values), unique_targets=len(unique))

    async def _research(self, workspace, target):
        revision = tuple((provider.name, provider.revision) for provider in self.providers)
        # Company name can influence provider matching; don't reuse an unrelated name's result.
        key = (*target_key(target), target.company_name, revision, "classifier-v2")
        cached = await self.store.cached(workspace, key, self.clock())
        if cached:
            return cached.model_copy(update={"cache_hit": True})
        job_key = (workspace, key)
        if job_key not in self._active:
            if len(self._active) >= 128:
                raise ResearchLimit("Capacidade temporariamente esgotada.")
            task = asyncio.create_task(self._collect(workspace, key, target))
            self._active[job_key] = task

            def completed(done):
                if self._active.get(job_key) is done:
                    del self._active[job_key]
                if not done.cancelled():
                    done.exception()  # Consume errors even if a disconnected client stopped awaiting.

            task.add_done_callback(completed)
        task = self._active[job_key]
        try:
            return (await asyncio.shield(task)).model_copy(deep=True)
        finally:
            if task.done() and self._active.get(job_key) is task:
                del self._active[job_key]

    async def _collect(self, workspace, key, target):
        async with self._concurrency:
            outcomes = []
            for provider in self.providers:
                try:
                    outcome = await asyncio.wait_for(provider.collect(target, workspace, self.clock()), 12)
                    if outcome.provider != provider.name:
                        raise ValueError("Collector identity mismatch")
                    if any(e.synthetic for e in outcome.evidence) and not self.allow_synthetic:
                        raise ValueError("Synthetic evidence cannot enter real research")
                    outcome = outcome.model_copy(update={"evidence": outcome.evidence[:10]})
                except Exception:
                    outcome = ProviderOutcome(provider=provider.name, state="error", error_code="collection_failed")
                outcomes.append(outcome)
            checked_at = self.clock()
            evidence = deduplicate([item for outcome in outcomes for item in outcome.evidence])
            errors = any(outcome.state == "error" for outcome in outcomes)
            ambiguous = any(outcome.state == "ambiguous" for outcome in outcomes)
            disabled = any(outcome.state == "disabled" for outcome in outcomes) or not outcomes
            ttl = 60 if errors or ambiguous or disabled else 3600
            conclusion = classify(target, evidence, checked_at, errors=errors, ambiguous=ambiguous)
            if disabled:
                conclusion.limitations.append("Há fontes desativadas ou não configuradas; a cobertura é parcial.")
            report = Report(id=str(uuid4()), target=target, checked_at=checked_at, expires_at=checked_at + timedelta(seconds=ttl),
                conclusion=conclusion, evidence=evidence, sources=outcomes, coverage="partial" if errors or ambiguous or disabled else "complete_for_configured_sources",
                synthetic=any(item.synthetic for item in evidence))
            await self.store.save(workspace, key, report)
            return report
