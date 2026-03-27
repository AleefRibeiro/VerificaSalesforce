from __future__ import annotations

from dataclasses import dataclass
import re
from typing import Pattern


STRONG = "strong"
MEDIUM = "medium"
WEAK = "weak"


@dataclass(frozen=True)
class PatternSpec:
    key: str
    regex: Pattern[str]
    weight: int
    reason: str
    strength: str
    cap: int
    products: tuple[str, ...] = ()


PATTERN_SPECS = [
    PatternSpec(
        key="service_force_domain",
        regex=re.compile(r"\bservice\.force\.com\b", re.IGNORECASE),
        weight=50,
        reason="Domínio service.force.com encontrado",
        strength=STRONG,
        cap=130,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="lightning_force_domain",
        regex=re.compile(r"\blightning\.force\.com\b", re.IGNORECASE),
        weight=50,
        reason="Domínio lightning.force.com encontrado",
        strength=STRONG,
        cap=130,
        products=("Experience Cloud", "Service Cloud"),
    ),
    PatternSpec(
        key="salesforce_scrt_domain",
        regex=re.compile(r"\b(?:[a-z0-9-]+\.)*salesforce-scrt\.com\b", re.IGNORECASE),
        weight=50,
        reason="Domínio *.salesforce-scrt.com encontrado",
        strength=STRONG,
        cap=130,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="salesforce_domain",
        regex=re.compile(r"\b(?:[a-z0-9-]+\.)*salesforce\.com\b", re.IGNORECASE),
        weight=45,
        reason="Domínio *.salesforce.com encontrado",
        strength=STRONG,
        cap=120,
        products=("Service Cloud", "Experience Cloud"),
    ),
    PatternSpec(
        key="force_domain",
        regex=re.compile(r"\b(?:[a-z0-9-]+\.)*force\.com\b", re.IGNORECASE),
        weight=42,
        reason="Domínio *.force.com encontrado",
        strength=STRONG,
        cap=120,
        products=("Service Cloud", "Experience Cloud"),
    ),
    PatternSpec(
        key="visualforce_domain",
        regex=re.compile(r"\b(?:[a-z0-9-]+\.)*visualforce\.com\b|\bvisualforce\b", re.IGNORECASE),
        weight=40,
        reason="Indicador Visualforce encontrado",
        strength=STRONG,
        cap=110,
        products=("Service Cloud", "Experience Cloud"),
    ),
    PatternSpec(
        key="embeddedservice",
        regex=re.compile(r"\bembeddedservice(?:_bootstrap)?\b", re.IGNORECASE),
        weight=45,
        reason="Indicador Embedded Service do Salesforce encontrado",
        strength=STRONG,
        cap=120,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="liveagent",
        regex=re.compile(r"\b(?:liveagent|salesforceliveagent)\b", re.IGNORECASE),
        weight=42,
        reason="Indicador de Live Agent do Salesforce encontrado",
        strength=STRONG,
        cap=115,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="salesforce_named_subdomain",
        regex=re.compile(
            r"\b(?:https?://)?(?:[a-z0-9-]+\.)*salesforce(?:-[a-z0-9-]+)*\.(?:com|net|org|br|io|co|cloud|site|app|biz)\b",
            re.IGNORECASE,
        ),
        weight=38,
        reason="Subdomínio com nomenclatura 'salesforce-*' encontrado",
        strength=STRONG,
        cap=100,
        products=("Service Cloud", "Experience Cloud", "Marketing Cloud"),
    ),
    PatternSpec(
        key="pardot",
        regex=re.compile(r"\bpardot\b", re.IGNORECASE),
        weight=35,
        reason="Indicador Pardot encontrado",
        strength=MEDIUM,
        cap=75,
        products=("Marketing Cloud",),
    ),
    PatternSpec(
        key="exacttarget",
        regex=re.compile(r"\b(?:exacttarget|mc\.exacttarget)\b", re.IGNORECASE),
        weight=35,
        reason="Indicador ExactTarget/Marketing Cloud encontrado",
        strength=MEDIUM,
        cap=75,
        products=("Marketing Cloud",),
    ),
    PatternSpec(
        key="marketingcloud",
        regex=re.compile(r"\b(?:marketingcloud|marketingcloudapps)\b", re.IGNORECASE),
        weight=32,
        reason="Indicador Marketing Cloud encontrado",
        strength=MEDIUM,
        cap=70,
        products=("Marketing Cloud",),
    ),
    PatternSpec(
        key="salesforce_interactions",
        regex=re.compile(r"\bsalesforceinteractions\b", re.IGNORECASE),
        weight=30,
        reason="SDK Salesforce Interactions encontrado",
        strength=MEDIUM,
        cap=70,
        products=("Marketing Cloud",),
    ),
    PatternSpec(
        key="experience_siteforce",
        regex=re.compile(r"\b(?:experience\s*cloud|siteforce)\b", re.IGNORECASE),
        weight=30,
        reason="Indicador Experience Cloud/Siteforce encontrado",
        strength=MEDIUM,
        cap=75,
        products=("Experience Cloud",),
    ),
    PatternSpec(
        key="commerce_cloud",
        regex=re.compile(r"\b(?:demandware|commerce\s*cloud|dwac)\b", re.IGNORECASE),
        weight=30,
        reason="Indicador Commerce Cloud/Demandware encontrado",
        strength=MEDIUM,
        cap=75,
        products=("Commerce Cloud",),
    ),
    PatternSpec(
        key="customer360",
        regex=re.compile(r"\bcustomer\s*360\b", re.IGNORECASE),
        weight=18,
        reason="Termo Customer 360 encontrado",
        strength=WEAK,
        cap=35,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="salesforce_product_clouds",
        regex=re.compile(r"\b(?:sales\s*cloud|service\s*cloud|health\s*cloud)\b", re.IGNORECASE),
        weight=18,
        reason="Nomenclatura de produto Salesforce (Sales/Service/Health Cloud) encontrada",
        strength=WEAK,
        cap=35,
        products=("Service Cloud",),
    ),
    PatternSpec(
        key="salesforce_generic",
        regex=re.compile(r"\bsalesforce\b", re.IGNORECASE),
        weight=12,
        reason="String genérica Salesforce encontrada",
        strength=WEAK,
        cap=30,
        products=(),
    ),
]

PATTERN_INDEX = {item.key: item for item in PATTERN_SPECS}

STRONG_PATTERN_KEYS = {item.key for item in PATTERN_SPECS if item.strength == STRONG}
MEDIUM_PATTERN_KEYS = {item.key for item in PATTERN_SPECS if item.strength == MEDIUM}
WEAK_PATTERN_KEYS = {item.key for item in PATTERN_SPECS if item.strength == WEAK}

MARKETING_PATTERN_KEYS = {
    "pardot",
    "exacttarget",
    "marketingcloud",
    "salesforce_interactions",
}
EXPERIENCE_PATTERN_KEYS = {
    "experience_siteforce",
    "lightning_force_domain",
}
SERVICE_PATTERN_KEYS = {
    "service_force_domain",
    "embeddedservice",
    "liveagent",
    "salesforce_scrt_domain",
}
COMMERCE_PATTERN_KEYS = {
    "commerce_cloud",
}

CORE_PATTERN_KEYS = {
    "service_force_domain",
    "lightning_force_domain",
    "salesforce_scrt_domain",
    "salesforce_domain",
    "force_domain",
    "visualforce_domain",
    "embeddedservice",
    "liveagent",
    "salesforce_named_subdomain",
}

DOMAIN_HINTS = {
    "force.com",
    "salesforce.com",
    "salesforce-scrt.com",
    "visualforce.com",
    "salesforceliveagent.com",
    "marketingcloudapps.com",
    "exacttarget.com",
    "mc.exacttarget",
    "pardot",
    "demandware",
    "salesforce",
}
