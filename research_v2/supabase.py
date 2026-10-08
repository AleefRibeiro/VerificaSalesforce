"""Prepared pilot adapters. No environment loading, provisioning or default activation."""
from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Protocol
from uuid import UUID

import httpx

from .models import Report, utc_now
from .service import ResearchUnavailable, ResearchScope


class SupabaseConfigurationError(ValueError):
    def __init__(self):
        super().__init__("A configuração privada do piloto está incompleta ou inválida.")


def canonical_uuid(value):
    if not isinstance(value, str) or str(UUID(value)) != value:
        raise ValueError("UUID required")
    return value


@dataclass(frozen=True)
class SupabaseConnection:
    project_ref: str
    publishable_key: str = field(repr=False)
    secret_key: str = field(repr=False)

    def __post_init__(self):
        if (not all(isinstance(v, str) for v in (self.project_ref, self.publishable_key, self.secret_key))
                or not re.fullmatch(r"[a-z0-9]{20}", self.project_ref)
                or not re.fullmatch(r"sb_publishable_[A-Za-z0-9_-]{16,256}", self.publishable_key)
                or not re.fullmatch(r"sb_secret_[A-Za-z0-9_-]{16,256}", self.secret_key)):
            raise SupabaseConfigurationError()

    @property
    def origin(self):
        return f"https://{self.project_ref}.supabase.co"


class JSONTransport(Protocol):
    async def request(self, method, url, *, headers, body=None): ...


class SupabaseHTTPTransport:
    """Fixed origin, bounded JSON, no redirects/proxies/retries or response logging."""
    def __init__(self, origin, *, transport=None):
        self.origin, self.transport = origin, transport
        self._concurrency = asyncio.Semaphore(4)

    async def request(self, method, url, *, headers, body=None):
        if not url.startswith(self.origin + "/") or method not in {"GET", "POST"}:
            raise ResearchUnavailable()
        try:
            async with asyncio.timeout(3), self._concurrency, httpx.AsyncClient(transport=self.transport, trust_env=False, follow_redirects=False,
                                        timeout=httpx.Timeout(1.5), limits=httpx.Limits(max_connections=4)) as client:
                async with client.stream(method, url, headers=headers, json=body) as response:
                    if response.status_code != 200 or not re.match(r"application/json(?:\s*;|$)", response.headers.get("content-type", ""), re.I):
                        raise ResearchUnavailable()
                    content = bytearray()
                    async for chunk in response.aiter_bytes():
                        content.extend(chunk)
                        if len(content) > 1_048_576:
                            raise ResearchUnavailable()
                    return json.loads(content)
        except Exception:
            # Discard request/response objects and exception text containing keys or tokens.
            raise ResearchUnavailable() from None


class SupabasePrivateRPC:
    FUNCTIONS = frozenset({"verify_research_session", "charge_research_quota", "get_cached_report", "get_private_report", "save_private_report", "list_private_history", "get_history_report"})

    def __init__(self, connection: SupabaseConnection, transport: JSONTransport):
        self.connection, self.transport = connection, transport

    async def call(self, function, body):
        if function not in self.FUNCTIONS:
            raise ResearchUnavailable()
        return await self.transport.request("POST", self.connection.origin + "/rest/v1/rpc/" + function,
            headers={"apikey": self.connection.secret_key, "Content-Profile": "averon_private",
                     "Accept-Profile": "averon_private", "Content-Type": "application/json"}, body=body)


class SupabaseSessionVerifier:
    def __init__(self, connection, rpc, transport, *, clock=utc_now, allowed_workspace=None):
        self.connection, self.rpc, self.transport, self.clock = connection, rpc, transport, clock
        self.allowed_workspace = allowed_workspace

    async def verify(self, token):
        # Import here to keep research_api's default app independent of this optional adapter.
        from research_api import Principal
        try:
            if not isinstance(token, str) or len(token) > 4096 or token.count(".") != 2 or re.search(r"\s", token):
                return None
            # Supabase Auth verifies the EXACT token before its claims are read below.
            user = await self.transport.request("GET", self.connection.origin + "/auth/v1/user",
                headers={"apikey": self.connection.publishable_key, "Authorization": "Bearer " + token})
            subject = canonical_uuid(user["id"])
            if user.get("is_anonymous") is not False or not user.get("email_confirmed_at"):
                return None
            encoded = token.split(".")[1]
            claims = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))
            # Claims identify the verified session, never its workspace or permissions.
            if claims.get("sub") != subject or claims.get("iss") != self.connection.origin + "/auth/v1" or claims.get("aud") != "authenticated":
                return None
            session_id = canonical_uuid(claims["session_id"])
            exp = claims["exp"]
            if isinstance(exp, bool) or not isinstance(exp, (int, float)) or not math.isfinite(exp):
                return None
            expires = datetime.fromtimestamp(exp, timezone.utc)
            if expires <= self.clock():
                return None
            membership = await self.rpc.call("verify_research_session", {"p_user_id": subject, "p_session_id": session_id})
            if not isinstance(membership, dict):
                return None
            workspace = canonical_uuid(membership["workspace"])
            if self.allowed_workspace is not None and workspace != self.allowed_workspace:
                return None
            permissions = membership["permissions"]
            if not isinstance(permissions, list) or not permissions or any(p not in {"research:read", "research:write"} for p in permissions):
                return None
            if "research:read" not in permissions:
                return None
            return Principal(subject, workspace, frozenset(permissions), expires)
        except Exception:
            return None


class SupabaseResearchStore:
    """Every read/cache/write is scoped to the verified user within the workspace."""
    def __init__(self, rpc):
        self.rpc = rpc

    @staticmethod
    def owner(scope):
        if not isinstance(scope, ResearchScope):
            raise ResearchUnavailable()
        return {"p_workspace": canonical_uuid(scope.workspace), "p_user_id": canonical_uuid(scope.user_id)}

    @staticmethod
    def key_hash(key):
        return hashlib.sha256(json.dumps(key, ensure_ascii=True, separators=(",", ":"), allow_nan=False).encode()).hexdigest()

    @staticmethod
    def validated(value, now, *, historical=False):
        if value is None:
            return None
        try:
            report = Report.model_validate(value)
            canonical_uuid(report.id)
            if (report.synthetic or any(e.synthetic for e in report.evidence)
                    or any(e.synthetic for source in report.sources for e in source.evidence)
                    or report.checked_at.tzinfo is None or report.expires_at.tzinfo is None
                    or report.expires_at <= report.checked_at or report.checked_at > now
                    or (not historical and report.expires_at <= now)):
                raise ValueError()
            return report
        except Exception:
            raise ResearchUnavailable() from None

    async def charge(self, workspace, count):
        try:
            owner = self.owner(workspace)
            if type(count) is not int or not 1 <= count <= 5:
                raise ValueError()
            result = await self.rpc.call("charge_research_quota", {**owner, "p_count": count})
            if type(result) is not bool:
                raise ValueError()
            return result
        except Exception:
            raise ResearchUnavailable() from None

    async def cached(self, workspace, key, now):
        try:
            owner = self.owner(workspace)
            return self.validated(await self.rpc.call("get_cached_report", {**owner, "p_cache_key": self.key_hash(key)}), now)
        except Exception:
            raise ResearchUnavailable() from None

    async def report(self, workspace, report_id, now):
        try:
            owner = self.owner(workspace)
            canonical_uuid(report_id)
        except ValueError:
            return None
        try:
            return self.validated(await self.rpc.call("get_private_report", {**owner, "p_report_id": report_id}), now)
        except Exception:
            raise ResearchUnavailable() from None

    async def save(self, workspace, key, report):
        try:
            owner = self.owner(workspace)
            self.validated(report.model_dump(mode="json"), report.checked_at)
            result = await self.rpc.call("save_private_report", {**owner, "p_cache_key": self.key_hash(key), "p_report": report.model_dump(mode="json")})
            if result is not True:
                raise ValueError()
        except Exception:
            raise ResearchUnavailable() from None

    async def history(self, scope, offset, now):
        from .history import HistoryPage
        try:
            if type(offset) is not int or not 0 <= offset <= 127:
                raise ValueError()
            result = await self.rpc.call("list_private_history", {**self.owner(scope), "p_offset": offset})
            page = HistoryPage.model_validate(result)
            if page.next_offset is not None and page.next_offset != offset + 20:
                raise ValueError()
            if any(item.retained_until <= now or item.checked_at > now for item in page.items):
                raise ValueError()
            return page
        except Exception:
            raise ResearchUnavailable() from None

    async def history_report(self, scope, report_id, now):
        try:
            owner = self.owner(scope)
            canonical_uuid(report_id)
        except ValueError:
            return None
        try:
            return self.validated(await self.rpc.call("get_history_report", {**owner, "p_report_id": report_id}), now, historical=True)
        except Exception:
            raise ResearchUnavailable() from None
