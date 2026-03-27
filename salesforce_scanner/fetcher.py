from __future__ import annotations

from dataclasses import dataclass
from typing import Any
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SalesforceScanner/1.0; +https://example.local/scanner)"
)


@dataclass
class FetchResult:
    url: str
    final_url: str | None
    status_code: int | None
    text: str
    error: str | None


def normalize_url(raw_url: str) -> str:
    value = raw_url.strip()
    if not value:
        raise ValueError("URL vazia")

    if value.startswith("//"):
        value = f"https:{value}"
    elif "://" not in value:
        value = f"https://{value}"

    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError(f"URL inválida: {raw_url}")

    return value


def get_origin(url: str) -> str:
    parsed = urlparse(url)
    return f"{parsed.scheme}://{parsed.netloc}"


def create_session() -> requests.Session:
    session = requests.Session()
    session.headers.update({"User-Agent": DEFAULT_USER_AGENT})
    return session


def fetch_text(
    session: requests.Session,
    url: str,
    timeout: int = 12,
    max_bytes: int = 2_000_000,
) -> FetchResult:
    try:
        response = session.get(url, timeout=timeout, allow_redirects=True)
        raw = response.content[:max_bytes]
        text = raw.decode(response.encoding or "utf-8", errors="replace")
        return FetchResult(
            url=url,
            final_url=response.url,
            status_code=response.status_code,
            text=text,
            error=None,
        )
    except requests.exceptions.Timeout:
        return FetchResult(url=url, final_url=None, status_code=None, text="", error="timeout")
    except requests.exceptions.SSLError:
        return FetchResult(url=url, final_url=None, status_code=None, text="", error="ssl_error")
    except requests.RequestException as exc:
        return FetchResult(
            url=url,
            final_url=None,
            status_code=None,
            text="",
            error=f"request_error: {exc}",
        )


def extract_page_assets(html: str, base_url: str) -> dict[str, list[str]]:
    soup = BeautifulSoup(html or "", "html.parser")

    script_urls: list[str] = []
    inline_scripts: list[str] = []
    iframes: list[str] = []
    links: list[str] = []

    for tag in soup.find_all("script"):
        src = tag.get("src")
        if src:
            script_urls.append(urljoin(base_url, src))
        else:
            content = tag.string or tag.text or ""
            if content.strip():
                inline_scripts.append(content)

    for frame in soup.find_all("iframe"):
        src = frame.get("src")
        if src:
            iframes.append(urljoin(base_url, src))

    for anchor in soup.find_all("a"):
        href = anchor.get("href")
        if href:
            links.append(urljoin(base_url, href))

    return {
        "script_urls": _unique_http_urls(script_urls),
        "inline_scripts": inline_scripts,
        "iframes": _unique_http_urls(iframes),
        "links": _unique_http_urls(links),
    }


def download_scripts(
    session: requests.Session,
    script_urls: list[str],
    timeout: int = 10,
    max_scripts: int = 25,
    max_bytes: int = 500_000,
) -> tuple[dict[str, str], list[str]]:
    scripts_content: dict[str, str] = {}
    errors: list[str] = []

    for script_url in script_urls[:max_scripts]:
        result = fetch_text(session, script_url, timeout=timeout, max_bytes=max_bytes)
        if result.error:
            errors.append(f"script_fetch_failed: {script_url} ({result.error})")
            continue
        scripts_content[script_url] = result.text

    return scripts_content, errors


def fetch_public_resources(
    session: requests.Session,
    reference_url: str,
    timeout: int = 10,
) -> tuple[dict[str, FetchResult], list[str]]:
    origin = get_origin(reference_url)
    resources = {
        "robots.txt": urljoin(f"{origin}/", "robots.txt"),
        "sitemap.xml": urljoin(f"{origin}/", "sitemap.xml"),
    }

    results: dict[str, FetchResult] = {}
    errors: list[str] = []

    for name, url in resources.items():
        result = fetch_text(session, url, timeout=timeout, max_bytes=2_000_000)
        results[name] = result
        if result.error:
            errors.append(f"{name}_fetch_failed: {result.error}")

    return results, errors


def render_with_playwright(
    url: str,
    timeout_ms: int = 20_000,
    max_requests: int = 250,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "final_url": url,
        "rendered_html": "",
        "network_requests": [],
        "domains_called": [],
        "redirect_chain": [],
        "cookies": [],
        "error": None,
    }

    try:
        from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
        from playwright.sync_api import sync_playwright
    except Exception as exc:  # pragma: no cover
        payload["error"] = f"playwright_import_error: {exc}"
        return payload

    try:
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            context = browser.new_context(ignore_https_errors=True)
            page = context.new_page()

            request_urls: list[str] = []
            domains: set[str] = set()
            redirect_chain: list[str] = []

            def on_request(request: Any) -> None:
                if len(request_urls) >= max_requests:
                    return
                request_urls.append(request.url)
                host = urlparse(request.url).hostname
                if host:
                    domains.add(host.lower())

            def on_main_frame_navigation(frame: Any) -> None:
                if frame != page.main_frame:
                    return
                current = frame.url
                if current and (not redirect_chain or redirect_chain[-1] != current):
                    redirect_chain.append(current)

            page.on("request", on_request)
            page.on("framenavigated", on_main_frame_navigation)

            try:
                page.goto(url, wait_until="networkidle", timeout=timeout_ms)
            except PlaywrightTimeoutError:
                page.goto(url, wait_until="domcontentloaded", timeout=timeout_ms)

            payload["final_url"] = page.url
            payload["rendered_html"] = page.content()
            payload["network_requests"] = request_urls
            payload["domains_called"] = sorted(domains)
            payload["redirect_chain"] = redirect_chain
            payload["cookies"] = context.cookies()

            context.close()
            browser.close()
    except Exception as exc:
        payload["error"] = f"playwright_runtime_error: {exc}"

    return payload


def _unique_http_urls(urls: list[str]) -> list[str]:
    unique: list[str] = []
    seen: set[str] = set()
    for item in urls:
        parsed = urlparse(item)
        if parsed.scheme not in {"http", "https"}:
            continue
        normalized = item.strip()
        if normalized in seen:
            continue
        seen.add(normalized)
        unique.append(normalized)
    return unique
