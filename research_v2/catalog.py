"""Reviewed public facts, separate from private reports. No automatic publication."""
from __future__ import annotations

import asyncio
import unicodedata
from datetime import datetime
from typing import Literal
from urllib.parse import urlsplit, urlunsplit
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from .models import TargetInput, utc_now
from .resolution import resolve_target
from .service import ResearchUnavailable, ResearchScope

CatalogStatus = Literal["Uso confirmado", "Indício", "Não confirmado"]


def search_key(value: str) -> str:
    value = value.strip()
    if "://" in value or ("." in value and " " not in value):
        try:
            return resolve_target(TargetInput(domain=value)).domain
        except ValueError:
            pass
    return " ".join("".join(c for c in unicodedata.normalize("NFKD", value.lower()) if not unicodedata.combining(c)).split())


def source_url(value: str) -> str:
    url = urlsplit(value)
    if (url.scheme != "https" or not url.hostname or url.username or url.password or url.port
            or len(value) > 2048 or any(c.isspace() for c in value)):
        raise ValueError("Fonte HTTPS pública necessária")
    resolve_target(TargetInput(domain=url.hostname))
    # No fragments or query strings with possible personal tracking/secrets are published.
    if url.query or url.fragment:
        raise ValueError("Use o link da fonte sem parâmetros ou fragmentos")
    return urlunsplit(("https", url.netloc.lower(), url.path or "/", "", ""))


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class ContributionInput(StrictModel):
    domain: str = Field(min_length=3, max_length=253)
    company_name: str = Field(min_length=2, max_length=200)
    action: Literal["confirmar", "contestar"]
    reason: str = Field(min_length=20, max_length=1200)
    source_url: str = Field(max_length=2048)
    published_at: datetime | None = None

    @field_validator("domain")
    @classmethod
    def domain_valid(cls, value):
        return resolve_target(TargetInput(domain=value)).domain

    @field_validator("company_name", "reason")
    @classmethod
    def clean_text(cls, value):
        value = " ".join(value.split())
        if any(ord(c) < 32 for c in value):
            raise ValueError("Texto inválido")
        return value

    @field_validator("source_url")
    @classmethod
    def url_valid(cls, value):
        return source_url(value)

    @field_validator("published_at")
    @classmethod
    def date_valid(cls, value):
        if value is not None and (value.tzinfo is None or value > utc_now()):
            raise ValueError("Data da fonte inválida")
        return value

    @model_validator(mode="after")
    def lengths_after_cleaning(self):
        if len(self.company_name) < 2 or len(self.reason) < 20:
            raise ValueError("Nome e motivo necessários")
        return self


class ReviewInput(StrictModel):
    decision: Literal["aprovar", "rejeitar"]
    qualification: str = Field(min_length=20, max_length=1200)
    status: CatalogStatus | None = None
    direct_evidence: bool = False

    @model_validator(mode="after")
    def valid_decision(self):
        self.qualification = " ".join(self.qualification.split())
        if len(self.qualification) < 20 or (self.decision == "aprovar" and self.status is None):
            raise ValueError("Decisão precisa de justificativa e classificação")
        if self.decision == "rejeitar" and (self.status is not None or self.direct_evidence):
            raise ValueError("Rejeição não altera o catálogo")
        return self


class Contribution(ContributionInput):
    id: UUID
    submitted_at: datetime
    state: Literal["pendente", "aprovada", "rejeitada"] = "pendente"
    reviewed_at: datetime | None = None
    review_note: str | None = None

    @field_validator("submitted_at", "reviewed_at")
    @classmethod
    def aware_history(cls, value):
        if value is not None and (value.tzinfo is None or value > utc_now()):
            raise ValueError("Data de revisão inválida")
        return value


class PublicEvidence(StrictModel):
    source_url: str
    published_at: datetime | None
    reviewed_at: datetime
    qualification: str = Field(min_length=20, max_length=1200)
    action: Literal["confirmar", "contestar"]

    _url = field_validator("source_url")(source_url)
    _dates = field_validator("published_at", "reviewed_at")(Contribution.date_valid.__func__)


class Company(StrictModel):
    domain: str = Field(max_length=253)
    company_name: str = Field(min_length=2, max_length=200)
    status: CatalogStatus
    qualification: str = Field(min_length=20, max_length=1200)
    reviewed_at: datetime
    evidence: list[PublicEvidence] = Field(min_length=1, max_length=20)
    _domain = field_validator("domain")(Contribution.domain_valid.__func__)
    _date = field_validator("reviewed_at")(Contribution.date_valid.__func__)


class CatalogPage(StrictModel):
    items: list[Company] = Field(max_length=20)
    next_offset: int | None = Field(default=None, ge=20, le=10000)


class ContributionPage(StrictModel):
    items: list[Contribution] = Field(max_length=20)
    next_offset: int | None = Field(default=None, ge=20, le=10000)


class CatalogConflict(ValueError):
    pass


def validate_approval(contribution, review):
    if review.status == "Uso confirmado" and (not review.direct_evidence or contribution.published_at is None
                                                or contribution.action != "confirmar"):
        raise CatalogConflict("Uso confirmado exige evidência direta, datada e revisada.")


class UnavailableCatalog:
    async def search(self, *args):
        raise ResearchUnavailable()

    detail = submit = own = pending = review = search


class MemoryCatalogStore:
    """Offline test adapter only; never installed by the configured pilot factory."""
    def __init__(self, workspace, moderator, *, clock=utc_now):
        self.workspace, self.moderator, self.clock = workspace, moderator, clock
        self._companies, self._contributions = {}, {}
        self._lock = asyncio.Lock()

    def _scope(self, owner):
        if not isinstance(owner, ResearchScope) or owner.workspace != self.workspace:
            raise ResearchUnavailable()

    @staticmethod
    def _page(values, offset, cls):
        return cls(items=values[offset:offset+20], next_offset=offset+20 if len(values)>offset+20 else None)

    async def search(self, query, offset=0):
        key = search_key(query)
        values = sorted((c.model_copy(deep=True, update={"evidence": c.evidence[-1:]}) for c in self._companies.values()
                         if not key or search_key(c.company_name).startswith(key) or c.domain.startswith(key)), key=lambda c:c.domain)
        return self._page(values, offset, CatalogPage)

    async def detail(self, domain):
        value = self._companies.get(domain)
        return value.model_copy(deep=True) if value else None

    async def submit(self, owner, value):
        self._scope(owner)
        async with self._lock:
            pending = [c for o,c in self._contributions.values() if o==owner and c.state=="pendente"]
            if len(pending)>=20 or len(self._contributions)>=10000:
                raise CatalogConflict("Limite de contribuições pendentes atingido.")
            if any(c.domain==value.domain and c.action==value.action and c.source_url==value.source_url for c in pending):
                raise CatalogConflict("Esta contribuição já aguarda revisão.")
            entry = Contribution(**value.model_dump(), id=uuid4(), submitted_at=self.clock())
            self._contributions[str(entry.id)] = (owner,entry)
            return entry.model_copy(deep=True)

    async def own(self, owner, offset=0):
        self._scope(owner)
        return self._page(sorted((c.model_copy(deep=True) for o,c in self._contributions.values() if o==owner),
                                key=lambda c:(c.submitted_at,str(c.id)),reverse=True),offset,ContributionPage)

    async def pending(self, owner, offset=0):
        self._scope(owner)
        if owner.user_id!=self.moderator:
            raise ResearchUnavailable()
        return self._page(sorted((c.model_copy(deep=True) for _,c in self._contributions.values() if c.state=="pendente"),
                                key=lambda c:(c.submitted_at,str(c.id))),offset,ContributionPage)

    async def review(self, owner, entry_id, value):
        self._scope(owner)
        if owner.user_id!=self.moderator:
            raise ResearchUnavailable()
        async with self._lock:
            pair = self._contributions.get(entry_id)
            if not pair or pair[1].state!="pendente":
                raise CatalogConflict("Contribuição indisponível ou já revisada.")
            entry = pair[1]
            validate_approval(entry,value)
            now = self.clock()
            entry.state = "aprovada" if value.decision=="aprovar" else "rejeitada"
            entry.reviewed_at, entry.review_note = now, value.qualification
            if value.decision=="aprovar":
                previous = self._companies.get(entry.domain)
                evidence = (previous.evidence if previous else []) + [PublicEvidence(source_url=entry.source_url,
                    published_at=entry.published_at, reviewed_at=now, qualification=value.qualification, action=entry.action)]
                self._companies[entry.domain] = Company(domain=entry.domain, company_name=entry.company_name,
                    status=value.status, qualification=value.qualification, reviewed_at=now, evidence=evidence[-20:])
            return entry.model_copy(deep=True)


class SupabaseCatalogStore:
    def __init__(self, rpc, workspace):
        self.rpc, self.workspace = rpc, workspace

    async def _call(self, function, args, model):
        try:
            value = await self.rpc.call(function,{"p_workspace":self.workspace,**args})
            if isinstance(value,dict) and value.get("conflict") is True:
                raise CatalogConflict("Contribuição indisponível, duplicada ou classificação incompatível.")
            return model.model_validate(value) if value is not None else None
        except CatalogConflict:
            raise
        except Exception:
            raise ResearchUnavailable() from None

    def _owner(self, owner):
        from .supabase import canonical_uuid
        if not isinstance(owner,ResearchScope) or owner.workspace!=self.workspace:
            raise ResearchUnavailable()
        return {"p_user_id":canonical_uuid(owner.user_id)}

    async def search(self, query, offset=0):
        value = await self._call("search_public_catalog",{"p_query":search_key(query),"p_offset":offset},CatalogPage)
        if value is None: raise ResearchUnavailable()
        return value

    async def detail(self, domain):
        return await self._call("get_public_company",{"p_domain":domain},Company)

    async def submit(self, owner, value):
        result = await self._call("submit_catalog_contribution",{**self._owner(owner),"p_contribution":value.model_dump(mode="json")},Contribution)
        if result is None: raise ResearchUnavailable()
        return result

    async def own(self, owner, offset=0):
        value = await self._call("list_catalog_contributions",{**self._owner(owner),"p_offset":offset,"p_moderation":False},ContributionPage)
        if value is None: raise ResearchUnavailable()
        return value

    async def pending(self, owner, offset=0):
        value = await self._call("list_catalog_contributions",{**self._owner(owner),"p_offset":offset,"p_moderation":True},ContributionPage)
        if value is None: raise ResearchUnavailable()
        return value

    async def review(self, owner, entry_id, value):
        result = await self._call("review_catalog_contribution",{**self._owner(owner),"p_id":entry_id,"p_review":value.model_dump(mode="json")},Contribution)
        if result is None: raise ResearchUnavailable()
        return result
