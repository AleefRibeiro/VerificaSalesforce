from __future__ import annotations

from collections import defaultdict
import json
from datetime import datetime, timezone
from pathlib import Path


def build_report(
    input_url: str,
    normalized_url: str,
    final_url: str,
    evidence: list[dict],
    domains_found: list[str],
    checked_resources: list[str],
    errors: list[str],
    score_details: dict,
    decision: dict,
    inferred_products: list[str],
) -> dict:
    serialized_evidence = [
        {
            "type": item.get("type"),
            "value": item.get("value"),
            "reason": item.get("reason"),
            "pattern_key": item.get("pattern_key"),
            "pattern_strength": item.get("pattern_strength"),
            "count": item.get("count", 1),
            "domain": item.get("domain"),
            "products": item.get("products", []),
        }
        for item in evidence
    ]

    evidence_by_source = _group_evidence_by_source(evidence)
    evidence_by_pattern = _group_evidence_by_pattern(evidence)

    return {
        "input_url": input_url,
        "normalized_url": normalized_url,
        "final_url": final_url,
        "score": int(decision.get("score", score_details.get("total_score", 0))),
        "classification": decision.get("classification", "Nenhum sinal encontrado"),
        "salesforce_detected": bool(decision.get("salesforce_detected", False)),
        "rationale": decision.get("rationale", ""),
        "triggered_rules": decision.get("triggered_rules", []),
        "inferred_products": inferred_products,
        "score_details": {
            "total_score": score_details.get("total_score", 0),
            "score_by_pattern": score_details.get("score_by_pattern", {}),
            "score_by_source": score_details.get("score_by_source", {}),
            "strong_patterns": score_details.get("strong_patterns", []),
            "medium_patterns": score_details.get("medium_patterns", []),
            "weak_patterns": score_details.get("weak_patterns", []),
            "cross_source_patterns": score_details.get("cross_source_patterns", []),
        },
        "evidence": serialized_evidence,
        "evidence_by_source": evidence_by_source,
        "evidence_by_pattern": evidence_by_pattern,
        "domains_found": domains_found,
        "checked_resources": checked_resources,
        "errors": errors,
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
    }


def print_terminal_report(report_data: dict, max_evidence: int = 12) -> None:
    print("=" * 88)
    print("Salesforce Public Evidence Scanner")
    print("=" * 88)
    print(f"URL informada     : {report_data['input_url']}")
    print(f"URL final         : {report_data['final_url']}")
    print(f"Score             : {report_data['score']}")
    print(f"Classificação     : {report_data['classification']}")
    print(f"Salesforce detect.: {report_data['salesforce_detected']}")

    rationale = report_data.get("rationale")
    if rationale:
        print(f"Rationale         : {rationale}")

    products = report_data.get("inferred_products", [])
    if products:
        print(f"Produtos inferidos: {', '.join(products)}")

    triggered_rules = report_data.get("triggered_rules", [])
    if triggered_rules:
        print("-")
        print("Regras determinísticas acionadas:")
        for rule in triggered_rules:
            print(f"- {rule}")

    print("-")
    evidence = report_data.get("evidence", [])
    if evidence:
        print(f"Principais evidências ({min(len(evidence), max_evidence)} de {len(evidence)}):")
        for item in evidence[:max_evidence]:
            strength = item.get("pattern_strength", "n/a")
            count = item.get("count", 1)
            print(
                f"- [{item['type']}] [{strength}] {item['value']}"
                f" (x{count}) -> {item['reason']}"
            )
    else:
        print("Nenhuma evidência direta encontrada.")

    evidence_by_source = report_data.get("evidence_by_source", {})
    if evidence_by_source:
        print("-")
        print("Resumo por fonte:")
        for source, count in evidence_by_source.items():
            print(f"- {source}: {count}")

    score_details = report_data.get("score_details", {})
    score_by_source = score_details.get("score_by_source", {})
    if score_by_source:
        print("-")
        print("Pontuação por fonte:")
        for source, points in score_by_source.items():
            print(f"- {source}: {points}")

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

    print("=" * 88)


def save_json_report(report_data: dict, output_path: str) -> Path:
    path = Path(output_path).expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(report_data, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _group_evidence_by_source(evidence: list[dict]) -> dict[str, int]:
    grouped: dict[str, int] = defaultdict(int)
    for item in evidence:
        grouped[str(item.get("type", "unknown"))] += int(item.get("count", 1) or 1)
    return dict(sorted(grouped.items()))


def _group_evidence_by_pattern(evidence: list[dict]) -> dict[str, dict]:
    grouped: dict[str, dict] = {}

    for item in evidence:
        pattern = str(item.get("pattern_key", "unknown"))
        entry = grouped.setdefault(
            pattern,
            {
                "strength": item.get("pattern_strength", "weak"),
                "total_occurrences": 0,
                "sources": set(),
                "sample_reason": item.get("reason", ""),
            },
        )
        entry["total_occurrences"] += int(item.get("count", 1) or 1)
        entry["sources"].add(str(item.get("type", "unknown")))

    serialized: dict[str, dict] = {}
    for pattern, entry in grouped.items():
        serialized[pattern] = {
            "strength": entry["strength"],
            "total_occurrences": entry["total_occurrences"],
            "sources": sorted(entry["sources"]),
            "sample_reason": entry["sample_reason"],
        }

    return dict(sorted(serialized.items()))
