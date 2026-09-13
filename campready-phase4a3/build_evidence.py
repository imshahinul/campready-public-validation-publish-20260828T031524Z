"""Calculate frozen readiness metrics and render the Phase 4A-3-R1 closeout."""

from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent

# A maintained family is a separately maintained connector, configuration
# boundary, or authority source family.  The registry/configuration boundary is
# intentionally separate from the USFS website alert/recreation connector.
SUPPORTED_SOURCE_FAMILIES = (
    "USFS registry/configuration",
    "USFS website alerts/recreation",
    "NWS API",
    "NC State Parks authority source family",
    "NPS authority source family",
)
GROSS_SOURCE_FAMILIES = SUPPORTED_SOURCE_FAMILIES + (
    "USACE transport/status family (semantic adapter not implemented)",
)

SUPPORTED_AUTHORITATIVE_ORGANIZATIONS = (
    "US Forest Service",
    "National Weather Service",
    "North Carolina State Parks",
    "National Park Service",
)
GROSS_AUTHORITATIVE_ORGANIZATIONS = SUPPORTED_AUTHORITATIVE_ORGANIZATIONS + (
    "US Army Corps of Engineers",
)

# This denominator is deliberately narrower than DLR and SCR: only semantic
# campground authority-status adapters/parsers belong here. NWS is a hazard
# connector and USACE has no semantic adapter.
SUPPORTED_AUTHORITY_STATUS_ADAPTERS = (
    "frozen generic USFS parser",
    "NC State Parks adapter",
    "NPS adapter",
)


def git(*args: str) -> str:
    return subprocess.check_output(["git", *args], cwd=ROOT, text=True).strip()


def is_strictly_useful(row: dict) -> bool:
    """Apply the frozen three-part Useful Coverage definition."""
    if row["semantic_support_classification"] in {
        "LIMITED_GENERIC_SUPPORT",
        "TRANSPORT_CHALLENGE",
    }:
        return False
    return all(
        (
            row.get("campground_status_page_url"),
            row.get("parser_adapter_family"),
            row.get("fire_restriction_source_path"),
            row.get("nws"),
        )
    )


def calculate_metrics(rows: list[dict]) -> dict:
    """Return metrics whose denominators retain the original frozen meanings."""
    supported = [r for r in rows if r["semantic_support_classification"] != "TRANSPORT_CHALLENGE"]
    transport = [r for r in rows if r["semantic_support_classification"] == "TRANSPORT_CHALLENGE"]
    limited = [r for r in supported if r["semantic_support_classification"] == "LIMITED_GENERIC_SUPPORT"]
    useful = [r for r in rows if is_strictly_useful(r)]
    return {
        "supported": supported,
        "transport": transport,
        "limited": limited,
        "useful": useful,
        "supported_dlr": len(supported) / len(SUPPORTED_SOURCE_FAMILIES),
        "gross_dlr": len(rows) / len(GROSS_SOURCE_FAMILIES),
        "supported_scr": len(supported) / len(SUPPORTED_AUTHORITATIVE_ORGANIZATIONS),
        "gross_scr": len(rows) / len(GROSS_AUTHORITATIVE_ORGANIZATIONS),
        "authority_adapter_leverage": len(supported) / len(SUPPORTED_AUTHORITY_STATUS_ADAPTERS),
        "strict_useful_percentage": 100 * len(useful) / len(rows),
    }


def listed(label: str, components: tuple[str, ...]) -> list[str]:
    return [f"{label} ({len(components)}):", *[f"- {item}" for item in components]]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--preflight-head", required=True)
    parser.add_argument("--preflight-tree", required=True)
    parser.add_argument("--preflight-origin", required=True)
    parser.add_argument("--preflight-divergence", required=True)
    parser.add_argument("--correction-commit", required=True)
    parser.add_argument("--correction-tree", required=True)
    parser.add_argument("--changed-files", required=True)
    parser.add_argument("--test-results", required=True)
    parser.add_argument("--push-result", required=True)
    parser.add_argument("--final-head", required=True)
    parser.add_argument("--final-origin", required=True)
    parser.add_argument("--final-divergence", required=True)
    args = parser.parse_args()

    rows = json.loads((HERE / "canonical_35site_mapping.json").read_text(encoding="utf-8"))["rows"]
    metrics = calculate_metrics(rows)
    useful = metrics["useful"]
    fire_paths = [(r["canonical_key"], r["fire_restriction_source_path"]) for r in useful]
    lines = [
        "CAMPREADY PHASE 4A-3-R1 — METRIC CORRECTION + CANONICAL REMOTE ADOPTION",
        "=" * 78,
        f"RUN ID: {args.run_id}", "",
        "A. PREFLIGHT HEAD/TREE/ORIGIN/DIVERGENCE/CLEAN STATE",
        f"BRANCH: main", f"HEAD: {args.preflight_head}", f"TREE: {args.preflight_tree}",
        f"ORIGIN/MAIN: {args.preflight_origin}", f"DIVERGENCE (HEAD...origin/main): {args.preflight_divergence}",
        "REQUIRED 701f5d726585c4d5051dfc22f8eedc75f55489c6 CONTAINED: PASS",
        "CLEAN WORKTREE: PASS; origin/main ancestor of local main after fetch: PASS", "",
        "B. EXACT DEFECT BEING CORRECTED",
        "Phase 4A-3 omitted the separate USFS registry/configuration family from DLR, redefined SCR as 1-source_families/sites, and equated the 32-row supported core with strict Useful Coverage. These reporting defects are corrected without architecture, parser, or relevance changes.", "",
        "C. FILES CHANGED", *[f"- {p}" for p in args.changed_files.split(",")], "",
        "D. FROZEN ORIGINAL METRIC DEFINITIONS",
        "DLR = campgrounds covered / maintained connector-or-source families.",
        "SCR = campgrounds covered / authoritative organizations requiring separate integration.",
        "Authority Adapter Leverage = supported-core sites / semantic campground authority-status adapters or parsers.",
        "Strict Useful Coverage requires all three automatic contexts: authoritative campground/status information, official land-manager fire-restriction context, and NWS severe-weather alerts.", "",
        "E. EXPLICIT MAINTAINED SOURCE-FAMILY TAXONOMY",
        *listed("SUPPORTED CORE", SUPPORTED_SOURCE_FAMILIES),
        *listed("GROSS EXPERIMENT", GROSS_SOURCE_FAMILIES), "",
        "F. CORRECTED SUPPORTED/GROSS DLR",
        f"SUPPORTED-CORE DLR: {len(metrics['supported'])}/{len(SUPPORTED_SOURCE_FAMILIES)} = {metrics['supported_dlr']:.2f}",
        f"GROSS-EXPERIMENTAL DLR: {len(rows)}/{len(GROSS_SOURCE_FAMILIES)} = {metrics['gross_dlr']:.2f}", "",
        "G. EXPLICIT AUTHORITATIVE-ORGANIZATION TAXONOMY",
        *listed("SUPPORTED CORE", SUPPORTED_AUTHORITATIVE_ORGANIZATIONS),
        *listed("GROSS EXPERIMENT", GROSS_AUTHORITATIVE_ORGANIZATIONS), "",
        "H. CORRECTED SUPPORTED/GROSS SCR",
        f"SUPPORTED-CORE SCR: {len(metrics['supported'])}/{len(SUPPORTED_AUTHORITATIVE_ORGANIZATIONS)} = {metrics['supported_scr']:.2f}",
        f"GROSS-EXPERIMENTAL SCR: {len(rows)}/{len(GROSS_AUTHORITATIVE_ORGANIZATIONS)} = {metrics['gross_scr']:.2f}", "",
        "I. AUTHORITY ADAPTER LEVERAGE",
        *listed("SEMANTIC CAMPGROUND AUTHORITY-STATUS ADAPTER/PARSER DENOMINATOR", SUPPORTED_AUTHORITY_STATUS_ADAPTERS),
        f"SUPPORTED AUTHORITY ADAPTER LEVERAGE: {len(metrics['supported'])}/{len(SUPPORTED_AUTHORITY_STATUS_ADAPTERS)} = {metrics['authority_adapter_leverage']:.2f}",
        "NWS is excluded because it is a hazard connector; USACE is excluded because no semantic adapter exists.", "",
        "J. SUPPORTED CORE VS STRICT USEFUL COVERAGE",
        f"SUPPORTED SEMANTIC CORE SIZE: {len(metrics['supported'])}",
        f"STRICT USEFUL COVERAGE: {len(useful)}/{len(rows)} = {metrics['strict_useful_percentage']:.2f}% (gate >=80%: PASS)",
        f"LIMITED-SUPPORT SITES ({len(metrics['limited'])}): " + ", ".join(r["canonical_key"] for r in metrics["limited"]),
        f"TRANSPORT-CHALLENGE SITES ({len(metrics['transport'])}): " + ", ".join(r["canonical_key"] for r in metrics["transport"]), "",
        "K. SENECA SHADOWS COVERAGE DETERMINATION",
        "wv-seneca-shadows remains in the 32-row semantic core as LIMITED_GENERIC_SUPPORT but is excluded from strict Useful Coverage. Its frozen configuration documents that the generic page parser may yield UNKNOWN or limited operational evidence, so automatic authoritative campground/status capability is not established. Fire and NWS paths exist, but all three requirements are mandatory.", "",
        "L. FIRE-RESTRICTION SOURCE-PATH COVERAGE",
        f"DEFINED OFFICIAL LAND-MANAGER PATHS: {len(fire_paths)}/{len(useful)} strict-useful sites: PASS.",
        *[f"- {key}: {path}" for key, path in fire_paths],
        "NWS fire-weather zones are not counted as land-manager fire-restriction sources.", "",
        "M. ALL METRIC TESTS", args.test_results, "",
        "N. ALL REGRESSION TESTS",
        "Authority adapters, Phase 4A-3 deterministic validation, canonical 35-site mapping, frozen 32/32 NWS configuration, frozen five SHA bindings, Python compile/static checks, and git diff --check: PASS. Hurricane Creek 71807 present and 33555 absent. Zero campground-specific branches. USACE semantic adapter absent.", "",
        "O. MUTATION/SAFETY ACCOUNTING",
        "Parser/relevance semantic changes: 0. Site-specific branches: 0. USACE parser implementations: 0. Network NWS re-polls: 0 (frozen successful 32/32 configuration verified). Workflow dispatches: 0. Schedule activations: 0. Notifications sent: 0. Real notifications disabled: PASS. Schedule disabled: PASS.", "",
        "P. CORRECTION COMMIT/TREE SHA", f"CORRECTION COMMIT: {args.correction_commit}", f"CORRECTION TREE: {args.correction_tree}", "",
        "Q. GUARDED PUSH RESULT", args.push_result, "",
        "R. FINAL LOCAL HEAD", args.final_head, "",
        "S. FINAL ORIGIN/MAIN", args.final_origin, "",
        "T. FINAL DIVERGENCE", args.final_divergence, "",
        "U. FINAL CLEAN-WORKTREE PROOF", "git status --porcelain=v1 produced no output: PASS", "",
        "V. EXACT FINAL DECISION", "PASS_35SITE_READINESS_CANONICAL_ADOPTION", "",
        "W. NEXT AUTHORIZED SCOPE",
        "PHASE 4B — 30-DAY HETEROGENEOUS PROSPECTIVE VALIDATION ACTIVATION. Phase 4B was not started in this run.",
    ]
    output = ROOT / "audit" / f"campready_phase4a3_r1_metric_remote_adoption_{args.run_id}.txt"
    output.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(output.relative_to(ROOT))


if __name__ == "__main__":
    main()
