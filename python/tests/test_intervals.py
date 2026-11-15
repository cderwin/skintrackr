import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from skintrackr.clients.intervals import (
    API_URL,
    STREAM_TYPES,
    SUMMARY_FIELDS,
    IntervalsClient,
    IntervalsError,
    RateLimiter,
    merge_streams,
)
from skintrackr.models import Activity, Stream
from tests.conftest import ACTIVITY, ACTIVITY_ID, API_KEY, ATHLETE_ID, FakeIntervals, basic_auth_key, summary

# --- requests


def test_request_sends_api_key_as_basic_auth(intervals: FakeIntervals) -> None:
    intervals.add("GET", "/athlete/0", {"id": "i1"})
    assert asyncio.run(intervals.client().request("GET", f"{API_URL}/athlete/0")) == b'{"id":"i1"}'
    assert basic_auth_key(intervals.requests[0]) == API_KEY


def test_request_without_key_sends_no_authorization(intervals: FakeIntervals) -> None:
    with pytest.raises(IntervalsError):  # the fake answers 401
        asyncio.run(intervals.client(api_key="").request("GET", f"{API_URL}/x", headers={"X-Test": "1"}))
    assert "Authorization" not in intervals.requests[0].headers
    assert intervals.requests[0].headers["X-Test"] == "1"


@pytest.mark.parametrize("status", [301, 400, 401, 404, 429, 500])
def test_request_raises_on_bad_status(intervals: FakeIntervals, status: int) -> None:
    intervals.add("GET", "/athlete/0", {}, status=status)
    with pytest.raises(IntervalsError, match=f"invalid status {status}") as error:
        asyncio.run(intervals.client().request("GET", f"{API_URL}/athlete/0"))
    assert error.value.status_code == status
    assert error.value.unauthorized is (status == 401)


def test_wrong_key_is_unauthorized(intervals: FakeIntervals) -> None:
    intervals.add_athlete()
    with pytest.raises(IntervalsError) as error:
        asyncio.run(intervals.client("wrong").get_athlete())
    assert error.value.unauthorized


def test_request_raises_on_network_error() -> None:
    def fail(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    client = IntervalsClient(http=httpx.AsyncClient(transport=httpx.MockTransport(fail)))
    with pytest.raises(IntervalsError, match="unknown error"):
        asyncio.run(client.request("GET", f"{API_URL}/athlete/0"))


def test_request_saves_bodies_in_debug_mode(intervals: FakeIntervals, tmp_path: Path) -> None:
    intervals.add("GET", "/athlete/0", {"id": "i1"})
    client = IntervalsClient(http=intervals.http(), api_key=API_KEY, debug_dir=tmp_path)
    asyncio.run(client.request("GET", f"{API_URL}/athlete/0"))
    (saved,) = tmp_path.glob(".request-debug-*.log")
    assert saved.read_bytes() == b'{"id":"i1"}'


def test_with_api_key_keeps_the_rest(intervals: FakeIntervals) -> None:
    client = intervals.client(api_key="")
    derived = client.with_api_key("k")
    assert (derived.api_key, derived.http, derived.limiter) == ("k", client.http, client.limiter)
    assert client.api_key == ""


# --- athletes and activities


def test_get_athlete(intervals: FakeIntervals) -> None:
    intervals.add_athlete()
    athlete = asyncio.run(intervals.client().get_athlete())
    assert (athlete.id, athlete.name) == (ATHLETE_ID, "Skinner")


def test_list_activities(intervals: FakeIntervals) -> None:
    intervals.add_activities([summary("i1", "2026-01-02"), summary("i2", "2025-12-31"), summary("i3", "2026-01-03")])
    activities = asyncio.run(intervals.client().list_activities(ATHLETE_ID, date(2026, 1, 1)))
    assert [a.id for a in activities] == ["i3", "i1"]
    assert dict(intervals.requests[0].url.params) == {"oldest": "2026-01-01", "fields": SUMMARY_FIELDS}
    assert activities[0].type == "BackcountrySki"


def test_get_activity(intervals: FakeIntervals) -> None:
    intervals.add_activity()
    activity = asyncio.run(intervals.client().get_activity(ACTIVITY_ID))
    assert (activity.id, activity.icu_athlete_id, activity.name) == (ACTIVITY_ID, ATHLETE_ID, "Morning Skin")
    assert activity.description == ""  # null in the response


def test_get_activity_not_found(intervals: FakeIntervals) -> None:
    with pytest.raises(IntervalsError, match="invalid status 404"):
        asyncio.run(intervals.client().get_activity("i999"))


def test_get_activity_stream_requests_types(intervals: FakeIntervals) -> None:
    intervals.add_activity()
    points = asyncio.run(intervals.client().get_activity_stream(ACTIVITY_ID))
    assert dict(intervals.requests[0].url.params) == {"types": STREAM_TYPES}
    assert [(p.time, p.latitude, p.longitude, p.altitude, p.distance) for p in points] == [
        (0, 47.6062, -122.3321, 56.2, 0),
        (5, 47.60625, -122.33215, 57.0, 6.5),
    ]


def test_archive_streams_of_activity_without_streams(intervals: FakeIntervals) -> None:
    client = intervals.client()
    assert asyncio.run(client.get_streams_json("i1")) == b"[]"  # every stream: a 404 means none
    assert "types" not in intervals.requests[0].url.params
    with pytest.raises(IntervalsError):
        asyncio.run(client.get_streams_json("i1", STREAM_TYPES))  # GPX export still needs them


def test_export_activity_gpx(intervals: FakeIntervals) -> None:
    intervals.add_activity()
    client = intervals.client()
    document = asyncio.run(client.export_activity_gpx(asyncio.run(client.get_activity(ACTIVITY_ID))))
    assert document.startswith(b"<?xml")
    assert b"<name>Morning Skin</name>" in document
    assert b"<time>2024-01-15T08:30:05Z</time>" in document


def test_export_activity_gpx_rejects_invalid_start_date(intervals: FakeIntervals) -> None:
    with pytest.raises(IntervalsError, match="error parsing activity start date"):
        asyncio.run(intervals.client().export_activity_gpx(Activity(id="i1", start_date="yesterday")))
    assert intervals.requests == []


def test_export_activity_gpx_stream_error(intervals: FakeIntervals) -> None:
    activity = Activity(id=ACTIVITY_ID, start_date=str(ACTIVITY["start_date"]))
    with pytest.raises(IntervalsError, match="invalid status 404"):
        asyncio.run(intervals.client().export_activity_gpx(activity))


# --- merging streams


def test_merge_streams_all_types() -> None:
    points = merge_streams([Stream("heartrate", [120]), Stream("temp", [-4])])
    assert (points[0].heart_rate, points[0].temperature) == (120, -4)


def test_merge_streams_empty() -> None:
    assert merge_streams([]) == []


def test_merge_streams_drops_samples_without_a_position_and_fills_gaps() -> None:
    points = merge_streams(
        [
            Stream("time", [0, 1, 2, 3]),
            Stream("latlng", [None, 47.1, None, 47.3], [None, -121.1, -121.2, -121.3]),
            Stream("altitude", [None, 1000.0, None, 1002.0]),
        ]
    )
    assert [(p.time, p.latitude, p.altitude) for p in points] == [(1, 47.1, 1000.0), (3, 47.3, 1002.0)]
    filled = merge_streams([Stream("altitude", [None, 5.0, None, 7.0])])
    assert [p.altitude for p in filled] == [5.0, 5.0, 5.0, 7.0]


def test_merge_streams_skips_unknown_types_when_asked() -> None:
    assert len(merge_streams([Stream("watts", [1, 2]), Stream("time", [0, 1])], skip_unknown=True)) == 2


@pytest.mark.parametrize(
    ("streams", "message"),
    [
        ([Stream("time", [0, 1]), Stream("altitude", [1])], "does not match size of first stream"),
        ([Stream("time", [0, 1]), Stream("latlng", [1.0, 2.0], [3.0])], 'stream "latlng": size \\(1\\)'),
        ([Stream("watts", [100])], "unrecognized stream type: watts"),
        ([Stream("time", ["x"])], 'error decoding stream "time"'),
    ],
)
def test_merge_streams_rejects_malformed_streams(streams: list[Stream], message: str) -> None:
    with pytest.raises(IntervalsError, match=message):
        merge_streams(streams)


# --- rate limits

NOON = datetime(2026, 11, 14, 12, 7, tzinfo=UTC)


class Clock:
    """A fake clock that sleeping advances."""

    def __init__(self, now: datetime = NOON) -> None:
        self.now = now
        self.slept: list[float] = []

    def __call__(self) -> datetime:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self.now += timedelta(seconds=seconds)


def limiter(limits: tuple[int, int] = (100, 1000), spacing: float = 0.0) -> tuple[RateLimiter, Clock]:
    clock = Clock()
    return RateLimiter(limits=limits, spacing=spacing, clock=clock, sleep=clock.sleep), clock


def headers(remaining: str, limit: str = "100,1000") -> dict[str, str]:
    return {"X-RateLimit-Limit": limit, "X-RateLimit-Remaining": remaining}


@pytest.mark.parametrize(
    ("remaining", "delay"),
    [
        (None, 0.0),  # nothing known yet
        ("90,900", 0.0),
        ("10,900", 8 * 60),  # within 10% of the 15-minute limit: until 12:15
        ("90,100", 11 * 3600 + 53 * 60),  # within 10% of the daily limit: until midnight UTC
    ],
)
def test_limiter_delay_from_reported_usage(remaining: str | None, delay: float) -> None:
    rate, _ = limiter()
    if remaining:
        rate.update(headers(remaining))
    assert rate.delay() == delay


def test_limiter_takes_limits_from_responses_and_ignores_garbage() -> None:
    rate, _ = limiter(limits=(10_000, 100_000))
    rate.update(headers("5,500", limit="200,2000"))
    assert rate.limits == (200, 2000) and rate.delay() == 8 * 60
    rate.update({"X-RateLimit-Limit": "nonsense", "X-RateLimit-Remaining": "1,1"})
    assert rate.limits == (200, 2000)


def test_limiter_counts_requests_without_headers_and_resets_each_window() -> None:
    rate, clock = limiter(limits=(10, 1000))
    for _ in range(9):
        rate.update({})
    assert rate.delay() == 8 * 60  # 9 of 10 in this quarter hour
    asyncio.run(rate.wait())
    assert clock.slept == [8 * 60]
    assert rate.delay() == 0.0  # a new window
    rate.update({})
    assert rate.delay() == 0.0


def test_limiter_paces_background_requests() -> None:
    rate, clock = limiter(spacing=0.2)
    asyncio.run(rate.wait())
    asyncio.run(rate.wait())
    assert clock.slept == [0.2]


@pytest.mark.parametrize(("retry_after", "delay"), [("30", 30.0), (None, 60.0), ("soon", 60.0)])
def test_limiter_block(retry_after: str | None, delay: float) -> None:
    rate, _ = limiter()
    rate.block(retry_after)
    assert rate.delay() == delay


def test_background_requests_wait_and_retry_on_429(intervals: FakeIntervals) -> None:
    rate, clock = limiter()
    responses = iter([httpx.Response(429, headers={"Retry-After": "12"}), httpx.Response(200, json={"ok": 1})])
    intervals.routes[("GET", "/x")] = lambda _: next(responses)
    client = IntervalsClient(http=intervals.http(), api_key=API_KEY, limiter=rate).in_background()

    assert asyncio.run(client.request("GET", "https://intervals.icu/x")) == b'{"ok":1}'
    assert clock.slept == [12.0]
    assert len(intervals.requests) == 2


def test_foreground_requests_neither_wait_nor_retry(intervals: FakeIntervals) -> None:
    rate, clock = limiter()
    rate.update(headers("0,0"))
    intervals.routes[("GET", "/x")] = lambda _: httpx.Response(429)
    client = IntervalsClient(http=intervals.http(), api_key=API_KEY, limiter=rate)
    with pytest.raises(IntervalsError):
        asyncio.run(client.request("GET", "https://intervals.icu/x"))
    assert clock.slept == []


def test_derived_clients_share_the_limiter(intervals: FakeIntervals) -> None:
    client = intervals.client()
    assert client.with_api_key("k").in_background().limiter is client.limiter
