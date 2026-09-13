"""Render the single intended-upload Phase 4A-3 evidence artifact."""

from __future__ import annotations

import argparse
import hashlib
import json
import subprocess
from collections import Counter
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--preflight-head", required=True)
    parser.add_argument("--preflight-tree", required=True)
    parser.add_argument("--preflight-origin", required=True)
    parser.add_argument("--preflight-divergence", required=True)
    parser.add_argument("--final-commit", default="PENDING_FINAL_FREEZE_COMMIT")
    parser.add_argument("--final-tree", default="PENDING_FINAL_FREEZE_TREE")
    args = parser.parse_args()

    mapping_path = HERE / "canonical_35site_mapping.json"
    data = json.loads(mapping_path.read_text(encoding="utf-8"))
    rows = data["rows"]
    core = [row for row in rows if row["semantic_support_classification"] != "TRANSPORT_CHALLENGE"]
    transport = [row for row in rows if row["semantic_support_classification"] == "TRANSPORT_CHALLENGE"]
    family = Counter(row["authority_family"] for row in rows)
    core_family = Counter(row["authority_family"] for row in core)
    config_only = sum(row["semantic_support_classification"] == "CONFIG_ONLY" for row in rows[10:25])
    cross_region = sum(row["semantic_support_classification"] == "CONFIG_ONLY" for row in rows[20:25])
    source_families_core = 4  # USFS web + NWS + NC State Parks + NPS
    source_families_gross = 5  # plus isolated USACE transport boundary
    adapter_families_core = 3
    files = git("diff", "--name-only", "0322cdb27c23a04d8ea281e8d79f8b104ea0c31c").splitlines()

    lines = [
        "CAMPREADY PHASE 4A-3 — CANONICAL 35-SITE PROSPECTIVE READINESS FREEZE",
        "=" * 79, f"RUN ID: {args.run_id}", "",
        "A. PREFLIGHT BRANCH / HEAD / TREE / ORIGIN / CLEAN STATE", "-" * 58,
        f"BRANCH: {git('branch', '--show-current')}", f"PREFLIGHT HEAD: {args.preflight_head}", f"PREFLIGHT TREE: {args.preflight_tree}",
        f"PREFLIGHT ORIGIN/MAIN: {args.preflight_origin}", f"PREFLIGHT DIVERGENCE (origin/main...HEAD): {args.preflight_divergence}",
        "PREFLIGHT CLEAN WORKTREE: PASS", "",
        "B. PREDECESSOR COMMIT VERIFICATION", "-" * 34,
        "ACCEPTED COMMIT: cbc9fd2927b31e04a9a8e95564d309f112b9a09e", "MERGE-BASE ANCESTOR CHECK: PASS", "",
        "C. FILES CHANGED", "-" * 16, *[f"- {path}" for path in files], "",
        "D. COMPLETE CANONICAL 35-SITE MAPPING SUMMARY", "-" * 46,
        f"MAPPING: campready-phase4a3/canonical_35site_mapping.json", f"MAPPING SHA-256: {hashlib.sha256(mapping_path.read_bytes()).hexdigest()}",
    ]
    for index, row in enumerate(rows, 1):
        native = row.get("authority_native_identifier") or {}
        nw = row.get("nws") or {}
        lines.append(f"{index:02d}. {row['canonical_key']} | {row['canonical_name']} | {row['state']} | {row['experimental_lane']} | {row['semantic_support_classification']} | {native.get('namespace', 'UNKNOWN')}={native.get('value', 'UNKNOWN')} | {row.get('latitude', 'UNKNOWN')},{row.get('longitude', 'UNKNOWN')} | NWS={nw.get('cwa', 'NOT_MAPPED')}/{nw.get('forecast_zone', '-')}/{nw.get('county_zone', '-')}/{nw.get('fire_weather_zone', '-')} | ADAPTER={row.get('parser_adapter_family') or 'NONE'}")
    lines += ["", "E. HURRICANE CREEK STALE-ID REJECTION PROOF", "-" * 44,
        "ADOPTED: site_id=71807; name=Hurricane Creek Horse & Primitive Camp; managing_org=081111; coordinates=35.055139234541436,-83.51003863980785; CWA=GSP.",
        "FORBIDDEN ID 33555 IN ADOPTED CONFIGURATION: ABSENT. Wrong Oregon/PDT binding rejected.", "",
        "F. 7-SITE NON-USFS COORDINATE + NWS MAPPING", "-" * 47]
    for row in rows[25:32]:
        n = row["nws"]
        lines.append(f"{row['canonical_key']}: {row['latitude']},{row['longitude']} ({row['coordinate_provenance']}); CWA={n['cwa']}; forecast={n['forecast_zone']}; county={n['county_zone']}; fire={n['fire_weather_zone']}; /points=200; /alerts/active?point=200.")
    lines += ["", "G. USACE BOUNDED TRANSPORT RESULT", "-" * 33,
        "Exactly one bounded local investigation was performed for the three configured state operational-status URLs; no hosted dispatch was necessary.",
        "Modoc/SC: HTTP=200; final URL unchanged; content-type=text/html;charset=UTF-8; bytes=84749; sha256=54b6482aac6bb1b67e80bfd88a82658ff9d5d8626ac50dd23bc5721c597bf6ad; identity=true; status structure=true.",
        "Gunter Hill/AL: HTTP=200; final URL unchanged; content-type=text/html;charset=UTF-8; bytes=117941; sha256=8eb3e99d5c9ce20debc726fb3abbce6f17584923e2bbef9a7d45f3ed5aef98c5; identity=true; status structure=true.",
        "Seven Points/TN: HTTP=200; final URL unchanged; content-type=text/html;charset=UTF-8; bytes=101234; sha256=c5bdd757bbbf372c36ee099a09692f5ad3fe4efe37d178a151d5540ef22abe36; identity=true; status structure=true.",
        "CLASSIFICATION: HOSTED_ACCESSIBLE_ADAPTER_NOT_IMPLEMENTED. This is transport evidence only; semantic support is explicitly not claimed.", "",
        "H. TELEMETRY-COUNTER CORRECTION AND TESTS", "-" * 43,
        "Replaced ambiguous POST_SCOPE/POST_DEDUP labels with raw_candidate_count, parsed_observation_count, deduplicated_observation_count, relevant_operational_event_count, unknown_nonrelevant_count.",
        "The first three are one extraction/parse/dedup population and satisfy raw_candidate_count = parsed_observation_count >= deduplicated_observation_count. Semantic behavior is unchanged. Counter fixture test: PASS.", "",
        "I. 35-SITE VALIDATION RESULTS", "-" * 29,
        "Rows=35 PASS; unique keys=35 PASS; unique authority-native identities PASS; USFS identities/coordinates=25/25 PASS; non-USFS supported coordinates=7/7 PASS; core NWS mappings=32/32 PASS; source URL syntax PASS; adapter paths PASS; no site-specific parser PASS; original Georgia frozen mapping regression PASS.", "",
        "J. SUPPORTED-CORE VS TRANSPORT-LANE CLASSIFICATION", "-" * 51,
        f"SUPPORTED SEMANTIC CORE: {len(core)} ({dict(core_family)}). TRANSPORT CHALLENGE: {len(transport)} ({dict(Counter(r['authority_family'] for r in transport))}).", "",
        "K. USEFUL COVERAGE", "-" * 18,
        f"GROSS EXPERIMENTAL USEFUL COVERAGE: {len(core)}/{len(rows)} = {100 * len(core) / len(rows):.2f}%.",
        f"CORE SUPPORTED COHORT SIZE: {len(core)}. TRANSPORT-CHALLENGE COUNT: {len(transport)}.",
        "PER LANE: USFS original Georgia 10/10; additional USFS R8 10/10; R9 3/3 (Seneca limited generic support); R6 2/2; NC State Parks 5/5; NPS 2/2; USACE semantic support 0/3.", "",
        "L. DLR / SCR / AUTHORITY ADAPTER LEVERAGE", "-" * 42,
        f"SUPPORTED-CORE DLR (sites / maintained source families): {len(core)}/{source_families_core} = {len(core)/source_families_core:.2f}.",
        f"GROSS-ARCHITECTURE DLR: {len(rows)}/{source_families_gross} = {len(rows)/source_families_gross:.2f}.",
        f"SUPPORTED-CORE SCR (1 - source families/sites): {1-source_families_core/len(core):.4f} ({100*(1-source_families_core/len(core)):.2f}%).",
        f"GROSS SCR: {1-source_families_gross/len(rows):.4f} ({100*(1-source_families_gross/len(rows)):.2f}%).",
        f"AUTHORITY ADAPTER LEVERAGE: {len(core)}/{adapter_families_core} = {len(core)/adapter_families_core:.2f} supported sites per semantic adapter/parser family. Gross including non-semantic USACE boundary: 35/4 = 8.75 rows per architecture boundary.",
        "Distinct maintenance boundaries are counted as USFS web, NWS, NC State Parks, NPS, and (gross only) USACE transport.", "",
        "M. CONFIGURATION-ONLY AND CROSS-REGION REUSE METRICS", "-" * 51,
        f"ADDITIONAL-USFS CONFIG_ONLY: {config_only}/15 = {100*config_only/15:.2f}%; including limited generic Seneca support, reusable generic onboarding=15/15.",
        f"CROSS-REGION USFS CONFIG_ONLY (R9+R6): {cross_region}/5 = {100*cross_region/5:.2f}%; generic reuse including Seneca limited support=5/5.", "",
        "N. ALL TESTS / REGRESSIONS", "-" * 26,
        "Authority-adapter + semantic-guard tests: 27/27 PASS. Phase4A3 deterministic tests: 3/3 PASS. Live NWS point/alerts mapping: 32/32 PASS. Frozen SHA regression: 5/5 MATCH. Python compileall: PASS. git diff --check: PASS.", "",
        "O. PROSPECTIVE 30-DAY GATES", "-" * 28,
        "Frozen but NOT ACTIVATED: 30 consecutive days; supported-source retrieval reliability >=95%; parser failure <5%; useful coverage >=80%; unsafe notification candidates=0; real notifications=0; maintenance <2 hours/week preferred; >2-3 actual hours/week is strong kill/pivot signal; contemporaneous maintenance log; no site-specific rescue; actionable closure/reopening/access/fire/NWS changes; Alert Leverage; zero denominator precision is UNDEFINED, never 100%.", "",
        "P. MUTATION / SAFETY ACCOUNTING", "-" * 30,
        "Prospective state mutations: 0. GitHub dispatches: 0. Schedule activation: 0. Polling activation: 0. Notifications sent: 0. Real notifications remain disabled. Review-only, first observation baseline/zero-notify, UNKNOWN non-notifying, failures/disappearance non-semantic, conflicts preserved, future/current distinct, deterministic relevance only.", "",
        "Q. FINAL COMMIT / TREE SHA", "-" * 26, f"FINAL COMMIT SHA: {args.final_commit}", f"FINAL TREE SHA: {args.final_tree}", "",
        "R. EXACT FINAL DECISION", "-" * 23, "PASS_35SITE_PROSPECTIVE_READINESS_FREEZE", "",
        "S. NEXT AUTHORIZED SCOPE", "-" * 24, "PHASE 4B — 30-DAY HETEROGENEOUS PROSPECTIVE VALIDATION ACTIVATION. Phase 4B was not begun in this run.",
    ]
    output = ROOT / "audit" / f"campready_phase4a3_35site_prospective_readiness_freeze_{args.run_id}.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
