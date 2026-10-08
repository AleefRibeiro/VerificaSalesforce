"""Offline contract harness. Test-only session and empty collector; never research findings."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from research_api import Principal, create_app
from research_v2.models import ProviderOutcome, Report
from research_v2.history import HistoryPage, HistoryEntry
from research_v2.providers import ProviderPolicy
from research_v2.service import ResearchService, ResearchScope


def export_contract():
    now = datetime.now(timezone.utc)

    class TestOnlyVerifier:
        async def verify(self, token):
            if token != "offline-contract-test-session":
                return None
            return Principal("offline-subject", "offline-workspace", frozenset({"research:read", "research:write"}), now + timedelta(hours=1))

    class EmptyOfflineCollector:
        name = "offline_empty_fixture"
        requires_key = False
        revision = "offline-contract-test"
        policy = ProviderPolicy(enabled=True, authorized_workspaces=frozenset({"offline-workspace"}))
        calls = 0

        async def collect(self, *args):
            self.calls += 1
            return ProviderOutcome(provider=self.name, state="ok", evidence=[])

    collector = EmptyOfflineCollector()
    service = ResearchService([collector], clock=lambda: now)
    headers = {"Authorization": "Bearer offline-contract-test-session"}

    def receipt(response):
        assert response.status_code == 200
        assert response.headers["cache-control"] == "no-store"
        return response.json()

    with TestClient(create_app(verifier=TestOnlyVerifier(), service=service, clock=lambda: now)) as client:
        access = receipt(client.get("/v2/access", headers=headers))
        assert collector.calls == 0
        single = receipt(client.post("/v2/research", headers=headers, json={"domain": "contract-company.example", "company_name": "Offline test label"}))
        batch = receipt(client.post("/v2/research/batch", headers=headers, json={"targets": [{"domain": "contract-company.example"}, {"domain": "second-contract.example"}]}))
        assert single["evidence"] == [] and single["conclusion"]["status"] == "Inconclusivo"
        assert all(report["evidence"] == [] for report in batch["reports"])
    historic = Report.model_validate(single).model_copy(update={"checked_at": now - timedelta(days=2), "expires_at": now - timedelta(days=2) + timedelta(hours=1)})
    expected_owner = ResearchScope("offline-workspace", "offline-subject")

    class OfflineHistoryStore:
        async def history(self, owner, offset, clock):
            assert owner == expected_owner
            return HistoryPage(items=[HistoryEntry(id=historic.id, domain=historic.target.domain, checked_at=historic.checked_at,
                saved_at=historic.checked_at, retained_until=now + timedelta(days=28), status=historic.conclusion.status)], next_offset=None)

        async def history_report(self, owner, report_id, clock):
            assert owner == expected_owner
            return historic if report_id == historic.id else None

    count_before = collector.calls
    with TestClient(create_app(verifier=TestOnlyVerifier(), service=ResearchService([collector], store=OfflineHistoryStore()), clock=lambda: now, isolate_users=True)) as client:
        history = receipt(client.get("/v2/history", headers=headers))
        history_report = receipt(client.get("/v2/history/" + historic.id, headers=headers))
        assert collector.calls == count_before
    return {"test_only": True, "network_calls": 0, "access": access, "single": single, "batch": batch, "history": history, "history_report": history_report}


if __name__ == "__main__":
    print(json.dumps(export_contract(), ensure_ascii=False))
