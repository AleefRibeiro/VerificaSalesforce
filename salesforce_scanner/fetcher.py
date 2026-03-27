from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass
from html import unescape
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import httpx
import requests
from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36"
)

DEFAULT_REQUEST_HEADERS = {
    "User-Agent": DEFAULT_USER_AGENT,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "pt-BR,pt;q=0.9,en-US;q=0.8,en;q=0.7",
    "Cache-Control": "no-cache",
}

_NON_PAGE_EXTENSIONS = {
    ".js",
    ".css",
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".svg",
    ".ico",
    ".webp",
    ".woff",
    ".woff2",
    ".ttf",
    ".eot",
    ".pdf",
    ".zip",
    ".rar",
    ".7z",
    ".mp4",
    ".mp3",
    ".avi",
    ".mov",
    ".json",
    ".xml",
}

_LOC_TAG_PATTERN = re.compile(r"<loc>\s*(.*?)\s*</loc>", re.IGNORECASE | re.DOTALL)
_ROBOTS_SITEMAP_PATTERN = re.compile(r"(?im)^\s*sitemap:\s*(\S+)\s*$")

SCRIPT_PATH_PRIORITY_TERMS = (
    "salesforce",
    "force",
    "embedded",
    "service",
    "chat",
    "liveagent",
    "support",
    "community",
    "experience",
    "pardot",
    "marketingcloud",
)

HOST_PRIORITY_HINTS = (
    "salesforce",
    "force.com",
    "visualforce",
    "liveagent",
    "exacttarget",
    "marketingcloud",
    "pardot",
)

SUBDOMAIN_PRIORITY_TERMS = (
    "salesforce",
    "help",
    "support",
    "portal",
    "community",
    "communities",
    "login",
    "members",
    "customers",
    "customer",
    "selfservice",
    "experience",
    "partners",
    "partner",
    "cases",
    "case",
    "service",
    "atendimento",
    "sac",
    "chat",
)

SALESFORCE_BRAND_SUFFIXES = (
    "my.salesforce.com",
    "lightning.force.com",
    "my.site.com",
    "salesforce-sites.com",
    "service.force.com",
    "visualforce.com",
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
    session.headers.update(DEFAULT_REQUEST_HEADERS)
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
    reference_url: str = "",
    timeout: int = 10,
    max_scripts: int = 25,
    max_bytes: int = 500_000,
) -> tuple[dict[str, str], list[str]]:
    scripts_content: dict[str, str] = {}
    errors: list[str] = []

    prioritized_scripts = prioritize_script_urls(
        script_urls=script_urls,
        reference_url=reference_url,
        max_scripts=max_scripts,
    )

    for script_url in prioritized_scripts:
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


def discover_public_surface(
    session: requests.Session,
    start_url: str,
    seed_links: list[str] | None = None,
    robots_txt: str = "",
    sitemap_xml: str = "",
    timeout: int = 10,
    max_pages: int = 20,
    max_depth: int = 1,
    max_sitemaps: int = 8,
    max_sitemap_urls: int = 600,
    max_links_per_page: int = 40,
    max_subdomains: int = 40,
) -> dict[str, Any]:
    seed_links = seed_links or []
    errors: list[str] = []

    candidate_pages: list[str] = []
    candidate_pages_seen: set[str] = set()

    sitemaps_checked: list[str] = []
    sitemaps_seen: set[str] = set()

    def add_candidate_page(url: str) -> None:
        normalized = _normalize_crawl_url(url)
        if not normalized:
            return
        if not _is_internal_url(normalized, start_url):
            return
        if not _is_probable_page_url(normalized):
            return
        if normalized in candidate_pages_seen:
            return
        if len(candidate_pages) >= max_sitemap_urls:
            return
        candidate_pages_seen.add(normalized)
        candidate_pages.append(normalized)

    for link in seed_links:
        add_candidate_page(link)

    subdomain_urls, subdomain_errors = discover_subdomains_from_ct_logs(
        session=session,
        reference_url=start_url,
        timeout=timeout,
        max_subdomains=max_subdomains,
    )
    errors.extend(subdomain_errors)
    for subdomain_url in subdomain_urls:
        add_candidate_page(subdomain_url)

    sitemap_queue: deque[str] = deque()

    for sitemap_url in extract_sitemaps_from_robots(robots_txt):
        normalized = _normalize_crawl_url(sitemap_url)
        if normalized and _is_internal_url(normalized, start_url):
            sitemap_queue.append(normalized)

    default_sitemap_url = urljoin(f"{get_origin(start_url)}/", "sitemap.xml")
    sitemap_queue.append(default_sitemap_url)

    for loc in extract_sitemap_locations(sitemap_xml):
        if _looks_like_sitemap_url(loc):
            normalized_loc = _normalize_crawl_url(loc)
            if normalized_loc and _is_internal_url(normalized_loc, start_url):
                sitemap_queue.append(normalized_loc)
        else:
            add_candidate_page(loc)

    while sitemap_queue and len(sitemaps_checked) < max_sitemaps:
        sitemap_url = _normalize_crawl_url(sitemap_queue.popleft())
        if not sitemap_url or sitemap_url in sitemaps_seen:
            continue
        sitemaps_seen.add(sitemap_url)
        sitemaps_checked.append(sitemap_url)

        result = fetch_text(session, sitemap_url, timeout=timeout, max_bytes=2_000_000)
        if result.error:
            errors.append(f"sitemap_fetch_failed: {sitemap_url} ({result.error})")
            continue

        for loc in extract_sitemap_locations(result.text):
            if _looks_like_sitemap_url(loc):
                nested = _normalize_crawl_url(loc)
                if nested and _is_internal_url(nested, start_url) and nested not in sitemaps_seen:
                    if len(sitemaps_seen) + len(sitemap_queue) < max_sitemaps * 3:
                        sitemap_queue.append(nested)
            else:
                add_candidate_page(loc)

    visited_pages: list[str] = []
    visited_seen: set[str] = set()
    queued_seen: set[str] = set()

    discovered_html: list[str] = []
    discovered_links: list[str] = []
    discovered_links_seen: set[str] = set()

    discovered_script_urls: list[str] = []
    discovered_script_urls_seen: set[str] = set()

    discovered_inline_scripts: list[str] = []
    discovered_inline_seen: set[str] = set()

    queue: deque[tuple[str, int]] = deque()

    def enqueue(url: str, depth: int) -> None:
        normalized = _normalize_crawl_url(url)
        if not normalized:
            return
        if not _is_internal_url(normalized, start_url):
            return
        if normalized in queued_seen or normalized in visited_seen:
            return
        queued_seen.add(normalized)
        queue.append((normalized, depth))

    enqueue(start_url, 0)
    for candidate in candidate_pages:
        enqueue(candidate, 1)

    while queue and len(visited_pages) < max_pages:
        url, depth = queue.popleft()
        visited_seen.add(url)

        result = fetch_text(session, url, timeout=timeout, max_bytes=1_500_000)
        if result.error:
            errors.append(f"discover_fetch_failed: {url} ({result.error})")
            continue

        final_url = _normalize_crawl_url(result.final_url or url)
        if not final_url or not _is_internal_url(final_url, start_url):
            continue

        if final_url in visited_pages:
            continue

        visited_pages.append(final_url)
        discovered_html.append(result.text)

        assets = extract_page_assets(result.text, final_url)

        for script_url in assets["script_urls"]:
            normalized_script = _normalize_crawl_url(script_url)
            if not normalized_script:
                continue
            if normalized_script in discovered_script_urls_seen:
                continue
            discovered_script_urls_seen.add(normalized_script)
            discovered_script_urls.append(normalized_script)

        for inline_script in assets["inline_scripts"]:
            cleaned = inline_script.strip()
            if not cleaned:
                continue
            if cleaned in discovered_inline_seen:
                continue
            if len(discovered_inline_scripts) >= 400:
                break
            discovered_inline_seen.add(cleaned)
            discovered_inline_scripts.append(cleaned)

        internal_links: list[str] = []
        for link in assets["links"] + assets["iframes"]:
            normalized_link = _normalize_crawl_url(link)
            if not normalized_link:
                continue
            if not _is_internal_url(normalized_link, start_url):
                continue
            if not _is_probable_page_url(normalized_link):
                continue

            if normalized_link not in discovered_links_seen:
                discovered_links_seen.add(normalized_link)
                discovered_links.append(normalized_link)

            internal_links.append(normalized_link)

        if depth < max_depth:
            for link in internal_links[:max_links_per_page]:
                enqueue(link, depth + 1)

    return {
        "pages_visited": visited_pages,
        "page_html": discovered_html,
        "links_found": discovered_links,
        "script_urls": discovered_script_urls,
        "inline_scripts": discovered_inline_scripts,
        "subdomain_urls": subdomain_urls,
        "sitemaps_checked": sitemaps_checked,
        "sitemap_urls": candidate_pages,
        "errors": errors,
    }


def extract_sitemap_locations(xml_text: str) -> list[str]:
    if not xml_text:
        return []

    locs: list[str] = []
    for match in _LOC_TAG_PATTERN.findall(xml_text):
        value = unescape(match).strip()
        if not value:
            continue
        locs.append(value)

    return _unique_http_urls(locs)


def extract_sitemaps_from_robots(robots_text: str) -> list[str]:
    if not robots_text:
        return []
    return _unique_http_urls(_ROBOTS_SITEMAP_PATTERN.findall(robots_text))


def discover_subdomains_from_ct_logs(
    session: requests.Session,
    reference_url: str,
    timeout: int = 10,
    max_subdomains: int = 40,
) -> tuple[list[str], list[str]]:
    errors: list[str] = []
    host = urlparse(reference_url).hostname
    if not host:
        return [], errors

    domain = _domain_key(host)
    if not domain:
        return [], errors

    query_url = f"https://crt.sh/?q=%25.{domain}&output=json"
    result = fetch_text(session, query_url, timeout=timeout, max_bytes=4_000_000)
    if result.error:
        errors.append(f"crtsh_query_failed: {result.error}")
        return [], errors

    try:
        import json

        payload = json.loads(result.text or "[]")
    except Exception as exc:
        errors.append(f"crtsh_parse_failed: {exc}")
        return [], errors

    hosts_seen: set[str] = set()
    hosts_ranked: list[str] = []

    for row in payload:
        name_value = str(row.get("name_value", "")).strip()
        if not name_value:
            continue
        for item in name_value.splitlines():
            cleaned = item.strip().lower().lstrip("*.").strip(".")
            if not cleaned:
                continue
            if _domain_key(cleaned) != domain:
                continue
            if cleaned in hosts_seen:
                continue
            hosts_seen.add(cleaned)
            hosts_ranked.append(cleaned)

    if not hosts_ranked:
        return [], errors

    hosts_ranked.sort(
        key=lambda h: (
            0 if any(term in h for term in SUBDOMAIN_PRIORITY_TERMS) else 1,
            len(h),
            h,
        )
    )

    urls = [f"https://{host_name}/" for host_name in hosts_ranked[:max(0, max_subdomains)]]
    return urls, errors


def discover_salesforce_brand_domains(
    session: requests.Session,
    reference_url: str,
    timeout: int = 8,
    max_domains: int = 20,
    max_tokens: int = 3,
) -> tuple[list[str], list[str]]:
    """Probe CT logs for brand-related domains under known Salesforce suffixes.

    This phase is passive and independent from the target homepage response,
    which helps when the root site is protected by anti-bot/WAF controls.
    """
    errors: list[str] = []
    host = urlparse(reference_url).hostname
    if not host:
        return [], errors

    tokens = _company_tokens(host, max_tokens=max_tokens)
    if not tokens:
        return [], errors

    found_hosts: set[str] = set()

    for token in tokens:
        query_url = f"https://crt.sh/?q={token}&output=json"
        result = fetch_text(
            session,
            query_url,
            timeout=max(3, timeout),
            max_bytes=4_000_000,
        )
        if result.error:
            errors.append(f"crtsh_brand_probe_failed: {token} ({result.error})")
            continue

        try:
            import json

            payload = json.loads(result.text or "[]")
        except Exception as exc:
            errors.append(f"crtsh_brand_probe_parse_failed: {token} ({exc})")
            continue

        for row in payload:
            name_value = str(row.get("name_value", "")).strip()
            if not name_value:
                continue
            for item in name_value.splitlines():
                cleaned = item.strip().lower().lstrip("*.").strip(".")
                if not cleaned:
                    continue
                if token not in cleaned:
                    continue
                if not any(cleaned.endswith(suffix) for suffix in SALESFORCE_BRAND_SUFFIXES):
                    continue
                found_hosts.add(cleaned)

        if len(found_hosts) >= max_domains:
            break

    if not found_hosts:
        return [], errors

    ranked_hosts = sorted(
        found_hosts,
        key=lambda host_name: (
            0 if host_name.endswith(".my.salesforce.com") else 1,
            0 if host_name.endswith(".lightning.force.com") else 1,
            0 if host_name.endswith(".service.force.com") else 1,
            len(host_name),
            host_name,
        ),
    )
    urls = [f"https://{host_name}/" for host_name in ranked_hosts[: max(1, max_domains)]]
    return urls, errors


def prioritize_script_urls(
    script_urls: list[str],
    reference_url: str,
    max_scripts: int,
) -> list[str]:
    ref_host = (urlparse(reference_url).hostname or "").lower()
    ref_domain_key = _domain_key(ref_host) if ref_host else ""

    scored: list[tuple[int, int, str]] = []
    for idx, url in enumerate(_unique_http_urls(script_urls)):
        parsed = urlparse(url)
        host = (parsed.hostname or "").lower()
        path = (parsed.path or "").lower()
        score = 0

        if any(hint in host for hint in HOST_PRIORITY_HINTS):
            score += 120

        host_key = _domain_key(host) if host else ""
        if ref_domain_key and host_key == ref_domain_key:
            score += 80
        elif ref_host and (host == ref_host or host.endswith(f".{ref_host}")):
            score += 50

        for term in SCRIPT_PATH_PRIORITY_TERMS:
            if term in path:
                score += 18

        if "bundle" in path or "chunk" in path or "main" in path:
            score += 8

        if path.endswith(".js"):
            score += 4

        scored.append((score, -idx, url))

    scored.sort(reverse=True)
    return [url for _, _, url in scored[: max(1, max_scripts)]]


def serialize_cookies_for_analysis(cookies: list[dict]) -> tuple[list[str], list[dict]]:
    cookie_strings: list[str] = []
    structured: list[dict] = []

    for cookie in cookies:
        name = str(cookie.get("name", "")).strip()
        if not name:
            continue

        domain = str(cookie.get("domain", "")).strip().lower()
        path = str(cookie.get("path", "")).strip() or "/"
        secure = bool(cookie.get("secure", False))
        http_only = bool(cookie.get("httpOnly", False))
        same_site = str(cookie.get("sameSite", "")).strip() or "unspecified"
        value_prefix = str(cookie.get("value", ""))[:32]

        structured_item = {
            "name": name,
            "domain": domain,
            "path": path,
            "secure": secure,
            "http_only": http_only,
            "same_site": same_site,
            "value_prefix": value_prefix,
        }
        structured.append(structured_item)

        cookie_strings.append(
            (
                f"cookie_name={name}; cookie_domain={domain}; cookie_path={path}; "
                f"secure={secure}; httponly={http_only}; samesite={same_site}; "
                f"value_prefix={value_prefix}"
            )
        )

    return cookie_strings, structured


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


# ------------------------------------------------------------------ #
# Async HTTP helpers (httpx-based)                                    #
# ------------------------------------------------------------------ #

async def _async_fetch_text(
    client: httpx.AsyncClient,
    url: str,
    max_bytes: int = 2_000_000,
) -> FetchResult:
    """Fetch a URL asynchronously using the shared httpx client."""
    try:
        response = await client.get(url, follow_redirects=True)
        raw = response.content[:max_bytes]
        encoding = response.encoding or "utf-8"
        text = raw.decode(encoding, errors="replace")
        return FetchResult(
            url=url,
            final_url=str(response.url),
            status_code=response.status_code,
            text=text,
            error=None,
        )
    except httpx.TimeoutException:
        return FetchResult(url=url, final_url=None, status_code=None, text="", error="timeout")
    except httpx.ConnectError as exc:
        return FetchResult(
            url=url, final_url=None, status_code=None, text="",
            error=f"connect_error: {exc}",
        )
    except Exception as exc:
        return FetchResult(
            url=url, final_url=None, status_code=None, text="",
            error=f"request_error: {exc}",
        )


def _make_async_client(timeout: int) -> httpx.AsyncClient:
    """Create a shared httpx.AsyncClient with connection pooling."""
    return httpx.AsyncClient(
        headers=DEFAULT_REQUEST_HEADERS,
        timeout=httpx.Timeout(timeout, connect=min(timeout, 10)),
        limits=httpx.Limits(max_connections=20, max_keepalive_connections=10),
        verify=False,  # mirrors requests session behaviour (ignore_https_errors)
        follow_redirects=True,
    )


async def download_scripts_async(
    script_urls: list[str],
    reference_url: str = "",
    timeout: int = 10,
    max_scripts: int = 25,
    max_bytes: int = 500_000,
) -> tuple[dict[str, str], list[str]]:
    """Download JS scripts concurrently using httpx."""
    scripts_content: dict[str, str] = {}
    errors: list[str] = []

    prioritized = prioritize_script_urls(
        script_urls=script_urls,
        reference_url=reference_url,
        max_scripts=max_scripts,
    )

    if not prioritized:
        return scripts_content, errors

    async with _make_async_client(timeout) as client:
        tasks = [_async_fetch_text(client, url, max_bytes=max_bytes) for url in prioritized]
        results = await asyncio.gather(*tasks, return_exceptions=True)

    for url, result in zip(prioritized, results):
        if isinstance(result, Exception):
            errors.append(f"script_fetch_failed: {url} ({result})")
            continue
        if result.error:
            errors.append(f"script_fetch_failed: {url} ({result.error})")
            continue
        scripts_content[url] = result.text

    return scripts_content, errors


async def discover_public_surface_async(
    start_url: str,
    seed_links: list[str] | None = None,
    robots_txt: str = "",
    sitemap_xml: str = "",
    timeout: int = 10,
    max_pages: int = 20,
    max_depth: int = 1,
    max_sitemaps: int = 8,
    max_sitemap_urls: int = 600,
    max_links_per_page: int = 40,
    max_subdomains: int = 40,
) -> dict[str, Any]:
    """Async version of discover_public_surface — fetches pages concurrently."""
    seed_links = seed_links or []
    errors: list[str] = []

    candidate_pages: list[str] = []
    candidate_pages_seen: set[str] = set()

    sitemaps_checked: list[str] = []
    sitemaps_seen: set[str] = set()

    def add_candidate_page(url: str) -> None:
        normalized = _normalize_crawl_url(url)
        if not normalized:
            return
        if not _is_internal_url(normalized, start_url):
            return
        if not _is_probable_page_url(normalized):
            return
        if normalized in candidate_pages_seen:
            return
        if len(candidate_pages) >= max_sitemap_urls:
            return
        candidate_pages_seen.add(normalized)
        candidate_pages.append(normalized)

    for link in seed_links:
        add_candidate_page(link)

    async with _make_async_client(timeout) as client:
        # ---- subdomain discovery (crt.sh) --------------------------------
        subdomain_urls, subdomain_errors = await _discover_subdomains_async(
            client=client,
            reference_url=start_url,
        )
        errors.extend(subdomain_errors)
        for subdomain_url in subdomain_urls:
            add_candidate_page(subdomain_url)

        # ---- sitemap crawl -----------------------------------------------
        sitemap_queue: deque[str] = deque()

        for sitemap_url in extract_sitemaps_from_robots(robots_txt):
            normalized = _normalize_crawl_url(sitemap_url)
            if normalized and _is_internal_url(normalized, start_url):
                sitemap_queue.append(normalized)

        default_sitemap_url = urljoin(f"{get_origin(start_url)}/", "sitemap.xml")
        sitemap_queue.append(default_sitemap_url)

        for loc in extract_sitemap_locations(sitemap_xml):
            if _looks_like_sitemap_url(loc):
                normalized_loc = _normalize_crawl_url(loc)
                if normalized_loc and _is_internal_url(normalized_loc, start_url):
                    sitemap_queue.append(normalized_loc)
            else:
                add_candidate_page(loc)

        while sitemap_queue and len(sitemaps_checked) < max_sitemaps:
            sitemap_url = _normalize_crawl_url(sitemap_queue.popleft())
            if not sitemap_url or sitemap_url in sitemaps_seen:
                continue
            sitemaps_seen.add(sitemap_url)
            sitemaps_checked.append(sitemap_url)

            result = await _async_fetch_text(client, sitemap_url, max_bytes=2_000_000)
            if result.error:
                errors.append(f"sitemap_fetch_failed: {sitemap_url} ({result.error})")
                continue

            for loc in extract_sitemap_locations(result.text):
                if _looks_like_sitemap_url(loc):
                    nested = _normalize_crawl_url(loc)
                    if (
                        nested
                        and _is_internal_url(nested, start_url)
                        and nested not in sitemaps_seen
                    ):
                        if len(sitemaps_seen) + len(sitemap_queue) < max_sitemaps * 3:
                            sitemap_queue.append(nested)
                else:
                    add_candidate_page(loc)

        # ---- page crawl (concurrent batches) -----------------------------
        visited_pages: list[str] = []
        visited_seen: set[str] = set()
        queued_seen: set[str] = set()

        discovered_html: list[str] = []
        discovered_links: list[str] = []
        discovered_links_seen: set[str] = set()

        discovered_script_urls: list[str] = []
        discovered_script_urls_seen: set[str] = set()

        discovered_inline_scripts: list[str] = []
        discovered_inline_seen: set[str] = set()

        # Build the initial queue as a flat list so we can batch it.
        page_queue: list[tuple[str, int]] = []

        def enqueue(url: str, depth: int) -> None:
            normalized = _normalize_crawl_url(url)
            if not normalized:
                return
            if not _is_internal_url(normalized, start_url):
                return
            if normalized in queued_seen or normalized in visited_seen:
                return
            queued_seen.add(normalized)
            page_queue.append((normalized, depth))

        enqueue(start_url, 0)
        for candidate in candidate_pages:
            enqueue(candidate, 1)

        # Process in concurrent batches of up to 8 pages at a time.
        _BATCH_SIZE = 8
        page_queue_deque: deque[tuple[str, int]] = deque(page_queue)

        while page_queue_deque and len(visited_pages) < max_pages:
            remaining_slots = max_pages - len(visited_pages)
            batch = []
            while page_queue_deque and len(batch) < min(_BATCH_SIZE, remaining_slots):
                batch.append(page_queue_deque.popleft())

            if not batch:
                break

            fetch_tasks = [
                _async_fetch_text(client, url, max_bytes=1_500_000)
                for url, _ in batch
            ]
            batch_results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

            for (url, depth), result in zip(batch, batch_results):
                visited_seen.add(url)

                if isinstance(result, Exception):
                    errors.append(f"discover_fetch_failed: {url} ({result})")
                    continue
                if result.error:
                    errors.append(f"discover_fetch_failed: {url} ({result.error})")
                    continue

                final_url = _normalize_crawl_url(result.final_url or url)
                if not final_url or not _is_internal_url(final_url, start_url):
                    continue
                if final_url in visited_pages:
                    continue

                visited_pages.append(final_url)
                discovered_html.append(result.text)

                assets = extract_page_assets(result.text, final_url)

                for script_url in assets["script_urls"]:
                    normalized_script = _normalize_crawl_url(script_url)
                    if not normalized_script or normalized_script in discovered_script_urls_seen:
                        continue
                    discovered_script_urls_seen.add(normalized_script)
                    discovered_script_urls.append(normalized_script)

                for inline_script in assets["inline_scripts"]:
                    cleaned = inline_script.strip()
                    if not cleaned or cleaned in discovered_inline_seen:
                        continue
                    if len(discovered_inline_scripts) >= 400:
                        break
                    discovered_inline_seen.add(cleaned)
                    discovered_inline_scripts.append(cleaned)

                internal_links: list[str] = []
                for link in assets["links"] + assets["iframes"]:
                    normalized_link = _normalize_crawl_url(link)
                    if not normalized_link:
                        continue
                    if not _is_internal_url(normalized_link, start_url):
                        continue
                    if not _is_probable_page_url(normalized_link):
                        continue

                    if normalized_link not in discovered_links_seen:
                        discovered_links_seen.add(normalized_link)
                        discovered_links.append(normalized_link)

                    internal_links.append(normalized_link)

                if depth < max_depth:
                    for link in internal_links[:max_links_per_page]:
                        enqueue(link, depth + 1)
                        page_queue_deque.append((link, depth + 1))

    return {
        "pages_visited": visited_pages,
        "page_html": discovered_html,
        "links_found": discovered_links,
        "script_urls": discovered_script_urls,
        "inline_scripts": discovered_inline_scripts,
        "subdomain_urls": subdomain_urls,
        "sitemaps_checked": sitemaps_checked,
        "sitemap_urls": candidate_pages,
        "errors": errors,
    }


async def _discover_subdomains_async(
    client: httpx.AsyncClient,
    reference_url: str,
    max_subdomains: int = 40,
) -> tuple[list[str], list[str]]:
    """Async version of discover_subdomains_from_ct_logs."""
    errors: list[str] = []
    host = urlparse(reference_url).hostname
    if not host:
        return [], errors

    domain = _domain_key(host)
    if not domain:
        return [], errors

    query_url = f"https://crt.sh/?q=%25.{domain}&output=json"
    result = await _async_fetch_text(client, query_url, max_bytes=4_000_000)
    if result.error:
        errors.append(f"crtsh_query_failed: {result.error}")
        return [], errors

    try:
        import json

        payload = json.loads(result.text or "[]")
    except Exception as exc:
        errors.append(f"crtsh_parse_failed: {exc}")
        return [], errors

    hosts_seen: set[str] = set()
    hosts_ranked: list[str] = []

    for row in payload:
        name_value = str(row.get("name_value", "")).strip()
        if not name_value:
            continue
        for item in name_value.splitlines():
            cleaned = item.strip().lower().lstrip("*.").strip(".")
            if not cleaned:
                continue
            if _domain_key(cleaned) != domain:
                continue
            if cleaned in hosts_seen:
                continue
            hosts_seen.add(cleaned)
            hosts_ranked.append(cleaned)

    if not hosts_ranked:
        return [], errors

    hosts_ranked.sort(
        key=lambda h: (
            0 if any(term in h for term in SUBDOMAIN_PRIORITY_TERMS) else 1,
            len(h),
            h,
        )
    )

    urls = [f"https://{host_name}/" for host_name in hosts_ranked[: max(0, max_subdomains)]]
    return urls, errors


def _is_probable_page_url(url: str) -> bool:
    parsed = urlparse(url)
    if parsed.scheme not in {"http", "https"}:
        return False

    path = (parsed.path or "").lower()
    if any(path.endswith(ext) for ext in _NON_PAGE_EXTENSIONS):
        return False

    return True


def _looks_like_sitemap_url(url: str) -> bool:
    parsed = urlparse(url)
    path = (parsed.path or "").lower()
    return path.endswith(".xml") or "sitemap" in path


def _normalize_crawl_url(url: str) -> str:
    parsed = urlsplit(url.strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return ""
    return urlunsplit((parsed.scheme, parsed.netloc, parsed.path, parsed.query, ""))


def _is_internal_url(candidate_url: str, reference_url: str) -> bool:
    candidate_host = urlparse(candidate_url).hostname
    reference_host = urlparse(reference_url).hostname
    if not candidate_host or not reference_host:
        return False

    candidate_key = _domain_key(candidate_host)
    reference_key = _domain_key(reference_host)

    if candidate_key and reference_key:
        return candidate_key == reference_key

    ref = reference_host.lower()
    cand = candidate_host.lower()
    return cand == ref or cand.endswith(f".{ref}")


def _domain_key(hostname: str) -> str:
    host = hostname.lower()
    try:
        import tldextract

        extracted = tldextract.extract(host)
        top_domain = getattr(extracted, "top_domain_under_public_suffix", "")
        if top_domain:
            return top_domain.lower()
    except Exception:
        return host

    return host


def _company_tokens(hostname: str, max_tokens: int = 3) -> list[str]:
    host = hostname.lower().strip(".")
    if not host:
        return []

    try:
        import tldextract

        extracted = tldextract.extract(host)
        base = extracted.domain or host.split(".")[0]
    except Exception:
        base = host.split(".")[0]

    common_skip = {
        "www",
        "com",
        "net",
        "org",
        "co",
        "io",
        "br",
        "app",
        "cloud",
        "site",
    }
    raw_parts = re.split(r"[^a-z0-9]+", base)

    tokens: list[str] = []
    seen: set[str] = set()
    for part in raw_parts:
        token = part.strip()
        if len(token) < 3:
            continue
        if token in common_skip:
            continue
        if token in seen:
            continue
        seen.add(token)
        tokens.append(token)
        if len(tokens) >= max(1, max_tokens):
            break

    return tokens


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
