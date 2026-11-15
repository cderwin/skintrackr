"""IntervalsClient: every request skintrackr makes to the intervals.icu API (https://intervals.icu/api-docs.html)."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any, Self

import attrs
import httpx
from attrs import define, evolve, field, frozen

from skintrackr.gpx import GpxMetadata, build_gpx
from skintrackr.logging import get_logger
from skintrackr.models import Activity, ActivitySummary, Athlete, Stream, StreamPoint, loads

logger = get_logger(__name__)

API_URL = "https://intervals.icu/api/v1"
# API keys are sent with HTTP Basic auth, with this username and the key as the password
API_KEY_USERNAME = "API_KEY"
# the athlete id that stands for the API key's own athlete
SELF = "0"
# streams requested for GPX exports (the archive requests every stream)
STREAM_TYPES = "latlng,altitude,time"
# the activity list asks only for what the index keeps
SUMMARY_FIELDS = ",".join(a.name for a in attrs.fields(ActivitySummary))
# sources intervals.icu lists but doesn't serve through its API: their activities are stubs
UNAVAILABLE_SOURCES = frozenset({"STRAVA"})

# stream type -> the StreamPoint attribute it fills (latlng fills two, so it's handled separately)
_STREAM_ATTRIBUTES = {
    "time": "time",
    "altitude": "altitude",
    "distance": "distance",
    "heartrate": "heart_rate",
    "temp": "temperature",
}


class IntervalsError(Exception):
    """An intervals.icu request failed, or its response wasn't what was expected.

    Attributes:
        status_code: The HTTP status, if intervals.icu answered.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code

    @property
    def unauthorized(self) -> bool:
        """intervals.icu rejected the API key (it's wrong, or was revoked)."""
        return self.status_code in (401, 403)


QUARTER_HOUR = timedelta(minutes=15)
DAY = timedelta(days=1)
# requests per 15 minutes and per UTC day; responses that report limits override these
DEFAULT_LIMITS = (6_250, 50_000)
# seconds between background requests: intervals.icu also limits each IP to 10 requests a second (unreported)
BACKGROUND_SPACING = 0.2
# wait after a 429 that has no Retry-After
DEFAULT_RETRY_AFTER = 60.0


def _window_start(now: datetime, length: timedelta) -> datetime:
    """The start of the rate-limit window `now` is in: windows run on quarter hours and UTC days."""
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day + (now - start_of_day) // length * length


def _parse_pair(value: str | None) -> tuple[int, int] | None:
    try:
        short, daily = (int(part) for part in (value or "").split(","))
    except ValueError:
        return None
    return short, daily


@define
class RateLimiter:
    """intervals.icu's rate limits (per 15 minutes and per UTC day), and pacing for background requests.

    Responses may report the limits (`X-RateLimit-Limit` and `X-RateLimit-Remaining`, each "15-minute,daily");
    otherwise the limiter counts the requests it sees against `limits`. Background requests (imports, syncs) are
    spaced `spacing` seconds apart, and wait for the next window once usage comes within `headroom` of a limit,
    which keeps that share for pages: those never wait.

    Attributes:
        limits: Requests allowed per 15 minutes and per day, until a response reports them.
        headroom: Share of each limit kept free of background requests.
        spacing: Minimum seconds between background requests.
        clock: Current time (for tests).
        sleep: How to wait (for tests).
    """

    limits: tuple[int, int] = DEFAULT_LIMITS
    headroom: float = 0.1
    spacing: float = BACKGROUND_SPACING
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    _usage: tuple[int, int] = field(default=(0, 0), init=False)
    _windows: tuple[datetime, datetime] | None = field(default=None, init=False)
    _blocked_until: datetime | None = field(default=None, init=False)
    _last_background: datetime | None = field(default=None, init=False)

    def _roll(self, now: datetime) -> None:
        """Start counting afresh in each window that has ended."""
        windows = (_window_start(now, QUARTER_HOUR), _window_start(now, DAY))
        previous = self._windows or windows
        self._usage = (
            self._usage[0] if windows[0] == previous[0] else 0,
            self._usage[1] if windows[1] == previous[1] else 0,
        )
        self._windows = windows

    def update(self, headers: Mapping[str, str]) -> None:
        """Record a response: the usage it reports, else one more request."""
        self._roll(self.clock())
        headers = {name.lower(): value for name, value in headers.items()}
        limits = _parse_pair(headers.get("x-ratelimit-limit"))
        remaining = _parse_pair(headers.get("x-ratelimit-remaining"))
        if limits is not None and remaining is not None:
            self.limits = limits
            self._usage = (limits[0] - remaining[0], limits[1] - remaining[1])
        else:
            self._usage = (self._usage[0] + 1, self._usage[1] + 1)

    def block(self, retry_after: str | None = None) -> None:
        """Hold background requests after a 429: for `Retry-After` seconds, or a minute."""
        try:
            seconds = float(retry_after or DEFAULT_RETRY_AFTER)
        except ValueError:
            seconds = DEFAULT_RETRY_AFTER
        self._blocked_until = self.clock() + timedelta(seconds=seconds)

    def _near_limit(self, window: int) -> bool:
        return self._usage[window] >= self.limits[window] * (1 - self.headroom)

    def delay(self) -> float:
        """Seconds a background request should wait now."""
        now = self.clock()
        self._roll(now)
        if self._near_limit(1):
            until = _window_start(now, DAY) + DAY
        elif self._near_limit(0):
            until = _window_start(now, QUARTER_HOUR) + QUARTER_HOUR
        else:
            paced = self._last_background + timedelta(seconds=self.spacing) if self._last_background else now
            until = max(paced, self._blocked_until or now)
        return max(0.0, (until - now).total_seconds())

    async def wait(self) -> None:
        """Wait until a background request may be sent."""
        if (seconds := self.delay()) > 0:
            if seconds > 1:  # not just pacing
                logger.info("waiting for the intervals.icu rate limit", extra={"seconds": round(seconds)})
            await self.sleep(seconds)
        self._last_background = self.clock()


@frozen
class IntervalsClient:
    """Async client for intervals.icu.

    One client (and its connection pool) is shared by the whole server; `with_api_key` derives a client that
    acts for a particular athlete.

    Attributes:
        http: HTTP client to send requests with.
        api_key: The athlete's API key; empty for requests without one.
        debug_dir: If set, every response body is also written here (`DEBUG_INTERVALS_RESPONSE_BODY`).
        limiter: Rate limits, shared by every client derived from this one.
        background: Requests are paced and wait for the rate limit (and retry on 429) rather than competing
            with pages.
    """

    http: httpx.AsyncClient
    api_key: str = field(default="", repr=False)
    debug_dir: Path | None = None
    limiter: RateLimiter = field(factory=RateLimiter)
    background: bool = False

    def with_api_key(self, api_key: str) -> Self:
        """A client that sends requests with an athlete's API key."""
        return evolve(self, api_key=api_key)

    def in_background(self) -> Self:
        """A client for background work (imports, syncs): paced, and waiting out rate limits instead of failing."""
        return evolve(self, background=True)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Send a request to intervals.icu and return the response body.

        Args:
            method: HTTP method.
            url: Absolute URL.
            params: Query parameters.
            headers: Extra headers. The API key, if any, is added automatically.

        Raises:
            IntervalsError: The request failed or intervals.icu answered with a non-2xx status.
        """
        auth = httpx.BasicAuth(API_KEY_USERNAME, self.api_key) if self.api_key else None
        while True:
            if self.background:
                await self.limiter.wait()
            try:
                response = await self.http.request(method, url, params=params, headers=headers, auth=auth)
            except httpx.HTTPError as e:
                logger.error("unknown http exception", extra={"method": method, "url": url, "err": str(e)})
                raise IntervalsError(f"http request failed: unknown error: {e}") from e
            self.limiter.update(response.headers)
            if not (response.status_code == 429 and self.background):
                break
            logger.warning("rate limited by intervals.icu; waiting to retry", extra={"url": url})
            self.limiter.block(response.headers.get("retry-after"))

        if self.debug_dir is not None:
            body_path = self.debug_dir / f".request-debug-{secrets.token_urlsafe(18)}.log"
            logger.debug("serializing response body for debugging", extra={"url": url, "body_path": str(body_path)})
            body_path.write_bytes(response.content)

        if not response.is_success:
            logger.error(
                "http response received with bad status_code",
                extra={"method": method, "url": url, "status_code": response.status_code},
            )
            raise IntervalsError(f"http request failed, invalid status {response.status_code}", response.status_code)
        return response.content

    # --- athletes

    async def get_athlete(self, athlete_id: str = SELF) -> Athlete:
        """An athlete; by default the API key's own (which checks the key)."""
        return _decode(await self.request("GET", f"{API_URL}/athlete/{athlete_id}"), Athlete)

    # --- activities

    async def list_activities(self, athlete_id: str, oldest: date) -> list[ActivitySummary]:
        """The athlete's activities that started on or after `oldest` (a local date), newest first."""
        params = {"oldest": oldest.isoformat(), "fields": SUMMARY_FIELDS}
        body = await self.request("GET", f"{API_URL}/athlete/{athlete_id}/activities", params=params)
        return _decode(body, list[ActivitySummary])

    async def get_activity_json(self, activity_id: str) -> bytes:
        """Fetch an activity, as intervals.icu's JSON."""
        return await self.request("GET", f"{API_URL}/activity/{activity_id}")

    async def get_activity(self, activity_id: str) -> Activity:
        """Fetch an activity."""
        return _decode(await self.get_activity_json(activity_id), Activity)

    async def get_streams_json(self, activity_id: str, types: str | None = None) -> bytes:
        """Fetch an activity's streams (comma-separated `types`; all of them if None), as intervals.icu's JSON.

        Activities without streams (e.g. manual ones) are archived with an empty list: when every stream is asked
        for (by the archive, right after fetching the activity), a 404 is returned as `[]`.
        """
        params = {"types": types} if types else None
        try:
            return await self.request("GET", f"{API_URL}/activity/{activity_id}/streams.json", params=params)
        except IntervalsError as e:
            if e.status_code == 404 and types is None:
                return b"[]"
            raise

    async def get_activity_stream(self, activity_id: str) -> list[StreamPoint]:
        """Fetch an activity's location, altitude and time streams, merged into one point per sample.

        Raises:
            IntervalsError: The request failed, or the streams are malformed or of different lengths.
        """
        return merge_streams(_decode(await self.get_streams_json(activity_id, STREAM_TYPES), list[Stream]))

    async def export_activity_gpx(self, activity: Activity) -> bytes:
        """Export an activity's track as a GPX document.

        Args:
            activity: The activity, from `get_activity`.

        Raises:
            IntervalsError: The activity's start date is invalid, or fetching its streams failed.
        """
        start = _start_time(activity)
        return activity_gpx(activity, await self.get_activity_stream(activity.id), start)


def _start_time(activity: Activity) -> datetime:
    try:
        return datetime.fromisoformat(activity.start_date)
    except ValueError as e:
        raise IntervalsError(f"error parsing activity start date {activity.start_date!r}: {e}") from e


def activity_gpx(activity: Activity, points: list[StreamPoint], start: datetime | None = None) -> bytes:
    """An activity's track as a GPX document, from its merged streams.

    Raises:
        IntervalsError: The activity's start date is invalid.
    """
    metadata = GpxMetadata(name=activity.name, type=activity.type, time=start or _start_time(activity))
    return build_gpx(points, metadata)


def _filled(values: list[Any]) -> list[float]:
    """A stream's values with gaps (nulls) filled by the last value before them (or the first one after)."""
    last = next((value for value in values if value is not None), 0.0)
    filled = []
    for value in values:
        if value is not None:
            last = value
        filled.append(float(last))
    return filled


def merge_streams(streams: list[Stream], *, skip_unknown: bool = False) -> list[StreamPoint]:
    """Merge streams (one list of values per type) into one point per sample.

    Samples without a position (nulls in `latlng`, e.g. GPS dropouts) are left out; gaps in other streams take
    the previous value.

    Args:
        streams: The activity's streams.
        skip_unknown: Ignore stream types a StreamPoint has no field for (e.g. in archived streams),
            rather than raising.

    Raises:
        IntervalsError: A stream's length differs from the first stream's, a value is malformed, or a stream
            type is unknown.
    """
    if skip_unknown:
        streams = [s for s in streams if s.type == "latlng" or s.type in _STREAM_ATTRIBUTES]
    if not streams:
        return []
    size = len(streams[0].data)
    points = [StreamPoint() for _ in range(size)]
    unplaced: set[int] = set()
    for stream in streams:
        if stream.type != "latlng" and stream.type not in _STREAM_ATTRIBUTES:
            raise IntervalsError(f"unrecognized stream type: {stream.type}")
        columns = [stream.data, stream.data2] if stream.type == "latlng" else [stream.data]
        for column in columns:
            if len(column) != size:
                raise IntervalsError(
                    f'error validating stream "{stream.type}": size ({len(column)}) does not match size of first stream ({size})'
                )
        try:
            if stream.type == "latlng":
                for index, (point, latitude, longitude) in enumerate(zip(points, stream.data, stream.data2)):
                    if latitude is None or longitude is None:
                        unplaced.add(index)
                    else:
                        point.latitude, point.longitude = float(latitude), float(longitude)
            else:
                for point, value in zip(points, _filled(stream.data)):
                    setattr(point, _STREAM_ATTRIBUTES[stream.type], value)
        except (TypeError, ValueError) as e:
            raise IntervalsError(f'error decoding stream "{stream.type}": {e}') from e
    return [point for index, point in enumerate(points) if index not in unplaced]


def _decode[T](body: bytes, cls: type[T]) -> T:
    try:
        return loads(body, cls)
    except Exception as e:
        raise IntervalsError(f"error decoding response as {getattr(cls, '__name__', cls)}: {e}") from e
