"""Minimal history contract; owner identities are deliberately not returned."""
from datetime import datetime
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

from .models import Status
from .resolution import resolve_target
from .models import TargetInput


class HistoryEntry(BaseModel):
    model_config = ConfigDict(extra="forbid")
    id: UUID
    domain: str = Field(max_length=253)
    checked_at: datetime
    saved_at: datetime
    retained_until: datetime
    status: Status

    @field_validator("domain")
    @classmethod
    def valid_domain(cls, value):
        if resolve_target(TargetInput(domain=value)).domain != value:
            raise ValueError("Canonical domain required")
        return value

    @field_validator("checked_at", "saved_at", "retained_until")
    @classmethod
    def aware(cls, value):
        if value.tzinfo is None:
            raise ValueError("Timezone required")
        return value


class HistoryPage(BaseModel):
    model_config = ConfigDict(extra="forbid")
    items: list[HistoryEntry] = Field(max_length=20)
    next_offset: int | None = Field(default=None, ge=20, le=127)
