"""Data exchanged with Strava and clients, and the converter that (de)serializes it.

Attribute names are the JSON keys, so `converter.unstructure` produces the same documents the Go server
wrote (e.g. the archived activity metadata). Decoding is lenient, like Go's: unknown keys are ignored and
missing or null values take the field's default.
"""

import json
from typing import Any

from attrs import define, field
from cattrs.preconf.json import make_converter

converter = make_converter()

LatLng = tuple[float, float]


def _structure_latlng(value: Any, _: type) -> LatLng:
    # Strava sends [] for activities without GPS; Go decodes that into [0, 0]
    if not value:
        return (0.0, 0.0)
    latitude, longitude = value
    return (float(latitude), float(longitude))


converter.register_structure_hook_func(lambda cls: cls == LatLng, _structure_latlng)


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
class AthleteRef:
    """The athlete an activity belongs to."""

    id: int = 0


@define
class StravaActivity:
    """The parts of a Strava activity skintrackr keeps (the fields of Go's `StravaActivity`)."""

    id: int = 0
    athlete: AthleteRef = field(factory=AthleteRef)
    name: str = ""
    distance: float = 0.0
    moving_time: int = 0
    elapsed_time: int = 0
    total_elevation_gain: float = 0.0
    type: str = ""
    start_date: str = ""
    start_latlng: LatLng = (0.0, 0.0)
    end_latlng: LatLng = (0.0, 0.0)
    description: str = ""
    calories: float = 0.0
    suffer_score: float = 0.0


@define
class ActivityMap:
    """An activity's route: Strava's simplified track, as a Google encoded polyline."""

    summary_polyline: str = ""


@define
class ActivitySummary:
    """One row of an athlete's activity index: decoded from Strava's activity list or an activity's details."""

    id: int = 0
    name: str = ""
    sport_type: str = ""
    start_date_local: str = ""
    total_elevation_gain: float = 0.0
    distance: float = 0.0
    moving_time: int = 0
    map: ActivityMap = field(factory=ActivityMap)


@define
class ImportStatus:
    """Progress of an athlete's bulk import.

    Attributes:
        state: `idle` (never run), `running`, `done` or `failed`.
        activities: Activities in the index after the summary step.
        tracks_done: Activities archived in full (details and streams) so far.
        tracks_total: Activities to archive in full.
        error: Why it failed, if it did.
        updated_at: Unix time of the last change.
    """

    state: str = "idle"
    activities: int = 0
    tracks_done: int = 0
    tracks_total: int = 0
    error: str = ""
    updated_at: int = 0


@define
class RawStream:
    """One stream from Strava's activity streams endpoint (`data` holds one value per sample)."""

    type: str
    data: list[Any] = field(factory=list)
    original_size: int = 0


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
class TokenInfo:
    """A Strava access token, its refresh token and its expiry (Unix seconds)."""

    access_token: str
    refresh_token: str
    expires_at: int


@define
class TokenAthlete:
    """The athlete a Strava token exchange is for."""

    id: int = 0
    username: str = ""


@define
class TokenResponse:
    """Strava's response to an authorization-code or refresh-token exchange."""

    access_token: str
    refresh_token: str
    expires_at: int
    token_type: str = ""
    expires_in: int = 0
    athlete: TokenAthlete = field(factory=TokenAthlete)

    def token_info(self) -> TokenInfo:
        """The parts of the response that are stored."""
        return TokenInfo(access_token=self.access_token, refresh_token=self.refresh_token, expires_at=self.expires_at)


@define
class PushEvent:
    """A Strava webhook event (https://developers.strava.com/docs/webhooks/)."""

    object_type: str = ""
    object_id: int = 0
    aspect_type: str = ""
    updates: dict[str, Any] = field(factory=dict)
    owner_id: int = 0
    subscription_id: int = 0
    event_time: int = 0


@define
class Subscription:
    """A Strava webhook subscription."""

    id: int
