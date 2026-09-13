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

    def test_mixed_campground_and_boat_ramp_closure(self):
        result = self.parse_case(self.sites[4], "Hibernia will be closed for the season. Most boat ramps at Kerr Lake are closed due to low water levels.")
        closed = [item for item in result.observations if item.operational_signal == "CLOSED"]
        self.assertEqual(len(closed), 1)
        self.assertIn("Hibernia", closed[0].normalized_text)
        self.assertTrue(any("outside campground operational scope" in (item.unknown_reason or "") for item in result.observations))

    def test_boat_ramp_only_closure_is_not_campground_closed(self):
        result = self.parse_case(self.sites[4], "The boat ramp at Hibernia is closed due to low water levels.")
        self.assertFalse(any(item.operational_signal == "CLOSED" for item in result.observations))
        self.assertTrue(all(not item.notifying for item in result.observations))

    def test_restroom_only_closure_is_not_campground_closed(self):
        result = self.parse_case(self.sites[3], "The restrooms at Holly Point are closed for repairs.")
        self.assertFalse(any(item.operational_signal == "CLOSED" for item in result.observations))

    def test_explicit_campground_closure_survives_joint_facility_scope(self):
        result = self.parse_case(self.sites[4], "Hibernia Campground and its restrooms are closed for seasonal construction.")
        self.assertTrue(any(item.operational_signal == "CLOSED" and item.relevant for item in result.observations))

    def test_repeated_identical_fragments_collapse(self):
        site = self.sites[2]
        fragment = '<section class="alert">Hanging Rock Family Campground is closed for improvement.</section>'
        result = MODULE.NCStateParksAdapter().parse(site, self.retrieval(site, fragment * 3), "CAMPGROUND_PAGE")
        self.assertEqual(sum(item.operational_signal == "CLOSED" for item in result.observations), 1)
        self.assertEqual(result.post_scope_filter_count, 1)

    def test_overlapping_short_and_long_closures_collapse(self):
        result = self.parse_case(self.sites[2], "Hanging Rock Family Campground is closed for campground improvement. Hanging Rock Family Campground is closed for campground improvement while two shower houses and campsites are reconstructed.")
        self.assertEqual(sum(item.operational_signal == "CLOSED" for item in result.observations), 1)

    def test_duplicate_dom_order_does_not_change_event_identity(self):
        site = self.sites[2]
        short = '<section class="alert">Hanging Rock Family Campground is closed for campground improvement.</section>'
        long = '<section class="alert">Hanging Rock Family Campground is closed for campground improvement while new campsites are built.</section>'
        adapter = MODULE.NCStateParksAdapter()
        first = adapter.parse(site, self.retrieval(site, short + long), "CAMPGROUND_PAGE").observations
        second = adapter.parse(site, self.retrieval(site, long + short + short), "CAMPGROUND_PAGE").observations
        first_closed = [(item.source_event_id, item.fingerprint) for item in first if item.operational_signal == "CLOSED"]
        second_closed = [(item.source_event_id, item.fingerprint) for item in second if item.operational_signal == "CLOSED"]
        self.assertEqual(first_closed, second_closed)

    def test_materially_different_closures_are_retained(self):
        result = self.parse_case(self.sites[2], "Hanging Rock Family Campground is closed due to flooding. Hanging Rock Family Campground will be closed for construction.")
        self.assertEqual(sum(item.operational_signal == "CLOSED" for item in result.observations), 2)

    def test_named_nc_closures_remain_closed(self):
        examples = (
            (self.sites[2], "The Family Campground is closed for campground improvement."),
            (self.sites[3], "Holly Point is currently closed for construction."),
            (self.sites[4], "Beginning August 1, 2026, Hibernia will be closed for the season."),
        )
        for site, text in examples:
            with self.subTest(site=site["campground_key"]):
                result = self.parse_case(site, text)
                self.assertTrue(any(item.operational_signal == "CLOSED" and item.relevant for item in result.observations))

    def test_irrelevant_notice_remains_unknown_non_notifying(self):
        result = self.parse_case(self.sites[0], "Carolina Beach is accepting applications for a marina clerk; this notice is open until filled.")
        self.assertTrue(all(item.operational_signal == "UNKNOWN" and not item.notifying for item in result.observations))

    def test_nps_facility_scope_guard_and_existing_suite(self):
        for site in self.sites[5:]:
            result = self.parse_case(site, f"The visitor center near {site['aliases'][0]} is closed today.")
            self.assertFalse(any(item.operational_signal == "CLOSED" for item in result.observations))


if __name__ == "__main__":
    unittest.main(verbosity=2)
