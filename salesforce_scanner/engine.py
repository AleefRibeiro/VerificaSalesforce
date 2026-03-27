from __future__ import annotations

import asyncio
from dataclasses import dataclass

from .analyzer import analyze_sources
from .fetcher import (
    create_session,
    discover_salesforce_brand_domains,
    discover_public_surface_async,
    download_scripts_async,
    extract_page_assets,
    fetch_public_resources,
    fetch_text,
    normalize_url,
    render_with_playwright,
    serialize_cookies_for_analysis,
)
from .report import build_report
from .scorer import compute_score, decide_classification, infer_products

# Pattern keys that, when found with sufficient confidence, allow the scan to
# skip remaining discovery work and return early.
_EARLY_EXIT_PATTERNS = frozenset(
    {
        "service_force_domain",
        "embeddedservice",
        "liveagent",
        "force_domain",
        "salesforce_scrt_domain",
    }
)


@dataclass
class ScanOptions:
    max_scripts: int = 80
    max_requests: int = 250
    http_timeout: int = 12
    playwright_timeout_ms: int = 20000
    skip_playwright: bool = False
    no_discovery: bool = False
    discovery_max_pages: int = 25
    discovery_max_depth: int = 1
    discovery_max_sitemaps: int = 10
    discovery_max_subdomains: int = 40
    domain_probe_enabled: bool = True
    domain_probe_max_domains: int = 20
    # Overall wall-clock timeout for the entire scan (seconds).  0 = no limit.
    timeout: int = 30


def run_scan(url: str, options: ScanOptions | None = None) -> dict:
    """Synchronous entry point — runs the async implementation in a new event loop.

    Called via ``asyncio.to_thread()`` from the FastAPI layer so it never
    blocks the main event loop.
    """
    opts = options or ScanOptions()
    return asyncio.run(_async_scan(url, opts))


async def async_run_scan(url: str, options: ScanOptions | None = None) -> dict:
    """Async entry point for callers that already own an event loop."""
    opts = options or ScanOptions()
    if opts.timeout > 0:
        return await asyncio.wait_for(_async_scan(url, opts), timeout=opts.timeout)
    return await _async_scan(url, opts)


async def _async_scan(url: str, opts: ScanOptions) -> dict:
    """Core scan logic — fully async so I/O phases run concurrently."""
    normalized_url = normalize_url(url)

    checked_resources: list[str] = []
    errors: list[str] = []

    session = create_session()

    # ------------------------------------------------------------------ #
    # Phase 1: initial HTML fetch + public resources (robots/sitemap)     #
    # Run both concurrently.                                               #
    # ------------------------------------------------------------------ #
    initial_fetch_coro = asyncio.to_thread(
        fetch_text, session, normalized_url, opts.http_timeout
    )
    public_resources_coro = asyncio.to_thread(
        fetch_public_resources, session, normalized_url, opts.http_timeout
    )
    if opts.domain_probe_enabled:
        brand_probe_coro = asyncio.to_thread(
            discover_salesforce_brand_domains,
            session,
            normalized_url,
            min(8, max(3, opts.http_timeout)),
            max(1, opts.domain_probe_max_domains),
        )
        initial_fetch, (resource_results, resource_errors), (
            brand_domain_probe_urls,
            brand_probe_errors,
        ) = await asyncio.gather(
            initial_fetch_coro, public_resources_coro, brand_probe_coro
        )
        checked_resources.append("brand_domain_probe")
        errors.extend(brand_probe_errors)
    else:
        initial_fetch, (resource_results, resource_errors) = await asyncio.gather(
            initial_fetch_coro, public_resources_coro
        )
        brand_domain_probe_urls = []

    checked_resources.append("html_initial")
    if initial_fetch.error:
        errors.append(f"initial_fetch_failed: {initial_fetch.error}")
        base_url = normalized_url
        html_initial = ""
    else:
        base_url = initial_fetch.final_url or normalized_url
        html_initial = initial_fetch.text

    initial_assets = extract_page_assets(html_initial, base_url)

    checked_resources.extend(["robots.txt", "sitemap.xml"])
    errors.extend(resource_errors)

    robots_txt = resource_results["robots.txt"].text if "robots.txt" in resource_results else ""
    sitemap_xml = resource_results["sitemap.xml"].text if "sitemap.xml" in resource_results else ""

    # ------------------------------------------------------------------ #
    # Phase 2: Playwright rendering (blocking — runs in thread pool)      #
    # ------------------------------------------------------------------ #
    rendered_html = ""
    rendered_assets: dict[str, list[str]] = {
        "script_urls": [],
        "inline_scripts": [],
        "iframes": [],
        "links": [],
    }
    network_requests: list[str] = []
    cookies: list[str] = []
    redirect_chain: list[str] = []
    playwright_final_url = base_url

    if not opts.skip_playwright:
        pw_data = await asyncio.to_thread(
            render_with_playwright,
            base_url,
            opts.playwright_timeout_ms,
            max(1, opts.max_requests),
        )
        checked_resources.extend(["html_rendered", "network_requests", "cookies"])

        if pw_data.get("error"):
            errors.append(str(pw_data["error"]))

        rendered_html = pw_data.get("rendered_html", "")
        network_requests = pw_data.get("network_requests", [])
        redirect_chain = pw_data.get("redirect_chain", [])
        playwright_final_url = pw_data.get("final_url") or base_url

        if rendered_html:
            rendered_assets = extract_page_assets(rendered_html, playwright_final_url)

        cookie_items = pw_data.get("cookies", [])
        cookies, _ = serialize_cookies_for_analysis(cookie_items)

    # ------------------------------------------------------------------ #
    # Early-exit check: if we already have strong Salesforce signals from #
    # the initial HTML + network requests, skip the expensive discovery   #
    # and script-download phases.                                         #
    # ------------------------------------------------------------------ #
    early_sources: dict[str, list[str] | str] = {
        "html_initial": html_initial,
        "html_rendered": rendered_html,
        "script_url": initial_assets["script_urls"] + rendered_assets["script_urls"],
        "iframe": initial_assets["iframes"] + rendered_assets["iframes"],
        "link": initial_assets["links"] + rendered_assets["links"],
        "network_request": network_requests,
        "cookie": cookies,
        "robots_txt": robots_txt,
        "sitemap_xml": sitemap_xml,
        "redirect_chain": redirect_chain,
        "script_content": initial_assets["inline_scripts"] + rendered_assets["inline_scripts"],
        "discovered_url": [],
        "discovered_html": [],
        "discovered_link": [],
        "discovered_script_url": [],
        "discovered_script_content": [],
        "sitemap_url": [],
        "subdomain_url": [],
        "brand_domain_probe": brand_domain_probe_urls,
    }
    early_evidence, _ = analyze_sources(early_sources)
    skip_discovery = _has_critical_evidence(early_evidence)

    # ------------------------------------------------------------------ #
    # Phase 3: Discovery + script download (concurrent)                   #
    # ------------------------------------------------------------------ #
    discovered_urls: list[str] = []
    discovered_html_list: list[str] = []
    discovered_links: list[str] = []
    discovered_script_urls: list[str] = []
    discovered_inline_scripts: list[str] = []
    sitemap_urls_discovered: list[str] = []
    subdomain_urls_discovered: list[str] = []

    all_script_urls = _dedupe_preserve_order(
        initial_assets["script_urls"] + rendered_assets["script_urls"]
    )
    all_inline_scripts = (
        initial_assets["inline_scripts"] + rendered_assets["inline_scripts"]
    )

    if not opts.no_discovery and not skip_discovery:
        discovery_seed_links = (
            initial_assets["links"]
            + initial_assets["iframes"]
            + rendered_assets["links"]
            + rendered_assets["iframes"]
        )

        # Run discovery and initial script downloads concurrently.
        discovery_coro = discover_public_surface_async(
            start_url=playwright_final_url,
            seed_links=discovery_seed_links,
            robots_txt=robots_txt,
            sitemap_xml=sitemap_xml,
            timeout=opts.http_timeout,
            max_pages=max(1, opts.discovery_max_pages),
            max_depth=max(0, opts.discovery_max_depth),
            max_sitemaps=max(1, opts.discovery_max_sitemaps),
            max_subdomains=max(0, opts.discovery_max_subdomains),
        )
        scripts_coro = download_scripts_async(
            script_urls=all_script_urls,
            reference_url=playwright_final_url,
            timeout=opts.http_timeout,
            max_scripts=max(1, opts.max_scripts),
        )

        discovery_data, (scripts_content, script_errors) = await asyncio.gather(
            discovery_coro, scripts_coro
        )

        checked_resources.extend(["discovery", "discovered_pages"])
        errors.extend(discovery_data.get("errors", []))

        discovered_urls = discovery_data.get("pages_visited", [])
        discovered_html_list = discovery_data.get("page_html", [])
        discovered_links = discovery_data.get("links_found", [])
        discovered_script_urls = discovery_data.get("script_urls", [])
        discovered_inline_scripts = discovery_data.get("inline_scripts", [])
        sitemap_urls_discovered = discovery_data.get("sitemap_urls", [])
        subdomain_urls_discovered = discovery_data.get("subdomain_urls", [])

        if discovery_data.get("sitemaps_checked"):
            checked_resources.append("sitemap_discovery")
        if subdomain_urls_discovered:
            checked_resources.append("subdomain_discovery")

        all_script_urls = _dedupe_preserve_order(
            all_script_urls + discovered_script_urls
        )
        all_inline_scripts = all_inline_scripts + discovered_inline_scripts
    else:
        # Discovery skipped — still download scripts from initial/rendered pages.
        scripts_content, script_errors = await download_scripts_async(
            script_urls=all_script_urls,
            reference_url=playwright_final_url,
            timeout=opts.http_timeout,
            max_scripts=max(1, opts.max_scripts),
        )
        if skip_discovery:
            checked_resources.append("early_exit")

    checked_resources.append("scripts")
    errors.extend(script_errors)

    all_iframes = _dedupe_preserve_order(initial_assets["iframes"] + rendered_assets["iframes"])
    all_links = _dedupe_preserve_order(
        initial_assets["links"] + rendered_assets["links"] + discovered_links
    )

    # ------------------------------------------------------------------ #
    # Phase 4: Analysis                                                    #
    # ------------------------------------------------------------------ #
    sources: dict[str, list[str] | str] = {
        "html_initial": html_initial,
        "html_rendered": rendered_html,
        "script_url": all_script_urls,
        "script_content": list(scripts_content.values()) + all_inline_scripts,
        "iframe": all_iframes,
        "link": all_links,
        "network_request": network_requests,
        "cookie": cookies,
        "robots_txt": robots_txt,
        "sitemap_xml": sitemap_xml,
        "sitemap_url": sitemap_urls_discovered,
        "subdomain_url": subdomain_urls_discovered,
        "brand_domain_probe": brand_domain_probe_urls,
        "redirect_chain": redirect_chain,
        "discovered_url": discovered_urls,
        "discovered_html": discovered_html_list,
        "discovered_link": discovered_links,
        "discovered_script_url": discovered_script_urls,
        "discovered_script_content": discovered_inline_scripts,
    }

    evidence, domains_found = analyze_sources(sources)
    score_details = compute_score(evidence)
    inferred_products = infer_products(evidence)
    decision = decide_classification(evidence, score_details, inferred_products)

    final_url = playwright_final_url or base_url
    report_data = build_report(
        input_url=url,
        normalized_url=normalized_url,
        final_url=final_url,
        evidence=evidence,
        domains_found=domains_found,
        checked_resources=_dedupe_preserve_order(checked_resources),
        errors=errors,
        score_details=score_details,
        decision=decision,
        inferred_products=inferred_products,
    )

    return report_data


def _has_critical_evidence(evidence: list[dict]) -> bool:
    """Return True when early evidence is strong enough to skip discovery."""
    strong_hits = [
        item
        for item in evidence
        if item.get("pattern_key") in _EARLY_EXIT_PATTERNS
        and item.get("pattern_strength") in {"strong", "medium"}
    ]
    return len(strong_hits) >= 2


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result
