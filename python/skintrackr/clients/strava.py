"""StravaClient: every request skintrackr makes to Strava's OAuth and API endpoints."""

import secrets
from collections.abc import Mapping
from datetime import datetime
from pathlib import Path
from typing import Self
from urllib.parse import urlencode

import httpx
from attrs import evolve, field, frozen

from skintrackr.gpx import GpxMetadata, build_gpx
from skintrackr.logging import get_logger
from skintrackr.models import RawStream, StravaActivity, StreamPoint, Subscription, TokenResponse, loads

logger = get_logger(__name__)

AUTH_URL = "https://www.strava.com/oauth/authorize"
TOKEN_URL = "https://www.strava.com/oauth/token"
API_URL = "https://www.strava.com/api/v3"
SUBSCRIPTIONS_URL = f"{API_URL}/push_subscriptions"
SCOPE = "read,activity:read_all"
# streams requested for GPX exports
STREAM_KEYS = "latlng,altitude,time"

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
    """

    http: httpx.AsyncClient
    token: str = field(default="", repr=False)
    client_id: str = ""
    client_secret: str = field(default="", repr=False)
    debug_dir: Path | None = None

    def with_token(self, token: str) -> Self:
        """A client that sends requests as the athlete `token` belongs to."""
        return evolve(self, token=token)

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
        try:
            response = await self.http.request(method, url, params=params, data=form, headers=all_headers)
        except httpx.HTTPError as e:
            logger.error("unknown http exception", extra={"method": method, "url": url, "err": str(e)})
            raise StravaError(f"http request failed: unknown error: {e}") from e

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

    async def get_activity(self, activity_id: str) -> StravaActivity:
        """Fetch an activity's details."""
        return _decode(await self.request("GET", f"{API_URL}/activities/{activity_id}"), StravaActivity)

    async def get_activity_stream(self, activity_id: str) -> list[StreamPoint]:
        """Fetch an activity's location, altitude and time streams, merged into one point per sample.

        Raises:
            StravaError: The request failed, or the streams are malformed or of different lengths.
        """
        url = f"{API_URL}/activities/{activity_id}/streams"
        streams = _decode(await self.request("GET", url, params={"keys": STREAM_KEYS}), list[RawStream])
        return merge_streams(streams)

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
        try:
            start = datetime.fromisoformat(activity.start_date)
        except ValueError as e:
            raise StravaError(f"error parsing activity start date {activity.start_date!r}: {e}") from e
        points = await self.get_activity_stream(str(activity.id))
        metadata = GpxMetadata(
            name=activity.name,
            type=activity.type,
            time=start,
            use_heart_rate=use_heart_rate,
            use_temperature=use_temperature,
        )
        return build_gpx(points, metadata)


def merge_streams(streams: list[RawStream]) -> list[StreamPoint]:
    """Merge Strava streams (one list of values per type) into one point per sample.

    Raises:
        StravaError: A stream's length differs from the first stream's or its own `original_size`, a value
            is malformed, or a stream type is unknown.
    """
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
