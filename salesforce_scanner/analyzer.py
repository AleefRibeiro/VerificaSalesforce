from __future__ import annotations

from collections import OrderedDict
from typing import Iterable
from urllib.parse import urlparse

from .patterns import DOMAIN_HINTS, PATTERN_SPECS

URL_SOURCE_TYPES = {
    "script_url",
    "iframe",
    "link",
    "network_request",
    "redirect_chain",
    "discovered_url",
    "discovered_link",
    "discovered_script_url",
    "sitemap_url",
    "subdomain_url",
}

_REASON_BY_SOURCE = {
    "html_initial": "Indicador encontrado no HTML inicial",
    "html_rendered": "Indicador encontrado no HTML renderizado",
    "script_url": "URL de script com indicador Salesforce",
    "script_content": "Conteúdo JavaScript com indicador Salesforce",
    "iframe": "IFrame com indicador Salesforce",
    "link": "Link com indicador Salesforce",
    "network_request": "Request de rede para recurso com indicador Salesforce",
    "cookie": "Cookie com possível relação a Salesforce",
    "robots_txt": "Indicador encontrado em robots.txt",
    "sitemap_xml": "Indicador encontrado em sitemap.xml",
    "sitemap_url": "URL de sitemap com indicador Salesforce",
    "redirect_chain": "Redirecionamento com indicador Salesforce",
    "discovered_url": "Página descoberta com indicador Salesforce",
    "discovered_link": "Link descoberto com indicador Salesforce",
    "discovered_html": "Indicador encontrado em HTML de página descoberta",
    "discovered_script_url": "Script descoberto com indicador Salesforce",
    "discovered_script_content": "Conteúdo JS de página descoberta com indicador Salesforce",
    "subdomain_url": "Subdomínio público descoberto com indicador Salesforce",
}


def analyze_sources(sources: dict[str, list[str] | str]) -> tuple[list[dict], list[str]]:
    evidence_by_key: "OrderedDict[tuple[str, str, str], dict]" = OrderedDict()
    domains_found: set[str] = set()

    for source_type, raw_values in sources.items():
        values = _normalize_values(raw_values)
        for value in values:
            if not value:
                continue

            for pattern in PATTERN_SPECS:
                match = pattern.regex.search(value)
                if not match:
                    continue

                matched_text = match.group(0)
                evidence_value = value if source_type in URL_SOURCE_TYPES else matched_text
                normalized_domain = extract_domain(evidence_value)
                if normalized_domain and _is_domain_interesting(normalized_domain):
                    domains_found.add(normalized_domain)

                domains_found.update(_extract_interesting_domains([matched_text]))

                dedupe_token = (
                    normalized_domain
                    if source_type in URL_SOURCE_TYPES and normalized_domain
                    else _canonical_text(matched_text)
                )
                dedupe_key = (pattern.key, source_type, dedupe_token)

                if dedupe_key in evidence_by_key:
                    evidence_by_key[dedupe_key]["count"] += 1
                    continue

                evidence_by_key[dedupe_key] = {
                    "type": source_type,
                    "value": _truncate(evidence_value),
                    "reason": f"{_REASON_BY_SOURCE.get(source_type, 'Indicador encontrado')}: {pattern.reason}",
                    "pattern_key": pattern.key,
                    "pattern_strength": pattern.strength,
                    "weight": pattern.weight,
                    "pattern_cap": pattern.cap,
                    "products": list(pattern.products),
                    "domain": normalized_domain,
                    "matched_text": matched_text,
                    "count": 1,
                }

    evidence = list(evidence_by_key.values())
    evidence.sort(
        key=lambda item: (
            _strength_rank(item.get("pattern_strength", "weak")),
            item.get("weight", 0),
            item.get("count", 1),
        ),
        reverse=True,
    )
    return evidence, sorted(domains_found)


def extract_domain(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.hostname:
        return _normalize_domain(parsed.hostname)

    candidate = value.strip().lower().strip("/ ")
    if "." in candidate and " " not in candidate:
        return _normalize_domain(candidate)
    return None


def _normalize_domain(domain: str) -> str:
    return domain.lower().strip().strip(".")


def _is_domain_interesting(domain: str) -> bool:
    return any(hint in domain for hint in DOMAIN_HINTS)


def _normalize_values(raw_values: list[str] | str) -> list[str]:
    if isinstance(raw_values, str):
        return [raw_values]
    return [item for item in raw_values if isinstance(item, str)]


def _canonical_text(value: str) -> str:
    return " ".join(value.lower().split())


def _truncate(value: str, max_len: int = 320) -> str:
    if len(value) <= max_len:
        return value
    return f"{value[: max_len - 3]}..."


def _extract_interesting_domains(values: Iterable[str]) -> set[str]:
    domains: set[str] = set()
    for value in values:
        domain = extract_domain(value)
        if domain and _is_domain_interesting(domain):
            domains.add(domain)
    return domains


def _strength_rank(strength: str) -> int:
    if strength == "strong":
        return 3
    if strength == "medium":
        return 2
    return 1
