from __future__ import annotations

from typing import Iterable
from urllib.parse import urlparse

from .patterns import DOMAIN_HINTS, PATTERN_SPECS

URL_SOURCE_TYPES = {"script_url", "iframe", "link", "network_request", "redirect_chain"}

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
    "redirect_chain": "Redirecionamento com indicador Salesforce",
}


def analyze_sources(sources: dict[str, list[str] | str]) -> tuple[list[dict], list[str]]:
    evidence: list[dict] = []
    evidence_seen: set[tuple[str, str, str]] = set()
    domains_found: set[str] = set()

    for source_type, raw_values in sources.items():
        values = _normalize_values(raw_values)
        for value in values:
            if not value:
                continue

            if source_type in URL_SOURCE_TYPES:
                domain = extract_domain(value)
                if domain and _is_domain_interesting(domain):
                    domains_found.add(domain)

            for pattern in PATTERN_SPECS:
                match = pattern.regex.search(value)
                if not match:
                    continue

                matched_text = match.group(0)
                evidence_value = value if source_type in URL_SOURCE_TYPES else matched_text
                dedupe_key = (source_type, pattern.key, evidence_value.lower())

                if dedupe_key in evidence_seen:
                    continue

                evidence_seen.add(dedupe_key)
                domains_found.update(_extract_interesting_domains([evidence_value, matched_text]))

                evidence.append(
                    {
                        "type": source_type,
                        "value": _truncate(evidence_value),
                        "reason": f"{_REASON_BY_SOURCE.get(source_type, 'Indicador encontrado')}: {pattern.reason}",
                        "pattern_key": pattern.key,
                        "weight": pattern.weight,
                    }
                )

    evidence.sort(key=lambda item: item["weight"], reverse=True)
    return evidence, sorted(domains_found)


def extract_domain(value: str) -> str | None:
    parsed = urlparse(value)
    if parsed.hostname:
        return parsed.hostname.lower()

    candidate = value.strip().lower().strip("/ ")
    if "." in candidate and " " not in candidate:
        return candidate
    return None


def _is_domain_interesting(domain: str) -> bool:
    return any(hint in domain for hint in DOMAIN_HINTS)


def _normalize_values(raw_values: list[str] | str) -> list[str]:
    if isinstance(raw_values, str):
        return [raw_values]
    return [item for item in raw_values if isinstance(item, str)]


def _truncate(value: str, max_len: int = 280) -> str:
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
