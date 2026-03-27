from __future__ import annotations

import unittest

from salesforce_scanner.analyzer import analyze_sources
from salesforce_scanner.scorer import compute_score, decide_classification, infer_products


class DecisionEngineTests(unittest.TestCase):
    def _make_evidence(
        self,
        pattern_key: str,
        source_type: str,
        weight: int,
        strength: str,
        cap: int,
        value: str = "sample",
        products: list[str] | None = None,
    ) -> dict:
        return {
            "pattern_key": pattern_key,
            "type": source_type,
            "weight": weight,
            "pattern_strength": strength,
            "pattern_cap": cap,
            "value": value,
            "products": products or [],
            "reason": "test",
            "count": 1,
        }

    def test_score_by_multiple_sources_with_cap(self) -> None:
        evidence = [
            self._make_evidence("embeddedservice", "script_content", 45, "strong", 100),
            self._make_evidence("embeddedservice", "network_request", 45, "strong", 100),
            self._make_evidence("embeddedservice", "html_rendered", 45, "strong", 100),
        ]

        details = compute_score(evidence)
        self.assertEqual(details["total_score"], 100)
        self.assertEqual(details["score_by_pattern"]["embeddedservice"], 100)

    def test_dedup_by_domain_for_url_evidence(self) -> None:
        sources = {
            "network_request": [
                "https://abc.service.force.com/a",
                "https://abc.service.force.com/b",
            ]
        }
        evidence, _ = analyze_sources(sources)
        filtered = [
            item
            for item in evidence
            if item["pattern_key"] == "service_force_domain" and item["type"] == "network_request"
        ]

        self.assertEqual(len(filtered), 1)
        self.assertEqual(filtered[0]["count"], 2)
        self.assertEqual(filtered[0]["domain"], "abc.service.force.com")

    def test_deterministic_confirmation_for_embedded_plus_force_domain(self) -> None:
        evidence = [
            self._make_evidence(
                "embeddedservice",
                "script_content",
                45,
                "strong",
                120,
                products=["Service Cloud"],
            ),
            self._make_evidence(
                "force_domain",
                "network_request",
                42,
                "strong",
                120,
                value="https://foo.force.com/api",
                products=["Service Cloud"],
            ),
        ]

        details = compute_score(evidence)
        products = infer_products(evidence)
        decision = decide_classification(evidence, details, products)

        self.assertEqual(decision["classification"], "Confirmado")
        self.assertTrue(decision["salesforce_detected"])

    def test_marketing_only_not_force_core_confirmation(self) -> None:
        evidence = [
            self._make_evidence(
                "exacttarget",
                "script_content",
                35,
                "medium",
                75,
                products=["Marketing Cloud"],
            ),
            self._make_evidence(
                "marketingcloud",
                "script_url",
                32,
                "medium",
                70,
                products=["Marketing Cloud"],
            ),
        ]

        details = compute_score(evidence)
        products = infer_products(evidence)
        decision = decide_classification(evidence, details, products)

        self.assertIn("Marketing Cloud", decision["classification"])
        self.assertFalse(decision["classification"].startswith("Confirmado"))

    def test_my_salesforce_domain_confirms(self) -> None:
        evidence = [
            self._make_evidence(
                "my_salesforce_domain",
                "brand_domain_probe",
                55,
                "strong",
                140,
                value="https://acme.my.salesforce.com/",
                products=["Service Cloud", "Experience Cloud"],
            )
        ]

        details = compute_score(evidence)
        products = infer_products(evidence)
        decision = decide_classification(evidence, details, products)

        self.assertEqual(decision["classification"], "Confirmado")
        self.assertTrue(decision["salesforce_detected"])

    def test_weak_signal_alone_is_not_detected(self) -> None:
        evidence = [
            self._make_evidence(
                "salesforce_generic",
                "html_initial",
                12,
                "weak",
                30,
            )
        ]

        details = compute_score(evidence)
        products = infer_products(evidence)
        decision = decide_classification(evidence, details, products)

        self.assertEqual(decision["classification"], "Indício fraco / revisar manualmente")
        self.assertFalse(decision["salesforce_detected"])

    def test_infer_commerce_cloud(self) -> None:
        evidence = [
            self._make_evidence(
                "commerce_cloud",
                "script_content",
                30,
                "medium",
                75,
                products=["Commerce Cloud"],
            )
        ]

        products = infer_products(evidence)
        self.assertIn("Commerce Cloud", products)


if __name__ == "__main__":
    unittest.main()
