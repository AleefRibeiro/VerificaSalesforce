from __future__ import annotations

import argparse
import sys

from salesforce_scanner.analyzer import analyze_sources
from salesforce_scanner.fetcher import (
    create_session,
    discover_public_surface,
    download_scripts,
    extract_page_assets,
    fetch_public_resources,
    fetch_text,
    normalize_url,
    render_with_playwright,
)
from salesforce_scanner.report import build_report, print_terminal_report, save_json_report
from salesforce_scanner.scorer import classify_score, compute_score, salesforce_detected


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Scanner passivo para detectar evidências públicas de Salesforce.",
    )
    parser.add_argument("url", help="URL ou domínio para análise")
    parser.add_argument(
        "--json-output",
        default="scan_result.json",
        help="Arquivo JSON de saída (padrão: scan_result.json)",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="Exibe logs intermediários",
    )
    parser.add_argument(
        "--max-scripts",
        type=int,
        default=80,
        help="Limite de scripts externos para baixar",
    )
    parser.add_argument(
        "--max-requests",
        type=int,
        default=250,
        help="Limite de requests de rede capturados pelo Playwright",
    )
    parser.add_argument(
        "--http-timeout",
        type=int,
        default=12,
        help="Timeout HTTP em segundos",
    )
    parser.add_argument(
        "--playwright-timeout-ms",
        type=int,
        default=20000,
        help="Timeout do Playwright em milissegundos",
    )
    parser.add_argument(
        "--skip-playwright",
        action="store_true",
        help="Pula a etapa de renderização com Playwright",
    )
    parser.add_argument(
        "--no-discovery",
        action="store_true",
        help="Desativa descoberta de URLs internas (discovery)",
    )
    parser.add_argument(
        "--discovery-max-pages",
        type=int,
        default=25,
        help="Máximo de páginas internas para discovery",
    )
    parser.add_argument(
        "--discovery-max-depth",
        type=int,
        default=1,
        help="Profundidade máxima do discovery interno",
    )
    parser.add_argument(
        "--discovery-max-sitemaps",
        type=int,
        default=10,
        help="Máximo de sitemaps processados durante discovery",
    )
    parser.add_argument(
        "--discovery-max-subdomains",
        type=int,
        default=40,
        help="Máximo de subdomínios públicos (CT logs) adicionados ao discovery",
    )
    return parser.parse_args()


def vlog(enabled: bool, message: str) -> None:
    if enabled:
        print(f"[verbose] {message}")


def main() -> int:
    args = parse_args()

    try:
        normalized_url = normalize_url(args.url)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2

    checked_resources: list[str] = []
    errors: list[str] = []

    session = create_session()

    vlog(args.verbose, f"Analisando URL normalizada: {normalized_url}")
    initial_fetch = fetch_text(session, normalized_url, timeout=args.http_timeout)
    checked_resources.append("html_initial")

    if initial_fetch.error:
        errors.append(f"initial_fetch_failed: {initial_fetch.error}")
        base_url = normalized_url
        html_initial = ""
    else:
        base_url = initial_fetch.final_url or normalized_url
        html_initial = initial_fetch.text

    initial_assets = extract_page_assets(html_initial, base_url)

    resource_results, resource_errors = fetch_public_resources(
        session,
        reference_url=base_url,
        timeout=args.http_timeout,
    )
    checked_resources.extend(["robots.txt", "sitemap.xml"])
    errors.extend(resource_errors)

    robots_txt = resource_results["robots.txt"].text if "robots.txt" in resource_results else ""
    sitemap_xml = resource_results["sitemap.xml"].text if "sitemap.xml" in resource_results else ""

    rendered_html = ""
    rendered_assets = {
        "script_urls": [],
        "inline_scripts": [],
        "iframes": [],
        "links": [],
    }
    network_requests: list[str] = []
    cookies: list[str] = []
    redirect_chain: list[str] = []
    playwright_final_url = base_url

    if args.skip_playwright:
        vlog(args.verbose, "Etapa Playwright ignorada por flag --skip-playwright")
    else:
        vlog(args.verbose, "Executando renderização com Playwright")
        pw_data = render_with_playwright(
            base_url,
            timeout_ms=args.playwright_timeout_ms,
            max_requests=max(1, args.max_requests),
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
        for item in cookie_items:
            name = item.get("name", "")
            value = item.get("value", "")
            if name:
                cookies.append(f"{name}={value}")

    discovered_urls: list[str] = []
    discovered_html_list: list[str] = []
    discovered_links: list[str] = []
    discovered_script_urls: list[str] = []
    discovered_inline_scripts: list[str] = []
    sitemap_urls_discovered: list[str] = []
    subdomain_urls_discovered: list[str] = []

    if args.no_discovery:
        vlog(args.verbose, "Discovery interno desativado por flag --no-discovery")
    else:
        vlog(args.verbose, "Executando discovery interno (sitemap/robots/crawl raso)")
        discovery_seed_links = (
            initial_assets["links"]
            + initial_assets["iframes"]
            + rendered_assets["links"]
            + rendered_assets["iframes"]
        )

        discovery_data = discover_public_surface(
            session=session,
            start_url=playwright_final_url,
            seed_links=discovery_seed_links,
            robots_txt=robots_txt,
            sitemap_xml=sitemap_xml,
            timeout=args.http_timeout,
            max_pages=max(1, args.discovery_max_pages),
            max_depth=max(0, args.discovery_max_depth),
            max_sitemaps=max(1, args.discovery_max_sitemaps),
            max_subdomains=max(0, args.discovery_max_subdomains),
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
        initial_assets["script_urls"] + rendered_assets["script_urls"] + discovered_script_urls
    )
    all_inline_scripts = (
        initial_assets["inline_scripts"]
        + rendered_assets["inline_scripts"]
        + discovered_inline_scripts
    )
    all_iframes = _dedupe_preserve_order(initial_assets["iframes"] + rendered_assets["iframes"])
    all_links = _dedupe_preserve_order(
        initial_assets["links"] + rendered_assets["links"] + discovered_links
    )

    vlog(args.verbose, f"Scripts agregados para download: {len(all_script_urls)}")
    scripts_content, script_errors = download_scripts(
        session,
        all_script_urls,
        timeout=args.http_timeout,
        max_scripts=max(1, args.max_scripts),
    )
    checked_resources.append("scripts")
    errors.extend(script_errors)

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
        "redirect_chain": redirect_chain,
        "discovered_url": discovered_urls,
        "discovered_html": discovered_html_list,
        "discovered_link": discovered_links,
        "discovered_script_url": discovered_script_urls,
        "discovered_script_content": discovered_inline_scripts,
    }

    evidence, domains_found = analyze_sources(sources)
    score = compute_score(evidence)
    classification = classify_score(score)
    detected = salesforce_detected(score)

    final_url = playwright_final_url or base_url
    report_data = build_report(
        input_url=args.url,
        normalized_url=normalized_url,
        final_url=final_url,
        score=score,
        classification=classification,
        detected=detected,
        evidence=evidence,
        domains_found=domains_found,
        checked_resources=_dedupe_preserve_order(checked_resources),
        errors=errors,
    )

    print_terminal_report(report_data)

    if args.json_output:
        output_path = save_json_report(report_data, args.json_output)
        print(f"JSON salvo em: {output_path}")

    return 0


def _dedupe_preserve_order(items: list[str]) -> list[str]:
    seen: set[str] = set()
    result: list[str] = []
    for item in items:
        if item in seen:
            continue
        seen.add(item)
        result.append(item)
    return result


if __name__ == "__main__":
    raise SystemExit(main())
