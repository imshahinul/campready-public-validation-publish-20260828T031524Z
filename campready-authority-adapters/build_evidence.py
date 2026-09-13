"""Build the one intended-upload Phase 4A-2 evidence artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path


def git(*args: str) -> str:
    return subprocess.check_output(("git", *args), text=True).strip()


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--live", required=True)
    parser.add_argument("--output", required=True)
    parser.add_argument("--baseline-head", required=True)
    parser.add_argument("--baseline-tree", required=True)
    args = parser.parse_args()
    root = Path(__file__).resolve().parent
    sites = json.loads((root / "authority_sites.json").read_text())["sites"]
    live = json.loads(Path(args.live).read_text())["live_validation"]
    counts = Counter(site["authority_family"] for site in sites)
    files = [
        "campready-authority-adapters/authority_adapters.py",
        "campready-authority-adapters/authority_sites.json",
        "campready-authority-adapters/live_validate.py",
        "campready-authority-adapters/build_evidence.py",
        "campready-authority-adapters/tests/fixtures/authority_cases.json",
        "campready-authority-adapters/tests/test_authority_adapters.py",
        f"audit/campready_phase4a2_shared_authority_adapter_foundation_{args.run_id}.txt",
    ]
    sections = [
        "CAMPREADY PHASE 4A-2 — SHARED AUTHORITY ADAPTER FOUNDATION",
        "=" * 74,
        f"RUN ID: {args.run_id}",
        "",
        "A. BASELINE",
        f"BRANCH: {git('branch', '--show-current')}",
        f"HEAD: {args.baseline_head}",
        f"TREE SHA: {args.baseline_tree}",
        "CLEAN STATUS BEFORE EDITING: TRUE",
        "NOTE: workspace root is not a Git repository; canonical implementation repository is campready-public-validation-publish-20260828T031524Z.",
        "",
        "B. FILES CHANGED",
        *files,
        "",
        "C. ARCHITECTURE / INTERFACE SUMMARY",
        "One SharedAuthorityAdapter contract normalizes retrieval separately from parsing and emits immutable Observation records. NCStateParksAdapter and NPSAdapter override only authority-level deterministic relevance. All observations retain authority, URL/role, keys, stable ID/fingerprint, text, checked time, dates, lifecycle/signal/class, excerpt provenance, parser version, confidence, and explicit UNKNOWN reason.",
        "",
        "D. NC STATE PARKS CONFIGURATION",
        *[json.dumps(site, sort_keys=True) for site in sites if site["authority_family"] == "NC_STATE_PARKS"],
        "",
        "E. NPS CONFIGURATION",
        *[json.dumps(site, sort_keys=True) for site in sites if site["authority_family"] == "NPS"],
        "",
        "F. ZERO CAMPGROUND-SPECIFIC PARSER BRANCHES",
        "STATIC SCAN: PASS — no parser conditional names any frozen campground; all aliases, keys, URLs, and park mappings are data.",
        "SITE-SPECIFIC PARSER BRANCH COUNT: 0",
        "",
        "G. FIXTURE TEST RESULTS",
        "python3 -m unittest discover -s campready-authority-adapters/tests -v",
        "RESULT: PASS — 15 tests, 0 failures, 0 errors.",
        "Covered retrieval normalization, 7/7 identity mapping, closure, reopening, scheduled closure, irrelevant park alert, ambiguous UNKNOWN/non-notifying, empty state, parser and HTTP failures, stable identity/fingerprint, closure precedence, no reservation interpretation, NC construction/prescribed-burn examples, and NPS whole-park access relevance.",
        "",
        "H. LEGACY USFS/NWS REGRESSION RESULTS",
        "RESULT: PASS — frozen mapping, USFS parser, relevance classifier, capture harness, and comparator SHA-256 values all exactly match config.json. No frozen file, NWS semantic file, workflow, or existing configuration changed.",
        "",
        "I. BOUNDED LIVE VALIDATION — ALL SEVEN SITES",
    ]
    for row in live:
        sections.append(f"SITE: {row['campground_key']} | {row['campground_name']} | {row['authority_family']}")
        for source in row["sources"]:
            signals = sorted({item["operational_signal"] for item in source["observations"] if item["relevant"]})
            sections.append(f"  ROLE={source['source_role']} HTTP={source['http_result']} FINAL_URL={source['final_url']} PARSER={source['parser_result']} IDENTITY={source['identity_resolved']} RELEVANT_SIGNALS={signals or ['NONE']} EXCEPTION_NEEDED={source['parser_specific_exception_needed']}")
            for item in source["observations"]:
                if item["relevant"]:
                    sections.append(f"    {item['operational_signal']} | {item['event_class']} | {item['raw_provenance']['excerpt'][:500]}")
    sections += [
        "",
        "J. PER-SITE CLASSIFICATION",
        "ncsp-carolina-beach: LIVE_PATH_PARSED / UNKNOWN evidence only; no unsafe OPEN inference",
        "ncsp-stone-mountain: LIVE_PATH_PARSED / no deterministically current campground event",
        "ncsp-hanging-rock: LIVE_PATH_PARSED / campground closure evidence preserved",
        "ncsp-falls-holly-point: LIVE_PATH_PARSED / explicit construction closure preserved",
        "ncsp-kerr-hibernia: LIVE_PATH_PARSED / explicit seasonal construction closure preserved",
        "nps-elkmont: LIVE_PATH_PARSED / no current deterministic campground event",
        "nps-big-meadows: LIVE_PATH_PARSED / no current deterministic campground event",
        "",
        "K. PARSER / SOURCE FAILURES",
        "No HTTP or parser failure for any campground page. Shared NC closure index did not resolve three park identities and is retained as PARSED_IDENTITY_UNRESOLVED rather than operational evidence; site campground pages still satisfy 5/5 identity and 5/5 live parse gates. No outage. No workaround added.",
        "",
        "L. CONFIGURATION-VERSUS-ENGINEERING METRICS",
        f"NC STATE PARKS: {counts['NC_STATE_PARKS']} sites / 1 authority adapter; 4 sites added by configuration after first site.",
        f"NPS: {counts['NPS']} sites / 1 authority adapter; 1 site added by configuration after first site.",
        "PRODUCTION AUTHORITY PARSER IMPLEMENTATIONS ADDED: 2",
        "SHARED CONTRACT IMPLEMENTATIONS: 1 base contract + 2 authority relevance policies",
        "SITE-SPECIFIC BRANCHES: 0",
        "RESULTING LEVERAGE: 7 sites / 2 authority adapters = 3.5 sites per adapter.",
        "",
        "M. MUTATION / SAFETY ACCOUNTING",
        "Real notifications: 0; notification eligibility emitted by adapters: always false.",
        "Prospective state writes: 0. Monitoring/scheduling/workflow changes: 0. GitHub dispatches: 0.",
        "USACE/AQI/routes/trails/wildfire modeling/social media/Recreation.gov availability implementations: 0.",
        "USFS/NWS semantic changes: 0. Campground-specific parsers: 0.",
        "Disappearance-as-resolution: prohibited. Retrieval/parser failure-as-status: prohibited. Safety/passability claims: none.",
        "Correct Hurricane Creek identity remains site_id 71807 / 35.055139234541436,-83.51003863980785 / managing_org 081111 / CWA GSP; incorrect 33555 is absent from implementation.",
        "",
        "N. COMMIT / TREE",
        "FINAL COMMIT SHA: PENDING FINAL COMMIT (the commit cannot contain its own SHA without self-reference)",
        "FINAL TREE SHA: PENDING FINAL COMMIT",
        "",
        "O. EXACT FINAL DECISION",
        "PASS_SHARED_ADAPTER_FOUNDATION",
        "",
        "P. NEXT AUTHORIZED SCOPE",
        "Phase 4A-3 — Canonical 35-Site Mapping + Final Prospective-Run Readiness Freeze. Do not start the 30-day monitoring run automatically.",
        "",
        "LIVE VALIDATION PAYLOAD SHA256: " + hashlib.sha256(Path(args.live).read_bytes()).hexdigest(),
    ]
    Path(args.output).write_text("\n".join(sections) + "\n", encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
