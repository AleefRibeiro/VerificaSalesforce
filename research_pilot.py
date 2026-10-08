"""Future entrypoint; no remote configuration or environment reads in this checkpoint."""
from dataclasses import dataclass, field
import json
import os

from research_api import create_app
from research_v2.providers import ProviderPolicy, PublicHTMLProvider
from research_v2.service import ResearchService
from research_v2.supabase import SupabaseConnection, SupabaseHTTPTransport, SupabasePrivateRPC, SupabaseResearchStore, SupabaseSessionVerifier


@dataclass(frozen=True)
class PilotSettings:
    enabled: bool = False
    connection: SupabaseConnection | None = field(default=None, repr=False)
    workspace: str | None = None
    allowed_origins: tuple[str, ...] = ()
    public_html_enabled: bool = False


def build_pilot(settings=PilotSettings(), *, transport=None, html_fetcher=None):
    if not settings.enabled:
        return create_app()
    from research_v2.supabase import SupabaseConfigurationError, canonical_uuid
    try:
        canonical_uuid(settings.workspace)
        if not isinstance(settings.connection, SupabaseConnection) or not settings.allowed_origins:
            raise ValueError()
    except Exception:
        raise SupabaseConfigurationError() from None
    transport = transport or SupabaseHTTPTransport(settings.connection.origin)
    rpc = SupabasePrivateRPC(settings.connection, transport)
    store = SupabaseResearchStore(rpc)
    verifier = SupabaseSessionVerifier(settings.connection, rpc, transport, allowed_workspace=settings.workspace)
    # Only the explicitly approved workspace/source is eligible; no paid adapters.
    provider = PublicHTMLProvider(ProviderPolicy(enabled=settings.public_html_enabled,
        authorized_workspaces=frozenset({settings.workspace}),
        revision="public-html-pilot-enabled-v1" if settings.public_html_enabled else "public-html-pilot-disabled-v1"), fetcher=html_fetcher)
    service = ResearchService(providers=[provider], store=store, quota=store)
    return create_app(verifier=verifier, service=service, allowed_origins=settings.allowed_origins)


def settings_from_values(values):
    """Caller-supplied configuration, never a credentials file or an existing project."""
    if values.get("AVERON_PILOT_ENABLED") != "enabled":
        return PilotSettings()
    from research_v2.supabase import SupabaseConfigurationError
    try:
        origins = json.loads(values["AVERON_CORS_ORIGINS"])
        if not isinstance(origins, list) or not 1 <= len(origins) <= 4 or any(not isinstance(o, str) for o in origins):
            raise ValueError()
        source_flag = values.get("AVERON_PUBLIC_HTML_ENABLED", "disabled")
        if source_flag not in {"enabled", "disabled"}:
            raise ValueError()
        connection = SupabaseConnection(values["AVERON_SUPABASE_REF"], values["AVERON_SUPABASE_PUBLISHABLE_KEY"], values["AVERON_SUPABASE_SECRET_KEY"])
        return PilotSettings(enabled=True, connection=connection, workspace=values["AVERON_WORKSPACE_ID"],
                             allowed_origins=tuple(origins), public_html_enabled=source_flag == "enabled")
    except Exception:
        raise SupabaseConfigurationError() from None


def create_configured_pilot(values=None, *, transport=None):
    # Future uvicorn --factory entrypoint, only after remote configuration/rollout approval.
    return build_pilot(settings_from_values(os.environ if values is None else values), transport=transport)


app = build_pilot()  # Remains closed. Railway's current main:app is untouched.
