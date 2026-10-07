from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

from .classification import products_in_text, text_context
from .models import Evidence, ProviderOutcome, Target
from .resolution import same_domain
from .transport import ProviderJSONTransport, PublicHTMLFetcher


@dataclass(frozen=True)
class ProviderPolicy:
    enabled: bool = False
    authorized_workspaces: frozenset[str] = frozenset()
    revision: str = "disabled-v1"
    record_limit: int = 5
    api_key: str | None = field(default=None, repr=False)

    def __post_init__(self):
        if not 1 <= self.record_limit <= 5:
            raise ValueError("Provider record limit must be between 1 and 5.")


def date_value(value) -> datetime | None:
    if not isinstance(value, str):
        return None
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            return datetime.fromisoformat(value).replace(tzinfo=timezone.utc)
        date = datetime.fromisoformat(value.replace("Z", "+00:00"))
        return date.astimezone(timezone.utc) if date.tzinfo else None
    except ValueError:
        return None


class Provider:
    name = "base"
    requires_key = True

    def __init__(self, policy: ProviderPolicy | None = None, transport=None):
        self.policy = policy or ProviderPolicy()
        self.transport = transport or ProviderJSONTransport()

    @property
    def revision(self):
        return self.policy.revision

    async def collect(self, target: Target, workspace: str, checked_at: datetime) -> ProviderOutcome:
        if not self.policy.enabled or workspace not in self.policy.authorized_workspaces:
            return ProviderOutcome(provider=self.name, state="disabled")
        if self.requires_key and not self.policy.api_key:
            return ProviderOutcome(provider=self.name, state="error", error_code="provider_not_configured")
        try:
            return await self._collect(target, checked_at)
        except Exception:
            # No vendor payload, credentials, company data or exception text in errors/logs.
            return ProviderOutcome(provider=self.name, state="error", error_code="collection_failed")


class TheirStackProvider(Provider):
    name = "theirstack"
    URL = "https://api.theirstack.com/v1/jobs/search"

    async def _collect(self, target, checked_at):
        value = await self.transport.request("POST", self.URL, headers={"Authorization": "Bearer " + self.policy.api_key},
            json_body={"page": 0, "limit": self.policy.record_limit, "company_domain_or": [target.domain],
                       "job_description_pattern_or": ["(?i)salesforce|marketing\\s*cloud|pardot|exacttarget"],
                       "posted_at_max_age_days": 365, "include_total_results": False})
        records = value.get("data")
        if not isinstance(records, list):
            raise ValueError("Unexpected jobs response")
        evidence, mismatched = [], False
        for job in records[:self.policy.record_limit]:
            company = job.get("company_object") or {}
            domain = job.get("company_domain") or company.get("domain")
            if not same_domain(domain, target):
                mismatched = True
                continue
            description = str(job.get("description") or "")[:50000]
            text = str(job.get("job_title") or "") + " " + description
            relation, context, flags = text_context(text)
            industry = str(company.get("industry") or "")
            if re.search(r"staffing|recruitment|consulting|consultoria|recrutamento", industry, re.I) and relation == "self":
                relation = "unknown"
                flags.append("consultancy_or_recruiter_requires_attribution")
            if job.get("reposted"):
                flags.append("reposted_not_independent_source")
            evidence.append(Evidence(provider=self.name, origin_url=job.get("final_url") or job.get("url"),
                source_url=job.get("source_url") or job.get("url"), origin_id=str(job.get("id"))[:200],
                subject_domain=domain, kind="job_posting", products=products_in_text(text), excerpt=text[:1200],
                checked_at=checked_at, published_at=date_value(job.get("date_posted")), first_seen=date_value(job.get("discovered_at")),
                relation=relation, strength="medium", context=context, flags=flags))
        return ProviderOutcome(provider=self.name, state="ambiguous" if mismatched else "ok", evidence=evidence)


class ApolloProvider(Provider):
    name = "apollo"
    URL = "https://api.apollo.io/api/v1/organizations/enrich"

    async def _collect(self, target, checked_at):
        params = {"domain": target.domain}
        if target.company_name:
            params["name"] = target.company_name
        value = await self.transport.request("GET", self.URL, headers={"x-api-key": self.policy.api_key}, params=params)
        organization = value.get("organization")
        if not isinstance(organization, dict):
            raise ValueError("Unexpected organization response")
        if not same_domain(organization.get("primary_domain"), target):
            return ProviderOutcome(provider=self.name, state="ambiguous", error_code="organization_domain_mismatch")
        # Discard contacts, CRM/account data, revenue, personal data and parent/subsidiary technologies.
        metadata = {key: str(organization[key])[:200] for key in ("id", "name", "primary_domain", "industry") if organization.get(key) is not None}
        names = organization.get("technology_names") or []
        if not isinstance(names, list):
            names = []
        products = products_in_text(" ".join(str(name) for name in names[:200]))
        evidence = []
        if products:
            evidence.append(Evidence(provider=self.name, origin_url=self.URL + "?domain=" + target.domain,
                subject_domain=target.domain, kind="provider_signal", products=products,
                excerpt="Tecnologias listadas pelo fornecedor: " + ", ".join(products), checked_at=checked_at,
                relation="self", strength="weak", flags=["provider_has_no_observation_date", "not_direct_technical_confirmation"]))
        # Enrichment time is NOT a technology observation/publication date. Keep unknown dates null.
        return ProviderOutcome(provider=self.name, state="ok", evidence=evidence, metadata=metadata)


class PublicHTMLProvider(Provider):
    name, requires_key = "public_html", False

    def __init__(self, policy=None, fetcher=None):
        super().__init__(policy)
        self.fetcher = fetcher or PublicHTMLFetcher()

    async def _collect(self, target, checked_at):
        page = await self.fetcher.fetch(target.domain)
        soup = BeautifulSoup(page.html, "html.parser")
        references = []
        for node in soup.find_all(["script", "iframe"], limit=200):
            src = node.get("src")
            if not isinstance(src, str):
                continue
            parsed = urlsplit("https:" + src if src.startswith("//") else src)
            host = (parsed.hostname or "").lower()
            # References only. Never fetch these external resources or infer a Sales Cloud license.
            if parsed.scheme != "https" or parsed.username or parsed.password:
                continue
            if host.endswith((".salesforce-scrt.com", ".salesforceliveagent.com")):
                references.append(("Service Cloud", host))
            elif host.endswith(".pardot.com") or host == "pi.pardot.com":
                references.append(("Marketing Cloud", host))
            elif host.endswith(".force.com") and re.search(r"embeddedservice|liveagent", parsed.path, re.I):
                references.append(("Service Cloud", host))
        evidence = []
        if references:
            products = sorted({product for product, _ in references})
            evidence.append(Evidence(provider=self.name, origin_url=page.url, source_url=page.url, subject_domain=target.domain,
                kind="technical_reference", products=products, excerpt="Referências HTML a recursos: " + ", ".join(sorted({host for _, host in references})),
                checked_at=checked_at, last_seen=checked_at, relation="self", strength="strong",
                flags=["html_reference_not_runtime_or_license_verification"]))
        return ProviderOutcome(provider=self.name, state="ok", evidence=evidence)
