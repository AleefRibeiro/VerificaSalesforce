from __future__ import annotations


def compute_score(evidence: list[dict]) -> int:
    """Soma pesos por padrão único para evitar inflação por repetições."""
    score = 0
    scored_patterns: set[str] = set()

    for item in evidence:
        pattern_key = item.get("pattern_key")
        weight = int(item.get("weight", 0))
        if not pattern_key or pattern_key in scored_patterns:
            continue
        scored_patterns.add(pattern_key)
        score += weight

    return score


def classify_score(score: int) -> str:
    if score >= 70:
        return "Confirmado"
    if 45 <= score <= 69:
        return "Forte indício"
    if 20 <= score <= 44:
        return "Possível"
    return "Nenhum sinal encontrado"


def salesforce_detected(score: int) -> bool:
    return score >= 20
