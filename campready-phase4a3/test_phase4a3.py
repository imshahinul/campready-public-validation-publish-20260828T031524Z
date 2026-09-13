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


class Phase4A3Tests(unittest.TestCase):
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


if __name__ == "__main__":
    unittest.main(verbosity=2)
