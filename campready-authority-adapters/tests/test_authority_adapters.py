from __future__ import annotations

import importlib.util
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("authority_adapters", ROOT / "authority_adapters.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader
sys.modules[SPEC.name] = MODULE
SPEC.loader.exec_module(MODULE)


class AuthorityAdapterTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.sites = MODULE.load_sites(ROOT / "authority_sites.json")
        cls.cases = json.loads((ROOT / "tests/fixtures/authority_cases.json").read_text())

    def retrieval(self, site, body, url=None):
        page = f"<html><head><title>{site['campground_name']}</title></head><body>{body}</body></html>"
        return MODULE.Retrieval(url or site["campground_url"], True, 200, url or site["campground_url"], page.encode(), "2026-09-13T00:00:00+00:00")

    def parse_case(self, site, text, role="CONDITIONS_PAGE"):
        marked = f'<section class="official alert condition notice"><h2>Official notice</h2><p>{text}</p></section>'
        return MODULE.adapter_for(site["authority_family"]).parse(site, self.retrieval(site, marked), role)

    def test_source_retrieval_normalization(self):
        site = self.sites[0]
        result = self.parse_case(site, "  Carolina Beach State Park Campground   is closed. ")
        self.assertEqual(result.status, "PARSED")
        self.assertEqual(result.observations[0].raw_provenance["http_status"], 200)
        self.assertNotIn("  ", result.observations[0].normalized_text)

    def test_one_parser_resolves_every_frozen_identity(self):
        families = {}
        for site in self.sites:
            families.setdefault(site["authority_family"], set()).add(type(MODULE.adapter_for(site["authority_family"])))
            result = MODULE.adapter_for(site["authority_family"]).parse(site, self.retrieval(site, "<main>Official camping information.</main>"), "CAMPGROUND_PAGE")
            self.assertTrue(result.identity_resolved, site["campground_key"])
        self.assertEqual({key: len(value) for key, value in families.items()}, {"NC_STATE_PARKS": 1, "NPS": 1})

    def test_closure_notice(self):
        result = self.parse_case(self.sites[5], self.cases["closure"])
        self.assertTrue(any(item.operational_signal == "CLOSED" and item.event_class == "CAMPGROUND_STATUS" for item in result.observations))

    def test_reopening_or_lift(self):
        result = self.parse_case(self.sites[3], self.cases["reopening"])
        self.assertTrue(any(item.operational_signal == "OPEN" for item in result.observations))

    def test_scheduled_future_closure(self):
        result = self.parse_case(self.sites[6], self.cases["scheduled"])
        item = next(item for item in result.observations if item.relevant)
        self.assertEqual((item.operational_signal, item.lifecycle, item.notifying), ("SCHEDULED", "SCHEDULED", False))
        self.assertEqual(item.effective_start, "2099-01-10")

    def test_general_park_alert_does_not_auto_map(self):
        result = self.parse_case(self.sites[6], self.cases["general_unmapped"])
        self.assertTrue(all(not item.relevant and item.campground_key is None for item in result.observations))

    def test_ambiguous_notice_unknown_non_notifying(self):
        result = self.parse_case(self.sites[0], self.cases["ambiguous"])
        self.assertTrue(all(item.operational_signal == "UNKNOWN" and not item.notifying and item.unknown_reason for item in result.observations))

    def test_empty_no_current_alert(self):
        site = self.sites[1]
        result = MODULE.NCStateParksAdapter().parse(site, self.retrieval(site, "<main>Camping information and maps.</main>"), "CAMPGROUND_PAGE")
        self.assertEqual((result.status, result.observations), ("NO_CURRENT_EVENT", ()))

    def test_parser_failure_is_data_quality_failure(self):
        site = self.sites[0]
        bad = MODULE.Retrieval(site["campground_url"], True, 200, site["campground_url"], b"\xff", "2026-09-13T00:00:00+00:00")
        result = MODULE.NCStateParksAdapter().parse(site, bad, "CAMPGROUND_PAGE")
        self.assertEqual(result.status, "DATA_QUALITY_FAILURE")
        self.assertEqual(result.observations, ())

    def test_http_failure_is_separate_data_quality_failure(self):
        site = self.sites[5]
        failed = MODULE.Retrieval(site["conditions_url"], False, 503, site["conditions_url"], b"", "2026-09-13T00:00:00+00:00", "HTTP_ACCESS_FAILURE", "503")
        result = MODULE.NPSAdapter().parse(site, failed, "CONDITIONS_PAGE")
        self.assertEqual(result.status, "DATA_QUALITY_FAILURE")
        self.assertFalse(result.identity_resolved)

    def test_repeated_unchanged_identity_and_fingerprint(self):
        first = self.parse_case(self.sites[4], self.cases["nc_construction"]).observations[0]
        second = self.parse_case(self.sites[4], self.cases["nc_construction"]).observations[0]
        self.assertEqual((first.source_event_id, first.fingerprint), (second.source_event_id, second.fingerprint))

    def test_specific_closure_outranks_generic_open(self):
        result = self.parse_case(self.sites[5], self.cases["specific_over_generic"])
        self.assertTrue(any(item.operational_signal == "CLOSED" for item in result.observations))
        self.assertFalse(any(item.operational_signal == "OPEN" and item.event_class != "CAMPGROUND_STATUS" for item in result.observations))

    def test_no_reservation_availability_interpretation(self):
        result = self.parse_case(self.sites[6], self.cases["reservation"])
        self.assertTrue(all(item.operational_signal == "UNKNOWN" for item in result.observations))

    def test_known_nc_operational_examples(self):
        burn = self.parse_case(self.sites[2], self.cases["nc_burn"]).observations
        construction = self.parse_case(self.sites[4], self.cases["nc_construction"]).observations
        self.assertTrue(any(item.operational_signal == "CLOSED" and item.event_class == "CAMPGROUND_STATUS" for item in burn))
        self.assertTrue(any(item.operational_signal == "CLOSED" for item in construction))

    def test_nps_deterministic_whole_park_access(self):
        result = self.parse_case(self.sites[5], self.cases["whole_park_access"])
        self.assertTrue(any(item.relevant and item.event_class == "ACCESS_ALERT" for item in result.observations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
