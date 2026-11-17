import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

PHASE5 = Path(__file__).resolve().parents[1]
PILOT = PHASE5 / "pilot"
sys.path[:0] = [str(PILOT), str(PHASE5)]
import monitoring_producer as producer
from pilot_control import initial_state
from pilot_operator import PilotOperator
from pilot_store import canonical_bytes, initialize_evidence_root, start_pilot


class FixedClock:
    def __init__(self, value):
        self.value = value
    def __call__(self):
        return self.value


class LocalSources:
    """Deterministic source boundary; source parsers/classifiers remain real."""
    def __init__(self):
        mapping, _, _ = producer._bindings()
        self.mapping = mapping
        self.nws_features = {}
        self.campground_text = {}
        self.fail = set()
        self.parser_fail = set()
        self.alert_detail = None
        self.alert_detail_fail = False

    @staticmethod
    def feature(identifier, headline="Campground weather alert"):
        return {"id": f"https://api.weather.gov/alerts/{identifier}",
                "properties": {"event":"Winter Storm Warning", "severity":"Severe",
                               "certainty":"Likely", "urgency":"Expected", "headline":headline}}

    @staticmethod
    def result(url, ok=True, body=b""):
        return {"ok":ok, "http_status":200 if ok else 503,
                "final_url":url, "content_type":"text/html", "body":body if ok else b"",
                "failure":None if ok else "HTTPError: deterministic 503", "elapsed_ms":1.0}

    def __call__(self, url, accept="text/html,*/*;q=0.8"):
        if url in self.fail or (self.alert_detail_fail and self.alert_detail == url):
            return self.result(url, False)
        if "api.weather.gov/alerts/active?point=" in url:
            body = json.dumps({"features":self.nws_features.get(url, [])}).encode()
            return self.result(url, body=body)
        if url.endswith("/alerts"):
            link = (f'<a href="{self.alert_detail}">detail</a>'
                    if self.alert_detail and "chattahoochee-oconee" in url else "")
            return self.result(url, body=link.encode())
        if self.alert_detail and url == self.alert_detail:
            body = (b'<div id="block-wfs-content"><h1>Andrews Cove Campground Closure</h1>'
                    b'<p>Rec Sites Affected: Andrews Cove Campground</p>'
                    b'<p>Andrews Cove Campground is closed.</p></div>')
            return self.result(url, body=body)
        site = next((row for row in self.mapping.values() if row["campground_status_page_url"] == url), None)
        if site:
            if url in self.parser_fail:
                return self.result(url, body=b"<html>not a recognized source document</html>")
            text = self.campground_text.get(site["canonical_key"], f"{site['canonical_name']} official information")
            if site["authority_family"] == "USFS":
                body = f'<article class="wfs-rec__full"><h1>{site["canonical_name"]}</h1><p>{text}</p></article>'.encode()
            else:
                body = f'<html><title>{site["canonical_name"]}</title><div>{site["canonical_name"]}</div></html>'.encode()
            return self.result(url, body=body)
        raise AssertionError(f"unexpected source URL {url}")

    def nws_url(self, site_id):
        site = self.mapping[site_id]
        return site["nws"]["active_alerts_endpoint"].format(latitude=site["latitude"], longitude=site["longitude"])


class Phase55JMonitoringProducerTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.monitoring = self.base / "monitoring"
        self.sources = LocalSources()

    def tearDown(self):
        self.temp.cleanup()

    def produce(self, second=0, **kwargs):
        clock = FixedClock(datetime(2026,11,17,6,0,second,tzinfo=timezone.utc))
        return producer.produce(self.monitoring, fetcher=self.sources, clock=clock,
                                sleep=lambda _:None, **kwargs)

    def document(self, result):
        return json.loads(Path(result["artifact_path"]).read_text(encoding="utf-8"))

    def pilot_operator(self):
        root = self.base / "pilot"
        if not (root/"state"/"start_transaction.json").exists():
            initialize_evidence_root(root, allow_test_root=True)
            (root/"state"/"pilot_state.json").write_bytes(canonical_bytes(initial_state())+b"\n")
            start_pilot(root, owner_authorized=True, verified_role_binding_digest="a"*64,
                        allow_test_root=True, clock=lambda:datetime(2026,11,17,5,10,tzinfo=timezone.utc))
        return PilotOperator(root, allow_test_root=True)

    def test_frozen_bindings_zero_event_and_successful_sources(self):
        result = self.produce()
        document = self.document(result)
        self.assertEqual(document["frozen_cohort"], list(producer.COHORT_IDS))
        self.assertEqual(document["records"], [])
        self.assertGreater(result["successes"], 0)
        self.assertEqual(producer.validate_artifact(result["artifact_path"])["review_item_count"], 0)
        self.assertEqual(self.pilot_operator().prepare_review_items(result["artifact_path"])["result"], "NO_REVIEW_ITEMS_FOR_RUN")

    def test_one_and_multiple_items_feed_operator_unchanged(self):
        self.produce()
        self.sources.nws_features[self.sources.nws_url("wv-big-rock")] = [self.sources.feature("one")]
        one = self.produce(1)
        prepared = self.pilot_operator().prepare_review_items(one["artifact_path"])
        self.assertEqual((prepared["result"], len(prepared["review_items"]), prepared["selection_required"]),
                         ("REVIEW_ITEMS_READY", 1, False))

        other = self.base / "multiple"
        first = producer.produce(other, fetcher=LocalSources(), clock=FixedClock(datetime(2026,11,17,6,1,0,tzinfo=timezone.utc)), sleep=lambda _:None)
        sources = LocalSources()
        sources.nws_features[sources.nws_url("wv-big-rock")] = [sources.feature("a")]
        sources.nws_features[sources.nws_url("nps-elkmont")] = [sources.feature("b")]
        multiple = producer.produce(other, fetcher=sources, clock=FixedClock(datetime(2026,11,17,6,1,1,tzinfo=timezone.utc)), sleep=lambda _:None)
        prepared = self.pilot_operator().prepare_review_items(multiple["artifact_path"])
        self.assertEqual(len(prepared["review_items"]), 2)
        self.assertTrue(prepared["selection_required"])
        self.assertTrue(Path(first["artifact_path"]).exists())

    def test_retrieval_and_parser_failures_are_distinct_and_truthful(self):
        low = self.sources.mapping["low-gap"]["campground_status_page_url"]
        spinreel = self.sources.mapping["or-spinreel"]["campground_status_page_url"]
        self.sources.fail.add(low)
        self.sources.parser_fail.add(spinreel)
        result = self.produce()
        records = self.document(result)["records"]
        by_site = {row["site_id"]:row for row in records}
        self.assertEqual((by_site["low-gap"]["retrieval_status"], by_site["low-gap"]["parser_status"],
                          by_site["low-gap"]["data_quality_reason_code"]),
                         ("FINAL_FAILED", "NOT_RUN", "FINAL_RETRIEVAL_FAILURE"))
        self.assertEqual((by_site["or-spinreel"]["retrieval_status"], by_site["or-spinreel"]["parser_status"],
                          by_site["or-spinreel"]["data_quality_reason_code"]),
                         ("SUCCESS", "FAILED", "PARSER_FAILURE"))

    def test_alert_detail_final_failure_preserves_guard(self):
        self.sources.alert_detail = "https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/test-closure"
        self.sources.alert_detail_fail = True
        result = self.produce()
        prepared = self.pilot_operator().prepare_review_items(result["artifact_path"])
        guarded = [item for item in prepared["review_items"] if item["source_role"] == "ALERT_DETAIL"]
        self.assertEqual(len(guarded), 2)
        self.assertTrue(all(item["alert_detail_guard_required"] for item in guarded))
        self.assertTrue(all(item["lifecycle_classification"] == "NONE" for item in guarded))
        self.assertTrue(all("NO_SOURCE_SILENCE_AS_SAFE" in item["interpretation_prohibitions"] for item in guarded))

    def test_alert_detail_failure_retains_attributable_prior_state(self):
        self.sources.alert_detail = "https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts/test-closure"
        baseline = self.produce()
        before = self.document(baseline)["accepted_state"]
        self.assertTrue(any("ALERT_DETAIL" in key for key in before))
        self.sources.alert_detail_fail = True
        failed = self.produce(1)
        document = self.document(failed)
        self.assertTrue(any("ALERT_DETAIL" in key for key in document["accepted_state"]))
        detail = [row for row in document["records"] if row["source_role"] == "ALERT_DETAIL"]
        self.assertTrue(all(row["prior_accepted_state_ref"] for row in detail))

    def test_test_fixture_and_historical_timestamp_rejected_downstream(self):
        result = self.produce()
        document = self.document(result)
        fixture = copy.deepcopy(document); fixture["test_fixture"] = True
        fixture_path = self.base/"fixture.json"; fixture_path.write_text(json.dumps(fixture))
        with self.assertRaisesRegex(ValueError, "NON_PRODUCTION_SOURCE_REJECTED"):
            self.pilot_operator().prepare_review_items(fixture_path)
        dry = copy.deepcopy(document); dry["dry_run"] = True
        dry_path = self.base/"dry.json"; dry_path.write_text(json.dumps(dry))
        with self.assertRaisesRegex(ValueError, "NON_PRODUCTION_SOURCE_REJECTED"):
            self.pilot_operator().prepare_review_items(dry_path)
        historical = copy.deepcopy(document)
        historical.update(run_id="20261117T050000Z", generated_at="2026-11-17T05:00:00Z")
        historical_path = self.base/"historical.json"; historical_path.write_text(json.dumps(historical))
        with self.assertRaisesRegex(ValueError, "HISTORICAL_SOURCE_REJECTED"):
            self.pilot_operator().prepare_review_items(historical_path)

    def test_validator_rejects_noncohort_unauthorized_and_duplicates(self):
        self.sources.fail.add(self.sources.mapping["low-gap"]["campground_status_page_url"])
        result = self.produce(); document = self.document(result)
        noncohort = copy.deepcopy(document); noncohort["records"][0]["site_id"] = "outside-cohort"
        with self.assertRaisesRegex(ValueError, "RECORD_SITE_INVALID"):
            producer.validate_document(noncohort)
        role = copy.deepcopy(document); role["records"][0]["source_role"] = "ALERT_INDEX"
        with self.assertRaisesRegex(ValueError, "RECORD_SOURCE_INVALID"):
            producer.validate_document(role)
        duplicate = copy.deepcopy(document); duplicate["records"].append(copy.deepcopy(duplicate["records"][0]))
        duplicate["records"][-1]["provenance_record_ref"] = duplicate["source_evidence_ref"] + "#records/1"
        with self.assertRaisesRegex(ValueError, "DUPLICATE_RECORD_IDENTITY"):
            producer.validate_document(duplicate)

    def test_duplicate_run_and_partial_artifact_fail_closed(self):
        self.produce()
        with self.assertRaisesRegex(FileExistsError, "DUPLICATE_RUN_ID_PATH"):
            self.produce()
        partial = self.monitoring/".20261117T070000Z.json.tmp"
        partial.write_text('{"incomplete":')
        with self.assertRaisesRegex(ValueError, "COMPLETED_ARTIFACT_PATH_REQUIRED"):
            producer.validate_artifact(partial)

    def test_atomic_failure_exposes_no_completed_artifact(self):
        root = self.base/"atomic"
        with patch("monitoring_producer.os.link", side_effect=OSError("injected")):
            with self.assertRaises(OSError):
                producer.produce(root, fetcher=self.sources,
                                 clock=FixedClock(datetime(2026,11,17,6,2,0,tzinfo=timezone.utc)), sleep=lambda _:None)
        self.assertEqual(list(root.iterdir()), [])

    def test_real_flags_and_summary_contract(self):
        result = self.produce()
        document = self.document(result)
        self.assertIs(document["dry_run"], False); self.assertIs(document["test_fixture"], False)
        self.assertEqual(result["run_id"], "20261117T060000Z")
        self.assertEqual(len(result["artifact_sha256"]), 64)
        self.assertEqual(set(result["source_role_counts"]), producer.ALLOWED_ROLES)


if __name__ == "__main__":
    unittest.main()
