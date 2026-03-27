from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from html import unescape
import re
from typing import Any
from urllib.parse import urljoin, urlparse, urlsplit, urlunsplit

import requests
from bs4 import BeautifulSoup

DEFAULT_USER_AGENT = (
    "Mozilla/5.0 (compatible; SalesforceScanner/1.0; +https://example.local/scanner)"
)

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

    priority_terms = (
        "salesforce",
        "service",
        "support",
        "suporte",
        "atendimento",
        "help",
        "portal",
        "customer",
        "chat",
    )
    hosts_ranked.sort(
        key=lambda h: (
            0 if any(term in h for term in priority_terms) else 1,
            len(h),
            h,
        )
    )

    urls = [f"https://{host_name}/" for host_name in hosts_ranked[:max(0, max_subdomains)]]
    return urls, errors


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
