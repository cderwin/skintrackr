import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from skintrackr.models import ActivitySummary
from skintrackr.strava import (
    StravaActivity,
    StravaExportError,
    StravaMatch,
    apply_matches,
    match_activities,
    parse_activities,
)

# a made-up export in the real one's layout (some column names twice, quoted commas, newlines and quotes)
EXPORT = (Path(__file__).parent / "fixtures" / "strava-activities.csv").read_bytes()
HEADER = b"Activity ID,Activity Date,Activity Name,Activity Type\n"


def at(minute: int, second: int = 0) -> datetime:
    return datetime(2024, 1, 15, 8, minute, second, tzinfo=UTC)


def strava(strava_id: str, start: datetime, name: str = "") -> StravaActivity:
    return StravaActivity(id=strava_id, name=name or f"Strava {strava_id}", start=start)


def ours(activity_id: str, start: datetime | None, strava_id: str = "") -> ActivitySummary:
    start_date = start.strftime("%Y-%m-%dT%H:%M:%SZ") if start else ""
    return ActivitySummary(id=activity_id, name=f"Ours {activity_id}", start_date=start_date, strava_id=strava_id)


def matched(pairs: dict[str, StravaMatch]) -> dict[str, str]:
    return {activity_id: match.strava_id for activity_id, match in pairs.items()}


# --- parsing


def test_parses_the_export() -> None:
    assert parse_activities(EXPORT) == [
        StravaActivity(id="9001", name="Dawn patrol", start=datetime(2024, 1, 15, 8, 30, tzinfo=UTC)),
        StravaActivity(id="9002", name="Tour, with a comma", start=datetime(2024, 1, 16, 17, 2, 10, tzinfo=UTC)),
        StravaActivity(id="9003", name="Lift laps", start=datetime(2024, 2, 1, 12, 0, tzinfo=UTC)),
    ]


def test_parses_a_byte_order_mark_crlf_and_blank_lines() -> None:
    data = "﻿".encode() + HEADER.replace(b"\n", b"\r\n") + b'9001,"Jan 15, 2024, 8:30:00 AM", Skin ,Run\r\n\r\n'
    assert parse_activities(data) == [StravaActivity(id="9001", name="Skin", start=at(30))]


def test_parses_an_export_without_activities() -> None:
    assert parse_activities(HEADER) == []


@pytest.mark.parametrize(
    ("data", "message"),
    [
        (b"\xff\xfe\x00binary", "it isn't text"),
        (b"", "it has no Activity ID, Activity Date and Activity Name columns"),
        (b"Media Filename,Media Caption\nmedia/1.jpg,\n", "it has no Activity ID"),
        (HEADER + b'9001,"Jan 15, 2024, 8:30:00 AM"\n', "Row 2 of activities.csv is missing columns."),
        (HEADER + b'x1,"Jan 15, 2024, 8:30:00 AM",Skin,Run\n', "Row 2 of activities.csv has an invalid activity id: 'x1'."),
        (HEADER + b'9001,"15 janv. 2024, 08:30:00",Skin,Run\n', "can't read: '15 janv. 2024, 08:30:00'"),
    ],
)
def test_rejects_what_isnt_an_export(data: bytes, message: str) -> None:
    with pytest.raises(StravaExportError, match=re.escape(message)):
        parse_activities(data)


# --- matching


def test_matches_by_the_strava_id_intervals_found() -> None:
    # i1's Strava id wins over the activity starting at the same time
    pairs = match_activities([ours("i1", at(0), strava_id="2")], [strava("1", at(0)), strava("2", at(30))])
    assert pairs == {"i1": StravaMatch(strava_id="2", name="Strava 2")}


def test_an_activity_with_a_strava_id_missing_from_the_export_stays_unmatched() -> None:
    # e.g. an activity newer than the export: it isn't paired with another by its start
    assert match_activities([ours("i1", at(0), strava_id="99")], [strava("1", at(0))]) == {}


def test_matches_by_the_closest_start() -> None:
    activities = [ours("i1", at(0)), ours("i2", at(20)), ours("i3", at(40))]
    exports = [strava("1", at(0)), strava("2", at(23)), strava("3", at(55))]  # 3 is 15 minutes off
    assert matched(match_activities(activities, exports)) == {"i1": "1", "i2": "2"}


def test_matches_within_the_tolerance_only() -> None:
    activities = [ours("i1", at(0))]
    assert matched(match_activities(activities, [strava("1", at(10))])) == {"i1": "1"}
    assert match_activities(activities, [strava("1", at(10, 1))]) == {}
    assert match_activities(activities, [strava("1", at(10, 1))], tolerance=timedelta(minutes=11)) != {}


def test_matches_each_activity_once_closest_pairs_first() -> None:
    # i2 is closest to 1, so i1 gets 2 even though 1 is closer to it than 2 is
    activities = [ours("i1", at(0)), ours("i2", at(3))]
    exports = [strava("1", at(4)), strava("2", at(9))]
    assert matched(match_activities(activities, exports)) == {"i2": "1", "i1": "2"}


def test_a_strava_activity_matched_by_id_isnt_matched_again_by_start() -> None:
    activities = [ours("i1", at(0), strava_id="1"), ours("i2", at(0))]
    assert matched(match_activities(activities, [strava("1", at(0))])) == {"i1": "1"}


def test_activities_without_a_utc_start_match_by_id_only() -> None:
    # indexed before `start_date` was, until `migrate refresh-summaries`
    activities = [ours("i1", None), ours("i2", None, strava_id="2")]
    assert matched(match_activities(activities, [strava("1", at(0)), strava("2", at(5))])) == {"i2": "2"}


def test_matches_starts_with_an_offset() -> None:
    activity = ActivitySummary(id="i1", start_date="2024-01-15T00:30:00-08:00")
    assert matched(match_activities([activity], [strava("1", at(30))])) == {"i1": "1"}


# --- applying matches


def test_applies_strava_names_and_ids() -> None:
    activities = [ours("i1", at(0), strava_id="1"), ours("i2", at(5)), ours("i3", at(10))]
    matches = {"i1": StravaMatch(strava_id="1", name="Dawn patrol"), "i2": StravaMatch(strava_id="2", name="")}
    applied = apply_matches(activities, matches)
    assert [(a.id, a.name, a.strava_id) for a in applied] == [
        ("i1", "Dawn patrol", "1"),
        ("i2", "Ours i2", "2"),  # a blank Strava name keeps intervals.icu's
        ("i3", "Ours i3", ""),
    ]
    assert activities[0].name == "Ours i1"  # the index's rows are left as they are
