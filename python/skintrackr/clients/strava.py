"""StravaClient: every request skintrackr makes to Strava's OAuth and API endpoints."""

import asyncio
import secrets
from collections.abc import Awaitable, Callable, Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Self
from urllib.parse import urlencode

import httpx
from attrs import define, evolve, field, frozen

from skintrackr.gpx import GpxMetadata, build_gpx
from skintrackr.logging import get_logger
from skintrackr.models import ActivitySummary, RawStream, StravaActivity, StreamPoint, Subscription, TokenResponse, loads

logger = get_logger(__name__)

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
SUBSCRIPTIONS_URL = f"{API_URL}/push_subscriptions"
SCOPE = "read,activity:read_all"
# streams requested for GPX exports
STREAM_KEYS = "latlng,altitude,time"
# streams archived with each activity: everything Strava records (it returns only those the activity has)
ARCHIVE_STREAM_KEYS = "time,latlng,distance,altitude,velocity_smooth,heartrate,cadence,watts,temp,moving,grade_smooth"
# the most activities Strava lists per page
ACTIVITIES_PER_PAGE = 200

# stream type -> the StreamPoint attribute it fills (latlng fills two, so it's handled separately)
_STREAM_ATTRIBUTES = {
    "time": "time",
    "altitude": "altitude",
    "distance": "distance",
    "heartrate": "heart_rate",
    "temp": "temperature",
}


class StravaError(Exception):
    """A Strava request failed, or Strava's response wasn't what was expected.

    Attributes:
        status_code: The HTTP status, if Strava answered.
    """

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


QUARTER_HOUR = timedelta(minutes=15)


def _next_window(now: datetime, length: timedelta) -> datetime:
    """The start of the next rate-limit window: Strava's run on quarter hours and UTC days."""
    start_of_day = now.replace(hour=0, minute=0, second=0, microsecond=0)
    return start_of_day + ((now - start_of_day) // length + 1) * length


def _parse_pair(value: str | None) -> tuple[int, int] | None:
    try:
        short, daily = (int(part) for part in (value or "").split(","))
    except ValueError:
        return None
    return short, daily


@define
class RateLimiter:
    """Strava's app-wide read rate limits (per 15 minutes and per day), tracked from its response headers.

    Every response updates the usage. Background requests (imports) wait for the next window once usage comes
    within `headroom` of a limit, which keeps that share for webhooks and pages: those never wait.

    Attributes:
        headroom: Share of each limit kept free of background requests.
        clock: Current time (for tests).
        sleep: How to wait (for tests).
    """

    headroom: float = 0.1
    clock: Callable[[], datetime] = field(default=lambda: datetime.now(UTC), repr=False)
    sleep: Callable[[float], Awaitable[None]] = field(default=asyncio.sleep, repr=False)
    _usage: tuple[int, int] = field(default=(0, 0), init=False)
    _limits: tuple[int, int] | None = field(default=None, init=False)
    _blocked_until: datetime | None = field(default=None, init=False)

    def update(self, headers: Mapping[str, str]) -> None:
        """Record the usage a response reports (read limits if present, else the overall ones)."""
        headers = {name.lower(): value for name, value in headers.items()}
        usage = _parse_pair(headers.get("x-readratelimit-usage") or headers.get("x-ratelimit-usage"))
        limits = _parse_pair(headers.get("x-readratelimit-limit") or headers.get("x-ratelimit-limit"))
        if usage is not None and limits is not None:
            self._usage, self._limits = usage, limits

    def block(self) -> None:
        """Hold background requests until the next 15-minute window (after a 429)."""
        self._blocked_until = _next_window(self.clock(), QUARTER_HOUR)

    def _near_limit(self, window: int) -> bool:
        return self._limits is not None and self._usage[window] >= self._limits[window] * (1 - self.headroom)

    def delay(self) -> float:
        """Seconds a background request should wait now."""
        now = self.clock()
        if self._near_limit(1):
            until = _next_window(now, timedelta(days=1))
        elif self._near_limit(0):
            until = _next_window(now, QUARTER_HOUR)
        else:
            until = self._blocked_until or now
        return max(0.0, (until - now).total_seconds())

    async def wait(self) -> None:
        """Wait until a background request may be sent."""
        if (seconds := self.delay()) > 0:
            logger.info("waiting for the Strava rate limit window", extra={"seconds": round(seconds)})
            daily_reset = self._near_limit(1)
            await self.sleep(seconds)
            # the window has reset; the next response reports the actual usage
            self._usage = (0, 0 if daily_reset else self._usage[1])
            self._blocked_until = None


@frozen
class StravaClient:
    """Async client for Strava.

    One client (and its connection pool) is shared by the whole server; `with_token` derives a client that
    acts for a particular athlete.

    Attributes:
        http: HTTP client to send requests with.
        token: Athlete access token sent as a bearer token; empty for app-level requests.
        client_id: Strava API application id, for OAuth and subscription requests.
        client_secret: Strava API application secret, for OAuth and subscription requests.
        debug_dir: If set, every response body is also written here (`DEBUG_STRAVA_RESPONSE_BODY`).
        limiter: App-wide rate limits, shared by every client derived from this one.
        background: Requests wait for the rate limit (and retry on 429) rather than competing with pages.
    """

    http: httpx.AsyncClient
    token: str = field(default="", repr=False)
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    debug_dir: Path | None = None
    limiter: RateLimiter = field(factory=RateLimiter)
    background: bool = False

    def with_token(self, token: str) -> Self:
        """A client that sends requests as the athlete `token` belongs to."""
        return evolve(self, token=token)

    def in_background(self) -> Self:
        """A client for background work (imports): it waits for rate-limit windows instead of failing."""
        return evolve(self, background=True)

    async def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, str] | None = None,
        form: Mapping[str, str] | None = None,
        headers: Mapping[str, str] | None = None,
    ) -> bytes:
        """Send a request to Strava and return the response body.

        Args:
            method: HTTP method.
            url: Absolute URL.
            params: Query parameters.
            form: Fields to send as an `application/x-www-form-urlencoded` body.
            headers: Extra headers. The bearer token, if any, is added automatically.

        Raises:
            StravaError: The request failed or Strava answered with a non-2xx status.
        """
        all_headers = dict(headers or {})
        if self.token:
            all_headers["Authorization"] = f"Bearer {self.token}"
        while True:
            if self.background:
                await self.limiter.wait()
            try:
                response = await self.http.request(method, url, params=params, data=form, headers=all_headers)
            except httpx.HTTPError as e:
                logger.error("unknown http exception", extra={"method": method, "url": url, "err": str(e)})
                raise StravaError(f"http request failed: unknown error: {e}") from e
            self.limiter.update(response.headers)
            if not (response.status_code == 429 and self.background):
                break
            logger.warning("rate limited by Strava; waiting for the next window", extra={"url": url})
            self.limiter.block()

        if self.debug_dir is not None:
            body_path = self.debug_dir / f".request-debug-{secrets.token_urlsafe(18)}.log"
            logger.debug("serializing response body for debugging", extra={"url": url, "body_path": str(body_path)})
            body_path.write_bytes(response.content)

        if not response.is_success:
            logger.error(
                "http response received with bad status_code",
                extra={"method": method, "url": url, "status_code": response.status_code},
            )
            raise StravaError(f"http request failed, invalid status {response.status_code}", response.status_code)
        return response.content

    # --- OAuth

    def authorization_url(self, redirect_uri: str, state: str | None = None) -> str:
        """Strava's authorization page, which sends the athlete back to `redirect_uri` with a code."""
        params = {"client_id": self.client_id, "redirect_uri": redirect_uri, "response_type": "code", "scope": SCOPE}
        if state is not None:
            params["state"] = state
        return f"{AUTH_URL}?{urlencode(params)}"

    async def exchange_code(self, code: str) -> TokenResponse:
        """Exchange an authorization code from the OAuth callback for the athlete's tokens."""
        return await self._token_request({"code": code, "grant_type": "authorization_code"})

    async def refresh_token(self, refresh_token: str) -> TokenResponse:
        """Get a new access token with a refresh token."""
        return await self._token_request({"refresh_token": refresh_token, "grant_type": "refresh_token"})

    async def _token_request(self, fields: Mapping[str, str]) -> TokenResponse:
        form = {"client_id": self.client_id, "client_secret": self.client_secret, **fields}
        return _decode(await self.request("POST", TOKEN_URL, form=form), TokenResponse)

    # --- webhook subscriptions

    async def list_subscriptions(self) -> list[Subscription]:
        """The app's webhook subscriptions (Strava allows at most one)."""
        params = {"client_id": self.client_id, "client_secret": self.client_secret}
        return _decode(await self.request("GET", SUBSCRIPTIONS_URL, params=params), list[Subscription])

    async def create_subscription(self, callback_url: str, verify_token: str) -> Subscription:
        """Subscribe to webhook events; Strava first verifies `callback_url` with `verify_token`."""
        form = {
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "callback_url": callback_url,
            "verify_token": verify_token,
        }
        return _decode(await self.request("POST", SUBSCRIPTIONS_URL, form=form), Subscription)

    # --- activities

    async def list_activities(self, after: datetime, before: datetime | None = None) -> list[ActivitySummary]:
        """The athlete's activities that started between `after` and `before` (all pages)."""
        params = {"after": str(int(after.timestamp())), "per_page": str(ACTIVITIES_PER_PAGE)}
        if before is not None:
            params["before"] = str(int(before.timestamp()))
        activities: list[ActivitySummary] = []
        page = 1
        while True:
            body = await self.request("GET", f"{API_URL}/athlete/activities", params={**params, "page": str(page)})
            batch = _decode(body, list[ActivitySummary])
            activities.extend(batch)
            if len(batch) < ACTIVITIES_PER_PAGE:
                return activities
            page += 1

    async def get_activity_json(self, activity_id: str) -> bytes:
        """Fetch an activity's details, as Strava's JSON (a DetailedActivity)."""
        return await self.request("GET", f"{API_URL}/activities/{activity_id}")

    async def get_activity(self, activity_id: str) -> StravaActivity:
        """Fetch an activity's details."""
        return _decode(await self.get_activity_json(activity_id), StravaActivity)

    async def get_streams_json(self, activity_id: str, keys: str = STREAM_KEYS) -> bytes:
        """Fetch an activity's streams (comma-separated `keys`), as Strava's JSON (a list of streams).

        Manual activities have no streams: Strava answers 404, which is returned as an empty list.
        """
        try:
            return await self.request("GET", f"{API_URL}/activities/{activity_id}/streams", params={"keys": keys})
        except StravaError as e:
            if e.status_code == 404 and keys == ARCHIVE_STREAM_KEYS:
                return b"[]"
            raise

    async def get_activity_stream(self, activity_id: str) -> list[StreamPoint]:
        """Fetch an activity's location, altitude and time streams, merged into one point per sample.

        Raises:
            StravaError: The request failed, or the streams are malformed or of different lengths.
        """
        return merge_streams(_decode(await self.get_streams_json(activity_id), list[RawStream]))

    async def export_activity_gpx(
        self, activity: StravaActivity, *, use_heart_rate: bool = False, use_temperature: bool = False
    ) -> bytes:
        """Export an activity's track as a GPX document.

        Args:
            activity: The activity, from `get_activity`.
            use_heart_rate: Include heart rate on each point.
            use_temperature: Include temperature on each point.

        Raises:
            StravaError: The activity's start date is invalid, or fetching its streams failed.
        """
        start = _start_time(activity)
        points = await self.get_activity_stream(str(activity.id))
        return activity_gpx(activity, points, start, use_heart_rate=use_heart_rate, use_temperature=use_temperature)


def _start_time(activity: StravaActivity) -> datetime:
    try:
        return datetime.fromisoformat(activity.start_date)
    except ValueError as e:
        raise StravaError(f"error parsing activity start date {activity.start_date!r}: {e}") from e


def activity_gpx(
    activity: StravaActivity,
    points: list[StreamPoint],
    start: datetime | None = None,
    *,
    use_heart_rate: bool = False,
    use_temperature: bool = False,
) -> bytes:
    """An activity's track as a GPX document, from its merged streams.

    Raises:
        StravaError: The activity's start date is invalid.
    """
    metadata = GpxMetadata(
        name=activity.name,
        type=activity.type,
        time=start or _start_time(activity),
        use_heart_rate=use_heart_rate,
        use_temperature=use_temperature,
    )
    return build_gpx(points, metadata)


def merge_streams(streams: list[RawStream], *, skip_unknown: bool = False) -> list[StreamPoint]:
    """Merge Strava streams (one list of values per type) into one point per sample.

    Args:
        streams: Strava's streams.
        skip_unknown: Ignore stream types a StreamPoint has no field for (e.g. in archived streams),
            rather than raising.

    Raises:
        StravaError: A stream's length differs from the first stream's or its own `original_size`, a value
            is malformed, or a stream type is unknown.
    """
    if skip_unknown:
        streams = [s for s in streams if s.type == "latlng" or s.type in _STREAM_ATTRIBUTES]
    if not streams:
        return []
    size = streams[0].original_size
    points = [StreamPoint() for _ in range(size)]
    for stream in streams:
        if len(stream.data) != size:
            raise StravaError(
                f'error validating stream "{stream.type}": size ({len(stream.data)}) does not match size of first stream ({size})'
            )
        if len(stream.data) != stream.original_size:
            raise StravaError(
                f'error validating stream "{stream.type}": size ({len(stream.data)}) does not match original_size metadata ({stream.original_size})'
            )
        if stream.type != "latlng" and stream.type not in _STREAM_ATTRIBUTES:
            raise StravaError(f"unrecognized stream type: {stream.type}")
        try:
            for point, value in zip(points, stream.data, strict=True):
                if stream.type == "latlng":
                    point.latitude, point.longitude = float(value[0]), float(value[1])
                else:
                    setattr(point, _STREAM_ATTRIBUTES[stream.type], float(value))
        except (TypeError, ValueError, IndexError) as e:
            raise StravaError(f'error decoding stream "{stream.type}": {e}') from e
    return points


def _decode[T](body: bytes, cls: type[T]) -> T:
    try:
        return loads(body, cls)
    except Exception as e:
        raise StravaError(f"error decoding response as {getattr(cls, '__name__', cls)}: {e}") from e
