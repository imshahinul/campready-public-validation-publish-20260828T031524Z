from __future__ import annotations

import hashlib
import json
import re
import sys

from collections import Counter
from datetime import (
    date,
    datetime,
    timezone,
)
from pathlib import Path


FOREST = (
    "Chattahoochee-Oconee "
    "National Forest"
)

COHORT_DISTRICT = (
    "Chattooga River District"
)

EXPECTED_KEYS = {
    "andrews-cove",
    "low-gap",
    "upper-chattahoochee",
    "sarahs-creek",
    "tate-branch",
    "lake-russell",
    "lake-rabun",
    "willis-knob",
    "wildcat-1",
    "wildcat-2",
}

DISTRICTS = {
    "blue ridge ranger district": (
        "Blue Ridge Ranger District"
    ),
    "chattooga river ranger district": (
        "Chattooga River District"
    ),
    "chattooga river district": (
        "Chattooga River District"
    ),
    "conasauga ranger district": (
        "Conasauga Ranger District"
    ),
    "oconee ranger district": (
        "Oconee Ranger District"
    ),
}

INCLUDE_REASONS = {
    "EXACT_CAMPGROUND",
    "EXPLICIT_AFFECTED_SITE",
    "FOREST_WIDE",
    "DISTRICT_WIDE",
    "ACCESS_ROAD",
    "OFFICIAL_FIRE_RESTRICTION",
}

UNKNOWN_REASONS = {
    "ROAD_RELATION_UNPROVEN",
    "AMBIGUOUS_SCOPE",
}

EXCLUDE_REASONS = {
    "NO_CAMPGROUND_MATCH",
    "WRONG_CAMPGROUND",
    "WRONG_FOREST",
    "WRONG_DISTRICT",
    "GENERIC_EDUCATIONAL_NOTICE",
    "PRESCRIBED_FIRE_NO_OPERATIONAL_IMPACT",
    "OUTSIDE_V1_SCOPE",
}

EXPECTED_RELATIONSHIPS = {
    "include": 21,
    "unknown": 30,
    "exclude": 259,
    "total": 310,
}

EXPECTED_BASELINE_NOTIFY = 0
EXPECTED_NEW_EVENT_NOTIFY = 11


def norm(value):
    value = str(
        value or ""
    ).casefold()

    value = value.replace(
        "’",
        "'",
    )

    value = value.replace(
        "–",
        "-",
    )

    value = value.replace(
        "—",
        "-",
    )

    value = re.sub(
        r"[^a-z0-9#'/-]+",
        " ",
        value,
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def sha256_bytes(value):
    return hashlib.sha256(
        value
    ).hexdigest()


def month_date(value):
    if not value:
        return None

    try:
        return datetime.strptime(
            value,
            "%B %d, %Y",
        ).date()

    except Exception:
        return None


def lifecycle(
    start_value,
    end_value,
    reference_date,
):
    start = month_date(
        start_value
    )

    end = month_date(
        end_value
    )

    if (
        start is not None
        and start > reference_date
    ):
        return "SCHEDULED"

    if (
        end is not None
        and end < reference_date
    ):
        return (
            "EXPIRED_BY_EXPLICIT_DATE"
        )

    return "CURRENT_OR_UNBOUNDED"


def alias_in_text(
    text,
    aliases,
):
    haystack = (
        " "
        + norm(text)
        + " "
    )

    candidates = sorted(
        {
            norm(alias)
            for alias in aliases
            if norm(alias)
        },
        key=len,
        reverse=True,
    )

    for alias in candidates:
        needle = (
            " "
            + alias
            + " "
        )

        if needle in haystack:
            return alias

    return None


def event_text(event):
    return str(
        event.get(
            "text_excerpt",
            "",
        )
        or ""
    )


def body_before_metadata(event):
    text = event_text(
        event
    )

    marker = (
        text.casefold()
        .find(
            "alert start date:"
        )
    )

    if marker >= 0:
        text = text[
            :marker
        ]

    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    title = str(
        event.get(
            "title",
            "",
        )
    ).strip()

    if (
        lines
        and title
        and norm(
            lines[0]
        )
        == norm(title)
    ):
        lines = lines[1:]

    return "\n".join(
        lines
    )


def body_fragments(event):
    body = body_before_metadata(
        event
    )

    fragments = []

    for line in (
        body.splitlines()
    ):
        line = line.strip()

        if not line:
            continue

        parts = re.split(
            r"(?<=[.!?])\s+",
            line,
        )

        for part in parts:
            part = part.strip()

            if part:
                fragments.append(
                    part
                )

    return fragments


def parse_rec_sites_section(
    event,
):
    text = event_text(
        event
    )

    lines = [
        line.strip()
        for line in text.splitlines()
    ]

    start = None

    for index, line in enumerate(
        lines
    ):
        if (
            norm(line)
            == "rec sites affected"
        ):
            start = index + 1
            break

    if start is None:
        return []

    stop_labels = {
        "associated documents",
        "forest order",
        "maps",
        "images",
        "contact information",
        "alert start date",
        "alert end date",
        "order number",
    }

    values = []

    for line in lines[
        start:
    ]:
        line = line.strip()

        if not line:
            continue

        normalized = norm(
            line
        )

        if normalized in stop_labels:
            break

        values.append(
            line
        )

    return values


def affected_site_keys(
    event,
    sites,
):
    values = parse_rec_sites_section(
        event
    )

    joined = "\n".join(
        values
    )

    result = {}

    for key, site in (
        sites.items()
    ):
        matched = alias_in_text(
            joined,
            site.get(
                "aliases",
                [],
            ),
        )

        if matched:
            result[key] = matched

    return result


def named_districts(event):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    result = set()

    for needle, canonical in (
        DISTRICTS.items()
    ):
        if needle in text:
            result.add(
                canonical
            )

    return result


def wrong_forest_explicit(
    event,
):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    foreign_forests = [
        "cherokee national forest",
        "nantahala national forest",
        "pisgah national forest",
        "ocala national forest",
    ]

    own = norm(
        FOREST
    )

    foreign = any(
        value in text
        for value in foreign_forests
    )

    own_present = (
        own in text
    )

    return (
        foreign
        and not own_present
    )


def explicit_wrong_district(
    event,
):
    districts = named_districts(
        event
    )

    if not districts:
        return False

    if (
        COHORT_DISTRICT
        in districts
    ):
        return False

    return True


def educational_notice(
    event,
):
    title = norm(
        event.get(
            "title",
            "",
        )
    )

    text = norm(
        event_text(event)
    )

    title_markers = [
        "be bear-aware",
        "be bear aware",
        "waterfall dangers",
        "flash flood awareness",
        "wildfire prevention",
        "utv education",
        "call before you haul",
        "forest visitors",
    ]

    if any(
        marker in title
        for marker in title_markers
    ):
        return True

    if (
        "educate yourself"
        in text
        and
        "responsible outdoor recreation"
        in text
    ):
        return True

    return False


def general_rule_or_admin(
    event,
):
    title = norm(
        event.get(
            "title",
            "",
        )
    )

    markers = [
        "14 day camping limit",
        "forest supervisor's orders",
        "rules regulations and guidelines",
        "new phone number",
        "scan to pay",
    ]

    return any(
        marker in title
        for marker in markers
    )


def trail_only_event(
    event,
):
    title = norm(
        event.get(
            "title",
            "",
        )
    )

    if any(
        marker in title
        for marker in (
            "trailhead",
            "trail 12",
            "trail closed",
        )
    ):
        return True

    return False


def prescribed_fire(
    event,
):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    return (
        "prescribed fire"
        in text
        or
        "prescribed burn"
        in text
    )


def fire_restriction(
    event,
):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    return any(
        marker in text
        for marker in (
            "fire restriction",
            "campfire restriction",
            "campfires prohibited",
            "campfire prohibited",
            "fire restriction closure order",
        )
    )


def fire_resolution(
    event,
):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    restriction = (
        "restriction"
        in text
        or
        "campfire"
        in text
    )

    resolution = any(
        marker in text
        for marker in (
            "restriction lifted",
            "restrictions have been lifted",
            "restrictions lifted",
            "restriction terminated",
            "restrictions terminated",
            "termination of an order",
        )
    )

    return (
        restriction
        and resolution
    )


def forest_scope_explicit(
    event,
):
    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + event_text(event)
        )
    )

    forest_name = norm(
        FOREST
    )

    patterns = [
        "forest-wide",
        "forest wide",
        "all nfs lands",
        "all national forest system lands",
        (
            "throughout the "
            + forest_name
        ),
        (
            "entire "
            + forest_name
        ),
    ]

    if any(
        norm(pattern)
        in text
        for pattern in patterns
    ):
        return True

    if (
        fire_resolution(event)
        and forest_name
        in text
    ):
        return True

    return False


def broad_ambiguous_impact(
    event,
):
    title = norm(
        event.get(
            "title",
            "",
        )
    )

    text = norm(
        event_text(event)
    )

    if (
        "residual impacts from recent winter storms"
        in title
    ):
        return True

    if (
        "conditions can vary widely by location"
        in text
        and
        "recreation sites"
        in text
    ):
        return True

    return False


def road_operational_event(
    event,
):
    title = norm(
        event.get(
            "title",
            "",
        )
    )

    text = norm(
        (
            str(
                event.get(
                    "title",
                    "",
                )
            )
            + "\n"
            + body_before_metadata(
                event
            )
        )
    )

    road_present = any(
        marker in text
        for marker in (
            " road ",
            "roads ",
            "forest service road",
            "fsr ",
        )
    )

    operational = any(
        marker in text
        for marker in (
            "road closure",
            "road closed",
            "temporarily closed",
            "temporary closure",
            "reopens",
            "reopened",
            "will be closed",
            "remain closed",
            "closed to through traffic",
            "restricted access",
            "no access",
            "impassable",
            "road damage",
            "land slides",
            "landslides",
            "heavy equipment",
            "delays",
            "detours",
        )
    )

    if (
        "utv education"
        in title
        or educational_notice(
            event
        )
    ):
        return False

    return (
        road_present
        and operational
    )


def site_action(
    event,
    site,
):
    aliases = site.get(
        "aliases",
        [],
    )

    relevant_fragments = []
    amenity_fragments = []

    status_terms = [
        "has reopened",
        "have reopened",
        "reopened",
        "now open",
        "currently open",
        "temporarily closed",
        "campground closed",
        "campground is closed",
        "will close",
        "will be closed",
        "closure",
        "closed due to",
        "closed until",
        "no safe access",
    ]

    amenity_terms = [
        "drinking water",
        "water system",
        "potable water",
        "well system",
        "water unavailable",
        "restroom",
        "toilet",
        "amenity",
        "scan to pay",
        "payment",
    ]

    for fragment in body_fragments(
        event
    ):
        matched = alias_in_text(
            fragment,
            aliases,
        )

        if not matched:
            continue

        nf = norm(
            fragment
        )

        status = any(
            term in nf
            for term in status_terms
        )

        amenity = any(
            term in nf
            for term in amenity_terms
        )

        if status:
            relevant_fragments.append(
                fragment
            )

        elif amenity:
            amenity_fragments.append(
                fragment
            )

    if relevant_fragments:
        return {
            "kind": "V1_STATUS_OR_ACCESS",
            "evidence": (
                relevant_fragments
            ),
        }

    if amenity_fragments:
        return {
            "kind": "OUTSIDE_V1_AMENITY",
            "evidence": (
                amenity_fragments
            ),
        }

    return {
        "kind": "NONE",
        "evidence": [],
    }


def access_road_matches(
    event,
    site,
):
    text = (
        str(
            event.get(
                "title",
                "",
            )
        )
        + "\n"
        + body_before_metadata(
            event
        )
    )

    matches = []

    for road in site.get(
        "access_roads",
        [],
    ):
        identifier = road.get(
            "identifier"
        )

        if (
            identifier
            and alias_in_text(
                text,
                [identifier],
            )
        ):
            matches.append(
                identifier
            )

    return sorted(
        set(matches)
    )


def operational_effective_date(
    event,
):
    text = (
        str(
            event.get(
                "title",
                "",
            )
        )
        + "\n"
        + body_before_metadata(
            event
        )
    )

    match = re.search(
        r"\bbeginning\s+"
        r"(January|February|March|April|May|June|"
        r"July|August|September|October|November|December)"
        r"\s+(\d{1,2})\b",
        text,
        flags=re.I,
    )

    if not match:
        return None

    start = month_date(
        event.get(
            "start_date"
        )
    )

    if start is None:
        return None

    month = (
        match.group(1)
        .capitalize()
    )

    day = int(
        match.group(2)
    )

    return (
        f"{month} {day}, "
        f"{start.year}"
    )


def make_relation(
    relevance,
    reason,
    life,
    evidence,
    baseline_mode=True,
    resolution=False,
):
    current_notify = (
        relevance == "INCLUDE"
        and life
        == "CURRENT_OR_UNBOUNDED"
    )

    baseline_notify = (
        False
        if baseline_mode
        else current_notify
    )

    return {
        "relevance": relevance,
        "reason": reason,
        "lifecycle": life,
        "baseline_notify": (
            baseline_notify
        ),
        "new_event_notify": (
            current_notify
        ),
        "resolution": (
            bool(resolution)
        ),
        "evidence": evidence,
    }


def classify_event(
    event,
    sites,
    reference_date,
):
    life = lifecycle(
        event.get(
            "start_date"
        ),
        event.get(
            "end_date"
        ),
        reference_date,
    )

    affected = affected_site_keys(
        event,
        sites,
    )

    actions = {
        key: site_action(
            event,
            site,
        )
        for key, site
        in sites.items()
    }

    result = {}

    # R9: educational first.
    if educational_notice(
        event
    ):
        for key in sites:
            result[key] = make_relation(
                "EXCLUDE",
                "GENERIC_EDUCATIONAL_NOTICE",
                life,
                "educational/awareness content",
            )

        return result

    # R1: V1 eligibility before geography.
    if (
        general_rule_or_admin(
            event
        )
        or trail_only_event(
            event
        )
    ):
        for key in sites:
            result[key] = make_relation(
                "EXCLUDE",
                "OUTSIDE_V1_SCOPE",
                life,
                (
                    "official item does not represent "
                    "a frozen V1 trip-change event type"
                ),
            )

        return result

    # Fire resolution before normal fire restriction.
    if fire_resolution(
        event
    ):
        if forest_scope_explicit(
            event
        ):
            for key in sites:
                result[key] = make_relation(
                    "INCLUDE",
                    "OFFICIAL_FIRE_RESTRICTION",
                    life,
                    (
                        "forest-wide official fire "
                        "restriction resolution"
                    ),
                    resolution=True,
                )

        else:
            for key in sites:
                result[key] = make_relation(
                    "UNKNOWN",
                    "AMBIGUOUS_SCOPE",
                    life,
                    (
                        "fire restriction resolution "
                        "without deterministic scope"
                    ),
                    resolution=True,
                )

        return result

    if fire_restriction(
        event
    ):
        if forest_scope_explicit(
            event
        ):
            for key in sites:
                result[key] = make_relation(
                    "INCLUDE",
                    "OFFICIAL_FIRE_RESTRICTION",
                    life,
                    (
                        "official forest-wide "
                        "fire restriction"
                    ),
                )

        else:
            for key in sites:
                result[key] = make_relation(
                    "UNKNOWN",
                    "AMBIGUOUS_SCOPE",
                    life,
                    (
                        "fire restriction without "
                        "deterministic frozen scope"
                    ),
                )

        return result

    # Mixed/site-specific action evaluation.
    relevant_sites = {
        key
        for key, value
        in actions.items()
        if value["kind"]
        == "V1_STATUS_OR_ACCESS"
    }

    amenity_only_sites = {
        key
        for key, value
        in actions.items()
        if value["kind"]
        == "OUTSIDE_V1_AMENITY"
    }

    if (
        relevant_sites
        or amenity_only_sites
    ):
        for key in sites:
            if key in relevant_sites:
                result[key] = make_relation(
                    "INCLUDE",
                    "EXACT_CAMPGROUND",
                    life,
                    actions[key][
                        "evidence"
                    ],
                )

            elif key in amenity_only_sites:
                result[key] = make_relation(
                    "EXCLUDE",
                    "OUTSIDE_V1_SCOPE",
                    life,
                    actions[key][
                        "evidence"
                    ],
                )

            elif (
                affected
                and key in affected
            ):
                result[key] = make_relation(
                    "EXCLUDE",
                    "OUTSIDE_V1_SCOPE",
                    life,
                    (
                        "official affected-site listing "
                        "without a frozen V1 impact "
                        "for this campground"
                    ),
                )

            else:
                result[key] = make_relation(
                    "EXCLUDE",
                    "WRONG_CAMPGROUND",
                    life,
                    (
                        "site-specific alert does not "
                        "establish V1 impact for site"
                    ),
                )

        return result

    # Prescribed fire with no explicit campground V1 impact.
    if prescribed_fire(
        event
    ):
        for key in sites:
            result[key] = make_relation(
                "EXCLUDE",
                "PRESCRIBED_FIRE_NO_OPERATIONAL_IMPACT",
                life,
                (
                    "prescribed-fire notice without "
                    "deterministic campground V1 impact"
                ),
            )

        return result

    # R12 broad unresolved impacts remain UNKNOWN.
    if broad_ambiguous_impact(
        event
    ):
        for key in sites:
            result[key] = make_relation(
                "UNKNOWN",
                "AMBIGUOUS_SCOPE",
                life,
                (
                    "broad operational impacts with "
                    "location-specific effect unresolved"
                ),
            )

        return result

    # Road/access event.
    if road_operational_event(
        event
    ):
        # R7: explicit foreign authority first.
        if wrong_forest_explicit(
            event
        ):
            for key in sites:
                result[key] = make_relation(
                    "EXCLUDE",
                    "WRONG_FOREST",
                    life,
                    (
                        "official road event explicitly "
                        "belongs to another forest"
                    ),
                )

            return result

        if explicit_wrong_district(
            event
        ):
            for key in sites:
                result[key] = make_relation(
                    "EXCLUDE",
                    "WRONG_DISTRICT",
                    life,
                    (
                        "official road event explicitly "
                        "belongs to another ranger district"
                    ),
                )

            return result

        any_match = False

        road_matches = {}

        for key, site in (
            sites.items()
        ):
            matches = access_road_matches(
                event,
                site,
            )

            road_matches[key] = matches

            if matches:
                any_match = True

        for key in sites:
            matches = road_matches[
                key
            ]

            if matches:
                result[key] = make_relation(
                    "INCLUDE",
                    "ACCESS_ROAD",
                    life,
                    (
                        "official road event matches "
                        + ", ".join(
                            matches
                        )
                    ),
                )

            else:
                # R8: only truly in-scope unresolved
                # road relationships become UNKNOWN.
                result[key] = make_relation(
                    "UNKNOWN",
                    "ROAD_RELATION_UNPROVEN",
                    life,
                    (
                        "potentially in-scope road event "
                        "but campground-road relationship "
                        "is not frozen"
                    ),
                )

        return result

    # Other unaffected content.
    for key in sites:
        result[key] = make_relation(
            "EXCLUDE",
            "NO_CAMPGROUND_MATCH",
            life,
            (
                "no deterministic frozen V1 "
                "campground relationship"
            ),
        )

    return result


def one_event(
    events,
    fragment,
):
    needle = fragment.casefold()

    matches = [
        event
        for event in events
        if needle
        in str(
            event.get(
                "title",
                "",
            )
        ).casefold()
    ]

    if len(matches) != 1:
        return None

    return matches[0]


def relation_sets(
    event,
):
    relations = event.get(
        "corrected_relations",
        {},
    )

    return {
        state: {
            key
            for key, value
            in relations.items()
            if value[
                "relevance"
            ]
            == state
        }
        for state in (
            "INCLUDE",
            "UNKNOWN",
            "EXCLUDE",
        )
    }


def reason_for(
    event,
    key,
):
    return (
        event.get(
            "corrected_relations",
            {},
        )
        .get(
            key,
            {},
        )
        .get(
            "reason"
        )
    )


def check(
    name,
    condition,
    observed,
):
    return {
        "name": name,
        "pass": bool(
            condition
        ),
        "observed": observed,
    }


def main():
    mapping_path = Path(
        sys.argv[1]
    )

    live_path = Path(
        sys.argv[2]
    )

    r1_path = Path(
        sys.argv[3]
    )

    report_path = Path(
        sys.argv[4]
    )

    mapping_raw = (
        mapping_path.read_bytes()
    )

    live_raw = (
        live_path.read_bytes()
    )

    r1_raw = (
        r1_path.read_bytes()
    )

    mapping = json.loads(
        mapping_raw.decode(
            "utf-8"
        )
    )

    live = json.loads(
        live_raw.decode(
            "utf-8"
        )
    )

    r1 = json.loads(
        r1_raw.decode(
            "utf-8"
        )
    )

    rows = mapping.get(
        "rows",
        [],
    )

    sites = {
        row["canonical_key"]: row
        for row in rows
    }

    mapping_gate = (
        mapping.get(
            "decision"
        )
        == (
            "PASS_CANONICAL_10_SITE_"
            "RELEVANCE_MAPPING"
        )
        and len(rows) == 10
        and set(
            sites.keys()
        )
        == EXPECTED_KEYS
    )

    live_gate = (
        live.get(
            "decision"
        )
        == "PASS_LIVE_USFS_RELEVANCE_REPLAY"
        and len(
            live.get(
                "events",
                [],
            )
        )
        == 31
    )

    r1_gate = (
        r1.get(
            "decision"
        )
        == (
            "PASS_LIVE_REPLAY_"
            "ADJUDICATION_FREEZE"
        )
        and r1.get(
            "phase3a4_relevance_disposition"
        )
        == (
            "HOLD_CORRECTABLE_LIVE_RELEVANCE"
        )
    )

    validated = live.get(
        "validated_at_utc"
    )

    try:
        reference_date = (
            datetime.fromisoformat(
                validated.replace(
                    "Z",
                    "+00:00",
                )
            ).date()
        )

    except Exception:
        reference_date = date(
            2026,
            8,
            28,
        )

    events = []

    reason_counts = Counter()

    include_count = 0
    unknown_count = 0
    exclude_count = 0

    baseline_notify_count = 0
    new_event_notify_count = 0

    vocabulary_ok = True
    invalid_reasons = []

    lifecycle_notify_ok = True

    for source_event in (
        live.get(
            "events",
            [],
        )
    ):
        event = dict(
            source_event
        )

        corrected = classify_event(
            event,
            sites,
            reference_date,
        )

        event[
            "corrected_relations"
        ] = corrected

        event[
            "parsed_rec_sites"
        ] = parse_rec_sites_section(
            event
        )

        event[
            "parsed_affected_site_keys"
        ] = sorted(
            affected_site_keys(
                event,
                sites,
            ).keys()
        )

        event[
            "operational_effective_date"
        ] = operational_effective_date(
            event
        )

        event[
            "corrected_lifecycle"
        ] = lifecycle(
            event.get(
                "start_date"
            ),
            event.get(
                "end_date"
            ),
            reference_date,
        )

        event[
            "corrected_include_keys"
        ] = sorted(
            key
            for key, relation
            in corrected.items()
            if relation[
                "relevance"
            ]
            == "INCLUDE"
        )

        event[
            "corrected_unknown_keys"
        ] = sorted(
            key
            for key, relation
            in corrected.items()
            if relation[
                "relevance"
            ]
            == "UNKNOWN"
        )

        event[
            "corrected_exclude_keys"
        ] = sorted(
            key
            for key, relation
            in corrected.items()
            if relation[
                "relevance"
            ]
            == "EXCLUDE"
        )

        event[
            "baseline_notify_keys"
        ] = sorted(
            key
            for key, relation
            in corrected.items()
            if relation[
                "baseline_notify"
            ]
        )

        event[
            "new_event_notify_keys"
        ] = sorted(
            key
            for key, relation
            in corrected.items()
            if relation[
                "new_event_notify"
            ]
        )

        include_count += len(
            event[
                "corrected_include_keys"
            ]
        )

        unknown_count += len(
            event[
                "corrected_unknown_keys"
            ]
        )

        exclude_count += len(
            event[
                "corrected_exclude_keys"
            ]
        )

        baseline_notify_count += len(
            event[
                "baseline_notify_keys"
            ]
        )

        new_event_notify_count += len(
            event[
                "new_event_notify_keys"
            ]
        )

        for key, relation in (
            corrected.items()
        ):
            relevance = relation[
                "relevance"
            ]

            reason = relation[
                "reason"
            ]

            reason_counts[
                reason
            ] += 1

            if relevance == "INCLUDE":
                allowed = (
                    INCLUDE_REASONS
                )

            elif relevance == "UNKNOWN":
                allowed = (
                    UNKNOWN_REASONS
                )

            else:
                allowed = (
                    EXCLUDE_REASONS
                )

            if reason not in allowed:
                vocabulary_ok = False

                invalid_reasons.append({
                    "event": (
                        event.get(
                            "title"
                        )
                    ),
                    "campground": key,
                    "relevance": (
                        relevance
                    ),
                    "reason": reason,
                })

            if (
                relation[
                    "new_event_notify"
                ]
                and relation[
                    "lifecycle"
                ]
                != "CURRENT_OR_UNBOUNDED"
            ):
                lifecycle_notify_ok = False

        events.append(
            event
        )

    total = (
        include_count
        + unknown_count
        + exclude_count
    )

    accounting_ok = (
        total
        == EXPECTED_RELATIONSHIPS[
            "total"
        ]
    )

    aggregate_gate = (
        include_count
        == EXPECTED_RELATIONSHIPS[
            "include"
        ]
        and unknown_count
        == EXPECTED_RELATIONSHIPS[
            "unknown"
        ]
        and exclude_count
        == EXPECTED_RELATIONSHIPS[
            "exclude"
        ]
        and total
        == EXPECTED_RELATIONSHIPS[
            "total"
        ]
    )

    baseline_gate = (
        baseline_notify_count
        == EXPECTED_BASELINE_NOTIFY
    )

    new_event_notify_gate = (
        new_event_notify_count
        == EXPECTED_NEW_EVENT_NOTIFY
    )

    checks = []

    checks.append(
        check(
            "mapping gate",
            mapping_gate,
            mapping_gate,
        )
    )

    checks.append(
        check(
            "captured live replay gate",
            live_gate,
            live_gate,
        )
    )

    checks.append(
        check(
            "R1 adjudication gate",
            r1_gate,
            r1_gate,
        )
    )

    checks.append(
        check(
            "relationship aggregate",
            aggregate_gate,
            {
                "include": (
                    include_count
                ),
                "unknown": (
                    unknown_count
                ),
                "exclude": (
                    exclude_count
                ),
                "total": total,
            },
        )
    )

    checks.append(
        check(
            "baseline notification zero",
            baseline_gate,
            baseline_notify_count,
        )
    )

    checks.append(
        check(
            "new-event notification eligibility",
            new_event_notify_gate,
            new_event_notify_count,
        )
    )

    checks.append(
        check(
            "reason vocabulary",
            vocabulary_ok,
            invalid_reasons,
        )
    )

    checks.append(
        check(
            "expired/scheduled notification guard",
            lifecycle_notify_ok,
            lifecycle_notify_ok,
        )
    )

    fourteen = one_event(
        events,
        "14 Day Camping Limit",
    )

    lifted = one_event(
        events,
        "Campfire restriction lifted",
    )

    update = one_event(
        events,
        "Campground Updates:",
    )

    spring = one_event(
        events,
        "Spring 2026 Forest-wide Fire Restrictions",
    )

    utv = one_event(
        events,
        "UTV Education for Forest Visitors",
    )

    fsr55 = one_event(
        events,
        "Grassy Gap - Horseshoe Ridge Road",
    )

    duncan = one_event(
        events,
        "Duncan Ridge Road reopens",
    )

    old_ccc = one_event(
        events,
        "Old CCC Camp Road",
    )

    piney = one_event(
        events,
        "Forest Service Road 76",
    )

    chattooga = one_event(
        events,
        "Temporary Road Closure Alert",
    )

    residual = one_event(
        events,
        "Residual Impacts from Recent Winter Storms",
    )

    required_events = {
        "14_day": fourteen,
        "fire_lifted": lifted,
        "campground_update": update,
        "spring_fire": spring,
        "utv": utv,
        "fsr55": fsr55,
        "duncan": duncan,
        "old_ccc": old_ccc,
        "piney": piney,
        "chattooga_road": chattooga,
        "residual": residual,
    }

    for name, value in (
        required_events.items()
    ):
        checks.append(
            check(
                "required adjudicated event: "
                + name,
                value is not None,
                (
                    value.get(
                        "title"
                    )
                    if value
                    else None
                ),
            )
        )

    if fourteen:
        sets = relation_sets(
            fourteen
        )

        checks.append(
            check(
                "R1 14-day rule excluded",
                (
                    sets[
                        "EXCLUDE"
                    ]
                    == EXPECTED_KEYS
                    and all(
                        reason_for(
                            fourteen,
                            key,
                        )
                        == "OUTSIDE_V1_SCOPE"
                        for key
                        in EXPECTED_KEYS
                    )
                ),
                {
                    "include": sorted(
                        sets[
                            "INCLUDE"
                        ]
                    ),
                    "unknown": sorted(
                        sets[
                            "UNKNOWN"
                        ]
                    ),
                    "exclude_count": len(
                        sets[
                            "EXCLUDE"
                        ]
                    ),
                },
            )
        )

    if lifted:
        sets = relation_sets(
            lifted
        )

        resolutions = all(
            lifted[
                "corrected_relations"
            ][key][
                "resolution"
            ]
            for key
            in EXPECTED_KEYS
        )

        checks.append(
            check(
                "R4 forest-wide fire resolution",
                (
                    sets[
                        "INCLUDE"
                    ]
                    == EXPECTED_KEYS
                    and not sets[
                        "UNKNOWN"
                    ]
                    and resolutions
                    and not lifted[
                        "baseline_notify_keys"
                    ]
                ),
                {
                    "include_count": len(
                        sets[
                            "INCLUDE"
                        ]
                    ),
                    "resolution": (
                        resolutions
                    ),
                    "baseline_notify": (
                        lifted[
                            "baseline_notify_keys"
                        ]
                    ),
                },
            )
        )

    if update:
        sets = relation_sets(
            update
        )

        affected = set(
            update[
                "parsed_affected_site_keys"
            ]
        )

        checks.append(
            check(
                "R6 multi-value affected-site parser",
                affected
                == {
                    "low-gap",
                    "upper-chattahoochee",
                    "andrews-cove",
                },
                sorted(
                    affected
                ),
            )
        )

        checks.append(
            check(
                "R5 mixed-topic campground actions",
                (
                    sets[
                        "INCLUDE"
                    ]
                    == {
                        "low-gap"
                    }
                    and reason_for(
                        update,
                        "upper-chattahoochee",
                    )
                    == "OUTSIDE_V1_SCOPE"
                    and reason_for(
                        update,
                        "andrews-cove",
                    )
                    == "OUTSIDE_V1_SCOPE"
                ),
                {
                    "include": sorted(
                        sets[
                            "INCLUDE"
                        ]
                    ),
                    "upper_reason": (
                        reason_for(
                            update,
                            "upper-chattahoochee",
                        )
                    ),
                    "andrews_reason": (
                        reason_for(
                            update,
                            "andrews-cove",
                        )
                    ),
                },
            )
        )

    if spring:
        sets = relation_sets(
            spring
        )

        checks.append(
            check(
                "R3 expired fire restriction non-notifying",
                (
                    sets[
                        "INCLUDE"
                    ]
                    == EXPECTED_KEYS
                    and spring[
                        "corrected_lifecycle"
                    ]
                    == "EXPIRED_BY_EXPLICIT_DATE"
                    and not spring[
                        "baseline_notify_keys"
                    ]
                    and not spring[
                        "new_event_notify_keys"
                    ]
                ),
                {
                    "lifecycle": (
                        spring[
                            "corrected_lifecycle"
                        ]
                    ),
                    "baseline_notify": (
                        spring[
                            "baseline_notify_keys"
                        ]
                    ),
                    "new_event_notify": (
                        spring[
                            "new_event_notify_keys"
                        ]
                    ),
                },
            )
        )

    if utv:
        sets = relation_sets(
            utv
        )

        checks.append(
            check(
                "R9 UTV education excluded",
                (
                    sets[
                        "EXCLUDE"
                    ]
                    == EXPECTED_KEYS
                    and all(
                        reason_for(
                            utv,
                            key,
                        )
                        == (
                            "GENERIC_EDUCATIONAL_NOTICE"
                        )
                        for key
                        in EXPECTED_KEYS
                    )
                ),
                {
                    "unknown": sorted(
                        sets[
                            "UNKNOWN"
                        ]
                    ),
                    "exclude_count": len(
                        sets[
                            "EXCLUDE"
                        ]
                    ),
                },
            )
        )

    for label, event in (
        (
            "Blue Ridge FSR55",
            fsr55,
        ),
        (
            "Duncan Ridge",
            duncan,
        ),
        (
            "Old CCC Conasauga",
            old_ccc,
        ),
    ):
        if event:
            sets = relation_sets(
                event
            )

            checks.append(
                check(
                    "R7 foreign-district exclusion: "
                    + label,
                    (
                        sets[
                            "EXCLUDE"
                        ]
                        == EXPECTED_KEYS
                        and all(
                            reason_for(
                                event,
                                key,
                            )
                            == "WRONG_DISTRICT"
                            for key
                            in EXPECTED_KEYS
                        )
                    ),
                    {
                        "unknown": sorted(
                            sets[
                                "UNKNOWN"
                            ]
                        ),
                        "exclude_count": len(
                            sets[
                                "EXCLUDE"
                            ]
                        ),
                    },
                )
            )

    if old_ccc:
        checks.append(
            check(
                "R10 operational effective date",
                old_ccc[
                    "operational_effective_date"
                ]
                == "September 8, 2026",
                old_ccc[
                    "operational_effective_date"
                ],
            )
        )

    if piney:
        sets = relation_sets(
            piney
        )

        checks.append(
            check(
                "R8 Piney road remains unresolved",
                (
                    sets[
                        "UNKNOWN"
                    ]
                    == EXPECTED_KEYS
                    and all(
                        reason_for(
                            piney,
                            key,
                        )
                        == (
                            "ROAD_RELATION_UNPROVEN"
                        )
                        for key
                        in EXPECTED_KEYS
                    )
                    and not piney[
                        "baseline_notify_keys"
                    ]
                    and not piney[
                        "new_event_notify_keys"
                    ]
                ),
                {
                    "unknown_count": len(
                        sets[
                            "UNKNOWN"
                        ]
                    ),
                    "lifecycle": (
                        piney[
                            "corrected_lifecycle"
                        ]
                    ),
                    "notify": (
                        piney[
                            "new_event_notify_keys"
                        ]
                    ),
                },
            )
        )

    if chattooga:
        sets = relation_sets(
            chattooga
        )

        checks.append(
            check(
                "R8 Chattooga road remains unresolved",
                (
                    sets[
                        "UNKNOWN"
                    ]
                    == EXPECTED_KEYS
                    and all(
                        reason_for(
                            chattooga,
                            key,
                        )
                        == (
                            "ROAD_RELATION_UNPROVEN"
                        )
                        for key
                        in EXPECTED_KEYS
                    )
                ),
                {
                    "unknown_count": len(
                        sets[
                            "UNKNOWN"
                        ]
                    ),
                },
            )
        )

    if residual:
        sets = relation_sets(
            residual
        )

        checks.append(
            check(
                "R12 residual-impact ambiguity preserved",
                (
                    sets[
                        "UNKNOWN"
                    ]
                    == EXPECTED_KEYS
                    and all(
                        reason_for(
                            residual,
                            key,
                        )
                        == "AMBIGUOUS_SCOPE"
                        for key
                        in EXPECTED_KEYS
                    )
                ),
                {
                    "unknown_count": len(
                        sets[
                            "UNKNOWN"
                        ]
                    ),
                },
            )
        )

    all_checks_pass = all(
        item["pass"]
        for item in checks
    )

    # Relationship dedup/accounting.
    relationship_keys = []

    for event in events:
        event_id = event.get(
            "event_id"
        )

        for key in (
            event[
                "corrected_relations"
            ]
        ):
            relationship_keys.append(
                (
                    event_id,
                    key,
                )
            )

    relationship_dedup_ok = (
        len(
            relationship_keys
        )
        == len(
            set(
                relationship_keys
            )
        )
    )

    source_ids = [
        event.get(
            "event_id"
        )
        for event in events
    ]

    source_dedup_ok = (
        len(source_ids)
        == len(
            set(
                source_ids
            )
        )
    )

    unknown_pct = (
        round(
            100
            * unknown_count
            / total,
            2,
        )
        if total
        else 0.0
    )

    original_unknown = (
        live.get(
            "summary",
            {}
        )
        .get(
            "unknown_relationship_count"
        )
    )

    original_unknown_pct = (
        live.get(
            "summary",
            {}
        )
        .get(
            "unknown_relationship_pct"
        )
    )

    if (
        mapping_gate
        and live_gate
        and r1_gate
        and all_checks_pass
        and aggregate_gate
        and baseline_gate
        and new_event_notify_gate
        and vocabulary_ok
        and lifecycle_notify_ok
        and accounting_ok
        and relationship_dedup_ok
        and source_dedup_ok
    ):
        decision = (
            "PASS_CORRECTED_LIVE_"
            "RELEVANCE_REPLAY"
        )
        rc = 0

    else:
        decision = (
            "HOLD_CORRECTED_LIVE_"
            "RELEVANCE_REPLAY"
        )
        rc = 2

    event_summaries = []

    for event in events:
        event_summaries.append({
            "event_id": (
                event.get(
                    "event_id"
                )
            ),
            "title": (
                event.get(
                    "title"
                )
            ),
            "start_date": (
                event.get(
                    "start_date"
                )
            ),
            "end_date": (
                event.get(
                    "end_date"
                )
            ),
            "corrected_lifecycle": (
                event[
                    "corrected_lifecycle"
                ]
            ),
            "operational_effective_date": (
                event[
                    "operational_effective_date"
                ]
            ),
            "parsed_rec_sites": (
                event[
                    "parsed_rec_sites"
                ]
            ),
            "parsed_affected_site_keys": (
                event[
                    "parsed_affected_site_keys"
                ]
            ),
            "include_keys": (
                event[
                    "corrected_include_keys"
                ]
            ),
            "unknown_keys": (
                event[
                    "corrected_unknown_keys"
                ]
            ),
            "exclude_keys": (
                event[
                    "corrected_exclude_keys"
                ]
            ),
            "baseline_notify_keys": (
                event[
                    "baseline_notify_keys"
                ]
            ),
            "new_event_notify_keys": (
                event[
                    "new_event_notify_keys"
                ]
            ),
            "reason_counts": dict(
                Counter(
                    relation[
                        "reason"
                    ]
                    for relation
                    in event[
                        "corrected_relations"
                    ].values()
                )
            ),
        })

    report = {
        "validated_at_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        "reference_date": (
            reference_date.isoformat()
        ),

        "inputs": {
            "mapping_path": (
                str(
                    mapping_path
                )
            ),
            "mapping_sha256": (
                sha256_bytes(
                    mapping_raw
                )
            ),
            "live_replay_path": (
                str(
                    live_path
                )
            ),
            "live_replay_sha256": (
                sha256_bytes(
                    live_raw
                )
            ),
            "adjudication_path": (
                str(
                    r1_path
                )
            ),
            "adjudication_sha256": (
                sha256_bytes(
                    r1_raw
                )
            ),
        },

        "gates": {
            "mapping_gate": (
                mapping_gate
            ),
            "captured_live_gate": (
                live_gate
            ),
            "r1_adjudication_gate": (
                r1_gate
            ),
            "source_event_dedup_ok": (
                source_dedup_ok
            ),
            "relationship_dedup_ok": (
                relationship_dedup_ok
            ),
            "relationship_accounting_ok": (
                accounting_ok
            ),
            "reason_vocabulary_ok": (
                vocabulary_ok
            ),
            "lifecycle_notification_guard_ok": (
                lifecycle_notify_ok
            ),
            "all_adjudication_checks_pass": (
                all_checks_pass
            ),
        },

        "contract_guards": {
            "network_requests": 0,
            "new_source_families": 0,
            "source_discovery": False,
            "ai_only_relevance": False,
            "proximity_relevance": False,
            "baseline_ingestion": True,
            "baseline_notifications_allowed": False,
            "expired_current_notifications_allowed": False,
            "scheduled_notifications_allowed": False,
        },

        "summary": {
            "event_count": (
                len(events)
            ),
            "relationship_count": (
                total
            ),
            "include_relationship_count": (
                include_count
            ),
            "unknown_relationship_count": (
                unknown_count
            ),
            "exclude_relationship_count": (
                exclude_count
            ),
            "unknown_relationship_pct": (
                unknown_pct
            ),
            "baseline_notify_relationship_count": (
                baseline_notify_count
            ),
            "new_event_notify_eligible_relationship_count": (
                new_event_notify_count
            ),
            "original_3a4_unknown_relationship_count": (
                original_unknown
            ),
            "original_3a4_unknown_relationship_pct": (
                original_unknown_pct
            ),
            "unknown_relationship_reduction_count": (
                (
                    original_unknown
                    - unknown_count
                )
                if isinstance(
                    original_unknown,
                    int,
                )
                else None
            ),
            "reason_counts": dict(
                reason_counts
            ),
            "adjudication_check_score": (
                str(
                    sum(
                        item[
                            "pass"
                        ]
                        for item
                        in checks
                    )
                )
                + "/"
                + str(
                    len(checks)
                )
            ),
        },

        "checks": checks,

        "events": (
            event_summaries
        ),

        "decision": decision,

        "next_scope_if_pass": (
            "Phase 3A live-relevance logic may be frozen. "
            "Next checkpoint should define prospective "
            "baseline/change-detection semantics before "
            "starting the unattended monitoring cycle."
        ),
    }

    report_path.write_text(
        json.dumps(
            report,
            indent=2,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    print(
        "CampReady Phase 3A-4R2:",
        decision,
    )

    print(
        "Reference date:",
        reference_date,
    )

    print(
        "Events:",
        len(events),
    )

    print(
        "Relationships:",
        total,
    )

    print(
        "INCLUDE:",
        include_count,
    )

    print(
        "UNKNOWN:",
        unknown_count,
        f"({unknown_pct}%)",
    )

    print(
        "EXCLUDE:",
        exclude_count,
    )

    print(
        "Baseline notify:",
        baseline_notify_count,
    )

    print(
        "New-event notify eligible:",
        new_event_notify_count,
    )

    print(
        "Adjudication checks:",
        sum(
            item["pass"]
            for item in checks
        ),
        "/",
        len(checks),
    )

    print(
        "Reason vocabulary:",
        vocabulary_ok,
    )

    print(
        "Lifecycle notify guard:",
        lifecycle_notify_ok,
    )

    print()

    for item in checks:
        print(
            "PASS"
            if item["pass"]
            else "FAIL",
            item["name"],
        )

    print()

    for event in events:
        print(
            "EVENT:",
            event.get(
                "title"
            ),
        )

        print(
            "  lifecycle:",
            event[
                "corrected_lifecycle"
            ],
        )

        print(
            "  operational effective:",
            event[
                "operational_effective_date"
            ],
        )

        print(
            "  affected sites:",
            event[
                "parsed_affected_site_keys"
            ],
        )

        print(
            "  INCLUDE:",
            event[
                "corrected_include_keys"
            ],
        )

        print(
            "  UNKNOWN:",
            event[
                "corrected_unknown_keys"
            ],
        )

        print(
            "  baseline notify:",
            event[
                "baseline_notify_keys"
            ],
        )

        print(
            "  new-event notify:",
            event[
                "new_event_notify_keys"
            ],
        )

        print()

    return rc


raise SystemExit(
    main()
)
