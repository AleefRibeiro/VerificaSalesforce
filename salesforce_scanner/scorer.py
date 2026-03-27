from __future__ import annotations

from collections import defaultdict
from typing import Iterable

from .patterns import (
    CORE_PATTERN_KEYS,
    MARKETING_PATTERN_KEYS,
    SERVICE_PATTERN_KEYS,
    COMMERCE_PATTERN_KEYS,
    EXPERIENCE_PATTERN_KEYS,
)

DEFAULT_SOURCE_MULTIPLIER = 0.8
SOURCE_MULTIPLIERS = {
    "network_request": 1.45,
    "redirect_chain": 1.40,
    "script_url": 1.25,
    "iframe": 1.25,
    "discovered_script_url": 1.20,
    "subdomain_url": 1.15,
    "sitemap_url": 1.10,
    "script_content": 1.00,
    "discovered_script_content": 0.95,
    "html_rendered": 0.90,
    "html_initial": 0.80,
    "discovered_html": 0.80,
    "discovered_url": 0.75,
    "link": 0.65,
    "discovered_link": 0.65,
    "robots_txt": 0.55,
    "sitemap_xml": 0.55,
    "cookie": 0.50,
}


def compute_score(evidence: list[dict]) -> dict:
    score_by_pattern: dict[str, int] = {}
    score_by_source: dict[str, int] = defaultdict(int)
    sources_by_pattern: dict[str, set[str]] = defaultdict(set)
    pattern_strength: dict[str, str] = {}
    contributions: list[dict] = []

    for item in evidence:
        pattern_key = str(item.get("pattern_key", "")).strip()
        source_type = str(item.get("type", "")).strip()
        if not pattern_key or not source_type:
            continue

        base_weight = int(item.get("weight", 0) or 0)
        if base_weight <= 0:
            continue

        strength = str(item.get("pattern_strength", "weak"))
        cap = int(item.get("pattern_cap") or _default_cap(strength, base_weight))
        multiplier = SOURCE_MULTIPLIERS.get(source_type, DEFAULT_SOURCE_MULTIPLIER)
        raw_points = max(1, int(round(base_weight * multiplier)))

        current_pattern_score = score_by_pattern.get(pattern_key, 0)
        available = max(0, cap - current_pattern_score)
        applied_points = min(raw_points, available)
        if applied_points <= 0:
            continue

        score_by_pattern[pattern_key] = current_pattern_score + applied_points
        score_by_source[source_type] += applied_points
        sources_by_pattern[pattern_key].add(source_type)
        pattern_strength.setdefault(pattern_key, strength)

        contributions.append(
            {
                "pattern_key": pattern_key,
                "source_type": source_type,
                "base_weight": base_weight,
                "multiplier": multiplier,
                "raw_points": raw_points,
                "applied_points": applied_points,
                "pattern_cap": cap,
            }
        )

    total_score = sum(score_by_pattern.values())

    strong_patterns = sorted(
        [
            key
            for key, points in score_by_pattern.items()
            if points > 0 and pattern_strength.get(key) == "strong"
        ]
    )
    medium_patterns = sorted(
        [
            key
            for key, points in score_by_pattern.items()
            if points > 0 and pattern_strength.get(key) == "medium"
        ]
    )
    weak_patterns = sorted(
        [
            key
            for key, points in score_by_pattern.items()
            if points > 0 and pattern_strength.get(key) == "weak"
        ]
    )
    cross_source_patterns = sorted(
        [key for key, sources in sources_by_pattern.items() if len(sources) >= 2]
    )

    return {
        "total_score": total_score,
        "score_by_pattern": dict(sorted(score_by_pattern.items())),
        "score_by_source": dict(sorted(score_by_source.items())),
        "sources_by_pattern": {key: sorted(value) for key, value in sources_by_pattern.items()},
        "strong_patterns": strong_patterns,
        "medium_patterns": medium_patterns,
        "weak_patterns": weak_patterns,
        "cross_source_patterns": cross_source_patterns,
        "contributions": contributions,
    }


def infer_products(evidence: list[dict]) -> list[str]:
    products: set[str] = set()

    for item in evidence:
        for product in item.get("products", []):
            products.add(product)

        pattern_key = item.get("pattern_key")
        value = str(item.get("value", "")).lower()

        if pattern_key in SERVICE_PATTERN_KEYS:
            products.add("Service Cloud")
        if pattern_key in MARKETING_PATTERN_KEYS:
            products.add("Marketing Cloud")
        if pattern_key in EXPERIENCE_PATTERN_KEYS:
            products.add("Experience Cloud")
        if pattern_key in COMMERCE_PATTERN_KEYS:
            products.add("Commerce Cloud")

        if pattern_key == "force_domain" and any(token in value for token in ("/s/", "community", "portal")):
            products.add("Experience Cloud")

    order = ["Service Cloud", "Marketing Cloud", "Experience Cloud", "Commerce Cloud"]
    return [item for item in order if item in products]


def decide_classification(evidence: list[dict], score_details: dict, products: list[str]) -> dict:
    score = int(score_details.get("total_score", 0))
    strong_patterns = set(score_details.get("strong_patterns", []))
    medium_patterns = set(score_details.get("medium_patterns", []))
    weak_patterns = set(score_details.get("weak_patterns", []))

    triggered_rules: list[str] = []

    high_confidence_sources = {
        "network_request",
        "redirect_chain",
        "iframe",
        "script_url",
        "link",
        "discovered_script_url",
        "sitemap_url",
        "subdomain_url",
    }

    if _has_pattern_in_sources(evidence, "service_force_domain", high_confidence_sources):
        triggered_rules.append("service.force.com em fonte de rede/URL forte")
        return _build_decision(
            classification="Confirmado",
            detected=True,
            score=score,
            triggered_rules=triggered_rules,
            rationale="Confirmado porque houve domínio service.force.com em fonte forte de rede/URL.",
        )

    if _has_pattern(evidence, "embeddedservice") and _has_any_pattern(
        evidence,
        {"force_domain", "service_force_domain", "salesforce_scrt_domain", "salesforce_domain"},
    ):
        triggered_rules.append("embeddedservice + domínio force/salesforce")
        return _build_decision(
            classification="Confirmado",
            detected=True,
            score=score,
            triggered_rules=triggered_rules,
            rationale="Confirmado porque embeddedservice apareceu com domínio force/salesforce.",
        )

    if _has_pattern(evidence, "liveagent") and _has_any_pattern(
        evidence,
        {"force_domain", "service_force_domain", "salesforce_scrt_domain", "salesforce_domain"},
    ):
        triggered_rules.append("liveagent + domínio force/salesforce")
        return _build_decision(
            classification="Confirmado",
            detected=True,
            score=score,
            triggered_rules=triggered_rules,
            rationale="Confirmado porque liveagent apareceu com domínio force/salesforce.",
        )

    if len(strong_patterns) >= 2 and score_details.get("cross_source_patterns"):
        triggered_rules.append("múltiplos sinais fortes com confirmação cruzada")
        return _build_decision(
            classification="Confirmado",
            detected=True,
            score=score,
            triggered_rules=triggered_rules,
            rationale="Confirmado por múltiplos sinais fortes em fontes diferentes.",
        )

    non_weak_patterns = strong_patterns | medium_patterns
    has_core_signal = bool((strong_patterns | medium_patterns) & CORE_PATTERN_KEYS)

    marketing_only = bool(non_weak_patterns) and non_weak_patterns.issubset(MARKETING_PATTERN_KEYS)
    commerce_only = bool(non_weak_patterns) and non_weak_patterns.issubset(COMMERCE_PATTERN_KEYS)

    if not non_weak_patterns and weak_patterns:
        classification = "Indício fraco / revisar manualmente" if score >= 10 else "Nenhum sinal encontrado"
        detected = False
        rationale = (
            "Foram encontrados apenas sinais fracos sem confirmação por domínios ou integrações fortes."
        )
        return _build_decision(classification, detected, score, triggered_rules, rationale)

    if score >= 85:
        classification = "Confirmado"
    elif score >= 55:
        classification = "Forte indício"
    elif score >= 28:
        classification = "Possível"
    elif score >= 10:
        classification = "Indício fraco / revisar manualmente"
    else:
        classification = "Nenhum sinal encontrado"

    if marketing_only and not has_core_signal:
        if classification == "Confirmado":
            classification = "Forte indício (Marketing Cloud)"
        elif classification in {"Forte indício", "Possível"}:
            classification = f"{classification} (Marketing Cloud)"

    if commerce_only and not has_core_signal and classification in {"Forte indício", "Possível"}:
        classification = f"{classification} (Commerce Cloud)"

    detected = classification.startswith("Confirmado") or classification.startswith("Forte indício") or classification.startswith("Possível")

    rationale = (
        f"Classificação derivada do score {score}, com {len(strong_patterns)} sinais fortes, "
        f"{len(medium_patterns)} médios e {len(weak_patterns)} fracos."
    )
    if products:
        rationale += f" Produtos inferidos: {', '.join(products)}."

    return _build_decision(
        classification=classification,
        detected=detected,
        score=score,
        triggered_rules=triggered_rules,
        rationale=rationale,
    )


def _has_pattern(evidence: Iterable[dict], pattern_key: str) -> bool:
    return any(item.get("pattern_key") == pattern_key for item in evidence)


def _has_pattern_in_sources(evidence: Iterable[dict], pattern_key: str, sources: set[str]) -> bool:
    return any(
        item.get("pattern_key") == pattern_key and item.get("type") in sources
        for item in evidence
    )


def _has_any_pattern(evidence: Iterable[dict], pattern_keys: set[str]) -> bool:
    return any(item.get("pattern_key") in pattern_keys for item in evidence)


def _default_cap(strength: str, base_weight: int) -> int:
    if strength == "strong":
        return max(90, base_weight * 2)
    if strength == "medium":
        return max(60, int(base_weight * 1.8))
    return max(30, int(base_weight * 1.5))


def _build_decision(
    classification: str,
    detected: bool,
    score: int,
    triggered_rules: list[str],
    rationale: str,
) -> dict:
    return {
        "classification": classification,
        "salesforce_detected": detected,
        "score": score,
        "triggered_rules": triggered_rules,
        "rationale": rationale,
    }
