from __future__ import annotations

import hashlib
import html
import json
import math
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request

from collections import Counter
from datetime import (
    date,
    datetime,
    timezone,
)
from html.parser import HTMLParser
from pathlib import Path


UA = (
    "CampReady-Phase3A4-Audit/0.1 "
    "(official USFS live relevance replay)"
)

ALERT_INDEX = (
    "https://www.fs.usda.gov/"
    "r08/chattahoochee-oconee/alerts"
)

ALERT_PREFIX = (
    "/r08/chattahoochee-oconee/alerts/"
)

FOREST = (
    "Chattahoochee-Oconee "
    "National Forest"
)

DISTRICT = (
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
    "SOURCE_FETCH_FAILED",
    "SOURCE_PARSE_FAILED",
}

EXCLUDE_REASONS = {
    "WRONG_CAMPGROUND",
    "GENERIC_EDUCATIONAL_NOTICE",
    "PRESCRIBED_FIRE_NO_OPERATIONAL_IMPACT",
    "OUTSIDE_V1_SCOPE",
    "NO_CAMPGROUND_MATCH",
}


def normalize(value):
    value = html.unescape(
        str(value or "")
    )

    value = value.replace(
        "’",
        "'",
    )

    value = value.casefold()

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


def compact(value):
    value = html.unescape(
        str(value or "")
    )

    return re.sub(
        r"\s+",
        " ",
        value,
    ).strip()


def same_text(a, b):
    return (
        normalize(a)
        == normalize(b)
    )


def fetch(url, retries=2):
    last = None

    for attempt in range(
        retries + 1
    ):
        req = urllib.request.Request(
            url,
            headers={
                "User-Agent": UA,
                "Accept": (
                    "text/html,*/*;q=0.1"
                ),
            },
        )

        try:
            with urllib.request.urlopen(
                req,
                timeout=30,
            ) as response:
                body = response.read()

                return {
                    "status": response.status,
                    "final_url": (
                        response.geturl()
                    ),
                    "content_type": (
                        response.headers.get(
                            "Content-Type"
                        )
                    ),
                    "body": body,
                    "error": None,
                }

        except Exception as exc:
            last = exc

            if attempt < retries:
                time.sleep(
                    1.0
                    * (attempt + 1)
                )

    return {
        "status": (
            last.code
            if isinstance(
                last,
                urllib.error.HTTPError,
            )
            else None
        ),
        "final_url": url,
        "content_type": None,
        "body": b"",
        "error": repr(last),
    }


class IndexParser(HTMLParser):
    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.links = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
        if tag.lower() != "a":
            return

        href = dict(
            attrs
        ).get("href")

        if href:
            self.links.append(
                href
            )


def extract_alert_links(
    body,
):
    parser = IndexParser()

    parser.feed(
        body.decode(
            "utf-8",
            errors="replace",
        )
    )

    links = set()

    for href in parser.links:
        absolute = urllib.parse.urljoin(
            ALERT_INDEX,
            href,
        )

        parsed = urllib.parse.urlsplit(
            absolute
        )

        if not (
            parsed.path.startswith(
                ALERT_PREFIX
            )
        ):
            continue

        if (
            parsed.path.rstrip("/")
            == ALERT_INDEX
            .replace(
                "https://www.fs.usda.gov",
                "",
            )
            .rstrip("/")
        ):
            continue

        clean_url = (
            "https://www.fs.usda.gov"
            + parsed.path.rstrip("/")
        )

        links.add(
            clean_url
        )

    return sorted(
        links
    )


class ContentParser(HTMLParser):
    BLOCKS = {
        "article",
        "section",
        "div",
        "p",
        "li",
        "dt",
        "dd",
        "h1",
        "h2",
        "h3",
        "h4",
        "h5",
        "h6",
        "br",
    }

    SKIP = {
        "script",
        "style",
        "noscript",
        "svg",
        "nav",
        "footer",
        "aside",
    }

    def __init__(self):
        super().__init__(
            convert_charrefs=True
        )

        self.capture = False
        self.capture_div_depth = 0
        self.found_content_block = False

        self.skip_depth = 0

        self.parts = []

        self.in_h1 = False
        self.h1_parts = []

    def handle_starttag(
        self,
        tag,
        attrs,
    ):
        tag = tag.lower()

        attrs_dict = dict(
            attrs
        )

        if (
            tag == "div"
            and attrs_dict.get("id")
            == "block-wfs-content"
            and not self.capture
        ):
            self.capture = True
            self.capture_div_depth = 1
            self.found_content_block = True
            return

        if not self.capture:
            return

        if tag == "div":
            self.capture_div_depth += 1

        if tag in self.SKIP:
            self.skip_depth += 1
            return

        if (
            self.skip_depth == 0
            and tag == "h1"
        ):
            self.in_h1 = True

        if (
            self.skip_depth == 0
            and tag in self.BLOCKS
        ):
            self.parts.append(
                "\n"
            )

    def handle_endtag(
        self,
        tag,
    ):
        tag = tag.lower()

        if not self.capture:
            return

        if (
            tag in self.SKIP
            and self.skip_depth > 0
        ):
            self.skip_depth -= 1
            return

        if (
            self.skip_depth == 0
            and tag == "h1"
        ):
            self.in_h1 = False

        if (
            self.skip_depth == 0
            and tag in self.BLOCKS
        ):
            self.parts.append(
                "\n"
            )

        if tag == "div":
            self.capture_div_depth -= 1

            if (
                self.capture_div_depth
                <= 0
            ):
                self.capture = False

    def handle_data(
        self,
        data,
    ):
        if (
            not self.capture
            or self.skip_depth > 0
        ):
            return

        if data.strip():
            self.parts.append(
                data
            )

            if self.in_h1:
                self.h1_parts.append(
                    data
                )


def parse_content(
    body,
):
    parser = ContentParser()

    parser.feed(
        body.decode(
            "utf-8",
            errors="replace",
        )
    )

    text = "\n".join(
        line.strip()
        for line
        in "".join(
            parser.parts
        ).splitlines()
        if line.strip()
    )

    text = re.sub(
        r"\n{2,}",
        "\n",
        text,
    ).strip()

    title = compact(
        " ".join(
            parser.h1_parts
        )
    )

    return {
        "content_block_found": (
            parser.found_content_block
        ),
        "title": title,
        "text": text,
    }


def extract_date_field(
    text,
    label,
):
    pattern = (
        re.escape(label)
        + r"\s*:?\s*"
        + r"([A-Z][a-z]+"
        + r"\s+\d{1,2},"
        + r"\s+\d{4})"
    )

    match = re.search(
        pattern,
        text,
        flags=re.I,
    )

    return (
        compact(
            match.group(1)
        )
        if match
        else None
    )


def extract_line_field(
    text,
    label,
):
    lines = [
        compact(line)
        for line in text.splitlines()
        if compact(line)
    ]

    label_norm = normalize(
        label
    )

    for i, line in enumerate(
        lines
    ):
        line_norm = normalize(
            line
        )

        if (
            line_norm == label_norm
            or line_norm.startswith(
                label_norm + " "
            )
        ):
            if (
                line_norm
                != label_norm
            ):
                remainder = re.sub(
                    r"^"
                    + re.escape(label),
                    "",
                    line,
                    flags=re.I,
                )

                remainder = (
                    remainder
                    .lstrip(": ")
                    .strip()
                )

                if remainder:
                    return remainder

            if (
                i + 1
                < len(lines)
            ):
                return lines[
                    i + 1
                ]

    return None


def date_value(
    value,
):
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
    start,
    end,
    today,
):
    start_date = date_value(
        start
    )

    end_date = date_value(
        end
    )

    if (
        start_date is not None
        and start_date > today
    ):
        return "SCHEDULED"

    if (
        end_date is not None
        and end_date < today
    ):
        return (
            "EXPIRED_BY_EXPLICIT_DATE"
        )

    return "CURRENT_OR_UNBOUNDED"


def alias_match(
    evidence,
    aliases,
):
    haystack = (
        " "
        + normalize(
            evidence
        )
        + " "
    )

    candidates = sorted(
        {
            normalize(alias)
            for alias in aliases
            if normalize(alias)
        },
        key=len,
        reverse=True,
    )

    for alias in candidates:
        if (
            " "
            + alias
            + " "
        ) in haystack:
            return alias

    return None


def explicit_site_matches(
    event,
    sites,
):
    # Conservative exact-site evidence:
    # title + explicit Rec Sites Affected field.
    evidence = " ".join([
        event.get(
            "title"
        )
        or "",
        event.get(
            "rec_sites_affected"
        )
        or "",
    ])

    matches = {}

    for key, site in (
        sites.items()
    ):
        matched = alias_match(
            evidence,
            site["aliases"],
        )

        if matched:
            matches[key] = (
                matched
            )

    return matches


def known_road_matches(
    text,
    sites,
):
    matches = {}

    for key, site in (
        sites.items()
    ):
        roads = site.get(
            "access_roads",
            [],
        )

        for road in roads:
            identifier = road[
                "identifier"
            ]

            if alias_match(
                text,
                [identifier],
            ):
                matches.setdefault(
                    key,
                    [],
                ).append(
                    identifier
                )

    return matches


def extract_road_tokens(
    text,
):
    found = set()

    patterns = [
        (
            r"\bForest Service Road\s*"
            r"(?:No\.?\s*)?"
            r"([0-9]+[A-Za-z]?)\b"
        ),
        (
            r"\bFSR\s*[-#:]?\s*"
            r"([0-9]+[A-Za-z]?)\b"
        ),
    ]

    for pattern in patterns:
        for match in re.finditer(
            pattern,
            text,
            flags=re.I,
        ):
            found.add(
                "Forest Service Road "
                + match.group(1)
                .upper()
            )

    # Named roads are recorded only when
    # the official text clearly uses
    # the word "Road".
    for match in re.finditer(
        r"\b([A-Z][A-Za-z' -]{2,40}"
        r"\sRoad)\b",
        text,
    ):
        value = compact(
            match.group(1)
        )

        if len(value) <= 60:
            found.add(
                value
            )

    return sorted(
        found
    )


def forest_wide(
    text,
):
    n = normalize(
        text
    )

    patterns = [
        "forest-wide",
        "forest wide",
        (
            "entire chattahoochee-oconee "
            "national forest"
        ),
        (
            "throughout the "
            "chattahoochee-oconee "
            "national forest"
        ),
        (
            "all areas of the "
            "chattahoochee-oconee "
            "national forest"
        ),
        (
            "all national forest "
            "system lands"
        ),
    ]

    return any(
        normalize(pattern)
        in n
        for pattern in patterns
    )


def district_wide(
    text,
):
    n = normalize(
        text
    )

    patterns = [
        (
            "entire chattooga river "
            "district"
        ),
        (
            "throughout the chattooga "
            "river district"
        ),
        (
            "all areas of the "
            "chattooga river district"
        ),
        (
            "all sites in the "
            "chattooga river district"
        ),
    ]

    return any(
        normalize(pattern)
        in n
        for pattern in patterns
    )


def fire_restriction(
    text,
):
    n = normalize(
        text
    )

    return any(
        marker in n
        for marker in (
            "fire restriction",
            "campfire restriction",
            "campfires prohibited",
            "campfire prohibited",
            "fire restriction closure order",
        )
    )


def prescribed_fire(
    text,
):
    n = normalize(
        text
    )

    return (
        "prescribed fire" in n
        or "prescribed burn" in n
    )


def operational_terms(
    text,
):
    n = normalize(
        text
    )

    markers = [
        "closed",
        "closure",
        "temporarily closed",
        "restriction",
        "no access",
        "access closed",
        "road closed",
        "campground closed",
    ]

    return any(
        marker in n
        for marker in markers
    )


def educational_only(
    title,
    text,
):
    n = normalize(
        title
    )

    clear_titles = [
        "be bear aware",
        "waterfall safety",
        "wildfire prevention",
        "campfire safety",
        "safety reminder",
    ]

    if not any(
        marker in n
        for marker in clear_titles
    ):
        return False

    return not operational_terms(
        text
    )


def road_operational_event(
    text,
):
    n = normalize(
        text
    )

    road_word = (
        " road " in (
            " " + n + " "
        )
        or "forest service road"
        in n
        or "fsr " in n
    )

    impact = any(
        marker in n
        for marker in (
            "closed",
            "closure",
            "damage",
            "hazard",
            "no access",
            "impassable",
            "restricted",
        )
    )

    return (
        road_word
        and impact
    )


def classify_event(
    event,
    sites,
    today,
):
    start = event.get(
        "start_date"
    )

    end = event.get(
        "end_date"
    )

    life = lifecycle(
        start,
        end,
        today,
    )

    if event[
        "fetch_status"
    ] != 200:
        return {
            key: {
                "relevance": "UNKNOWN",
                "reason": (
                    "SOURCE_FETCH_FAILED"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": (
                    "detail page fetch failed"
                ),
            }
            for key in sites
        }

    if not event[
        "content_block_found"
    ]:
        return {
            key: {
                "relevance": "UNKNOWN",
                "reason": (
                    "SOURCE_PARSE_FAILED"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": (
                    "USFS content block not found"
                ),
            }
            for key in sites
        }

    title = event.get(
        "title"
    ) or ""

    text = event.get(
        "text"
    ) or ""

    explicit_sites = (
        explicit_site_matches(
            event,
            sites,
        )
    )

    roads_by_site = (
        known_road_matches(
            text,
            sites,
        )
    )

    roads_detected = (
        extract_road_tokens(
            text
        )
    )

    is_forest_wide = (
        forest_wide(
            text
        )
    )

    is_district_wide = (
        district_wide(
            text
        )
    )

    is_fire_restriction = (
        fire_restriction(
            text
        )
    )

    is_prescribed = (
        prescribed_fire(
            text
        )
    )

    is_operational = (
        operational_terms(
            text
        )
    )

    is_educational = (
        educational_only(
            title,
            text,
        )
    )

    is_road_event = (
        road_operational_event(
            text
        )
    )

    result = {}

    # ------------------------------------------------------
    # Clear generic education exclusion.
    # ------------------------------------------------------

    if is_educational:
        for key in sites:
            result[key] = {
                "relevance": "EXCLUDE",
                "reason": (
                    "GENERIC_EDUCATIONAL_NOTICE"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": title,
            }

        return result

    # ------------------------------------------------------
    # Exact / explicit site scope has priority.
    # If one or more campgrounds are explicitly identified,
    # only those sites are in scope.
    # ------------------------------------------------------

    if explicit_sites:
        for key in sites:
            if key in explicit_sites:
                result[key] = {
                    "relevance": "INCLUDE",
                    "reason": (
                        "EXACT_CAMPGROUND"
                    ),
                    "notify": (
                        life
                        != "SCHEDULED"
                    ),
                    "lifecycle": life,
                    "evidence": (
                        "official alert title/"
                        "affected-site field matched "
                        + explicit_sites[key]
                    ),
                }

            else:
                result[key] = {
                    "relevance": "EXCLUDE",
                    "reason": (
                        "WRONG_CAMPGROUND"
                    ),
                    "notify": False,
                    "lifecycle": life,
                    "evidence": (
                        "alert explicitly names "
                        "other frozen campground(s)"
                    ),
                }

        return result

    # ------------------------------------------------------
    # Prescribed fire without explicit operational scope.
    # ------------------------------------------------------

    if (
        is_prescribed
        and not is_operational
    ):
        for key in sites:
            result[key] = {
                "relevance": "EXCLUDE",
                "reason": (
                    "PRESCRIBED_FIRE_"
                    "NO_OPERATIONAL_IMPACT"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": (
                    "prescribed-fire notice "
                    "without explicit operational impact"
                ),
            }

        return result

    # ------------------------------------------------------
    # Official fire restriction with explicit broad scope.
    # ------------------------------------------------------

    if is_fire_restriction:
        if (
            is_forest_wide
            or is_district_wide
        ):
            for key in sites:
                result[key] = {
                    "relevance": "INCLUDE",
                    "reason": (
                        "OFFICIAL_FIRE_RESTRICTION"
                    ),
                    "notify": (
                        life
                        != "SCHEDULED"
                    ),
                    "lifecycle": life,
                    "evidence": (
                        "official fire restriction "
                        "with explicit "
                        + (
                            "forest-wide"
                            if is_forest_wide
                            else "district-wide"
                        )
                        + " scope"
                    ),
                }

            return result

        for key in sites:
            result[key] = {
                "relevance": "UNKNOWN",
                "reason": (
                    "AMBIGUOUS_SCOPE"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": (
                    "fire restriction detected "
                    "without deterministic "
                    "campground/forest/district scope"
                ),
            }

        return result

    # ------------------------------------------------------
    # Other explicit broad operational scopes.
    # ------------------------------------------------------

    if is_forest_wide:
        for key in sites:
            result[key] = {
                "relevance": "INCLUDE",
                "reason": (
                    "FOREST_WIDE"
                ),
                "notify": (
                    life
                    != "SCHEDULED"
                ),
                "lifecycle": life,
                "evidence": (
                    "explicit forest-wide scope"
                ),
            }

        return result

    if is_district_wide:
        for key in sites:
            result[key] = {
                "relevance": "INCLUDE",
                "reason": (
                    "DISTRICT_WIDE"
                ),
                "notify": (
                    life
                    != "SCHEDULED"
                ),
                "lifecycle": life,
                "evidence": (
                    "explicit Chattooga "
                    "River District-wide scope"
                ),
            }

        return result

    # ------------------------------------------------------
    # Road/access event.
    #
    # Known mapped road = INCLUDE.
    # Missing relationship = UNKNOWN.
    # We do not infer irrelevance from proximity or absence.
    # ------------------------------------------------------

    if is_road_event:
        for key in sites:
            matched_roads = (
                roads_by_site.get(
                    key,
                    [],
                )
            )

            if matched_roads:
                result[key] = {
                    "relevance": "INCLUDE",
                    "reason": (
                        "ACCESS_ROAD"
                    ),
                    "notify": (
                        life
                        != "SCHEDULED"
                    ),
                    "lifecycle": life,
                    "evidence": (
                        "official road text matched "
                        + ", ".join(
                            matched_roads
                        )
                    ),
                }

            else:
                result[key] = {
                    "relevance": "UNKNOWN",
                    "reason": (
                        "ROAD_RELATION_UNPROVEN"
                    ),
                    "notify": False,
                    "lifecycle": life,
                    "evidence": (
                        "road/access operational alert; "
                        "campground relationship not frozen"
                    ),
                }

        return result

    # ------------------------------------------------------
    # Prescribed fire with operational language but no
    # deterministic site/road/broad scope.
    # ------------------------------------------------------

    if (
        is_prescribed
        and is_operational
    ):
        for key in sites:
            result[key] = {
                "relevance": "UNKNOWN",
                "reason": (
                    "AMBIGUOUS_SCOPE"
                ),
                "notify": False,
                "lifecycle": life,
                "evidence": (
                    "prescribed-fire operational "
                    "impact found but campground "
                    "scope unresolved"
                ),
            }

        return result

    # ------------------------------------------------------
    # No frozen relevance relationship found.
    # ------------------------------------------------------

    for key in sites:
        result[key] = {
            "relevance": "EXCLUDE",
            "reason": (
                "NO_CAMPGROUND_MATCH"
            ),
            "notify": False,
            "lifecycle": life,
            "evidence": (
                "no deterministic V1 "
                "campground relevance relationship"
            ),
        }

    return result


def main():
    mapping_path = Path(
        sys.argv[1]
    )

    report_path = Path(
        sys.argv[2]
    )

    mapping_bytes = (
        mapping_path.read_bytes()
    )

    mapping_sha = hashlib.sha256(
        mapping_bytes
    ).hexdigest()

    mapping = json.loads(
        mapping_bytes.decode(
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

    index_result = fetch(
        ALERT_INDEX
    )

    alert_links = []

    if (
        index_result["status"]
        == 200
    ):
        alert_links = (
            extract_alert_links(
                index_result[
                    "body"
                ]
            )
        )

    # Defensive cap. The frozen source should
    # never require crawling an unbounded site.
    alert_links = (
        alert_links[:100]
    )

    events = []

    today = date.today()

    for idx, url in enumerate(
        alert_links,
        start=1,
    ):
        response = fetch(
            url
        )

        parsed = {
            "content_block_found": False,
            "title": "",
            "text": "",
        }

        if response["body"]:
            parsed = parse_content(
                response["body"]
            )

        title = parsed[
            "title"
        ]

        if not title:
            title = (
                urllib.parse
                .urlsplit(url)
                .path
                .rstrip("/")
                .split("/")[-1]
                .replace("-", " ")
                .strip()
            )

        text = parsed[
            "text"
        ]

        start_date = (
            extract_date_field(
                text,
                "Alert Start Date",
            )
        )

        end_date = (
            extract_date_field(
                text,
                "Alert End Date",
            )
        )

        rec_sites = (
            extract_line_field(
                text,
                "Rec Sites Affected",
            )
        )

        order_number = (
            extract_line_field(
                text,
                "Order Number",
            )
        )

        last_updated = (
            extract_line_field(
                text,
                "Last updated",
            )
        )

        event = {
            "event_id": (
                "usfs:"
                + hashlib.sha256(
                    url.encode(
                        "utf-8"
                    )
                ).hexdigest()[:20]
            ),
            "source_url": url,
            "fetch_status": (
                response["status"]
            ),
            "final_url": (
                response[
                    "final_url"
                ]
            ),
            "body_bytes": len(
                response["body"]
            ),
            "content_type": (
                response[
                    "content_type"
                ]
            ),
            "fetch_error": (
                response[
                    "error"
                ]
            ),
            "content_block_found": (
                parsed[
                    "content_block_found"
                ]
            ),
            "title": title,
            "start_date": (
                start_date
            ),
            "end_date": (
                end_date
            ),
            "rec_sites_affected": (
                rec_sites
            ),
            "order_number": (
                order_number
            ),
            "last_updated": (
                last_updated
            ),
            "text": text,
            "text_excerpt": (
                text[:6000]
            ),
        }

        event[
            "lifecycle"
        ] = lifecycle(
            start_date,
            end_date,
            today,
        )

        event[
            "exact_site_matches"
        ] = explicit_site_matches(
            event,
            sites,
        )

        event[
            "known_road_matches"
        ] = known_road_matches(
            text,
            sites,
        )

        event[
            "road_tokens"
        ] = extract_road_tokens(
            text
        )

        event[
            "forest_wide_detected"
        ] = forest_wide(
            text
        )

        event[
            "district_wide_detected"
        ] = district_wide(
            text
        )

        event[
            "fire_restriction_detected"
        ] = fire_restriction(
            text
        )

        event[
            "prescribed_fire_detected"
        ] = prescribed_fire(
            text
        )

        event[
            "road_operational_detected"
        ] = road_operational_event(
            text
        )

        relations = classify_event(
            event,
            sites,
            today,
        )

        event[
            "relations"
        ] = relations

        event[
            "include_keys"
        ] = sorted(
            key
            for key, relation
            in relations.items()
            if relation[
                "relevance"
            ]
            == "INCLUDE"
        )

        event[
            "unknown_keys"
        ] = sorted(
            key
            for key, relation
            in relations.items()
            if relation[
                "relevance"
            ]
            == "UNKNOWN"
        )

        event[
            "exclude_keys"
        ] = sorted(
            key
            for key, relation
            in relations.items()
            if relation[
                "relevance"
            ]
            == "EXCLUDE"
        )

        event[
            "notify_keys"
        ] = sorted(
            key
            for key, relation
            in relations.items()
            if relation[
                "notify"
            ]
        )

        event[
            "reason_counts"
        ] = dict(
            Counter(
                relation["reason"]
                for relation
                in relations.values()
            )
        )

        # Remove unbounded full text from final
        # report after classification. The excerpt
        # remains for human review.
        event.pop(
            "text",
            None,
        )

        events.append(
            event
        )

        time.sleep(
            0.1
        )

    event_count = len(
        events
    )

    detail_success = sum(
        event[
            "fetch_status"
        ] == 200
        for event in events
    )

    parse_success = sum(
        event[
            "fetch_status"
        ] == 200
        and event[
            "content_block_found"
        ]
        for event in events
    )

    required_success = (
        math.ceil(
            event_count
            * 0.95
        )
        if event_count
        else 0
    )

    relation_count = (
        event_count
        * len(sites)
    )

    include_count = sum(
        len(
            event[
                "include_keys"
            ]
        )
        for event in events
    )

    unknown_count = sum(
        len(
            event[
                "unknown_keys"
            ]
        )
        for event in events
    )

    exclude_count = sum(
        len(
            event[
                "exclude_keys"
            ]
        )
        for event in events
    )

    notify_count = sum(
        len(
            event[
                "notify_keys"
            ]
        )
        for event in events
    )

    events_with_include = sum(
        bool(
            event[
                "include_keys"
            ]
        )
        for event in events
    )

    events_with_unknown = sum(
        bool(
            event[
                "unknown_keys"
            ]
        )
        for event in events
    )

    events_with_notify = sum(
        bool(
            event[
                "notify_keys"
            ]
        )
        for event in events
    )

    events_excluded_only = sum(
        (
            not event[
                "include_keys"
            ]
            and not event[
                "unknown_keys"
            ]
        )
        for event in events
    )

    reason_counts = Counter()

    vocabulary_ok = True

    invalid_reasons = []

    notify_safety_ok = True

    for event in events:
        for key, relation in (
            event[
                "relations"
            ].items()
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
                    "event_id": (
                        event[
                            "event_id"
                        ]
                    ),
                    "campground": key,
                    "relevance": (
                        relevance
                    ),
                    "reason": reason,
                })

            if (
                relation["notify"]
                and relevance
                != "INCLUDE"
            ):
                notify_safety_ok = False

            if (
                relation["notify"]
                and relation[
                    "lifecycle"
                ]
                == "SCHEDULED"
            ):
                notify_safety_ok = False

    urls = [
        event["source_url"]
        for event in events
    ]

    unique_source_urls_ok = (
        len(urls)
        == len(
            set(urls)
        )
    )

    relationship_keys = []

    for event in events:
        for key, relation in (
            event[
                "relations"
            ].items()
        ):
            relationship_keys.append(
                (
                    event[
                        "event_id"
                    ],
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

    relation_accounting_ok = (
        include_count
        + unknown_count
        + exclude_count
        == relation_count
    )

    # No distance/radius/AI fields or decisions
    # exist anywhere in this replay.
    forbidden_inference_ok = True

    if (
        mapping_gate
        and index_result[
            "status"
        ] == 200
        and event_count >= 1
        and detail_success
        >= required_success
        and parse_success
        >= required_success
        and vocabulary_ok
        and notify_safety_ok
        and unique_source_urls_ok
        and relationship_dedup_ok
        and relation_accounting_ok
        and forbidden_inference_ok
    ):
        decision = (
            "PASS_LIVE_USFS_"
            "RELEVANCE_REPLAY"
        )
        rc = 0

    elif (
        index_result[
            "status"
        ] == 200
        and event_count >= 1
    ):
        decision = (
            "HOLD_LIVE_USFS_"
            "RELEVANCE_REPLAY"
        )
        rc = 2

    else:
        decision = (
            "FAIL_LIVE_USFS_"
            "RELEVANCE_REPLAY"
        )
        rc = 4

    event_summaries = []

    for event in events:
        event_summaries.append({
            "event_id": (
                event[
                    "event_id"
                ]
            ),
            "title": (
                event["title"]
            ),
            "source_url": (
                event[
                    "source_url"
                ]
            ),
            "fetch_status": (
                event[
                    "fetch_status"
                ]
            ),
            "content_block_found": (
                event[
                    "content_block_found"
                ]
            ),
            "start_date": (
                event[
                    "start_date"
                ]
            ),
            "end_date": (
                event[
                    "end_date"
                ]
            ),
            "lifecycle": (
                event[
                    "lifecycle"
                ]
            ),
            "rec_sites_affected": (
                event[
                    "rec_sites_affected"
                ]
            ),
            "order_number": (
                event[
                    "order_number"
                ]
            ),
            "last_updated": (
                event[
                    "last_updated"
                ]
            ),
            "exact_site_matches": (
                event[
                    "exact_site_matches"
                ]
            ),
            "road_tokens": (
                event[
                    "road_tokens"
                ]
            ),
            "known_road_matches": (
                event[
                    "known_road_matches"
                ]
            ),
            "forest_wide_detected": (
                event[
                    "forest_wide_detected"
                ]
            ),
            "district_wide_detected": (
                event[
                    "district_wide_detected"
                ]
            ),
            "fire_restriction_detected": (
                event[
                    "fire_restriction_detected"
                ]
            ),
            "prescribed_fire_detected": (
                event[
                    "prescribed_fire_detected"
                ]
            ),
            "road_operational_detected": (
                event[
                    "road_operational_detected"
                ]
            ),
            "include_keys": (
                event[
                    "include_keys"
                ]
            ),
            "unknown_keys": (
                event[
                    "unknown_keys"
                ]
            ),
            "exclude_keys": (
                event[
                    "exclude_keys"
                ]
            ),
            "notify_keys": (
                event[
                    "notify_keys"
                ]
            ),
            "reason_counts": (
                event[
                    "reason_counts"
                ]
            ),
            "text_excerpt": (
                event[
                    "text_excerpt"
                ]
            ),
        })

    report = {
        "validated_at_utc": (
            datetime.now(
                timezone.utc
            ).isoformat()
        ),

        "mapping_input": {
            "path": str(
                mapping_path
            ),
            "sha256": (
                mapping_sha
            ),
            "mapping_gate_pass": (
                mapping_gate
            ),
            "row_count": (
                len(rows)
            ),
        },

        "source": {
            "alert_index": (
                ALERT_INDEX
            ),
            "index_http_status": (
                index_result[
                    "status"
                ]
            ),
            "index_body_bytes": len(
                index_result[
                    "body"
                ]
            ),
            "index_error": (
                index_result[
                    "error"
                ]
            ),
            "live_alert_link_count": (
                event_count
            ),
        },

        "contract_guards": {
            "source_discovery": False,
            "new_source_families": 0,
            "proximity_inference": False,
            "ai_only_relevance": False,
            "unknown_is_valid": True,
            "scheduled_notify_prohibited": True,
        },

        "summary": {
            "event_count": (
                event_count
            ),
            "detail_fetch_success": (
                f"{detail_success}/"
                f"{event_count}"
            ),
            "detail_parse_success": (
                f"{parse_success}/"
                f"{event_count}"
            ),
            "required_95pct_success_count": (
                required_success
            ),

            "relationship_count": (
                relation_count
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
            "would_notify_relationship_count": (
                notify_count
            ),

            "include_relationship_pct": (
                round(
                    100
                    * include_count
                    / relation_count,
                    2,
                )
                if relation_count
                else 0.0
            ),

            "unknown_relationship_pct": (
                round(
                    100
                    * unknown_count
                    / relation_count,
                    2,
                )
                if relation_count
                else 0.0
            ),

            "exclude_relationship_pct": (
                round(
                    100
                    * exclude_count
                    / relation_count,
                    2,
                )
                if relation_count
                else 0.0
            ),

            "events_with_include": (
                events_with_include
            ),
            "events_with_unknown": (
                events_with_unknown
            ),
            "events_with_would_notify": (
                events_with_notify
            ),
            "events_excluded_only": (
                events_excluded_only
            ),

            "reason_counts": dict(
                reason_counts
            ),

            "reason_vocabulary_ok": (
                vocabulary_ok
            ),
            "invalid_reasons": (
                invalid_reasons
            ),
            "notify_safety_ok": (
                notify_safety_ok
            ),
            "source_url_dedup_ok": (
                unique_source_urls_ok
            ),
            "relationship_dedup_ok": (
                relationship_dedup_ok
            ),
            "relationship_accounting_ok": (
                relation_accounting_ok
            ),
            "forbidden_inference_ok": (
                forbidden_inference_ok
            ),
        },

        "events": (
            event_summaries
        ),

        "decision": decision,
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
        "CampReady Phase 3A-4:",
        decision,
    )

    print(
        "Mapping gate:",
        mapping_gate,
    )

    print(
        "Alert index HTTP:",
        index_result[
            "status"
        ],
    )

    print(
        "Live alert events:",
        event_count,
    )

    print(
        "Detail fetch:",
        f"{detail_success}/"
        f"{event_count}",
    )

    print(
        "Detail parse:",
        f"{parse_success}/"
        f"{event_count}",
    )

    print(
        "Relationships:",
        relation_count,
    )

    print(
        "INCLUDE:",
        include_count,
    )

    print(
        "UNKNOWN:",
        unknown_count,
    )

    print(
        "EXCLUDE:",
        exclude_count,
    )

    print(
        "Would notify:",
        notify_count,
    )

    print(
        "Events with INCLUDE:",
        events_with_include,
    )

    print(
        "Events with UNKNOWN:",
        events_with_unknown,
    )

    print(
        "Events excluded only:",
        events_excluded_only,
    )

    print()

    for event in events:
        print(
            "EVENT",
            event[
                "event_id"
            ],
        )

        print(
            "  title:",
            event["title"],
        )

        print(
            "  http:",
            event[
                "fetch_status"
            ],
            "parsed:",
            event[
                "content_block_found"
            ],
        )

        print(
            "  lifecycle:",
            event[
                "lifecycle"
            ],
            "start=",
            event[
                "start_date"
            ],
            "end=",
            event[
                "end_date"
            ],
        )

        print(
            "  exact-sites:",
            sorted(
                event[
                    "exact_site_matches"
                ].keys()
            ),
        )

        print(
            "  roads:",
            event[
                "road_tokens"
            ],
        )

        print(
            "  INCLUDE:",
            event[
                "include_keys"
            ],
        )

        print(
            "  UNKNOWN:",
            event[
                "unknown_keys"
            ],
        )

        print(
            "  NOTIFY:",
            event[
                "notify_keys"
            ],
        )

        print(
            "  reasons:",
            event[
                "reason_counts"
            ],
        )

        print()

    return rc


raise SystemExit(
    main()
)
