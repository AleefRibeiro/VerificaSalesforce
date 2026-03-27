from __future__ import annotations

import argparse
import sys

from salesforce_scanner.analyzer import analyze_sources
from salesforce_scanner.fetcher import (
    create_session,
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
        default=25,
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

    assets = extract_page_assets(html_initial, base_url)
    script_urls = assets["script_urls"]
    inline_scripts = assets["inline_scripts"]
    iframe_urls = assets["iframes"]
    links = assets["links"]

    vlog(args.verbose, f"Scripts encontrados no HTML inicial: {len(script_urls)}")
    scripts_content, script_errors = download_scripts(
        session,
        script_urls,
        timeout=args.http_timeout,
        max_scripts=max(1, args.max_scripts),
    )
    checked_resources.append("scripts")
    errors.extend(script_errors)

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

        cookie_items = pw_data.get("cookies", [])
        for item in cookie_items:
            name = item.get("name", "")
            value = item.get("value", "")
            if name:
                cookies.append(f"{name}={value}")

    sources: dict[str, list[str] | str] = {
        "html_initial": html_initial,
        "html_rendered": rendered_html,
        "script_url": script_urls,
        "script_content": list(scripts_content.values()) + inline_scripts,
        "iframe": iframe_urls,
        "link": links,
        "network_request": network_requests,
        "cookie": cookies,
        "robots_txt": robots_txt,
        "sitemap_xml": sitemap_xml,
        "redirect_chain": redirect_chain,
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
