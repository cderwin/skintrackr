"""Data exchanged with intervals.icu and clients, and the converter that (de)serializes it.

Attribute names are the JSON keys. Decoding is lenient: unknown keys are ignored, and missing or null values
take the field's default.
"""

import json
from typing import Any

from attrs import define, field
from cattrs.preconf.json import make_converter

converter = make_converter()


def _drop_nulls(value: Any) -> Any:
    """Remove null values from (nested) objects, so they take the field's default."""
    if isinstance(value, dict):
        return {key: _drop_nulls(item) for key, item in value.items() if item is not None}
    if isinstance(value, list):
        return [_drop_nulls(item) for item in value]
    return value


def loads[T](data: bytes | str, cls: type[T]) -> T:
    """Decode JSON into `cls`, leniently (see the module docstring)."""
    return converter.structure(_drop_nulls(json.loads(data)), cls)


@define
class Athlete:
    """An intervals.icu athlete (the parts skintrackr uses)."""

    id: str = ""
    name: str = ""


@define
class Activity:
    """The parts of an intervals.icu activity skintrackr uses (the archive keeps all of it)."""

    id: str = ""
    icu_athlete_id: str = ""
    name: str = ""
    type: str = ""
    start_date: str = ""
    start_date_local: str = ""
    distance: float = 0.0
    moving_time: int = 0
    elapsed_time: int = 0
    total_elevation_gain: float = 0.0
    description: str = ""
    calories: float = 0.0
    source: str = ""


@define
class ActivitySummary:
    """One row of an athlete's activity index: decoded from the activity list or an activity's details.

    Every field comes from the list's `fields=` selection (`SUMMARY_FIELDS`), so a listed summary equals the
    indexed one until the activity changes on intervals.icu.
    """

    id: str = ""
    name: str = ""
    type: str = ""
    # the start in UTC (e.g. `2024-01-15T08:30:00Z`), which Strava exports match by; empty in rows indexed before
    # it was added, until `migrate refresh-summaries`
    start_date: str = ""
    start_date_local: str = ""
    total_elevation_gain: float = 0.0
    distance: float = 0.0
    moving_time: int = 0
    icu_sync_date: str = ""
    source: str = ""
    # the activity's Strava id, once intervals.icu has matched it with one on Strava (possibly after archiving)
    strava_id: str = ""


@define
class ImportStatus:
    """Progress of an athlete's bulk import.

    Attributes:
        state: `idle` (never run), `running`, `done` or `failed`.
        activities: Activities in the index after the summary step.
        tracks_done: Activities archived in full (details and streams) so far.
        tracks_total: Activities to archive in full.
        skipped: Activities intervals.icu lists but doesn't serve through its API (synced from Strava).
        map_tracks_omitted: Activities with a track left off the map when the import finished, beyond the
            `tracks.MAP_TRACKS_LIMIT` most recent.
        error: Why it failed, if it did.
        updated_at: Unix time of the last change.
    """

    state: str = "idle"
    activities: int = 0
    tracks_done: int = 0
    tracks_total: int = 0
    skipped: int = 0
    map_tracks_omitted: int = 0
    error: str = ""
    updated_at: int = 0


@define
class Stream:
    """One stream from an activity's streams endpoint: one value per sample (`latlng` puts longitudes in `data2`)."""

    type: str
    data: list[Any] = field(factory=list)
    data2: list[Any] = field(factory=list)


@define
class StreamPoint:
    """One sample of an activity, merged from all its streams."""

    time: float = 0.0
    latitude: float = 0.0
    longitude: float = 0.0
    altitude: float = 0.0
    distance: float = 0.0
    heart_rate: float = 0.0
    temperature: float = 0.0


@define
class SportStyle:
    """How the map draws an activity type: its label and color (`#RRGGBB`), from the browser's sport table."""

    label: str = ""
    color: str = ""


@define
class TrackExportRequest:
    """An export of the tracks in an area (and its preview, which reads only the area and the filters).

    The export holds the chosen `activity_ids` when they're given (from the preview, ticked in the export dialog),
    else the tracks the area and filters find.

    Attributes:
        bbox: The area, `[west, south, east, north]` in degrees.
        sports: Activity types to export (the map's sport filter); empty exports every type.
        date_from: First local start date (`YYYYMMDD`), inclusive (the map's date filter).
        date_to: Last local start date (`YYYYMMDD`), inclusive.
        mode: `whole` exports every track that meets the area in full; `clip` exports the pieces inside it.
        folder: The CalTopo folder to put the tracks in; None (or blank) for none.
        units: `ft` or `m`, for the descriptions.
        styles: Each activity type's label and color.
        stroke_width: The lines' width, in pixels.
        activity_ids: The tracks to export, in place of the filters' search; None searches.
    """

    bbox: list[float] = field(factory=list)
    sports: list[str] = field(factory=list)
    date_from: int = 0
    date_to: int = 99_991_231
    mode: str = "whole"
    folder: str | None = None
    units: str = "m"
    styles: dict[str, SportStyle] = field(factory=dict)
    stroke_width: int = 3
    activity_ids: list[str] | None = None


@define
class ExportPreview:
    """The tracks an export would hold (newest first), and the most an export may hold."""

    activity_ids: list[str]
    limit: int
