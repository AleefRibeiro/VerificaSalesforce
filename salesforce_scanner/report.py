from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path


def build_report(
    input_url: str,
    normalized_url: str,
    final_url: str,
    score: int,
    classification: str,
    detected: bool,
    evidence: list[dict],
    domains_found: list[str],
    checked_resources: list[str],
    errors: list[str],
) -> dict:
    serialized_evidence = [
        {
            "type": item["type"],
            "value": item["value"],
            "reason": item["reason"],
        }
        for item in evidence
    ]

    return {
        "input_url": input_url,
        "normalized_url": normalized_url,
        "final_url": final_url,
        "score": score,
        "classification": classification,
        "salesforce_detected": detected,
        "evidence": serialized_evidence,
        "domains_found": domains_found,
        "checked_resources": checked_resources,
        "errors": errors,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def print_terminal_report(report_data: dict, max_evidence: int = 10) -> None:
    print("=" * 72)
    print("Salesforce Public Evidence Scanner")
    print("=" * 72)
    print(f"URL informada     : {report_data['input_url']}")
    print(f"URL final         : {report_data['final_url']}")
    print(f"Score             : {report_data['score']}")
    print(f"Classificação     : {report_data['classification']}")
    print(f"Salesforce detect.: {report_data['salesforce_detected']}")
    print("-")

    evidence = report_data.get("evidence", [])
    if evidence:
        print(f"Principais evidências ({min(len(evidence), max_evidence)} de {len(evidence)}):")
        for item in evidence[:max_evidence]:
            print(f"- [{item['type']}] {item['value']} -> {item['reason']}")
    else:
        print("Nenhuma evidência direta encontrada.")

    domains_found = report_data.get("domains_found", [])
    if domains_found:
        print("-")
        print("Domínios relacionados encontrados:")
        for domain in domains_found:
            print(f"- {domain}")

    errors = report_data.get("errors", [])
    if errors:
        print("-")
        print("Observações/erros não fatais:")
        for err in errors:
            print(f"- {err}")

    print("=" * 72)


def save_json_report(report_data: dict, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path
