"""Strava's names for an athlete's activities, from the `activities.csv` of their Strava data export.

Strava's export ("Download or Delete Your Account" → "Request your archive") is a zip; the browser uploads only its
`activities.csv`, one row per activity. Its columns used here are `Activity ID` (the Strava id), `Activity Date`
(the start in UTC, e.g. `Nov 17, 2026, 5:49:58 PM`) and `Activity Name`. Some column names appear twice (e.g. the
first `Distance` is in the athlete's units, the second in metres), so columns are found by their first occurrence.

Activities are matched to the athlete's (`match_activities`) by the Strava id intervals.icu found for them, then by
start time, and the matches (`StravaMatch`) override the index's names and Strava ids when shown (`apply_matches`).
"""

import csv
import io
from bisect import bisect_left, bisect_right
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from attrs import define, evolve, frozen

from skintrackr.models import ActivitySummary

# the export's `Activity Date` (UTC; strptime's `%b` is the C locale's English month abbreviation)
DATE_FORMAT = "%b %d, %Y, %I:%M:%S %p"
ID_COLUMN, DATE_COLUMN, NAME_COLUMN = "Activity ID", "Activity Date", "Activity Name"
# how far apart the starts of the same activity on intervals.icu and on Strava may be (Strava's start moves when
# an activity is cropped there); almost all are the same to the second
MATCH_TOLERANCE = timedelta(minutes=10)


class StravaExportError(ValueError):
    """The upload isn't a Strava export's `activities.csv`, or holds a row skintrackr can't read."""


@frozen
class StravaActivity:
    """One row of a Strava export's `activities.csv` (the parts skintrackr uses).

    Attributes:
        id: The Strava activity id.
        name: Its name on Strava.
        start: When it started, in UTC.
    """

    id: str
    name: str
    start: datetime


@define
class StravaMatch:
    """What an athlete's activity takes from the Strava activity it matched (stored per activity id)."""

    strava_id: str = ""
    name: str = ""


def parse_activities(data: bytes) -> list[StravaActivity]:
    """The activities in a Strava export's `activities.csv`.

    Raises:
        StravaExportError: It isn't one, or a row's id or date can't be read.
    """
    try:
        text = data.decode("utf-8-sig")
    except UnicodeDecodeError as e:
        raise StravaExportError("This isn't a Strava export's activities.csv (it isn't text).") from e
    rows = csv.reader(io.StringIO(text, newline=""))
    header = next(rows, [])
    try:
        id_at, date_at, name_at = (header.index(column) for column in (ID_COLUMN, DATE_COLUMN, NAME_COLUMN))
    except ValueError:
        raise StravaExportError(
            f"This isn't a Strava export's activities.csv (it has no {ID_COLUMN}, {DATE_COLUMN} and "
            f"{NAME_COLUMN} columns)."
        ) from None

    activities = []
    for line, row in enumerate(rows, start=2):
        if not any(row):
            continue
        try:
            strava_id, date, name = row[id_at].strip(), row[date_at].strip(), row[name_at].strip()
        except IndexError:
            raise StravaExportError(f"Row {line} of activities.csv is missing columns.") from None
        if not strava_id.isdigit():
            raise StravaExportError(f"Row {line} of activities.csv has an invalid activity id: {strava_id!r}.")
        try:
            start = datetime.strptime(date, DATE_FORMAT).replace(tzinfo=UTC)
        except ValueError:
            raise StravaExportError(
                f"Row {line} of activities.csv has a date skintrackr can't read: {date!r}. Only exports in "
                "English are supported."
            ) from None
        activities.append(StravaActivity(id=strava_id, name=name, start=start))
    return activities


def _utc_start(summary: ActivitySummary) -> datetime | None:
    try:
        return datetime.fromisoformat(summary.start_date).astimezone(UTC) if summary.start_date else None
    except ValueError:
        return None


def match_activities(
    summaries: Iterable[ActivitySummary], strava: Iterable[StravaActivity], tolerance: timedelta = MATCH_TOLERANCE
) -> dict[str, StravaMatch]:
    """Pair an athlete's activities with Strava's, each in at most one pair.

    An activity intervals.icu has already matched with a Strava activity (`strava_id`) pairs with that one only
    (it's left unpaired if the export doesn't have it). The others pair by the closest UTC start within
    `tolerance`, closest pairs first; those without a UTC start (indexed before it was) are left unpaired.

    Returns:
        Each paired activity's id, and what it takes from its Strava activity.
    """
    strava = list(strava)
    by_id = {activity.id: activity for activity in strava}
    pairs: dict[str, StravaActivity] = {}
    unpaired: list[tuple[datetime, ActivitySummary]] = []
    for summary in summaries:
        if summary.strava_id:
            if summary.strava_id in by_id:
                pairs[summary.id] = by_id[summary.strava_id]
        elif start := _utc_start(summary):
            unpaired.append((start, summary))

    paired_strava = {activity.id for activity in pairs.values()}
    timed = sorted((a for a in strava if a.id not in paired_strava), key=lambda a: a.start)
    starts = [activity.start for activity in timed]
    candidates = []
    for start, summary in unpaired:
        lo, hi = bisect_left(starts, start - tolerance), bisect_right(starts, start + tolerance)
        candidates.extend((abs(starts[i] - start), summary.id, timed[i]) for i in range(lo, hi))
    for _, activity_id, activity in sorted(candidates, key=lambda c: (c[0], c[1], c[2].id)):
        if activity_id not in pairs and activity.id not in paired_strava:
            pairs[activity_id] = activity
            paired_strava.add(activity.id)

    return {activity_id: StravaMatch(strava_id=a.id, name=a.name) for activity_id, a in pairs.items()}


def apply_matches(summaries: Iterable[ActivitySummary], matches: dict[str, StravaMatch]) -> list[ActivitySummary]:
    """The summaries with their Strava matches' names (unless blank) and Strava ids in place of intervals.icu's."""
    applied = []
    for summary in summaries:
        match = matches.get(summary.id)
        if match is not None:
            summary = evolve(summary, strava_id=match.strava_id, name=match.name or summary.name)
        applied.append(summary)
    return applied


@define
class StravaImport:
    """How an athlete's Strava export matched their activities.

    Attributes:
        strava_activities: Activities in the export.
        activities: The athlete's activities (in their index).
        matched: Activities paired with one in the export.
    """

    strava_activities: int
    activities: int
    matched: int
