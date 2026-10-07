from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Scope(str, Enum):
    DOMAIN = "domain"
    ENTITY = "entity"
    SUBSIDIARY = "subsidiary"
    GROUP = "group"


class Status(str, Enum):
    CONFIRMED = "Confirmado publicamente"
    STRONG = "Forte indício"
    POSSIBLE = "Possível"
    HISTORICAL = "Histórico"
    INCONCLUSIVE = "Inconclusivo"
    ERROR = "Erro"
    AMBIGUOUS = "Ambiguidade"


class TargetInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    domain: str = Field(min_length=3, max_length=253)
    company_name: str | None = Field(default=None, max_length=200)
    scope: Scope = Scope.DOMAIN
    entity_id: str | None = Field(default=None, max_length=100)


class Target(BaseModel):
    domain: str
    url: str
    company_name: str | None = None
    scope: Scope
    entity_id: str | None = None
    resolution: Literal["explicit_domain"] = "explicit_domain"
    legal_identity_verified: Literal[False] = False


class Evidence(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)
    provider: str
    origin_url: str | None = Field(default=None, max_length=2048)
    source_url: str | None = Field(default=None, max_length=2048)
    origin_id: str | None = Field(default=None, max_length=200)
    subject_domain: str | None = None
    scope: Scope = Scope.DOMAIN
    entity_id: str | None = None
    kind: Literal["technical_reference", "official_statement", "job_posting", "provider_signal", "enrichment"]
    products: list[str] = Field(default_factory=list, max_length=10)
    excerpt: str = Field(default="", max_length=1200)
    checked_at: datetime
    published_at: datetime | None = None
    first_seen: datetime | None = None
    last_seen: datetime | None = None
    relation: Literal["self", "client", "unknown"] = "unknown"
    strength: Literal["strong", "medium", "weak"] = "weak"
    context: Literal["current_signal", "migration_away", "unknown"] = "current_signal"
    flags: list[str] = Field(default_factory=list, max_length=20)
    synthetic: bool = False

    @field_validator("checked_at", "published_at", "first_seen", "last_seen")
    @classmethod
    def aware_dates(cls, value: datetime | None) -> datetime | None:
        if value is not None and (value.tzinfo is None or value.utcoffset() is None):
            raise ValueError("Dates require an explicit timezone.")
        return value.astimezone(timezone.utc) if value else None


class ProviderOutcome(BaseModel):
    provider: str
    state: Literal["ok", "disabled", "error", "ambiguous"]
    evidence: list[Evidence] = Field(default_factory=list, max_length=100)
    metadata: dict[str, str | int | None] = Field(default_factory=dict)
    error_code: str | None = None


class Conclusion(BaseModel):
    status: Status
    rationale: str
    products: list[str] = Field(default_factory=list)
    independent_origins: int = 0
    limitations: list[str] = Field(default_factory=list)


class Report(BaseModel):
    id: str
    target: Target
    checked_at: datetime
    expires_at: datetime
    conclusion: Conclusion
    evidence: list[Evidence]
    sources: list[ProviderOutcome]
    coverage: Literal["complete_for_configured_sources", "partial"]
    synthetic: bool = False
    cache_hit: bool = False


class BatchInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    targets: list[TargetInput] = Field(min_length=1, max_length=5)


class BatchResult(BaseModel):
    reports: list[Report]
    requested: int
    unique_targets: int
