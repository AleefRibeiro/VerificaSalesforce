from __future__ import annotations

import asyncio
import ipaddress
import os
import time
from urllib.parse import urlparse

from fastapi import FastAPI
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.requests import Request

from salesforce_scanner.engine import ScanOptions, run_scan
from salesforce_scanner.fetcher import normalize_url


class ScanRequest(BaseModel):
    url: str = Field(..., min_length=3, max_length=2048)


app = FastAPI(title="VerificaSalesforce API", version="1.1.1")

# Per-URL locks — allows concurrent scans for different URLs while
# preventing duplicate work for the exact same URL.
_url_locks: dict[str, asyncio.Lock] = {}
_url_locks_meta: dict[str, int] = {}  # tracks active waiters so we can GC idle locks
_url_locks_guard = asyncio.Lock()

# Simple in-memory result cache with TTL.
_CACHE_TTL_SECONDS = int(os.getenv("SCAN_CACHE_TTL_SECONDS", "3600"))
_result_cache: dict[str, tuple[float, dict]] = {}  # url -> (timestamp, report)

SCAN_RETRY_AFTER_SECONDS = max(1, int(os.getenv("SCAN_RETRY_AFTER_SECONDS", "8")))


def _parse_cors_origins() -> list[str]:
    raw = os.getenv("CORS_ALLOW_ORIGINS", "*").strip()
    if not raw or raw == "*":
        return ["*"]
    return [item.strip() for item in raw.split(",") if item.strip()]


app.add_middleware(
    CORSMiddleware,
    allow_origins=_parse_cors_origins(),
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _error_response(
    status_code: int,
    error: str,
    message: str,
    details: dict | list | str | None = None,
    headers: dict[str, str] | None = None,
) -> JSONResponse:
    payload: dict[str, object] = {"error": error, "message": message}
    if details is not None:
        payload["details"] = details
    return JSONResponse(status_code=status_code, content=payload, headers=headers)


@app.exception_handler(RequestValidationError)
async def validation_exception_handler(_: Request, exc: RequestValidationError) -> JSONResponse:
    return _error_response(
        400,
        "payload_invalid",
        "Payload inválido. Envie JSON no formato: {'url': 'https://empresa.com.br'}.",
        exc.errors(),
    )


@app.exception_handler(StarletteHTTPException)
async def http_exception_handler(_: Request, exc: StarletteHTTPException) -> JSONResponse:
    detail = exc.detail if isinstance(exc.detail, str) else "Erro na requisição"
    return _error_response(exc.status_code, "http_error", detail)


@app.exception_handler(Exception)
async def unhandled_exception_handler(_: Request, __: Exception) -> JSONResponse:
    return _error_response(
        500,
        "internal_error",
        "Erro interno ao processar a solicitação.",
    )


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/scan/status")
async def scan_status() -> dict[str, object]:
    async with _url_locks_guard:
        active_urls = [url for url, lock in _url_locks.items() if lock.locked()]
        concurrent_scans = len(active_urls)

    now = time.monotonic()
    cached_entries = sum(
        1 for ts, _ in _result_cache.values() if now - ts < _CACHE_TTL_SECONDS
    )

    return {
        "status": "ok",
        "concurrent_scans": concurrent_scans,
        "active_urls": active_urls,
        "cached_results": cached_entries,
        "cache_ttl_seconds": _CACHE_TTL_SECONDS,
        "retry_after_seconds": SCAN_RETRY_AFTER_SECONDS,
    }


async def _get_url_lock(url: str) -> asyncio.Lock:
    """Return (and lazily create) the per-URL lock, incrementing its waiter count."""
    async with _url_locks_guard:
        if url not in _url_locks:
            _url_locks[url] = asyncio.Lock()
            _url_locks_meta[url] = 0
        _url_locks_meta[url] += 1
        return _url_locks[url]


async def _release_url_lock(url: str) -> None:
    """Decrement waiter count and garbage-collect the lock when no longer needed."""
    async with _url_locks_guard:
        if url in _url_locks_meta:
            _url_locks_meta[url] -= 1
            if _url_locks_meta[url] <= 0:
                _url_locks.pop(url, None)
                _url_locks_meta.pop(url, None)


def _cache_get(url: str) -> dict | None:
    entry = _result_cache.get(url)
    if entry is None:
        return None
    ts, report = entry
    if time.monotonic() - ts > _CACHE_TTL_SECONDS:
        _result_cache.pop(url, None)
        return None
    return report


def _cache_set(url: str, report: dict) -> None:
    _result_cache[url] = (time.monotonic(), report)


@app.post("/scan", response_model=None)
async def scan(payload: ScanRequest):
    try:
        normalized_url = validate_target_url(payload.url)
    except ValueError as exc:
        return _error_response(400, "invalid_url", str(exc))

    # Fast path: return cached result if still fresh.
    cached = _cache_get(normalized_url)
    if cached is not None:
        return cached

    url_lock = await _get_url_lock(normalized_url)
    try:
        # If another coroutine is already scanning this URL, wait for it to
        # finish and then serve the result from cache rather than scanning twice.
        async with url_lock:
            # Re-check cache after acquiring the lock — a concurrent request
            # for the same URL may have just populated it.
            cached = _cache_get(normalized_url)
            if cached is not None:
                return cached

            options = ScanOptions()

            try:
                report = await asyncio.to_thread(run_scan, normalized_url, options)
            except TimeoutError:
                return _error_response(504, "timeout", "A análise excedeu o tempo limite.")
            except ValueError as exc:
                return _error_response(400, "invalid_url", str(exc))
            except Exception:
                return _error_response(
                    500,
                    "scanner_error",
                    "Erro interno ao executar a análise.",
                )

            access_error = _extract_access_error(report)
            if access_error:
                code, message = access_error
                return _error_response(
                    code,
                    "target_unreachable",
                    message,
                    {
                        "input_url": report.get("input_url"),
                        "normalized_url": report.get("normalized_url"),
                        "errors": report.get("errors", []),
                    },
                )

            # Cache successful results only.
            _cache_set(normalized_url, report)
            return report
    finally:
        await _release_url_lock(normalized_url)


def validate_target_url(raw_url: str) -> str:
    normalized = normalize_url(raw_url)
    parsed = urlparse(normalized)

    if parsed.scheme not in {"http", "https"}:
        raise ValueError("Somente URLs com http:// ou https:// são permitidas.")

    host = (parsed.hostname or "").strip().lower()
    if not host:
        raise ValueError("URL inválida: host ausente.")

    if host in {"localhost", "127.0.0.1", "0.0.0.0", "::1"}:
        raise ValueError("Hosts locais não são permitidos.")

    if host.endswith(".localhost") or host.endswith(".local") or host.endswith(".internal"):
        raise ValueError("Domínios locais/internos não são permitidos.")

    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return normalized

    if (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    ):
        raise ValueError("IPs internos, privados ou reservados não são permitidos.")

    return normalized


def _extract_access_error(report: dict) -> tuple[int, str] | None:
    errors = [str(item).lower() for item in report.get("errors", [])]
    if report.get("evidence"):
        return None

    initial_error = next((item for item in errors if item.startswith("initial_fetch_failed:")), "")
    if not initial_error:
        return None

    if "timeout" in initial_error:
        return (504, "O site alvo não respondeu dentro do timeout configurado.")

    if "ssl_error" in initial_error:
        return (502, "Não foi possível acessar o site alvo devido a erro SSL.")

    return (502, "Não foi possível acessar o site alvo para análise.")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=int(os.getenv("PORT", "8000")))
