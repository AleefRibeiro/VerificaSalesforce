"""Fictitious .example fixtures, offline Auth/RPC. No production data is published."""
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from research_api import create_app, Principal
from research_v2.catalog import MemoryCatalogStore, SupabaseCatalogStore, ContributionInput, ReviewInput
from research_v2.models import TargetInput, ProviderOutcome
from research_v2.service import ResearchService, ResearchUnavailable, ResearchScope
from research_v2.supabase import SupabasePrivateRPC, SupabaseSessionVerifier
from test_supabase_pilot import FakeTransport, CONNECTION, WORKSPACE, USER, OTHER_USER, NOW, token

MODERATOR = "00000000-0000-4000-8000-000000000009"
INPUT = {"domain":"catalog-fixture.example","company_name":"Árvore Offline", "action":"confirmar",
         "reason":"Esta fonte fictícia é usada apenas neste teste offline.","source_url":"https://source-fixture.example/evidence",
         "published_at":(NOW-timedelta(days=2)).isoformat()}
NOTE = "Fonte revisada no teste offline, sem afirmação sobre uma empresa real."


class Verifier:
    async def verify(self, value):
        identities = {"user-one":USER,"user-two":OTHER_USER,"moderator":MODERATOR,"password":USER}
        if value not in identities: return None
        permissions = {"research:read","research:write"}
        if value=="moderator": permissions.add("catalog:moderate")
        return Principal(identities[value],WORKSPACE,frozenset(permissions),NOW+timedelta(minutes=20),
                         "google" if value!="password" else None)


class Collector:
    name, revision = "public_html", "offline-counter"
    calls = 0
    async def collect(self, target, workspace, now):
        self.calls+=1
        return ProviderOutcome(provider=self.name,state="ok",evidence=[])


class CatalogAPITests(unittest.TestCase):
    def setUp(self):
        self.store = MemoryCatalogStore(WORKSPACE,MODERATOR)
        self.collector=Collector()
        self.service=ResearchService(providers=[self.collector])
        self.client=TestClient(create_app(verifier=Verifier(),service=self.service,catalog=self.store,
            isolate_users=True,require_google=True,allowed_origins=("https://offline-app.example",)))

    def headers(self, who="user-one"):
        return {"Authorization":"Bearer "+who}

    def submit(self, value=None, who="user-one"):
        result=self.client.post("/v2/catalog/contributions",json=value or INPUT,headers=self.headers(who))
        self.assertEqual(result.status_code,201,result.text)
        return result.json()

    def review(self, entry, decision="aprovar", status="Indício", who="moderator", **extra):
        return self.client.post("/v2/catalog/moderation/"+entry["id"],headers=self.headers(who),
            json={"decision":decision,"qualification":NOTE,"status":status,"direct_evidence":False,**extra})

    def test_anonymous_search_empty_never_collects_or_charges(self):
        result=self.client.get("/catalog/companies",params={"q":"offline"})
        self.assertEqual(result.json(),{"items":[],"next_offset":None})
        self.assertEqual(result.headers["cache-control"],"no-store")
        self.assertEqual(self.collector.calls,0)
        self.assertFalse(self.service._rates)
        for path in ["/v2/research","/v2/research/batch","/v2/catalog/contributions"]:
            self.assertEqual(self.client.post(path,json=INPUT).status_code,401)
        self.assertEqual(self.collector.calls,0)

    def test_google_gate_rejects_password_before_scan_or_contribution(self):
        self.assertEqual(self.client.post("/v2/research",json={"domain":INPUT["domain"]},headers=self.headers("password")).status_code,403)
        self.assertEqual(self.client.post("/v2/catalog/contributions",json=INPUT,headers=self.headers("password")).status_code,403)
        self.assertEqual(self.collector.calls,0)

    def test_user_cannot_list_or_moderate_even_with_spoofed_role(self):
        entry=self.submit()
        self.assertEqual(self.client.get("/v2/catalog/access",headers=self.headers()).json(),{"can_contribute":True,"can_moderate":False})
        self.assertEqual(self.client.get("/v2/catalog/moderation?admin=true",headers=self.headers()).status_code,403)
        self.assertEqual(self.review(entry,who="user-one").status_code,403)
        self.assertEqual(self.client.get("/catalog/companies").json()["items"],[])

    def test_pending_is_private_and_own_contributions_isolate_users(self):
        entry=self.submit()
        mine=self.client.get("/v2/catalog/contributions",headers=self.headers()).json()
        other=self.client.get("/v2/catalog/contributions?user_id="+USER,headers=self.headers("user-two")).json()
        self.assertEqual(mine["items"][0]["id"],entry["id"])
        self.assertEqual(other["items"],[])
        self.assertEqual(self.client.get("/catalog/companies/"+INPUT["domain"]).status_code,404)
        self.assertFalse(any(k in entry for k in ["user_id","workspace_id","reviewed_by"]))

    def test_approve_makes_only_reviewed_allowlisted_fields_public_and_searchable(self):
        entry=self.submit()
        self.assertEqual(self.review(entry).status_code,200)
        for q in ["arvore","ÁRVORE",INPUT["domain"],"https://"+INPUT["domain"]+"/private?q=discard"]:
            result=self.client.get("/catalog/companies",params={"q":q}).json()["items"]
            self.assertEqual(len(result),1,q)
            self.assertEqual(result[0]["status"],"Indício")
            self.assertEqual(datetime.fromisoformat(result[0]["evidence"][0]["published_at"]),datetime.fromisoformat(INPUT["published_at"]))
            self.assertEqual(set(result[0]),{"domain","company_name","status","qualification","reviewed_at","evidence"})
            self.assertNotIn("reason",result[0]["evidence"][0])
        self.assertEqual(self.review(entry).status_code,409)

    def test_rejection_never_changes_existing_public_record(self):
        first=self.submit(); self.review(first)
        previous=self.client.get("/catalog/companies").json()
        contested=self.submit(INPUT|{"action":"contestar","source_url":"https://other-fixture.example/evidence"},who="user-two")
        self.assertEqual(self.review(contested,decision="rejeitar",status=None).status_code,200)
        self.assertEqual(self.client.get("/catalog/companies").json(),previous)

    def test_contestation_approval_preserves_sources_and_qualifies_unconfirmed(self):
        first=self.submit(); self.review(first)
        contested=self.submit(INPUT|{"action":"contestar","published_at":None,"source_url":"https://other-fixture.example/evidence"})
        self.assertEqual(self.review(contested,status="Não confirmado").status_code,200)
        record=self.client.get("/catalog/companies/"+INPUT["domain"]).json()
        self.assertEqual(record["status"],"Não confirmado")
        self.assertEqual(len(record["evidence"]),2)
        self.assertIsNone(record["evidence"][1]["published_at"])
        self.assertEqual(record["qualification"],NOTE)

    def test_confirmed_requires_direct_dated_confirmation(self):
        no_date=self.submit(INPUT|{"published_at":None})
        self.assertEqual(self.review(no_date,status="Uso confirmado",direct_evidence=True).status_code,409)
        dated=self.submit(INPUT|{"source_url":"https://dated-fixture.example/evidence"})
        self.assertEqual(self.review(dated,status="Uso confirmado").status_code,409)
        self.assertEqual(self.review(dated,status="Uso confirmado",direct_evidence=True).status_code,200)

    def test_existing_company_blocks_single_and_batch_before_quota_or_collection(self):
        entry=self.submit();self.review(entry)
        for path,body in [("/v2/research",{"domain":INPUT["domain"]}),
            ("/v2/research/batch",{"targets":[{"domain":"missing-fixture.example"},{"domain":INPUT["domain"]}]})]:
            self.assertEqual(self.client.post(path,json=body,headers=self.headers()).status_code,409)
        self.assertEqual(self.collector.calls,0)
        self.assertFalse(self.service._rates)

    def test_new_scan_stays_private_and_does_not_publish_or_submit(self):
        result=self.client.post("/v2/research",json={"domain":"new-fixture.example"},headers=self.headers())
        self.assertEqual(result.status_code,200,result.text)
        self.assertEqual(self.collector.calls,1)
        self.assertEqual(self.client.get("/catalog/companies").json()["items"],[])
        self.assertEqual(self.client.get("/v2/catalog/moderation",headers=self.headers("moderator")).json()["items"],[])
        report_id=result.json()["id"]
        self.assertEqual(self.client.get("/v2/reports/"+report_id,headers=self.headers("user-two")).status_code,404)

    def test_required_reason_source_and_no_client_owner_or_admin(self):
        for values in [{"reason":""},{"source_url":"http://source.example"},{"source_url":"https://source.example/evidence?token=hidden"},
            {"user_id":OTHER_USER},{"admin":True},{"published_at":"2999-01-01T00:00:00Z"}]:
            self.assertEqual(self.client.post("/v2/catalog/contributions",json=INPUT|values,headers=self.headers()).status_code,422,values)

    def test_duplicate_pending_does_not_create_duplicates(self):
        self.submit()
        self.assertEqual(self.client.post("/v2/catalog/contributions",json=INPUT,headers=self.headers()).status_code,409)
        self.assertEqual(len(self.client.get("/v2/catalog/contributions",headers=self.headers()).json()["items"]),1)

    def test_public_cors_preflight_and_invalid_paging(self):
        response=self.client.options("/catalog/companies",headers={"Origin":"https://offline-app.example","Access-Control-Request-Method":"GET"})
        self.assertEqual(response.status_code,200)
        self.assertEqual(response.headers["access-control-allow-origin"],"https://offline-app.example")
        self.assertEqual(self.client.get("/catalog/companies?offset=-1").status_code,422)
        self.assertEqual(self.client.get("/catalog/companies",params={"q":"x"*201}).status_code,422)

    def test_unconfigured_catalog_returns_unavailable_not_fake_empty_results(self):
        result=TestClient(create_app()).get("/catalog/companies")
        self.assertEqual(result.status_code,503)


class CatalogVerifierTests(unittest.IsolatedAsyncioTestCase):
    async def test_google_identity_and_verified_amr_are_both_required(self):
        transport=FakeTransport();rpc=SupabasePrivateRPC(CONNECTION,transport)
        verifier=SupabaseSessionVerifier(CONNECTION,rpc,transport,clock=lambda:NOW,allowed_workspace=WORKSPACE)
        self.assertEqual((await verifier.verify(token())).identity_provider,"google")
        for amr in [[],[{"method":"password"}],[{"method":"oauth"},{"method":"password"}]]:
            self.assertIsNone((await verifier.verify(token(amr=amr))).identity_provider)
        transport.user["identities"]=[{"provider":"email"}]
        self.assertIsNone((await verifier.verify(token(user_metadata={"provider":"google","admin":True}))).identity_provider)
        transport.user["identities"]=[{"provider":"google"},{"provider":"github"}]
        self.assertIsNone((await verifier.verify(token())).identity_provider)

    async def test_moderation_permission_comes_from_authoritative_rpc_only(self):
        transport=FakeTransport();rpc=SupabasePrivateRPC(CONNECTION,transport)
        verifier=SupabaseSessionVerifier(CONNECTION,rpc,transport,clock=lambda:NOW,allowed_workspace=WORKSPACE)
        forged=token(user_metadata={"role":"admin","permissions":["catalog:moderate"]})
        self.assertNotIn("catalog:moderate",(await verifier.verify(forged)).permissions)
        transport.membership["permissions"].append("catalog:moderate")
        self.assertIn("catalog:moderate",(await verifier.verify(forged)).permissions)
        transport.membership=None
        self.assertIsNone(await verifier.verify(forged))

    async def test_store_passes_owner_scope_and_never_reads_private_reports(self):
        class RPC:
            calls=[]
            async def call(self, name, body):
                self.calls.append((name,body));return {"items":[],"next_offset":None}
        rpc=RPC();store=SupabaseCatalogStore(rpc,WORKSPACE)
        await store.search("Árvore")
        await store.own(ResearchScope(WORKSPACE,USER))
        self.assertEqual(rpc.calls[0],("search_public_catalog",{"p_workspace":WORKSPACE,"p_query":"arvore","p_offset":0}))
        self.assertEqual(rpc.calls[1][1]["p_user_id"],USER)
        with self.assertRaises(ResearchUnavailable):await store.own(ResearchScope("wrong",USER))
        with self.assertRaises(ResearchUnavailable):await store.own(WORKSPACE)
