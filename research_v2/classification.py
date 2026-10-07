from __future__ import annotations

import hashlib
import re
from datetime import datetime, timedelta
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .models import Conclusion, Evidence, Scope, Status, Target
from .resolution import InvalidTarget, normalize_domain, same_domain

NO_SIGNAL = "A ausência de sinais públicos não significa que a empresa não usa Salesforce."
CLIENT_CONTEXT = re.compile(r"for (?:our|a|the) client|para (?:nosso|um|o) cliente|client projects|projetos (?:de|para) clientes|on behalf of", re.I)
MIGRATION_AWAY = re.compile(r"(?:migrat\w*|mov\w*)\s+(?:off|away from)\s+salesforce|(?:migrat\w*|mov\w*)\s+from\s+salesforce\s+to|replac\w*\s+salesforce|saindo\s+(?:do\s+)?salesforce|migr\w*\s+de\s+salesforce\s+para|descontinu\w*\s+(?:o\s+)?salesforce", re.I)


def text_context(text: str) -> tuple[str, str, list[str]]:
    relation, context, flags = "self", "current_signal", []
    if CLIENT_CONTEXT.search(text):
        relation = "client"
        flags.append("recruiting_for_client")
    if MIGRATION_AWAY.search(text):
        context = "migration_away"
        flags.append("migration_away_not_current_confirmation")
    return relation, context, flags


def products_in_text(text: str) -> list[str]:
    patterns = {"Marketing Cloud": r"marketing\s*cloud|exacttarget|pardot", "Sales Cloud": r"\bsales\s+cloud\b",
                "Service Cloud": r"\bservice\s+cloud\b|embeddedservice|liveagent", "Experience Cloud": r"\bexperience\s+cloud\b",
                "Commerce Cloud": r"\bcommerce\s+cloud\b|demandware"}
    products = [name for name, pattern in patterns.items() if re.search(pattern, text, re.I)]
    if not products and re.search(r"\bsalesforce\b", text, re.I):
        products = ["Salesforce (produto não determinado)"]
    return products


def canonical_origin(url: str | None) -> str | None:
    if not url:
        return None
    try:
        parsed = urlsplit(url)
        if parsed.scheme not in {"https", "http"} or not parsed.hostname or parsed.username or parsed.password:
            return None
        query = [(k, v) for k, v in parse_qsl(parsed.query) if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "gh_src"}]
        return urlunsplit((parsed.scheme, parsed.netloc.lower(), parsed.path.rstrip("/") or "/", urlencode(sorted(query)), ""))
    except ValueError:
        return None


def deduplicate(evidence: list[Evidence]) -> list[Evidence]:
    """One origin can't be counted again merely because another vendor republishes it."""
    result: list[Evidence] = []
    origins: set[str] = set()
    content: set[str] = set()
    # Prefer explicit disqualifying context; otherwise prefer stronger original evidence.
    # A repost cannot hide a migration/client qualifier or replace direct evidence.
    ordered = sorted(evidence, key=lambda item: (item.relation != "client", item.context != "migration_away",
                                               {"strong": 0, "medium": 1, "weak": 2}[item.strength]))
    for item in ordered:
        origin = canonical_origin(item.origin_url or item.source_url)
        if not origin and item.origin_id:
            origin = item.provider + ":" + item.origin_id
        normalized_text = re.sub(r"\s+", " ", item.excerpt.strip().lower())
        try:
            subject = normalize_domain(item.subject_domain or "")
        except InvalidTarget:
            subject = str(item.subject_domain)
        # Missing text must not collapse all independent pages into one.
        fingerprint = hashlib.sha256((subject + ":" + normalized_text).encode()).hexdigest() if normalized_text else None
        if (origin and origin in origins) or (fingerprint and fingerprint in content):
            continue
        if origin:
            origins.add(origin)
        if fingerprint:
            content.add(fingerprint)
        result.append(item)
    return result


def classify(target: Target, evidence: list[Evidence], checked_at: datetime, *, errors: bool = False,
             ambiguous: bool = False, max_age_days: int = 180) -> Conclusion:
    if checked_at.tzinfo is None or max_age_days <= 0:
        raise ValueError("A timezone-aware review date and positive freshness window are required.")
    unique = deduplicate(evidence)
    eligible, historical = [], []
    limitations = [NO_SIGNAL, "A conclusão cobre somente o domínio/escopo informado e as fontes consultadas; não comprova contratos ou licenças."]
    for item in unique:
        if not same_domain(item.subject_domain, target) or item.relation != "self":
            continue
        if target.scope != Scope.DOMAIN and (item.scope != target.scope or not target.entity_id or item.entity_id != target.entity_id):
            continue
        if not canonical_origin(item.origin_url or item.source_url) or not item.products:
            continue
        source_date = item.last_seen if item.kind == "technical_reference" else item.published_at or item.last_seen
        dates = [item.checked_at, *[date for date in [source_date, item.published_at, item.first_seen, item.last_seen] if date]]
        if not source_date or any(date > checked_at for date in dates) or (item.first_seen and item.last_seen and item.first_seen > item.last_seen):
            continue
        if item.context == "migration_away" or source_date < checked_at - timedelta(days=max_age_days):
            historical.append(item)
        elif item.context == "current_signal":
            eligible.append(item)
    products = sorted({product for item in eligible for product in item.products})
    direct = [item for item in eligible if item.kind in {"technical_reference", "official_statement"} and item.strength == "strong"]
    if direct:
        status, rationale = Status.CONFIRMED, "Há referência direta e recente no domínio ou escopo analisado; a conclusão se limita a essa evidência pública."
    elif len([item for item in eligible if item.strength in {"strong", "medium"}]) >= 2:
        status, rationale = Status.STRONG, "Há sinais relevantes de origens distintas, ainda sem confirmação direta de uso."
    elif eligible:
        status, rationale = Status.POSSIBLE, "Há um sinal relevante que precisa de revisão de contexto e confirmação."
    elif historical:
        status, rationale = Status.HISTORICAL, "Há evidência antiga ou de saída/migração; ela não confirma o uso atual."
        products = sorted({product for item in historical for product in item.products})
    elif ambiguous:
        status, rationale = Status.AMBIGUOUS, "Uma fonte retornou outra empresa/escopo; não houve resolução segura da identidade."
    elif errors:
        status, rationale = Status.ERROR, "As fontes disponíveis falharam; não há uma pesquisa concluída para interpretar."
    else:
        status, rationale = Status.INCONCLUSIVE, "Não há evidência suficiente, atual e atribuída ao escopo informado."
    if errors:
        limitations.append("Coleta parcial: falhas de fonte não equivalem a ausência de uso.")
    if target.scope != Scope.DOMAIN:
        limitations.append("Evidência de um domínio, filial ou cliente não é promovida automaticamente a uso por toda a empresa/grupo.")
    return Conclusion(status=status, rationale=rationale, products=products, independent_origins=len(eligible), limitations=limitations)
