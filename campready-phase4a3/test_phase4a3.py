from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("validate_mapping", Path(__file__).with_name("validate_mapping.py"))
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)

METRIC_SPEC = importlib.util.spec_from_file_location("build_evidence", Path(__file__).with_name("build_evidence.py"))
METRICS = importlib.util.module_from_spec(METRIC_SPEC)
assert METRIC_SPEC.loader
sys.modules[METRIC_SPEC.name] = METRICS
METRIC_SPEC.loader.exec_module(METRICS)


class Phase4A3Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.mapping = json.loads(Path(__file__).with_name("canonical_35site_mapping.json").read_text())
        cls.metrics = METRICS.calculate_metrics(cls.mapping["rows"])

    def test_mapping_contract(self):
        result = MODULE.validate(False)
        self.assertEqual(result["status"], "PASS", result["errors"])
        self.assertEqual((result["row_count"], result["supported_core_size"], result["transport_challenge_count"]), (35, 32, 3))

    def test_no_site_specific_parser_or_branch(self):
        mapping = json.loads(Path(__file__).with_name("canonical_35site_mapping.json").read_text())
        adapters = (ROOT / "campready-authority-adapters/authority_adapters.py").read_text()
        allowed = {"FROZEN_USFS_GENERIC_PARSER", "NCStateParksAdapter", "NPSAdapter", None}
        self.assertTrue(all(row["parser_adapter_family"] in allowed for row in mapping["rows"]))
        self.assertNotIn("Seneca Shadows", adapters)
        self.assertNotIn("Hurricane Creek", adapters)

    def test_readiness_is_inert(self):
        mapping = json.loads(Path(__file__).with_name("canonical_35site_mapping.json").read_text())
        config = json.loads((ROOT / "campready-prospective-validation/config.json").read_text())
        self.assertFalse(mapping["prospective_gates"]["activated"])
        self.assertFalse(mapping["safety_policy"]["send_real_notifications"])
        self.assertFalse(config["policy"]["send_real_notifications"])
        self.assertFalse(config["migration"]["github_schedule_enabled"])

    def test_dlr_uses_maintained_source_families(self):
        self.assertEqual(len(METRICS.SUPPORTED_SOURCE_FAMILIES), 5)
        self.assertEqual(len(METRICS.GROSS_SOURCE_FAMILIES), 6)
        self.assertAlmostEqual(self.metrics["supported_dlr"], 6.40)
        self.assertAlmostEqual(self.metrics["gross_dlr"], 35 / 6)

    def test_usfs_registry_is_separate_from_usfs_website_family(self):
        self.assertIn("USFS registry/configuration", METRICS.SUPPORTED_SOURCE_FAMILIES)
        self.assertIn("USFS website alerts/recreation", METRICS.SUPPORTED_SOURCE_FAMILIES)
        self.assertNotEqual(METRICS.SUPPORTED_SOURCE_FAMILIES[0], METRICS.SUPPORTED_SOURCE_FAMILIES[1])

    def test_scr_uses_authoritative_organizations(self):
        self.assertEqual(len(METRICS.SUPPORTED_AUTHORITATIVE_ORGANIZATIONS), 4)
        self.assertEqual(len(METRICS.GROSS_AUTHORITATIVE_ORGANIZATIONS), 5)
        self.assertEqual(self.metrics["supported_scr"], 8.0)
        self.assertEqual(self.metrics["gross_scr"], 7.0)
        self.assertNotEqual(self.metrics["supported_scr"], 1 - 5 / 32)

    def test_authority_adapter_leverage_is_distinct(self):
        self.assertEqual(len(METRICS.SUPPORTED_AUTHORITY_STATUS_ADAPTERS), 3)
        self.assertNotIn("NWS", " ".join(METRICS.SUPPORTED_AUTHORITY_STATUS_ADAPTERS))
        self.assertAlmostEqual(self.metrics["authority_adapter_leverage"], 32 / 3)
        self.assertNotEqual(self.metrics["authority_adapter_leverage"], self.metrics["supported_dlr"])

    def test_strict_useful_coverage_is_separate_from_supported_core(self):
        self.assertEqual(len(self.metrics["supported"]), 32)
        self.assertEqual(len(self.metrics["useful"]), 31)
        self.assertAlmostEqual(self.metrics["strict_useful_percentage"], 100 * 31 / 35)
        self.assertGreaterEqual(self.metrics["strict_useful_percentage"], 80)

    def test_limited_generic_support_is_not_automatically_useful(self):
        seneca = next(r for r in self.mapping["rows"] if r["canonical_key"] == "wv-seneca-shadows")
        self.assertEqual(seneca["semantic_support_classification"], "LIMITED_GENERIC_SUPPORT")
        self.assertFalse(METRICS.is_strictly_useful(seneca))

    def test_usace_transport_sites_are_not_semantically_supported(self):
        self.assertEqual(len(self.metrics["transport"]), 3)
        self.assertTrue(all(not METRICS.is_strictly_useful(r) for r in self.metrics["transport"]))
        self.assertTrue(all(r not in self.metrics["supported"] for r in self.metrics["transport"]))

    def test_denominator_components_are_explicit_and_fire_paths_complete(self):
        for components in (
            METRICS.SUPPORTED_SOURCE_FAMILIES,
            METRICS.GROSS_SOURCE_FAMILIES,
            METRICS.SUPPORTED_AUTHORITATIVE_ORGANIZATIONS,
            METRICS.GROSS_AUTHORITATIVE_ORGANIZATIONS,
            METRICS.SUPPORTED_AUTHORITY_STATUS_ADAPTERS,
        ):
            self.assertTrue(components)
            self.assertTrue(all(isinstance(item, str) and item.strip() for item in components))
            self.assertEqual(len(components), len(set(components)))
        self.assertTrue(all(r["fire_restriction_source_path"] for r in self.metrics["useful"]))


if __name__ == "__main__":
    unittest.main(verbosity=2)
