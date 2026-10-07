"""Offline synthetic fixtures only. These tests make no public/vendor network calls."""
import asyncio
import unittest
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from research_api import Principal, create_app
from research_v2.classification import classify, deduplicate, products_in_text, text_context
from research_v2.models import Evidence, ProviderOutcome, Scope, Status, TargetInput
from research_v2.providers import ApolloProvider, ProviderPolicy, PublicHTMLProvider, TheirStackProvider
from research_v2.resolution import InvalidTarget, normalize_domain, resolve_target
from research_v2.service import MemoryStore, ResearchLimit, ResearchService
from research_v2.transport import FetchError, Page, PublicHTMLFetcher, ProviderJSONTransport, validate_addresses

NOW = datetime(2026, 10, 7, 12, tzinfo=timezone.utc)
TARGET = resolve_target(TargetInput(domain="synthetic-company.example"))


def fixture(**changes):
    fields = dict(provider="synthetic_fixture", origin_url="https://synthetic-company.example/jobs/1", subject_domain=TARGET.domain,
                  kind="job_posting", products=["Sales Cloud"], excerpt="Synthetic fixture: maintain our Sales Cloud instance",
                  checked_at=NOW, published_at=NOW - timedelta(days=10), relation="self", strength="medium", synthetic=True)
    fields.update(changes)
    return Evidence(**fields)


class ClassificationTests(unittest.TestCase):
    def test_no_signal_is_inconclusive_not_negative(self):
        result = classify(TARGET, [], NOW)
        self.assertEqual(result.status, Status.INCONCLUSIVE)
        self.assertIn("não significa", result.limitations[0])

    def test_consultancy_recruiting_for_client_is_not_self(self):
        relation, context, flags = text_context("Synthetic fixture: Salesforce developer for our client")
        self.assertEqual(relation, "client")
        self.assertEqual(classify(TARGET, [fixture(relation=relation, context=context, flags=flags)], NOW).status, Status.INCONCLUSIVE)

    def test_migration_away_is_historical(self):
        relation, context, flags = text_context("Synthetic fixture: migrate away from Salesforce to another CRM")
        self.assertEqual(classify(TARGET, [fixture(relation=relation, context=context, flags=flags)], NOW).status, Status.HISTORICAL)

    def test_migrating_from_salesforce_to_another_crm_is_historical(self):
        _, context, _ = text_context("Synthetic fixture: migrating from Salesforce to another CRM")
        self.assertEqual(context, "migration_away")

    def test_marketing_is_not_sales_cloud(self):
        self.assertEqual(products_in_text("Salesforce Marketing Cloud and Pardot"), ["Marketing Cloud"])
        self.assertNotIn("Sales Cloud", products_in_text("Salesforce administrator"))

    def test_repost_is_one_origin(self):
        original = fixture()
        repost = fixture(provider="second_synthetic_vendor", origin_url=original.origin_url + "?utm_source=vendor", excerpt="Different repost text")
        self.assertEqual(len(deduplicate([original, repost])), 1)
        self.assertEqual(classify(TARGET, [original, repost], NOW).status, Status.POSSIBLE)

    def test_syndicated_description_is_not_independent(self):
        copy = fixture(origin_url="https://synthetic-jobboard.example/jobs/99")
        self.assertEqual(len(deduplicate([fixture(), copy])), 1)

    def test_syndicated_description_normalizes_subject_domain(self):
        copy = fixture(origin_url="https://synthetic-jobboard.example/jobs/99", subject_domain="www." + TARGET.domain)
        self.assertEqual(len(deduplicate([fixture(), copy])), 1)

    def test_two_independent_medium_sources_are_strong_not_confirmed(self):
        second = fixture(origin_url="https://synthetic-company.example/jobs/2", excerpt="Synthetic fixture: operate our CRM")
        result = classify(TARGET, [fixture(), second], NOW)
        self.assertEqual(result.status, Status.STRONG)
        self.assertEqual(result.independent_origins, 2)

    def test_weak_source_cannot_upgrade_single_medium(self):
        second = fixture(origin_url="https://synthetic-source.example/2", excerpt="Synthetic weak signal", strength="weak")
        self.assertEqual(classify(TARGET, [fixture(), second], NOW).status, Status.POSSIBLE)

    def test_direct_reference_is_public_confirmation_with_limited_scope(self):
        direct = fixture(kind="technical_reference", strength="strong", last_seen=NOW, published_at=None)
        self.assertEqual(classify(TARGET, [direct], NOW).status, Status.CONFIRMED)

    def test_subsidiary_is_not_group(self):
        group = resolve_target(TargetInput(domain=TARGET.domain, scope=Scope.GROUP, entity_id="synthetic-parent"))
        subsidiary = fixture(scope=Scope.SUBSIDIARY, entity_id="synthetic-child", kind="technical_reference", strength="strong", last_seen=NOW)
        self.assertEqual(classify(group, [subsidiary], NOW).status, Status.INCONCLUSIVE)

    def test_other_domain_is_not_transferred(self):
        self.assertEqual(classify(TARGET, [fixture(subject_domain="other-synthetic.example")], NOW).status, Status.INCONCLUSIVE)

    def test_unknown_date_or_future_date_cannot_confirm(self):
        for sample in [fixture(published_at=None), fixture(published_at=NOW + timedelta(days=1))]:
            self.assertEqual(classify(TARGET, [sample], NOW).status, Status.INCONCLUSIVE)

    def test_old_publication_is_historical_even_when_checked_today(self):
        self.assertEqual(classify(TARGET, [fixture(published_at=NOW - timedelta(days=400))], NOW).status, Status.HISTORICAL)

    def test_duplicate_keeps_disqualifying_context(self):
        self.assertEqual(classify(TARGET, [fixture(), fixture(context="migration_away")], NOW).status, Status.HISTORICAL)

    def test_errors_and_ambiguity_are_explicit(self):
        self.assertEqual(classify(TARGET, [], NOW, errors=True).status, Status.ERROR)
        self.assertEqual(classify(TARGET, [], NOW, ambiguous=True).status, Status.AMBIGUOUS)


class ResolutionAndSSRFTests(unittest.IsolatedAsyncioTestCase):
    def test_normalizes_explicit_domain_without_guessing_identity(self):
        target = resolve_target(TargetInput(domain="https://www.synthetic-company.example/path", company_name="Synthetic label"))
        self.assertEqual(target.domain, TARGET.domain)
        self.assertFalse(target.legal_identity_verified)

    def test_rejects_ip_local_credentials_http_and_ports(self):
        for value in ["127.0.0.1", "[::1]", "169.254.169.254", "metadata.internal", "localhost", "http://example.com", "https://user@example.com", "https://example.com:8443", "example.com\\@127.0.0.1", "example.com%2f"]:
            with self.subTest(value=value), self.assertRaises(InvalidTarget):
                normalize_domain(value)

    def test_rejects_any_private_dns_answer_and_mapped_ipv6(self):
        for addresses in [["8.8.8.8", "10.0.0.1"], ["169.254.169.254"], ["::ffff:127.0.0.1"], ["::1"], ["224.0.0.1"], ["64:ff9b::a00:1"], ["2002:7f00:1::"], []]:
            with self.subTest(addresses=addresses), self.assertRaises(FetchError):
                validate_addresses(addresses)

    async def test_private_dns_prevents_connection(self):
        calls = []
        def connection(*args):
            calls.append(args)
            raise AssertionError("Must not connect")
        fetcher = PublicHTMLFetcher(resolver=lambda _: ["127.0.0.1"], connection_factory=connection)
        with self.assertRaises(FetchError):
            await fetcher.fetch(TARGET.domain)
        self.assertEqual(calls, [])

    async def test_external_redirect_is_blocked_before_next_lookup(self):
        lookups, reads = [], []
        class SyntheticFetcher(PublicHTMLFetcher):
            def _read(self, parsed, address, deadline):
                reads.append((parsed.hostname, address))
                return 302, {"location": "https://metadata.internal/"}, b""
        def resolver(host):
            lookups.append(host)
            return ["8.8.8.8"]
        with self.assertRaises(FetchError):
            await SyntheticFetcher(resolver=resolver).fetch(TARGET.domain)
        self.assertEqual(lookups, [TARGET.domain])
        self.assertEqual(reads[0][1], "8.8.8.8")

    async def test_http_redirect_is_rejected(self):
        class SyntheticFetcher(PublicHTMLFetcher):
            def _read(self, *args):
                return 302, {"location": "http://" + TARGET.domain + "/"}, b""
        with self.assertRaises(FetchError):
            await SyntheticFetcher(resolver=lambda _: ["8.8.8.8"]).fetch(TARGET.domain)

    async def test_vendor_url_allowlist(self):
        with self.assertRaises(FetchError):
            await ProviderJSONTransport().request("GET", "https://metadata.internal/", headers={})

    def test_tls_verification_is_enabled(self):
        import ssl
        from research_v2.transport import PinnedHTTPSConnection
        connection = PinnedHTTPSConnection(TARGET.domain, "8.8.8.8", 1)
        self.assertTrue(connection._context.check_hostname)
        self.assertEqual(connection._context.verify_mode, ssl.CERT_REQUIRED)
        connection.close()

    def test_response_body_is_bounded_and_connection_closes(self):
        class SyntheticResponse:
            status = 200
            def getheaders(self):
                return [("Content-Type", "text/html"), ("Content-Length", "1000")]
        connections = []
        class SyntheticConnection:
            sock = None
            def __init__(self, *args):
                self.closed = False
                connections.append(self)
            def request(self, *args, **kwargs):
                pass
            def getresponse(self):
                return SyntheticResponse()
            def close(self):
                self.closed = True
        from urllib.parse import urlsplit
        fetcher = PublicHTMLFetcher(connection_factory=SyntheticConnection, max_bytes=100)
        import time
        with self.assertRaises(FetchError):
            fetcher._read(urlsplit(TARGET.url), "8.8.8.8", time.monotonic() + 1)
        self.assertTrue(connections[0].closed)


class SyntheticTransport:
    def __init__(self, response):
        self.response, self.calls = response, []

    async def request(self, *args, **kwargs):
        self.calls.append((args, kwargs))
        return self.response


class ProviderTests(unittest.IsolatedAsyncioTestCase):
    def policy(self):
        return ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"synthetic-workspace"}), api_key="synthetic-fixture-key", revision="synthetic-test")

    async def test_every_provider_is_disabled_without_network(self):
        for cls in (ApolloProvider, TheirStackProvider):
            transport = SyntheticTransport({})
            result = await cls(transport=transport).collect(TARGET, "synthetic-workspace", NOW)
            self.assertEqual(result.state, "disabled")
            self.assertEqual(transport.calls, [])

    async def test_workspace_allowlist_prevents_paid_call(self):
        transport = SyntheticTransport({})
        result = await ApolloProvider(self.policy(), transport).collect(TARGET, "unauthorized-synthetic", NOW)
        self.assertEqual(result.state, "disabled")
        self.assertEqual(transport.calls, [])

    async def test_missing_key_is_error_without_request(self):
        transport = SyntheticTransport({})
        policy = ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"synthetic-workspace"}))
        result = await ApolloProvider(policy, transport).collect(TARGET, "synthetic-workspace", NOW)
        self.assertEqual(result.state, "error")
        self.assertEqual(transport.calls, [])

    async def test_apollo_contract_dates_and_metadata_minimization(self):
        transport = SyntheticTransport({"organization": {"id": "synthetic-id", "name": "Synthetic company", "primary_domain": TARGET.domain,
            "technology_names": ["Salesforce Marketing Cloud"], "phone": "synthetic-private-phone", "account": {"secret": "synthetic"},
            "suborganizations": [{"technology_names": ["Sales Cloud"]}]}})
        result = await ApolloProvider(self.policy(), transport).collect(TARGET, "synthetic-workspace", NOW)
        args, kwargs = transport.calls[0]
        self.assertEqual(args[0], "GET")
        self.assertIn("x-api-key", kwargs["headers"])
        self.assertEqual(kwargs["params"], {"domain": TARGET.domain})
        self.assertNotIn("phone", result.metadata)
        self.assertIsNone(result.evidence[0].last_seen)
        self.assertEqual(result.evidence[0].products, ["Marketing Cloud"])
        self.assertEqual(classify(TARGET, result.evidence, NOW).status, Status.INCONCLUSIVE)

    async def test_apollo_mismatch_never_remaps_domain(self):
        transport = SyntheticTransport({"organization": {"primary_domain": "other-synthetic.example", "technology_names": ["Salesforce"]}})
        result = await ApolloProvider(self.policy(), transport).collect(TARGET, "synthetic-workspace", NOW)
        self.assertEqual(result.state, "ambiguous")
        self.assertEqual(result.evidence, [])

    async def test_theirstack_contract_and_original_publication_date(self):
        transport = SyntheticTransport({"data": [{"id": 1, "company_domain": TARGET.domain, "company_object": {"industry": "retail"},
            "job_title": "Synthetic Salesforce role", "description": "Synthetic fixture: Sales Cloud for our client", "url": "https://synthetic-jobboard.example/1",
            "final_url": "https://synthetic-company.example/jobs/1", "source_url": "https://synthetic-jobboard.example/1",
            "date_posted": "2026-01-01", "date_reposted": "2026-10-07", "reposted": True, "discovered_at": "2026-01-02T12:00:00Z"}]})
        result = await TheirStackProvider(self.policy(), transport).collect(TARGET, "synthetic-workspace", NOW)
        args, kwargs = transport.calls[0]
        self.assertEqual(args[0], "POST")
        self.assertEqual(kwargs["json_body"]["company_domain_or"], [TARGET.domain])
        self.assertEqual(kwargs["json_body"]["page"], 0)
        self.assertLessEqual(kwargs["json_body"]["limit"], 5)
        self.assertEqual(result.evidence[0].published_at.date().isoformat(), "2026-01-01")
        self.assertEqual(result.evidence[0].relation, "client")

    async def test_consultancy_without_attribution_stays_unknown(self):
        transport = SyntheticTransport({"data": [{"company_domain": TARGET.domain, "company_object": {"industry": "management consulting"},
            "job_title": "Synthetic Salesforce role", "description": "Synthetic Salesforce team", "url": TARGET.url, "date_posted": "2026-10-01"}]})
        result = await TheirStackProvider(self.policy(), transport).collect(TARGET, "synthetic-workspace", NOW)
        self.assertEqual(result.evidence[0].relation, "unknown")

    async def test_public_html_references_only_no_brand_text_inference(self):
        class SyntheticPageFetcher:
            async def fetch(self, domain):
                return Page(url=TARGET.url, html='<p>Synthetic Salesforce partner</p><script src="https://pi.pardot.com/pd.js"></script>')
        policy = ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"synthetic-workspace"}))
        result = await PublicHTMLProvider(policy, SyntheticPageFetcher()).collect(TARGET, "synthetic-workspace", NOW)
        self.assertEqual(result.evidence[0].products, ["Marketing Cloud"])
        self.assertIsNone(result.evidence[0].published_at)
        self.assertEqual(result.evidence[0].last_seen, NOW)


class SyntheticCollector:
    name, revision = "synthetic_fixture", "synthetic-test"
    def __init__(self, evidence=None):
        self.calls, self.evidence = 0, evidence or []

    async def collect(self, target, workspace, checked_at):
        self.calls += 1
        return ProviderOutcome(provider=self.name, state="ok", evidence=self.evidence)


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_cache_dedup_ttl_and_workspace_isolation(self):
        collector, now = SyntheticCollector(), [NOW]
        service = ResearchService([collector], clock=lambda: now[0])
        target = TargetInput(domain=TARGET.domain)
        batch = await service.batch("synthetic-workspace", [target, target])
        self.assertEqual(batch.unique_targets, 1)
        self.assertEqual(collector.calls, 1)
        cached = await service.research("synthetic-workspace", target)
        self.assertTrue(cached.cache_hit)
        self.assertIsNone(await service.store.report("other-synthetic-workspace", cached.id, NOW))
        now[0] += timedelta(hours=2)
        await service.research("synthetic-workspace", target)
        self.assertEqual(collector.calls, 2)

    async def test_synthetic_evidence_is_rejected_by_runtime(self):
        service = ResearchService([SyntheticCollector([fixture()])], clock=lambda: NOW)
        report = await service.research("synthetic-workspace", TargetInput(domain=TARGET.domain))
        self.assertFalse(report.synthetic)
        self.assertEqual(report.evidence, [])
        self.assertEqual(report.conclusion.status, Status.ERROR)

    async def test_synthetic_test_mode_is_labeled(self):
        service = ResearchService([SyntheticCollector([fixture()])], clock=lambda: NOW, allow_synthetic=True)
        report = await service.research("synthetic-workspace", TargetInput(domain=TARGET.domain))
        self.assertTrue(report.synthetic)
        with self.assertRaises(ValueError):
            create_app(service=service)

    async def test_batch_and_rate_limits(self):
        service = ResearchService([], clock=lambda: NOW)
        target = TargetInput(domain=TARGET.domain)
        with self.assertRaises(ResearchLimit):
            await service.batch("synthetic-workspace", [target] * 6)
        for _ in range(10):
            await service.research("synthetic-workspace", target)
        with self.assertRaises(ResearchLimit):
            await service.research("synthetic-workspace", target)

    async def test_store_bound_and_expiry(self):
        store, service = MemoryStore(max_entries=1), ResearchService([], clock=lambda: NOW)
        report = await service.research("synthetic-workspace", TargetInput(domain=TARGET.domain))
        await store.save("synthetic-workspace", ("1",), report)
        self.assertIsNone(await store.report("synthetic-workspace", report.id, NOW + timedelta(hours=1)))

    async def test_store_evicts_oldest_report_at_capacity(self):
        store = MemoryStore(max_entries=1)
        service = ResearchService([], store=store, clock=lambda: NOW)
        first = await service.research("synthetic-workspace", TargetInput(domain=TARGET.domain))
        second = await service.research("synthetic-workspace", TargetInput(domain="second-synthetic.example"))
        self.assertIsNone(await store.report("synthetic-workspace", first.id, NOW))
        self.assertIsNotNone(await store.report("synthetic-workspace", second.id, NOW))

    async def test_singleflight_cleanup_after_client_cancellation(self):
        started, release = asyncio.Event(), asyncio.Event()
        class WaitingSyntheticCollector(SyntheticCollector):
            async def collect(self, *args):
                started.set()
                await release.wait()
                return await super().collect(*args)
        collector = WaitingSyntheticCollector()
        service = ResearchService([collector], clock=lambda: NOW)
        request = asyncio.create_task(service.research("synthetic-workspace", TargetInput(domain=TARGET.domain)))
        await started.wait()
        background = next(iter(service._active.values()))
        request.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await request
        release.set()
        await background
        await asyncio.sleep(0)
        self.assertEqual(service._active, {})
        self.assertTrue((await service.research("synthetic-workspace", TargetInput(domain=TARGET.domain))).cache_hit)
        self.assertEqual(collector.calls, 1)

    async def test_conflicting_name_is_resolved_before_collecting(self):
        service = ResearchService([], clock=lambda: NOW)
        with self.assertRaises(InvalidTarget):
            await service.batch("synthetic-workspace", [TargetInput(domain=TARGET.domain, company_name="Synthetic A"), TargetInput(domain=TARGET.domain, company_name="Synthetic B")])


class SyntheticSessionVerifier:
    async def verify(self, token):
        # Deliberately fake, tests only; this is not an OAuth implementation.
        if token not in {"synthetic-valid", "synthetic-other", "synthetic-read", "synthetic-expired"}:
            return None
        return Principal(subject="synthetic-subject", workspace="other-synthetic-workspace" if token == "synthetic-other" else "synthetic-workspace",
            permissions=frozenset({"research:read"}) if token == "synthetic-read" else frozenset({"research:read", "research:write"}),
            expires_at=NOW - timedelta(seconds=1) if token == "synthetic-expired" else NOW + timedelta(hours=1))


class APITests(unittest.TestCase):
    def test_access_default_rejects_missing_or_forged_session(self):
        with TestClient(create_app(clock=lambda: NOW)) as client:
            for headers in [{}, {"Authorization": "Bearer synthetic-valid"}, {"X-Workspace": "admin", "X-Role": "admin"}]:
                response = client.get("/v2/access", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["cache-control"], "no-store")

    def test_access_reports_disabled_sources_without_identity_or_token(self):
        with self.client() as client:
            response = client.get("/v2/access", headers={"Authorization": "Bearer synthetic-valid"})
            self.assertEqual(response.status_code, 200)
            self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(response.json(), {
                "verified": True, "expires_at": "2026-10-07T13:00:00+00:00",
                "permissions": ["research:read", "research:write"], "available_sources": [],
                "research_enabled": False, "max_batch": 5,
            })
            self.assertNotIn("synthetic-subject", response.text)
            self.assertNotIn("synthetic-workspace", response.text)
            self.assertNotIn("synthetic-valid", response.text)

    def test_access_readiness_is_workspace_specific_and_never_collects(self):
        class NeverCollectPublic(PublicHTMLProvider):
            async def collect(self, *args):
                raise AssertionError("Access checks must not collect evidence")
        public = NeverCollectPublic(ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"synthetic-workspace"})))
        unconfigured_paid = ApolloProvider(ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"synthetic-workspace"})))
        service = ResearchService([public, unconfigured_paid], clock=lambda: NOW)
        with TestClient(create_app(verifier=SyntheticSessionVerifier(), service=service, clock=lambda: NOW)) as client:
            ready = client.get("/v2/access", headers={"Authorization": "Bearer synthetic-valid"}).json()
            self.assertEqual(ready["available_sources"], ["public_html"])
            self.assertTrue(ready["research_enabled"])
            for token in ["synthetic-read", "synthetic-other"]:
                access = client.get("/v2/access", headers={"Authorization": "Bearer " + token}).json()
                self.assertFalse(access["research_enabled"])
            other = client.get("/v2/access", headers={"Authorization": "Bearer synthetic-other"}).json()
            self.assertEqual(other["available_sources"], [])

    def test_default_is_fail_closed_before_body_validation(self):
        with TestClient(create_app(clock=lambda: NOW)) as client:
            for headers in [{}, {"Authorization": "Bearer synthetic-valid"}, {"X-Workspace": "admin", "X-Role": "admin"}]:
                response = client.post("/v2/research", content="invalid-json", headers=headers)
                self.assertEqual(response.status_code, 401)
                self.assertEqual(response.headers["cache-control"], "no-store")
            self.assertEqual(client.get("/health").status_code, 200)
            self.assertEqual(client.get("/openapi.json").status_code, 404)

    def client(self):
        return TestClient(create_app(verifier=SyntheticSessionVerifier(), service=ResearchService([], clock=lambda: NOW), clock=lambda: NOW))

    def test_verified_api_creates_private_inconclusive_report(self):
        with self.client() as client:
            response = client.post("/v2/research", json={"domain": TARGET.domain}, headers={"Authorization": "Bearer synthetic-valid"})
            self.assertEqual(response.status_code, 200)
            report = response.json()
            self.assertEqual(report["conclusion"]["status"], Status.INCONCLUSIVE.value)
            self.assertEqual(report["coverage"], "partial")
            report_url = "/v2/reports/" + report["id"]
            self.assertEqual(client.get(report_url, headers={"Authorization": "Bearer synthetic-valid"}).status_code, 200)
            self.assertEqual(client.get(report_url, headers={"Authorization": "Bearer synthetic-other"}).status_code, 404)
            self.assertEqual(client.get(report_url).status_code, 401)

    def test_expired_unknown_and_forged_roles_cannot_write(self):
        with self.client() as client:
            for token in ["synthetic-expired", "forged-admin"]:
                self.assertEqual(client.post("/v2/research", json={"domain": TARGET.domain}, headers={"Authorization": "Bearer " + token, "X-Role": "admin"}).status_code, 401)
            self.assertEqual(client.post("/v2/research", json={"domain": TARGET.domain}, headers={"Authorization": "Bearer synthetic-read"}).status_code, 403)

    def test_body_and_batch_limits_after_session_check(self):
        with self.client() as client:
            headers = {"Authorization": "Bearer synthetic-valid"}
            self.assertEqual(client.post("/v2/research", content="x" * 32769, headers=headers).status_code, 413)
            self.assertEqual(client.post("/v2/research/batch", json={"targets": [{"domain": TARGET.domain}] * 6}, headers=headers).status_code, 422)
            self.assertEqual(client.post("/v2/research", json={"domain": TARGET.domain, "enable_apollo": True}, headers=headers).status_code, 422)

    def test_malformed_verifier_result_fails_closed(self):
        class BrokenVerifier:
            async def verify(self, token):
                return Principal("synthetic", "synthetic", frozenset({"research:write"}), "invalid-date")
        with TestClient(create_app(verifier=BrokenVerifier(), clock=lambda: NOW)) as client:
            self.assertEqual(client.post("/v2/research", json={"domain": TARGET.domain}, headers={"Authorization": "Bearer synthetic"}).status_code, 401)


if __name__ == "__main__":
    unittest.main()
