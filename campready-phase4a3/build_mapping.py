"""Build the frozen Phase 4A-3 cohort from accepted, traceable evidence."""

from __future__ import annotations

import copy
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
PHASE3_MAPPING = ROOT / "campready-prospective-validation/frozen/campready-canonical-relevance-mapping.json"
OUTPUT = Path(__file__).with_name("canonical_35site_mapping.json")
USFS_INFRA = "https://apps.fs.usda.gov/arcx/rest/services/EDW/EDW_InfraRecreationSites_01/MapServer/0"
NWS_POINTS = "https://api.weather.gov/points/{latitude},{longitude}"
NWS_ALERTS = "https://api.weather.gov/alerts/active?point={latitude},{longitude}"


def provenance(source: str, claim: str) -> dict[str, str]:
    return {"source": source, "claim": claim}


def nws(cwa: str, forecast: str, county: str, fire: str) -> dict[str, object]:
    return {
        "cwa": cwa,
        "forecast_zone": forecast,
        "county_zone": county,
        "fire_weather_zone": fire,
        "points_endpoint": NWS_POINTS,
        "active_alerts_endpoint": NWS_ALERTS,
        "mapping_validated_live": True,
        "active_alerts_request_succeeded": True,
        "provenance": "NWS API /points and /alerts/active?point live Phase 4A-3 verification",
    }


USFS = [
    ("nc-north-mills-river", "North Mills River Campground", "NC", "70715", "081107", 35.40763785194335, -82.64453978597471, "48148", "r08/northcarolina", "north-mills-river-campground", "National Forests in North Carolina", "UNKNOWN", ("GSP", "NCZ065", "NCC089", "NCZ065"), "CONFIG_ONLY"),
    ("nc-van-hook-glade", "Van Hook Glade Campground", "NC", "70521", "081111", 35.07781941962203, -83.24275127287943, None, "r08/northcarolina", "van-hook-glade-campground", "National Forests in North Carolina", "Nantahala Ranger District", ("GSP", "NCZ062", "NCC113", "NCZ062"), "CONFIG_ONLY"),
    ("nc-hurricane-creek", "Hurricane Creek Horse & Primitive Camp", "NC", "71807", "081111", 35.055139234541436, -83.51003863980785, None, "r08/northcarolina", "hurricane-creek-horse-primitive-campground", "National Forests in North Carolina", "Nantahala Ranger District", ("GSP", "NCZ062", "NCC113", "NCZ062"), "CONFIG_ONLY"),
    ("sc-burrells-ford", "Burrell's Ford Campground", "SC", "22577", "081202", 34.969011731480045, -83.11813693019847, "47065", "r08/francismarionsumter", "burrells-ford-campground", "Francis Marion and Sumter National Forests", "UNKNOWN", ("GSP", "SCZ101", "SCC073", "SCZ101"), "CONFIG_ONLY"),
    ("sc-brick-house", "Brick House Campground", "SC", "22527", "081201", 34.4483552228186, -81.70729094668407, "47233", "r08/francismarionsumter", "brick-house-campground", "Francis Marion and Sumter National Forests", "UNKNOWN", ("CAE", "SCZ020", "SCC071", "SCZ020"), "CONFIG_ONLY"),
    ("al-payne-lake", "Payne Lake Recreation Area", "AL", "15359", "080104", 32.88127128524199, -87.44588002762968, "30147", "r08/alabama", "payne-lake-recreation-area", "National Forests in Alabama", "UNKNOWN", ("BMX", "ALZ032", "ALC065", "ALZ032"), "CONFIG_ONLY"),
    ("al-clear-creek", "Clear Creek Recreation Area", "AL", "40002", "080101", 34.012149379185814, -87.26569143919073, "30085", "r08/alabama", "clear-creek-recreation-area", "National Forests in Alabama", "UNKNOWN", ("BMX", "ALZ014", "ALC133", "ALZ014"), "CONFIG_ONLY"),
    ("al-coleman-lake", "Coleman Lake Recreation Area", "AL", "40000", "080105", 33.78491626799285, -85.55892526139627, "30157", "r08/alabama", "coleman-lake-recreation-area", "National Forests in Alabama", "UNKNOWN", ("BMX", "ALZ021", "ALC029", "ALZ021"), "CONFIG_ONLY"),
    ("tn-indian-boundary", "Indian Boundary Campground", "TN", "50434", "080404", 35.399906793049205, -84.10690421414178, "35150", "r08/cherokee", "indian-boundary-recreation-area", "Cherokee National Forest", "UNKNOWN", ("MRX", "TNZ087", "TNC123", "TNZ087"), "CONFIG_ONLY"),
    ("tn-rock-creek", "Rock Creek Recreation Area", "TN", "50532", "080405", 36.1379070156318, -82.3506037419644, "34978", "r08/cherokee", "rock-creek-recreation-area", "Cherokee National Forest", "UNKNOWN", ("MRX", "TNZ045", "TNC171", "TNZ045"), "CONFIG_ONLY"),
    ("wv-big-rock", "Big Rock Campground", "WV", "90200", "092102", 38.295872528260354, -80.52466232970298, "6984", "r09/monongahela", "big-rock-campground", "Monongahela National Forest", "UNKNOWN", ("RLX", "WVZ520", "WVC067", "WVZ520"), "CONFIG_ONLY"),
    ("wv-seneca-shadows", "Seneca Shadows Campground", "WV", "90533", "092105", 38.82136767916264, -79.38632101654947, "7004", "r09/monongahela", "seneca-shadows-campground", "Monongahela National Forest", "UNKNOWN", ("LWX", "WVZ505", "WVC071", "WVZ505"), "LIMITED_GENERIC_SUPPORT"),
    ("wv-bear-heaven", "Bear Heaven Campground", "WV", "90107", "092101", 38.931559689167464, -79.68056611411481, None, "r09/monongahela", "bear-heaven-campground", "Monongahela National Forest", "UNKNOWN", ("RLX", "WVZ526", "WVC083", "WVZ526"), "CONFIG_ONLY"),
    ("or-spinreel", "Spinreel Campground", "OR", "63029", "061208", 43.56988519945235, -124.2060143315852, None, "r06/siuslaw", "spinreel-campground", "Siuslaw National Forest", "Oregon Dunes National Recreation Area", ("MFR", "ORZ021", "ORC011", "ORZ615"), "CONFIG_ONLY"),
    ("or-cold-springs", "Cold Springs Campground", "OR", "12617", "060105", 44.30904716503235, -121.63058907709843, "38592", "r06/deschutes", "cold-springs-campground", "Deschutes National Forest", "UNKNOWN", ("PDT", "ORZ509", "ORC017", "ORZ704"), "CONFIG_ONLY"),
]

NON_USFS = [
    ("ncsp-carolina-beach", "Carolina Beach State Park Campground", ["Carolina Beach", "family campground"], "NC", "NC_STATE_PARKS", "North Carolina Division of Parks and Recreation", "Carolina Beach State Park", "carolina-beach-state-park", 34.0472, -77.9066, "https://www.ncparks.gov/state-parks/carolina-beach-state-park", "Official park address GPS point", "https://www.ncparks.gov/state-parks/carolina-beach-state-park/camping", "https://www.ncparks.gov/blog-category/closures", ("ILM", "NCZ108", "NCC129", "NCZ108")),
    ("ncsp-stone-mountain", "Stone Mountain Family Campground", ["family campground"], "NC", "NC_STATE_PARKS", "North Carolina Division of Parks and Recreation", "Stone Mountain State Park", "stone-mountain-state-park", 36.3873, -81.0273, "https://www.ncparks.gov/state-parks/stone-mountain-state-park", "Official park address GPS point; park-level representative coordinate", "https://www.ncparks.gov/state-parks/stone-mountain-state-park/camping", "https://www.ncparks.gov/blog-category/closures", ("RNK", "NCZ002", "NCC005", "NCZ002")),
    ("ncsp-hanging-rock", "Hanging Rock Family Campground", ["family campground"], "NC", "NC_STATE_PARKS", "North Carolina Division of Parks and Recreation", "Hanging Rock State Park", "hanging-rock-state-park", 36.4119, -80.2541, "https://www.ncparks.gov/state-parks/hanging-rock-state-park", "Official main entrance/visitor-center GPS point; park-level representative coordinate", "https://www.ncparks.gov/state-parks/hanging-rock-state-park/camping-hanging-rock", "https://www.ncparks.gov/blog-category/closures", ("RNK", "NCZ004", "NCC169", "NCZ004")),
    ("ncsp-falls-holly-point", "Falls Lake — Holly Point Campground", ["Holly Point Campground", "Holly Point"], "NC", "NC_STATE_PARKS", "North Carolina Division of Parks and Recreation", "Falls Lake State Recreation Area", "falls-lake-state-recreation-area:holly-point", 36.0100, -78.6575, "https://www.ncparks.gov/state-parks/falls-lake-state-recreation-area", "Official Holly Point Campground GPS point", "https://www.ncparks.gov/state-parks/falls-lake-state-recreation-area/camping-falls-lake", "https://www.ncparks.gov/state-parks/falls-lake-state-recreation-area/news/campground-closures", ("RAH", "NCZ041", "NCC183", "NCZ041")),
    ("ncsp-kerr-hibernia", "Kerr Lake — Hibernia Campground", ["Hibernia Campground", "Hibernia recreation area", "Hibernia"], "NC", "NC_STATE_PARKS", "North Carolina Division of Parks and Recreation", "Kerr Lake State Recreation Area", "kerr-lake-state-recreation-area:hibernia", 36.5047, -78.3761, "https://www.ncparks.gov/state-parks/kerr-lake-state-recreation-area", "Official Hibernia Access GPS point", "https://www.ncparks.gov/state-parks/kerr-lake-state-recreation-area/camping-kerr-lake", "https://www.ncparks.gov/blog-category/closures", ("RAH", "NCZ009", "NCC181", "NCZ009")),
    ("nps-elkmont", "Elkmont Campground", ["Elkmont"], "TN", "NPS", "National Park Service", "Great Smoky Mountains National Park", "63D37502-C1A6-44B7-A2C3-C4F784C03318", 35.65875714573239, -83.58274047425141, "https://developer.nps.gov/api/v1/campgrounds?parkCode=grsm", "Official NPS Campgrounds API exact-name record", "https://www.nps.gov/grsm/planyourvisit/elkmont-campground.htm", "https://www.nps.gov/grsm/planyourvisit/conditions.htm", ("MRX", "TNZ074", "TNC155", "TNZ074")),
    ("nps-big-meadows", "Big Meadows Campground", ["Big Meadows"], "VA", "NPS", "National Park Service", "Shenandoah National Park", "75E4DC88-1332-456C-9F06-05E9F4E1CAB6", 38.5279623, -78.4369404, "https://developer.nps.gov/api/v1/campgrounds?parkCode=shen", "Official NPS Campgrounds API exact-name record", "https://www.nps.gov/shen/planyourvisit/big-meadows-campground.htm", "https://www.nps.gov/shen/planyourvisit/conditions.htm", ("LWX", "VAZ507", "VAC113", "VAZ507")),
]

USACE = [
    ("usace-modoc", "Modoc Campground", "SC", "Savannah District", "J. Strom Thurmond Lake", "https://corpslakes.erdc.dren.mil/visitors/status.cfm?state=SC"),
    ("usace-gunter-hill", "Gunter Hill Campground", "AL", "Mobile District", "Alabama River Lakes", "https://corpslakes.erdc.dren.mil/visitors/status.cfm?state=AL"),
    ("usace-seven-points", "Seven Points Campground", "TN", "Nashville District", "J. Percy Priest Lake", "https://corpslakes.erdc.dren.mil/visitors/status.cfm?state=TN"),
]


def original_rows() -> list[dict[str, object]]:
    frozen = json.loads(PHASE3_MAPPING.read_text(encoding="utf-8"))
    rows = []
    for original in frozen["rows"]:
        row = copy.deepcopy(original)
        row.update({
            "state": "GA",
            "experimental_lane": "USFS_ORIGINAL_GEORGIA",
            "authority_family": "USFS",
            "managing_authority": "USDA Forest Service",
            "authority_native_identifier": {"namespace": "USFS_RECAREA_ID", "value": str(original["usfs_recarea_id"])},
            "coordinate_provenance": original["identity_provenance"],
            "campground_status_page_url": original["recreation_page_url"],
            "alerts_conditions_page_url": "https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts",
            "fire_restriction_source_path": "https://www.fs.usda.gov/r08/chattahoochee-oconee/alerts",
            "parser_adapter_family": "FROZEN_USFS_GENERIC_PARSER",
            "parser_version": "sha256:8cc4c01b040f51ab07e28eff511946c7abd3371af509018b065da0fd07cedb9c",
            "semantic_support_classification": "CONFIG_ONLY",
            "nws": nws(original["nws_cwa"], original["nws_forecast_zone"], original["nws_county_zone"], original["nws_fire_weather_zone"]),
            "unresolved_optional_fields": original["unresolved_fields"],
            "coverage_limitations": ["Deterministic generic USFS semantics only; no safety or passability inference"],
            "mapping_provenance": [provenance("Accepted frozen Phase 3A mapping SHA-256 eebaa421e00a120aba3077f5db8608178e71454587452d2659e28f3336cf0590", "All original identity, name, aliases, coordinates, NWS zones, district and access-road claims preserved verbatim")],
        })
        rows.append(row)
    return rows


def usfs_rows() -> list[dict[str, object]]:
    rows = []
    for key, name, state, site_id, org, lat, lon, recarea, unit, slug, forest, district, zones, support in USFS:
        page = f"https://www.fs.usda.gov/{unit}/recreation/{slug}"
        alerts = f"https://www.fs.usda.gov/{unit}/alerts"
        limitations = ["Deterministic generic USFS semantics only; no safety or passability inference"]
        if support == "LIMITED_GENERIC_SUPPORT":
            limitations.append("Known generic-parser edge case: page may yield UNKNOWN or limited operational evidence; no campground-specific parser authorized")
        rows.append({
            "canonical_key": key, "canonical_name": name, "aliases": [name, name.replace(" Campground", "")], "state": state,
            "experimental_lane": "USFS_ADDITIONAL_R8" if unit.startswith("r08") else ("USFS_ADDITIONAL_R9" if unit.startswith("r09") else "USFS_ADDITIONAL_R6"),
            "authority_family": "USFS", "managing_authority": "USDA Forest Service", "forest_park_project": forest,
            "ranger_district_recreation_region_park_code": district, "managing_org": org,
            "authority_native_identifier": {"namespace": "USFS_INFRA_SITE_ID", "value": site_id}, "usfs_recarea_id": recarea,
            "latitude": lat, "longitude": lon, "coordinate_provenance": "Official USFS EDW InfraRecreationSites geometry; exact site_id query",
            "campground_status_page_url": page, "alerts_conditions_page_url": alerts, "fire_restriction_source_path": alerts,
            "parser_adapter_family": "FROZEN_USFS_GENERIC_PARSER", "parser_version": "sha256:8cc4c01b040f51ab07e28eff511946c7abd3371af509018b065da0fd07cedb9c",
            "semantic_support_classification": support, "nws": nws(*zones), "access_roads": [],
            "unresolved_optional_fields": ["access_roads"] + (["ranger_district"] if district == "UNKNOWN" else []),
            "coverage_limitations": limitations,
            "mapping_provenance": [provenance(USFS_INFRA + "/query", "Identity, managing_org and coordinate by exact USFS INFRA site_id"), provenance(page, "Official campground page identity and shared operational structure"), provenance(alerts, "Official forest alerts and fire-status source")],
        })
    return rows


def supported_non_usfs_rows() -> list[dict[str, object]]:
    rows = []
    for key, name, aliases, state, family, authority, park, native, lat, lon, coord_url, coord_claim, page, alerts, zones in NON_USFS:
        rows.append({
            "canonical_key": key, "canonical_name": name, "aliases": [name] + aliases, "state": state,
            "experimental_lane": family, "authority_family": family, "managing_authority": authority,
            "forest_park_project": park, "ranger_district_recreation_region_park_code": native.split(":", 1)[0],
            "authority_native_identifier": {"namespace": "NPS_CAMPGROUND_UUID" if family == "NPS" else "NC_PARK_OR_ACCESS_KEY", "value": native},
            "latitude": lat, "longitude": lon, "coordinate_provenance": coord_claim,
            "campground_status_page_url": page, "alerts_conditions_page_url": alerts,
            "fire_restriction_source_path": alerts, "parser_adapter_family": "NPSAdapter" if family == "NPS" else "NCStateParksAdapter",
            "parser_version": "shared-authority-adapter-v1.1", "semantic_support_classification": "GENERIC_AUTHORITY_ADAPTER",
            "nws": nws(*zones), "access_roads": [], "unresolved_optional_fields": ["access_roads"],
            "coverage_limitations": ["Generic authority adapter is conservative; ambiguous or non-campground notices remain UNKNOWN/non-relevant/non-notifying"] + (["Coordinate is an official park-level representative point, not asserted as a campsite centroid"] if "park-level" in coord_claim else []),
            "mapping_provenance": [provenance(coord_url, "Authority-native identity and " + coord_claim), provenance(page, "Official campground page and configured aliases"), provenance(alerts, "Official alerts/conditions source")],
        })
    return rows


def usace_rows() -> list[dict[str, object]]:
    return [{
        "canonical_key": key, "canonical_name": name, "aliases": [name, name.replace(" Campground", "")], "state": state,
        "experimental_lane": "USACE_TRANSPORT_CHALLENGE", "authority_family": "USACE", "managing_authority": "U.S. Army Corps of Engineers",
        "forest_park_project": project, "ranger_district_recreation_region_park_code": district,
        "authority_native_identifier": None, "latitude": None, "longitude": None,
        "coordinate_provenance": "UNKNOWN", "campground_status_page_url": None, "alerts_conditions_page_url": status_url,
        "fire_restriction_source_path": None, "parser_adapter_family": None, "parser_version": None,
        "semantic_support_classification": "TRANSPORT_CHALLENGE", "usace_transport_classification": "HOSTED_ACCESSIBLE_ADAPTER_NOT_IMPLEMENTED",
        "nws": None, "access_roads": [],
        "unresolved_optional_fields": ["authority_native_identifier", "coordinates", "campground_status_page_url", "fire_restriction_source_path", "access_roads", "NWS mapping"],
        "coverage_limitations": ["Transport/readiness identity observed, but no authorized semantic adapter exists", "Excluded from supported-core retrieval reliability denominator"],
        "mapping_provenance": [provenance(status_url, "Phase 4A-3 single bounded local probe returned HTTP 200 and contained the expected campground identity and operational-status structure")],
    } for key, name, state, district, project, status_url in USACE]


def main() -> None:
    rows = original_rows() + usfs_rows() + supported_non_usfs_rows() + usace_rows()
    artifact = {
        "schema_version": "campready-phase4a3-canonical-cohort-v1",
        "phase": "4A-3",
        "activation_status": "FROZEN_NOT_STARTED",
        "semantic_support_policy": "32-site supported core plus separately labelled 3-site USACE transport challenge lane",
        "rows": rows,
        "prospective_gates": {
            "duration_consecutive_days": 30, "activated": False, "supported_source_retrieval_reliability_minimum": 0.95,
            "parser_failure_maximum_exclusive": 0.05, "useful_coverage_minimum": 0.80, "unsafe_notification_candidates": 0,
            "real_notifications": 0, "human_maintenance_hours_per_week_preferred_maximum_exclusive": 2,
            "human_maintenance_kill_pivot_signal_hours_per_week": ">2-3", "maintenance_logged_contemporaneously": True,
            "site_specific_source_rescue": False,
            "consumer_actionable_classes": ["campground closure", "reopening/lift", "access alert", "official fire restriction/lift", "NWS severe-weather change"],
            "alert_leverage_reported": True, "zero_denominator_precision_reported_as": "UNDEFINED_NOT_100_PERCENT",
        },
        "safety_policy": {
            "send_real_notifications": False, "prospective_candidates": "REVIEW_ONLY", "safety_or_passability_claims": False,
            "first_accepted_observation": "BASELINE_ZERO_NOTIFY", "unknown_notifying": False,
            "retrieval_failure_is_status_change": False, "parser_failure_is_status_change": False,
            "disappearance_is_resolution": False, "source_conflicts_preserved": True,
            "scheduled_future_distinct_from_current": True, "relevance": "DETERMINISTIC_ONLY",
        },
    }
    OUTPUT.write_text(json.dumps(artifact, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")


if __name__ == "__main__":
    main()
