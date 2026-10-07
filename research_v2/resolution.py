from __future__ import annotations

import ipaddress
import re
from urllib.parse import urlsplit

from .models import Target, TargetInput


class InvalidTarget(ValueError):
    pass


def normalize_domain(value: str) -> str:
    """Only explicit HTTPS hostnames. No fuzzy brand, group or subsidiary guessing."""
    if not value or any(c.isspace() for c in value) or any(c in value for c in "\\%@"):
        raise InvalidTarget("Informe um domínio público explícito, sem credenciais.")
    try:
        parsed = urlsplit(value if "://" in value else "https://" + value)
        if parsed.scheme != "https" or parsed.username or parsed.password or parsed.port not in (None, 443):
            raise InvalidTarget("Somente HTTPS na porta padrão é permitido.")
        host = (parsed.hostname or "").encode("idna").decode("ascii").lower().rstrip(".")
    except (ValueError, UnicodeError) as exc:
        raise InvalidTarget("Domínio inválido.") from exc
    if host.startswith("www."):
        host = host[4:]
    try:
        ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        raise InvalidTarget("Informe um hostname público, não um endereço IP.")
    labels = host.split(".")
    if len(host) > 253 or len(labels) < 2 or not re.fullmatch(r"[a-z][a-z0-9-]{1,62}", labels[-1]):
        raise InvalidTarget("Informe um domínio público completo.")
    if any(not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label) for label in labels):
        raise InvalidTarget("Hostname inválido.")
    if labels[-1] in {"localhost", "local", "internal", "lan", "home", "invalid", "test"}:
        raise InvalidTarget("Domínios locais/internos não são permitidos.")
    return host


def resolve_target(value: TargetInput) -> Target:
    domain = normalize_domain(value.domain)
    return Target(domain=domain, url="https://" + domain + "/", company_name=value.company_name,
                  scope=value.scope, entity_id=value.entity_id)


def target_key(target: Target) -> tuple[str, str, str | None]:
    return target.domain, target.scope.value, target.entity_id


def same_domain(candidate: str | None, target: Target) -> bool:
    try:
        return candidate is not None and normalize_domain(candidate) == target.domain
    except InvalidTarget:
        return False
