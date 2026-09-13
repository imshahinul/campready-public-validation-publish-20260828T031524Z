"""Static validation and optional read-only live NWS regression for Phase 4A-3."""

from __future__ import annotations

import argparse
import json
import urllib.parse
import urllib.request
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse

ROOT = Path(__file__).resolve().parents[1]
MAPPING = Path(__file__).with_name("canonical_35site_mapping.json")
AUTHORITY_CONFIG = ROOT / "campready-authority-adapters/authority_sites.json"
FROZEN_PHASE3 = ROOT / "campready-prospective-validation/frozen/campready-canonical-relevance-mapping.json"
SUPPORTED = {"CONFIG_ONLY", "GENERIC_AUTHORITY_ADAPTER", "LIMITED_GENERIC_SUPPORT"}


def require(condition: bool, message: str, errors: list[str]) -> None:
    if not condition:
        errors.append(message)


def valid_url(value: object) -> bool:
    if value is None:
        return True
    parsed = urlparse(str(value))
    return parsed.scheme == "https" and bool(parsed.netloc)


def validate(live_nws: bool = False) -> dict[str, object]:
    data = json.loads(MAPPING.read_text(encoding="utf-8"))
    rows = data["rows"]
    errors: list[str] = []
    keys = [row["canonical_key"] for row in rows]
    require(len(rows) == 35, "row count must be 35", errors)
    require(len(set(keys)) == 35, "canonical keys must be unique", errors)
    require("33555" not in MAPPING.read_text(encoding="utf-8"), "forbidden Hurricane Creek site_id 33555 present", errors)
    hurricane = next((row for row in rows if row["canonical_key"] == "nc-hurricane-creek"), {})
    require(hurricane.get("authority_native_identifier", {}).get("value") == "71807", "Hurricane Creek site_id is not 71807", errors)
    require(hurricane.get("managing_org") == "081111", "Hurricane Creek managing_org is not 081111", errors)
    require((hurricane.get("latitude"), hurricane.get("longitude"), hurricane.get("nws", {}).get("cwa")) == (35.055139234541436, -83.51003863980785, "GSP"), "Hurricane Creek coordinate/CWA binding mismatch", errors)

    identities = []
    for row in rows:
        native = row.get("authority_native_identifier")
        if native:
            identities.append((row["authority_family"], native["namespace"], native["value"]))
        for field in ("campground_status_page_url", "alerts_conditions_page_url", "fire_restriction_source_path"):
            require(valid_url(row.get(field)), f"{row['canonical_key']}: invalid {field}", errors)
        require(bool(row.get("mapping_provenance")), f"{row['canonical_key']}: missing mapping provenance", errors)
    require(len(identities) == len(set(identities)), "duplicate authority-native identity", errors)

    usfs = [row for row in rows if row["authority_family"] == "USFS"]
    core = [row for row in rows if row["semantic_support_classification"] in SUPPORTED]
    transport = [row for row in rows if row["semantic_support_classification"] == "TRANSPORT_CHALLENGE"]
    require(len(usfs) == 25, "USFS count must be 25", errors)
    require(all(row.get("authority_native_identifier") and row.get("latitude") is not None and row.get("longitude") is not None for row in usfs), "all USFS rows need identity and coordinates", errors)
    require(len(core) == 32, "supported core must contain 32 sites", errors)
    require(len(transport) == 3 and all(row["authority_family"] == "USACE" for row in transport), "USACE transport lane must contain exactly three rows", errors)
    require(all(row.get("nws") and all(row["nws"].get(field) for field in ("cwa", "forecast_zone", "county_zone", "fire_weather_zone")) for row in core), "all supported rows need complete NWS mapping", errors)
    require(all(row.get("parser_adapter_family") in {"FROZEN_USFS_GENERIC_PARSER", "NCStateParksAdapter", "NPSAdapter"} for row in core), "unsupported parser/adapter reference", errors)
    require(all(row.get("parser_adapter_family") is None for row in transport), "transport rows must not name an adapter", errors)

    accepted = json.loads(FROZEN_PHASE3.read_text(encoding="utf-8"))["rows"]
    for old, new in zip(accepted, rows[:10], strict=True):
        for field in ("canonical_key", "canonical_name", "aliases", "usfs_recarea_id", "latitude", "longitude", "forest", "recreation_region", "nws_cwa", "nws_forecast_zone", "nws_county_zone", "nws_fire_weather_zone", "access_roads", "recreation_page_url"):
            require(old[field] == new[field], f"original Georgia regression: {old['canonical_key']} {field}", errors)

    authority_keys = {row["campground_key"] for row in json.loads(AUTHORITY_CONFIG.read_text(encoding="utf-8"))["sites"]}
    require(authority_keys == {row["canonical_key"] for row in rows if row["authority_family"] in {"NC_STATE_PARKS", "NPS"}}, "shared-adapter authority config mismatch", errors)

    live_results = []
    if live_nws:
        headers = {"User-Agent": "CampReady-Phase4A3/1.0 (read-only NWS validation)", "Accept": "application/geo+json"}
        for row in core:
            point = f"{row['latitude']:.6f},{row['longitude']:.6f}"
            point_url = "https://api.weather.gov/points/" + point
            alerts_url = "https://api.weather.gov/alerts/active?" + urllib.parse.urlencode({"point": point})
            try:
                with urllib.request.urlopen(urllib.request.Request(point_url, headers=headers), timeout=30) as response:
                    props = json.load(response)["properties"]
                    point_status = response.status
                with urllib.request.urlopen(urllib.request.Request(alerts_url, headers=headers), timeout=30) as response:
                    alerts = json.load(response)
                    alerts_status = response.status
                actual = (props["cwa"], props["forecastZone"].rsplit("/", 1)[-1], props["county"].rsplit("/", 1)[-1], props["fireWeatherZone"].rsplit("/", 1)[-1])
                expected = tuple(row["nws"][field] for field in ("cwa", "forecast_zone", "county_zone", "fire_weather_zone"))
                ok = point_status == alerts_status == 200 and isinstance(alerts.get("features"), list) and actual == expected
                live_results.append({"canonical_key": row["canonical_key"], "point_http": point_status, "alerts_http": alerts_status, "active_alert_count": len(alerts["features"]), "mapping_match": actual == expected, "ok": ok})
                require(ok, f"{row['canonical_key']}: live NWS validation mismatch", errors)
            except Exception as exc:
                live_results.append({"canonical_key": row["canonical_key"], "ok": False, "error": f"{type(exc).__name__}: {exc}"})
                errors.append(f"{row['canonical_key']}: live NWS failure")

    lane_counts = Counter(row["experimental_lane"] for row in rows)
    supported_lane_counts = Counter(row["experimental_lane"] for row in core)
    result = {
        "status": "PASS" if not errors else "FAIL", "errors": errors, "row_count": len(rows), "unique_key_count": len(set(keys)),
        "usfs_count": len(usfs), "supported_core_size": len(core), "transport_challenge_count": len(transport),
        "gross_useful_coverage": len(core) / len(rows), "lane_counts": dict(sorted(lane_counts.items())),
        "supported_lane_counts": dict(sorted(supported_lane_counts.items())), "live_nws_results": live_results,
    }
    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--live-nws", action="store_true")
    args = parser.parse_args()
    result = validate(args.live_nws)
    print(json.dumps(result, indent=2, sort_keys=True))
    return 0 if result["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
