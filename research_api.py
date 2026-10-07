"""Separate private API. Deployment remains on main:app until explicitly approved."""
from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from fastapi import FastAPI, HTTPException, Request

from research_v2.models import BatchInput, BatchResult, Report, TargetInput, utc_now
from research_v2.resolution import InvalidTarget
from research_v2.service import ResearchLimit, ResearchService


@dataclass(frozen=True)
class Principal:
    subject: str
    workspace: str
    permissions: frozenset[str]
    expires_at: datetime


class SessionVerifier(Protocol):
    """Validate an opaque token against trusted auth; never trust client claims/roles."""
    async def verify(self, token: str) -> Principal | None: ...


class DenyAllSessions:
    async def verify(self, token: str) -> None:
        return None


class PrivateResearchMiddleware:
    def __init__(self, app, verifier, clock=utc_now, max_body_bytes=32768):
        self.app, self.verifier, self.clock, self.max_body_bytes = app, verifier, clock, max_body_bytes

    async def reject(self, send, status, message):
        body = json.dumps({"detail": message}).encode()
        await send({"type": "http.response.start", "status": status, "headers": [(b"content-type", b"application/json"), (b"cache-control", b"no-store")]})
        await send({"type": "http.response.body", "body": body})

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http" or not (scope["path"] == "/v2" or scope["path"].startswith("/v2/")):
            return await self.app(scope, receive, send)
        authorizations = [value for key, value in scope.get("headers", []) if key.lower() == b"authorization"]
        principal = None
        if len(authorizations) == 1 and len(authorizations[0]) <= 4096:
            header = authorizations[0].decode("latin-1")
            if header.startswith("Bearer ") and header[7:] and not any(c.isspace() for c in header[7:]):
                try:
                    principal = await asyncio.wait_for(self.verifier.verify(header[7:]), 2)
                except Exception:
                    principal = None
        try:
            valid = (isinstance(principal, Principal) and isinstance(principal.subject, str) and 0 < len(principal.subject) <= 200
                     and isinstance(principal.workspace, str) and 0 < len(principal.workspace) <= 200
                     and isinstance(principal.permissions, frozenset) and isinstance(principal.expires_at, datetime)
                     and principal.expires_at.tzinfo is not None and principal.expires_at > self.clock())
        except Exception:
            valid = False
        if not valid:
            return await self.reject(send, 401, "Sessão verificada necessária.")
        required = "research:write" if scope["method"] == "POST" else "research:read"
        if required not in principal.permissions:
            return await self.reject(send, 403, "Permissão de pesquisa necessária.")
        scope.setdefault("state", {})["principal"] = principal
        if scope["method"] == "POST":
            content = bytearray()
            try:
                async with asyncio.timeout(5):
                    while True:
                        message = await receive()
                        if message["type"] == "http.disconnect":
                            return
                        content.extend(message.get("body", b""))
                        if len(content) > self.max_body_bytes:
                            return await self.reject(send, 413, "Corpo da requisição excede o limite.")
                        if not message.get("more_body"):
                            break
            except TimeoutError:
                return await self.reject(send, 408, "Tempo de envio excedido.")
            consumed = False

            async def limited_receive():
                nonlocal consumed
                if not consumed:
                    consumed = True
                    return {"type": "http.request", "body": bytes(content), "more_body": False}
                return await receive()
        else:
            limited_receive = receive

        async def private_send(message):
            if message["type"] == "http.response.start":
                message["headers"] = [*message.get("headers", []), (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
            await send(message)
        return await self.app(scope, limited_receive, private_send)


def create_app(*, verifier: SessionVerifier | None = None, service: ResearchService | None = None, clock=utc_now) -> FastAPI:
    service = service or ResearchService(clock=clock)
    if service.allow_synthetic:
        raise ValueError("An API cannot serve a service configured to allow synthetic research.")
    app = FastAPI(title="Averon Private Research API", version="2.0.0", docs_url=None, redoc_url=None, openapi_url=None)
    app.add_middleware(PrivateResearchMiddleware, verifier=verifier or DenyAllSessions(), clock=clock)

    @app.get("/health")
    async def health():
        return {"status": "ok", "version": "2.0.0", "research_access": "requires_verified_session"}

    @app.post("/v2/research", response_model=Report)
    async def research(value: TargetInput, request: Request):
        try:
            return await service.research(request.state.principal.workspace, value)
        except InvalidTarget as exc:
            raise HTTPException(422, str(exc)) from exc
        except ResearchLimit as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.post("/v2/research/batch", response_model=BatchResult)
    async def batch(value: BatchInput, request: Request):
        try:
            return await service.batch(request.state.principal.workspace, value.targets)
        except InvalidTarget as exc:
            raise HTTPException(422, str(exc)) from exc
        except ResearchLimit as exc:
            raise HTTPException(429, str(exc)) from exc

    @app.get("/v2/reports/{report_id}", response_model=Report)
    async def get_report(report_id: str, request: Request):
        report = await service.store.report(request.state.principal.workspace, report_id, clock())
        if report is None:
            raise HTTPException(404, "Relatório não disponível.")
        return report

    return app


app = create_app()
