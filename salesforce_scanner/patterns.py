from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Pattern


@dataclass(frozen=True)
class PatternSpec:
    key: str
    regex: Pattern[str]
    weight: int
    reason: str


PATTERN_SPECS = [
    PatternSpec(
        key="service_force_domain",
        regex=re.compile(r"\bservice\.force\.com\b", re.IGNORECASE),
        weight=50,
        reason="Domínio service.force.com encontrado",
    ),
    PatternSpec(
        key="lightning_force_domain",
        regex=re.compile(r"\blightning\.force\.com\b", re.IGNORECASE),
        weight=50,
        reason="Domínio lightning.force.com encontrado",
    ),
    PatternSpec(
        key="embeddedservice",
        regex=re.compile(r"\bembeddedservice(?:_bootstrap)?\b", re.IGNORECASE),
        weight=45,
        reason="Indicador Embedded Service do Salesforce encontrado",
    ),
    PatternSpec(
        key="liveagent",
        regex=re.compile(r"\b(?:liveagent|salesforceliveagent)\b", re.IGNORECASE),
        weight=40,
        reason="Indicador de Live Agent do Salesforce encontrado",
    ),
    PatternSpec(
        key="force_domain",
        regex=re.compile(r"\b(?:[a-z0-9-]+\.)*force\.com\b", re.IGNORECASE),
        weight=40,
        reason="Domínio *.force.com encontrado",
    ),
    PatternSpec(
        key="pardot",
        regex=re.compile(r"\bpardot\b", re.IGNORECASE),
        weight=35,
        reason="Indicador Pardot encontrado",
    ),
    PatternSpec(
        key="exacttarget",
        regex=re.compile(r"\b(?:exacttarget|mc\.exacttarget)\b", re.IGNORECASE),
        weight=35,
        reason="Indicador ExactTarget/Marketing Cloud encontrado",
    ),
    PatternSpec(
        key="experience_siteforce",
        regex=re.compile(r"\b(?:experience\s*cloud|siteforce)\b", re.IGNORECASE),
        weight=30,
        reason="Indicador Experience Cloud/Siteforce encontrado",
    ),
    PatternSpec(
        key="marketingcloud",
        regex=re.compile(r"\b(?:marketingcloud|marketingcloudapps)\b", re.IGNORECASE),
        weight=30,
        reason="Indicador Marketing Cloud encontrado",
    ),
    PatternSpec(
        key="commerce_cloud",
        regex=re.compile(r"\b(?:demandware|commerce\s*cloud)\b", re.IGNORECASE),
        weight=30,
        reason="Indicador Commerce Cloud/Demandware encontrado",
    ),
    PatternSpec(
        key="visualforce",
        regex=re.compile(r"\bvisualforce\b", re.IGNORECASE),
        weight=25,
        reason="Indicador Visualforce encontrado",
    ),
    PatternSpec(
        key="salesforce_generic",
        regex=re.compile(r"\bsalesforce\b", re.IGNORECASE),
        weight=15,
        reason="String genérica Salesforce encontrada",
    ),
]


DOMAIN_HINTS = {
    "force.com",
    "salesforce.com",
    "salesforce",
    "marketingcloudapps.com",
    "exacttarget.com",
    "pardot.com",
    "demandware.net",
}
