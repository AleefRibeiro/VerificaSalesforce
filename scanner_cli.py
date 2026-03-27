from __future__ import annotations

import argparse
import sys

from salesforce_scanner.engine import ScanOptions, run_scan
from salesforce_scanner.report import print_terminal_report, save_json_report


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


def main() -> int:
    args = parse_args()

    options = ScanOptions(
        max_scripts=max(1, args.max_scripts),
        max_requests=max(1, args.max_requests),
        http_timeout=max(1, args.http_timeout),
        playwright_timeout_ms=max(1, args.playwright_timeout_ms),
        skip_playwright=args.skip_playwright,
        no_discovery=args.no_discovery,
        discovery_max_pages=max(1, args.discovery_max_pages),
        discovery_max_depth=max(0, args.discovery_max_depth),
        discovery_max_sitemaps=max(1, args.discovery_max_sitemaps),
        discovery_max_subdomains=max(0, args.discovery_max_subdomains),
    )

    if args.verbose:
        print(f"[verbose] Iniciando análise de {args.url}")

    try:
        report_data = run_scan(args.url, options)
    except ValueError as exc:
        print(f"Erro: {exc}", file=sys.stderr)
        return 2
    except KeyboardInterrupt:
        print("Interrompido pelo usuário.", file=sys.stderr)
        return 130
    except Exception as exc:  # pragma: no cover
        print(f"Erro interno do scanner: {exc}", file=sys.stderr)
        return 1

    print_terminal_report(report_data)

    if args.json_output:
        output_path = save_json_report(report_data, args.json_output)
        print(f"JSON salvo em: {output_path}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
