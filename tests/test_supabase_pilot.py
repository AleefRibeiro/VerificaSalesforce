"""Offline mocks only: no Supabase account, user, credential or supplier is accessed."""
import base64
import json
import unittest
import asyncio
from types import SimpleNamespace
from datetime import datetime, timedelta, timezone

import httpx
from fastapi.testclient import TestClient

from research_api import Principal, create_app
from research_pilot import PilotSettings, build_pilot, settings_from_values
from research_v2.models import TargetInput
from research_v2.service import ResearchService, ResearchUnavailable, ResearchScope
from research_v2.supabase import (SupabaseConfigurationError, SupabaseConnection, SupabaseHTTPTransport,
    SupabasePrivateRPC, SupabaseResearchStore, SupabaseSessionVerifier)

NOW = datetime.now(timezone.utc)
USER = "00000000-0000-4000-8000-000000000001"
OTHER_USER = "00000000-0000-4000-8000-000000000002"
WORKSPACE = "00000000-0000-4000-8000-000000000003"
OTHER_WORKSPACE = "00000000-0000-4000-8000-000000000004"
SESSION = "00000000-0000-4000-8000-000000000005"
OWNER = ResearchScope(WORKSPACE, USER)
CONNECTION = SupabaseConnection("a" * 20, "sb_publishable_" + "offline_fixture_" * 2, "sb_secret_" + "offline_fixture_" * 2)
ORIGIN = "https://averon-tools.vercel.app"


def token(**changes):
    claims = {"sub": USER, "session_id": SESSION, "iss": CONNECTION.origin + "/auth/v1",
              "aud": "authenticated", "exp": int((NOW + timedelta(minutes=30)).timestamp()),
              "amr": [{"method": "oauth", "timestamp": int(NOW.timestamp())}],
              "user_metadata": {"workspace": OTHER_WORKSPACE, "permissions": ["admin"]}}
    claims.update(changes)
    encoded = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    # Signature verification is mocked at Auth. This string is never a real credential.
    return "offline-header." + encoded + ".offline-signature"


class FakeTransport:
    def __init__(self):
        self.calls = []
        self.user = {"id": USER, "is_anonymous": False, "email_confirmed_at": NOW.isoformat(), "identities": [{"provider": "google"}]}
        self.membership = {"workspace": WORKSPACE, "permissions": ["research:read", "research:write"]}
        self.results = {"charge_research_quota": True, "get_cached_report": None, "get_private_report": None, "save_private_report": True, "get_public_company": None}
        self.fail_auth = False

    async def request(self, method, url, *, headers, body=None):
        self.calls.append((method, url, headers, body))
        if url.endswith("/auth/v1/user"):
            if self.fail_auth:
                raise ResearchUnavailable()
            return self.user
        function = url.rsplit("/", 1)[1]
        if function == "verify_research_session":
            return self.membership if body == {"p_user_id": USER, "p_session_id": SESSION} else None
        return self.results[function]


class VerifierTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.rpc = SupabasePrivateRPC(CONNECTION, self.transport)
        self.verifier = SupabaseSessionVerifier(CONNECTION, self.rpc, self.transport, clock=lambda: NOW, allowed_workspace=WORKSPACE)

    async def test_verified_user_and_live_session_use_authoritative_membership(self):
        principal = await self.verifier.verify(token())
        self.assertEqual(principal.workspace, WORKSPACE)
        self.assertEqual(principal.permissions, frozenset({"research:read", "research:write"}))
        self.assertNotIn("admin", principal.permissions)
        self.assertEqual(self.transport.calls[0][2]["apikey"], CONNECTION.publishable_key)
        self.assertEqual(self.transport.calls[1][2]["apikey"], CONNECTION.secret_key)
        self.assertNotIn("Authorization", self.transport.calls[1][2])
        self.assertEqual(self.transport.calls[1][3], {"p_user_id": USER, "p_session_id": SESSION})

    async def test_invalid_signature_or_auth_failure_never_reaches_membership(self):
        self.transport.fail_auth = True
        self.assertIsNone(await self.verifier.verify(token()))
        self.assertEqual(len(self.transport.calls), 1)

    async def test_user_mismatch_is_denied(self):
        self.transport.user["id"] = OTHER_USER
        self.assertIsNone(await self.verifier.verify(token()))

    async def test_wrong_issuer_or_audience_is_denied(self):
        for changes in [{"iss": "https://other-project.invalid/auth/v1"}, {"aud": "anon"}]:
            self.assertIsNone(await self.verifier.verify(token(**changes)))

    async def test_missing_or_invalid_session_id_is_denied(self):
        for session in [None, "invalid", WORKSPACE.upper()]:
            self.assertIsNone(await self.verifier.verify(token(session_id=session)))

    async def test_expired_or_invalid_expiry_is_denied(self):
        for expiry in [NOW.timestamp() - 1, True, "later", float("inf")]:
            self.assertIsNone(await self.verifier.verify(token(exp=expiry)))

    async def test_anonymous_or_unconfirmed_identity_is_denied(self):
        for changes in [{"is_anonymous": True}, {"email_confirmed_at": None}]:
            self.transport.user = {"id": USER, "is_anonymous": False, "email_confirmed_at": NOW.isoformat()} | changes
            self.assertIsNone(await self.verifier.verify(token()))

    async def test_revoked_session_or_disabled_membership_is_denied(self):
        self.transport.membership = None
        self.assertIsNone(await self.verifier.verify(token()))

    async def test_other_workspace_or_unknown_permission_is_denied(self):
        for membership in [{"workspace": OTHER_WORKSPACE, "permissions": ["research:read"]},
                           {"workspace": WORKSPACE, "permissions": ["admin"]}]:
            self.transport.membership = membership
            self.assertIsNone(await self.verifier.verify(token()))

    async def test_keys_are_absent_from_connection_repr_and_config_errors(self):
        self.assertNotIn(CONNECTION.publishable_key, repr(CONNECTION))
        self.assertNotIn(CONNECTION.secret_key, repr(CONNECTION))
        with self.assertRaises(SupabaseConfigurationError):
            SupabaseConnection("bad-host", "invalid", "invalid")


class StoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.transport = FakeTransport()
        self.store = SupabaseResearchStore(SupabasePrivateRPC(CONNECTION, self.transport))

    async def report_fixture(self):
        return await ResearchService(providers=[], clock=lambda: NOW).research(WORKSPACE, TargetInput(domain="offline-company.example"))

    async def test_report_roundtrip_preserves_sources_and_nullable_dates(self):
        report = await self.report_fixture()
        await self.store.save(OWNER, ("offline-key",), report)
        self.transport.results["get_private_report"] = report.model_dump(mode="json")
        restored = await self.store.report(OWNER, report.id, NOW)
        self.assertEqual(restored, report)
        self.assertEqual(self.transport.calls[-1][3]["p_workspace"], WORKSPACE)

    async def test_invalid_id_returns_no_report_without_request(self):
        self.assertIsNone(await self.store.report(OWNER, "../other", NOW))
        self.assertEqual(self.transport.calls, [])

    async def test_expired_or_synthetic_storage_payload_fails_closed(self):
        report = await self.report_fixture()
        for changes in [{"expires_at": (NOW - timedelta(seconds=1)).isoformat()}, {"synthetic": True}]:
            self.transport.results["get_private_report"] = report.model_dump(mode="json") | changes
            with self.assertRaises(ResearchUnavailable):
                await self.store.report(OWNER, report.id, NOW)

    async def test_quota_has_no_memory_fallback_on_bad_rpc_response(self):
        self.transport.results["charge_research_quota"] = "true"
        with self.assertRaises(ResearchUnavailable):
            await self.store.charge(OWNER, 1)

    async def test_quota_denial_prevents_collection(self):
        self.transport.results["charge_research_quota"] = False
        service = ResearchService(providers=[], store=self.store, quota=self.store, clock=lambda: NOW)
        from research_v2.service import ResearchLimit
        with self.assertRaises(ResearchLimit):
            await service.research(OWNER, TargetInput(domain="offline-company.example"))
        self.assertEqual(len(self.transport.calls), 1)

    async def test_cache_key_hash_is_stable_and_has_no_plaintext(self):
        one = self.store.key_hash(("offline-company.example", None, (("public_html", "v1"),)))
        self.assertEqual(len(one), 64)
        self.assertEqual(one, self.store.key_hash(("offline-company.example", None, (("public_html", "v1"),))))
        self.assertNotEqual(one, self.store.key_hash(("other.example", None)))


class HTTPAndAPITests(unittest.IsolatedAsyncioTestCase):
    async def test_supabase_http_concurrency_is_bounded_per_process(self):
        active, maximum = 0, 0
        async def handle(request):
            nonlocal active, maximum
            active += 1; maximum = max(maximum, active)
            await asyncio.sleep(0.01)
            active -= 1
            return httpx.Response(200, json={"id": USER})
        transport = SupabaseHTTPTransport(CONNECTION.origin, transport=httpx.MockTransport(handle))
        await asyncio.gather(*(transport.request("GET", CONNECTION.origin + "/auth/v1/user", headers={}) for _ in range(8)))
        self.assertLessEqual(maximum, 4)

    async def test_redirect_and_non_json_responses_are_rejected(self):
        for response in [httpx.Response(302, headers={"location": "https://other.invalid"}), httpx.Response(200, text="invalid")]:
            transport = SupabaseHTTPTransport(CONNECTION.origin, transport=httpx.MockTransport(lambda req: response))
            with self.assertRaises(ResearchUnavailable):
                await transport.request("GET", CONNECTION.origin + "/auth/v1/user", headers={})

    async def test_wrong_origin_and_oversized_json_are_rejected(self):
        transport = SupabaseHTTPTransport(CONNECTION.origin, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"body": "x" * 1_048_576})))
        for url in ["https://unapproved.invalid/auth/v1/user", CONNECTION.origin + "/auth/v1/user"]:
            with self.assertRaises(ResearchUnavailable):
                await transport.request("GET", url, headers={})

    async def test_mock_http_json_roundtrip(self):
        transport = SupabaseHTTPTransport(CONNECTION.origin, transport=httpx.MockTransport(lambda req: httpx.Response(200, json={"id": USER})))
        self.assertEqual(await transport.request("GET", CONNECTION.origin + "/auth/v1/user", headers={}), {"id": USER})


class APIPilotTests(unittest.TestCase):
    def test_full_pilot_with_mock_html_auth_and_storage_only(self):
        class OfflineHTML:
            def __init__(self): self.calls = []
            async def fetch(self, domain):
                self.calls.append(domain)
                return SimpleNamespace(url="https://" + domain + "/", html='<script src="https://pi.pardot.com/offline-fixture.js"></script>')
        fake, html = FakeTransport(), OfflineHTML()
        app = build_pilot(PilotSettings(enabled=True, connection=CONNECTION, workspace=WORKSPACE,
            allowed_origins=(ORIGIN,), public_html_enabled=True), transport=fake, html_fetcher=html)
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer " + token()}
            access = client.get("/v2/access", headers=headers).json()
            self.assertTrue(access["research_enabled"])
            self.assertEqual(access["available_sources"], ["public_html"])
            response = client.post("/v2/research", json={"domain": "offline-company.example"}, headers=headers)
            self.assertEqual(response.status_code, 200)
            report = response.json()
            self.assertEqual(report["target"]["domain"], "offline-company.example")
            self.assertEqual(report["sources"][0]["provider"], "public_html")
            self.assertIsNone(report["evidence"][0]["published_at"])
            self.assertIn("html_reference_not_runtime_or_license_verification", report["evidence"][0]["flags"])
            self.assertEqual(html.calls, ["offline-company.example"])
            saved = next(call[3] for call in fake.calls if call[1].endswith("/save_private_report"))
            self.assertEqual(saved["p_workspace"], WORKSPACE)
            self.assertEqual(saved["p_user_id"], USER)
            self.assertEqual(saved["p_report"], report)

    def configured(self, fake=None, source=False):
        return build_pilot(PilotSettings(enabled=True, connection=CONNECTION, workspace=WORKSPACE,
            allowed_origins=(ORIGIN,), public_html_enabled=source), transport=fake or FakeTransport())

    def test_default_entrypoint_is_closed_and_legacy_scan_is_absent(self):
        with TestClient(build_pilot()) as client:
            self.assertEqual(client.get("/v2/access", headers={"Authorization": "Bearer " + token()}).status_code, 401)
            self.assertEqual(client.post("/scan", json={}).status_code, 404)

    def test_incomplete_pilot_settings_fail_closed(self):
        with self.assertRaises(SupabaseConfigurationError):
            build_pilot(PilotSettings(enabled=True))

    def test_disabled_config_does_not_read_secret_values(self):
        class DisabledValues(dict):
            def __getitem__(self, key):
                raise AssertionError("Secret settings must not be read while disabled")
        self.assertFalse(settings_from_values(DisabledValues()).enabled)

    def test_missing_or_invalid_config_raises_only_fixed_error(self):
        for values in [{"AVERON_PILOT_ENABLED": "enabled"},
                       {"AVERON_PILOT_ENABLED": "enabled", "AVERON_CORS_ORIGINS": "invalid sensitive offline body"}]:
            with self.assertRaises(SupabaseConfigurationError) as error:
                settings_from_values(values)
            self.assertNotIn("sensitive", str(error.exception))

    def test_preflight_is_allowed_only_for_exact_origin_and_headers(self):
        with TestClient(self.configured()) as client:
            response = client.options("/v2/research", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "authorization,content-type"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)
            self.assertNotIn("access-control-allow-credentials", response.headers)
            self.assertEqual(client.options("/v2/research", headers={"Origin": "https://unapproved.vercel.app", "Access-Control-Request-Method": "POST"}).status_code, 400)
            self.assertEqual(client.options("/v2/research", headers={"Origin": ORIGIN, "Access-Control-Request-Method": "POST", "Access-Control-Request-Headers": "x-workspace"}).status_code, 400)

    def test_unauthenticated_actual_request_is_still_denied_with_cors(self):
        with TestClient(self.configured()) as client:
            response = client.get("/v2/access", headers={"Origin": ORIGIN})
            self.assertEqual(response.status_code, 401)
            self.assertEqual(response.headers["access-control-allow-origin"], ORIGIN)

    def test_read_only_session_cannot_collect_and_discloses_no_identity(self):
        fake = FakeTransport(); fake.membership["permissions"] = ["research:read"]
        with TestClient(self.configured(fake, source=True)) as client:
            headers = {"Authorization": "Bearer " + token()}
            access = client.get("/v2/access", headers=headers).json()
            self.assertFalse(access["research_enabled"])
            self.assertNotIn(USER, json.dumps(access))
            self.assertEqual(client.post("/v2/research", json={"domain": "offline-company.example"}, headers=headers).status_code, 403)
            self.assertFalse(any("charge_research_quota" in call[1] for call in fake.calls))

    def test_sources_remain_disabled_without_explicit_source_flag(self):
        with TestClient(self.configured()) as client:
            access = client.get("/v2/access", headers={"Authorization": "Bearer " + token()}).json()
            self.assertEqual(access["available_sources"], [])
            self.assertFalse(access["research_enabled"])

    def test_storage_outage_returns_fixed_503_without_fallback(self):
        fake = FakeTransport(); fake.results["charge_research_quota"] = "invalid"
        with TestClient(self.configured(fake)) as client:
            response = client.post("/v2/research", json={"domain": "offline-company.example"}, headers={"Authorization": "Bearer " + token()})
            self.assertEqual(response.status_code, 503)
            self.assertNotIn(CONNECTION.secret_key, response.text)

    def test_wildcard_or_insecure_cors_config_is_rejected(self):
        for origin in ["*", "http://localhost", "https://*.vercel.app", ORIGIN + "/path", "https://user:password@host.invalid"]:
            with self.assertRaises(ValueError):
                create_app(allowed_origins=(origin,))


if __name__ == "__main__":
    unittest.main()
