"""Offline contract harness. Test-only session and empty collector; never research findings."""
import json
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fastapi.testclient import TestClient
from research_api import Principal, create_app
from research_v2.models import ProviderOutcome
from research_v2.providers import ProviderPolicy
from research_v2.service import ResearchService


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
    return {"test_only": True, "network_calls": 0, "access": access, "single": single, "batch": batch}


if __name__ == "__main__":
    print(json.dumps(export_contract(), ensure_ascii=False))
