"""Two-user authorization/retention tests; all Auth, HTML and persistence are mocked."""
import asyncio
import base64
import json
import unittest
from datetime import timedelta
from types import SimpleNamespace

from fastapi.testclient import TestClient

from research_pilot import PilotSettings, build_pilot
from research_v2.service import ResearchScope, ResearchService, ResearchUnavailable
from research_v2.supabase import SupabasePrivateRPC, SupabaseResearchStore
from research_v2.models import TargetInput
from test_supabase_pilot import NOW, USER, OTHER_USER, WORKSPACE, SESSION, CONNECTION, ORIGIN, token


class OwnerTransport:
    def __init__(self):
        self.calls, self.reports, self.cache = [], {}, {}
        self.active = {USER, OTHER_USER}

    async def request(self, method, url, *, headers, body=None):
        self.calls.append((url, body))
        if url.endswith("/auth/v1/user"):
            encoded = headers["Authorization"].split(".")[1]
            subject = json.loads(base64.urlsafe_b64decode(encoded + "=" * (-len(encoded) % 4)))["sub"]
            return {"id": subject, "is_anonymous": False, "email_confirmed_at": NOW.isoformat()}
        fn = url.rsplit("/", 1)[1]
        if fn == "verify_research_session":
            return {"workspace": WORKSPACE, "permissions": ["research:read", "research:write"]} if body["p_user_id"] in self.active and body["p_session_id"] == SESSION else None
        owner = (body["p_workspace"], body["p_user_id"])
        if fn == "charge_research_quota": return True
        if fn == "save_private_report":
            self.reports[(*owner, body["p_report"]["id"])] = body["p_report"]
            self.cache[(*owner, body["p_cache_key"])] = body["p_report"]
            return True
        if fn == "get_cached_report": return self.cache.get((*owner, body["p_cache_key"]))
        if fn in {"get_private_report", "get_history_report"}:
            return self.reports.get((*owner, body["p_report_id"]))
        if fn == "list_private_history":
            reports = [v for k, v in self.reports.items() if k[:2] == owner]
            start = body["p_offset"]
            return {"items": [{"id": r["id"], "domain": r["target"]["domain"], "checked_at": r["checked_at"],
                "saved_at": NOW.isoformat(), "retained_until": (NOW + timedelta(days=30)).isoformat(),
                "status": r["conclusion"]["status"]} for r in reports[start:start + 20]],
                "next_offset": start + 20 if len(reports) > start + 20 else None}
        raise AssertionError("Unmocked endpoint forbidden")


class OfflineHTML:
    def __init__(self): self.calls = []
    async def fetch(self, domain):
        self.calls.append(domain)
        await asyncio.sleep(0)
        return SimpleNamespace(url="https://" + domain + "/", html="<html>No technology statement in this offline fixture</html>")


class HistoryAPITests(unittest.TestCase):
    def setUp(self):
        self.transport, self.html = OwnerTransport(), OfflineHTML()
        self.app = build_pilot(PilotSettings(enabled=True, connection=CONNECTION, workspace=WORKSPACE,
            allowed_origins=(ORIGIN,), public_html_enabled=True), transport=self.transport, html_fetcher=self.html)

    def headers(self, user=USER): return {"Authorization": "Bearer " + token(sub=user)}

    def test_same_workspace_users_cannot_share_cache_report_or_history(self):
        with TestClient(self.app) as client:
            report = client.post("/v2/research", json={"domain": "offline-company.example"}, headers=self.headers()).json()
            for route in ["/v2/reports/", "/v2/history/"]:
                self.assertEqual(client.get(route + report["id"], headers=self.headers(OTHER_USER)).status_code, 404)
            self.assertEqual(client.get("/v2/history?user_id=" + USER, headers=self.headers(OTHER_USER)).json()["items"], [])
            friend = client.post("/v2/research", json={"domain": "offline-company.example"}, headers=self.headers(OTHER_USER)).json()
            self.assertNotEqual(report["id"], friend["id"])
            self.assertEqual(len(self.html.calls), 2)
            own = client.get("/v2/history", headers=self.headers()).json()
            other = client.get("/v2/history", headers=self.headers(OTHER_USER)).json()
            self.assertEqual([r["id"] for r in own["items"]], [report["id"]])
            self.assertEqual([r["id"] for r in other["items"]], [friend["id"]])
            self.assertNotIn(USER, json.dumps(own))

    def test_client_cannot_set_history_owner_in_request_body(self):
        with TestClient(self.app) as client:
            response = client.post("/v2/research", json={"domain": "offline-company.example", "user_id": OTHER_USER}, headers=self.headers())
            self.assertEqual(response.status_code, 422)
            self.assertEqual(self.html.calls, [])

    def test_revoked_membership_denies_history_without_storage_query(self):
        self.transport.active.remove(USER)
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/v2/history", headers=self.headers()).status_code, 401)
            self.assertFalse(any(url.endswith("/list_private_history") for url, _ in self.transport.calls))

    def test_history_requires_auth_has_no_store_headers_and_bounds_offset(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/v2/history").status_code, 401)
            response = client.get("/v2/history", headers=self.headers())
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            for offset in [-1, 128, "invalid"]:
                self.assertEqual(client.get(f"/v2/history?offset={offset}", headers=self.headers()).status_code, 422)

    def test_history_read_never_collects_html_or_charges_quota(self):
        with TestClient(self.app) as client:
            self.assertEqual(client.get("/v2/history", headers=self.headers()).status_code, 200)
        self.assertEqual(self.html.calls, [])
        self.assertFalse(any(url.endswith("/charge_research_quota") for url, _ in self.transport.calls))


class HistoryStoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.transport = OwnerTransport()
        self.store = SupabaseResearchStore(SupabasePrivateRPC(CONNECTION, self.transport))
        self.scope = ResearchScope(WORKSPACE, USER)

    async def test_plain_workspace_without_verified_owner_is_rejected(self):
        with self.assertRaises(ResearchUnavailable): await self.store.cached(WORKSPACE, ("fixture",), NOW)
        self.assertEqual(self.transport.calls, [])

    async def test_history_allows_expired_cache_without_renewing_original_date(self):
        report = await ResearchService(providers=[], clock=lambda: NOW - timedelta(days=2)).research(WORKSPACE, TargetInput(domain="offline-company.example"))
        self.transport.reports[(WORKSPACE, USER, report.id)] = report.model_dump(mode="json")
        historic = await self.store.history_report(self.scope, report.id, NOW)
        self.assertEqual(historic.checked_at, report.checked_at)
        self.assertEqual(historic.expires_at, report.expires_at)
        with self.assertRaises(ResearchUnavailable): await self.store.report(self.scope, report.id, NOW)

    async def test_synthetic_history_and_invalid_pagination_fail_closed(self):
        report = await ResearchService(providers=[], clock=lambda: NOW).research(WORKSPACE, TargetInput(domain="offline-company.example"))
        self.transport.reports[(WORKSPACE, USER, report.id)] = report.model_dump(mode="json") | {"synthetic": True}
        with self.assertRaises(ResearchUnavailable): await self.store.history_report(self.scope, report.id, NOW)
        for offset in [-1, 128, True]:
            with self.assertRaises(ResearchUnavailable): await self.store.history(self.scope, offset, NOW)

    async def test_concurrent_users_are_not_coalesced_into_one_report(self):
        from research_v2.providers import PublicHTMLProvider, ProviderPolicy
        html = OfflineHTML()
        provider = PublicHTMLProvider(ProviderPolicy(enabled=True, authorized_workspaces=frozenset({WORKSPACE})), fetcher=html)
        service = ResearchService(providers=[provider], store=self.store, quota=self.store)
        reports = await asyncio.gather(*(service.research(ResearchScope(WORKSPACE, user), TargetInput(domain="offline-company.example")) for user in [USER, OTHER_USER]))
        self.assertNotEqual(reports[0].id, reports[1].id)
        self.assertEqual(len(html.calls), 2)


if __name__ == "__main__": unittest.main()
