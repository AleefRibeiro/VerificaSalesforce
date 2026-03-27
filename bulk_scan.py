from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
import json
import math
from pathlib import Path
import subprocess
import sys
import tempfile
from typing import Any
from urllib.parse import urlparse

import tldextract


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Executa o scanner Salesforce em massa e gera resumo único.",
    )
    parser.add_argument(
        "--input-file",
        help="Arquivo com URLs (uma por linha; aceita linhas com texto)",
    )
    parser.add_argument(
        "--url",
        action="append",
        default=[],
        help="URL adicional (pode repetir --url várias vezes)",
    )
    parser.add_argument(
        "--output-txt",
        default="results/bulk_summary.txt",
        help="Arquivo TXT resumido de saída",
    )
    parser.add_argument(
        "--output-json",
        default="results/bulk_summary.json",
        help="Arquivo JSON agregado de saída",
    )
    parser.add_argument("--workers", type=int, default=4, help="Quantidade de workers paralelos")
    parser.add_argument("--site-timeout", type=int, default=240, help="Timeout por site (segundos)")

    parser.add_argument("--http-timeout", type=int, default=15)
    parser.add_argument("--playwright-timeout-ms", type=int, default=20000)
    parser.add_argument("--max-scripts", type=int, default=90)
    parser.add_argument("--max-requests", type=int, default=220)
    parser.add_argument("--discovery-max-pages", type=int, default=20)
    parser.add_argument("--discovery-max-depth", type=int, default=1)
    parser.add_argument("--discovery-max-sitemaps", type=int, default=8)
    parser.add_argument("--discovery-max-subdomains", type=int, default=25)
    parser.add_argument("--skip-playwright", action="store_true")
    parser.add_argument("--no-discovery", action="store_true")

    return parser.parse_args()


def main() -> int:
    args = parse_args()

    urls = collect_urls(args.input_file, args.url)
    if not urls:
        print("Nenhuma URL válida informada.", file=sys.stderr)
        return 2

    Path(args.output_txt).expanduser().resolve().parent.mkdir(parents=True, exist_ok=True)

    print(f"Iniciando varredura em massa: {len(urls)} URLs, workers={max(1, args.workers)}")

    results: list[dict[str, Any]] = []
    with ThreadPoolExecutor(max_workers=max(1, args.workers)) as executor:
        futures = {
            executor.submit(run_single_scan, url, args): url
            for url in urls
        }
        completed = 0
        for future in as_completed(futures):
            completed += 1
            url = futures[future]
            try:
                result = future.result()
            except Exception as exc:  # pragma: no cover
                result = {
                    "company": company_name_from_url(url),
                    "site": url,
                    "confidence_percent": 0,
                    "classification": "Nenhum sinal encontrado",
                    "status": f"erro interno: {exc}",
                    "score": 0,
                    "salesforce_detected": False,
                }
            results.append(result)
            print(
                f"[{completed:>3}/{len(urls)}] {url} -> {result['classification']} ({result['confidence_percent']}%)"
            )

    results.sort(key=lambda item: (item["confidence_percent"], item["score"]), reverse=True)

    txt_output = render_txt_summary(results)
    Path(args.output_txt).expanduser().resolve().write_text(txt_output, encoding="utf-8")

    json_payload = {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "total_sites": len(results),
        "detected_sites": sum(1 for item in results if item.get("salesforce_detected")),
        "unreachable_sites": sum(
            1
            for item in results
            if str(item.get("status", "")).startswith("inacessível")
        ),
        "results": results,
    }
    Path(args.output_json).expanduser().resolve().write_text(
        json.dumps(json_payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    print(f"Resumo TXT salvo em: {Path(args.output_txt).expanduser().resolve()}")
    print(f"Resumo JSON salvo em: {Path(args.output_json).expanduser().resolve()}")
    return 0


def run_single_scan(url: str, args: argparse.Namespace) -> dict[str, Any]:
    with tempfile.TemporaryDirectory(prefix="sfscan_") as tmp_dir:
        json_path = Path(tmp_dir) / "result.json"
        cmd = [
            sys.executable,
            "main.py",
            url,
            "--json-output",
            str(json_path),
            "--http-timeout",
            str(args.http_timeout),
            "--playwright-timeout-ms",
            str(args.playwright_timeout_ms),
            "--max-scripts",
            str(args.max_scripts),
            "--max-requests",
            str(args.max_requests),
            "--discovery-max-pages",
            str(args.discovery_max_pages),
            "--discovery-max-depth",
            str(args.discovery_max_depth),
            "--discovery-max-sitemaps",
            str(args.discovery_max_sitemaps),
            "--discovery-max-subdomains",
            str(args.discovery_max_subdomains),
        ]

        if args.skip_playwright:
            cmd.append("--skip-playwright")
        if args.no_discovery:
            cmd.append("--no-discovery")

        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=max(30, args.site_timeout),
                check=False,
            )
        except subprocess.TimeoutExpired:
            return {
                "company": company_name_from_url(url),
                "site": url,
                "confidence_percent": 0,
                "classification": "Nenhum sinal encontrado",
                "status": "inacessível (timeout por site)",
                "score": 0,
                "salesforce_detected": False,
            }

        if not json_path.exists():
            return {
                "company": company_name_from_url(url),
                "site": url,
                "confidence_percent": 0,
                "classification": "Nenhum sinal encontrado",
                "status": f"inacessível (falha ao gerar JSON, exit={proc.returncode})",
                "score": 0,
                "salesforce_detected": False,
            }

        payload = json.loads(json_path.read_text(encoding="utf-8"))
        score = int(payload.get("score", 0) or 0)
        classification = str(payload.get("classification", "Nenhum sinal encontrado"))
        detected = bool(payload.get("salesforce_detected", False))

        status = classify_site_status(payload)
        confidence_percent = confidence_from_report(payload)

        return {
            "company": company_name_from_url(url),
            "site": url,
            "confidence_percent": confidence_percent,
            "classification": classification,
            "status": status,
            "score": score,
            "salesforce_detected": detected,
        }


def collect_urls(input_file: str | None, cli_urls: list[str]) -> list[str]:
    collected: list[str] = []

    for url in cli_urls:
        norm = normalize_candidate_url(url)
        if norm:
            collected.append(norm)

    if input_file:
        path = Path(input_file).expanduser().resolve()
        if path.exists():
            for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
                norm = normalize_candidate_url(line)
                if norm:
                    collected.append(norm)

    deduped: list[str] = []
    seen: set[str] = set()
    for url in collected:
        if url in seen:
            continue
        seen.add(url)
        deduped.append(url)
    return deduped


def normalize_candidate_url(line: str) -> str:
    text = line.strip()
    if not text:
        return ""

    if "http://" in text or "https://" in text:
        idx = text.find("http://")
        if idx == -1:
            idx = text.find("https://")
        text = text[idx:].split()[0]
    else:
        if "." not in text or " " in text:
            return ""
        text = text.split()[0]
        text = f"https://{text}"

    parsed = urlparse(text)
    if not parsed.scheme or not parsed.netloc:
        return ""
    return text


def company_name_from_url(url: str) -> str:
    host = (urlparse(url).hostname or "").lower()
    if not host:
        return "Desconhecida"
    extracted = tldextract.extract(host)
    domain = extracted.domain or host.split(".")[0]
    return domain.replace("-", " ").strip().title() or "Desconhecida"


def classify_site_status(report: dict[str, Any]) -> str:
    errors = [str(item) for item in report.get("errors", [])]
    if any(item.startswith("initial_fetch_failed") for item in errors):
        return "inacessível (falha inicial)"
    if any("timeout" in item for item in errors) and not report.get("evidence"):
        return "inacessível (timeout/rede)"
    if errors:
        return "ok com ressalvas"
    return "ok"


def confidence_from_report(report: dict[str, Any]) -> int:
    score = int(report.get("score", 0) or 0)
    classification = str(report.get("classification", ""))

    if not report.get("evidence") and str(classify_site_status(report)).startswith("inacessível"):
        return 0

    base = round(100 * (1 - math.exp(-score / 140)))

    if classification.startswith("Confirmado"):
        base = max(base, 90)
    elif classification.startswith("Forte indício"):
        base = max(base, 70)
    elif classification.startswith("Possível"):
        base = max(base, 45)
    elif classification.startswith("Indício fraco"):
        base = max(base, 20)
    else:
        base = min(base, 30)

    return max(0, min(99, int(base)))


def render_txt_summary(results: list[dict[str, Any]]) -> str:
    total = len(results)
    detected = sum(1 for item in results if item.get("salesforce_detected"))
    inaccessible = sum(1 for item in results if str(item.get("status", "")).startswith("inacessível"))

    lines: list[str] = []
    lines.append("Salesforce Bulk Summary")
    lines.append("=" * 110)
    lines.append(
        f"Total: {total} | Detectados: {detected} | Inacessíveis: {inaccessible} | Gerado em: {datetime.now(timezone.utc).isoformat()}"
    )
    lines.append("-")
    lines.append(f"{'Empresa':<22} {'Site':<42} {'Salesforce%':>12} {'Classificação':<32} {'Status'}")
    lines.append("-" * 110)

    for item in results:
        company = str(item.get("company", ""))[:22]
        site = str(item.get("site", ""))[:42]
        pct = f"{int(item.get('confidence_percent', 0))}%"
        classification = str(item.get("classification", ""))[:32]
        status = str(item.get("status", ""))
        lines.append(f"{company:<22} {site:<42} {pct:>12} {classification:<32} {status}")

    lines.append("=" * 110)
    return "\n".join(lines) + "\n"


if __name__ == "__main__":
    raise SystemExit(main())
