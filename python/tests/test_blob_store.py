import asyncio
import json

import pytest
from botocore.exceptions import ClientError

from skintrackr.clients.blob_store import BlobStore, InvalidRangeError, archive_keys, tracks_key
from skintrackr.clients.intervals import IntervalsError
from skintrackr.config import BlobStorageConfig
from tests.conftest import ACTIVITY, ACTIVITY_ID, ATHLETE_ID, STREAMS, FakeBucket, FakeIntervals

OTHER_ATHLETE = "i9999"

KEYS = archive_keys(ATHLETE_ID, ACTIVITY_ID)


def archive(bucket: FakeBucket, intervals: FakeIntervals, activity_id: str = ACTIVITY_ID):
    return asyncio.run(bucket.store.archive_activity(intervals.client(), ATHLETE_ID, activity_id))


def test_archive_keys() -> None:
    prefix = f"athletes/{ATHLETE_ID}/activities/{ACTIVITY_ID}"
    assert (KEYS.activity, KEYS.streams, KEYS.track) == (
        f"{prefix}/activity.json.gz",
        f"{prefix}/streams.json.gz",
        f"{prefix}/track.geojson.gz",
    )
    assert tracks_key(ATHLETE_ID) == f"athletes/{ATHLETE_ID}/tracks.pmtiles"


def test_archive_activity_stores_intervals_json_gzipped(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    intervals.add_activity()
    summary = archive(bucket, intervals)

    assert bucket.keys() == {KEYS.activity, KEYS.streams, KEYS.track}
    assert bucket.json(KEYS.activity) == ACTIVITY  # as received, nothing dropped
    assert bucket.json(KEYS.streams) == STREAMS
    head = bucket.s3.head_object(Bucket=bucket.NAME, Key=KEYS.activity)
    assert (head["ContentType"], head["ContentEncoding"]) == ("application/json", "gzip")
    # every stream is requested
    assert "types" not in intervals.last("GET", f"/activity/{ACTIVITY_ID}/streams.json").url.params
    assert (summary.id, summary.name, summary.total_elevation_gain) == (ACTIVITY_ID, "Morning Skin", 0.8)


def test_archive_activity_writes_its_track(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    intervals.add_activity()
    archive(bucket, intervals)
    assert bucket.json(KEYS.track) == {
        "type": "Feature",
        "properties": {"id": ACTIVITY_ID, "type": "BackcountrySki", "date": 20240115},
        "geometry": {"type": "LineString", "coordinates": [[-122.3321, 47.6062], [-122.33215, 47.60625]]},
    }
    head = bucket.s3.head_object(Bucket=bucket.NAME, Key=KEYS.track)
    assert (head["ContentType"], head["ContentEncoding"]) == ("application/geo+json", "gzip")


def test_archive_activity_without_streams(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    bucket.put(KEYS.track)  # from before the activity's positions were removed
    intervals.add("GET", f"/activity/{ACTIVITY_ID}", ACTIVITY)  # streams answer 404
    archive(bucket, intervals)
    assert bucket.json(KEYS.streams) == []
    assert bucket.keys() == {KEYS.activity, KEYS.streams}  # no track, and the stale one is gone


def test_archive_activity_intervals_error(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    with pytest.raises(IntervalsError):
        archive(bucket, intervals, "i404")
    assert bucket.keys() == set()


def test_archive_activity_upload_error(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    intervals.add_activity()
    bucket.fail_writes()
    with pytest.raises(ClientError):
        archive(bucket, intervals)


def test_read_activity(intervals: FakeIntervals, bucket: FakeBucket) -> None:
    assert asyncio.run(bucket.store.read_activity(ATHLETE_ID, ACTIVITY_ID)) is None
    intervals.add_activity()
    archive(bucket, intervals)
    stored = asyncio.run(bucket.store.read_activity(ATHLETE_ID, ACTIVITY_ID))
    assert stored is not None
    activity, streams = stored
    assert activity.name == "Morning Skin"
    assert [s.type for s in streams] == ["time", "latlng", "altitude", "distance"]
    assert streams[1].data2 == [-122.3321, -122.33215]


def test_archived_activity_ids_count_only_complete_archives(bucket: FakeBucket) -> None:
    bucket.put(archive_keys(ATHLETE_ID, "i1").activity)
    bucket.put(archive_keys(ATHLETE_ID, "i1").streams)
    bucket.put(archive_keys(ATHLETE_ID, "i2").activity)  # streams not written yet
    bucket.put(archive_keys(OTHER_ATHLETE, "i3").streams)  # another athlete
    assert asyncio.run(bucket.store.archived_activity_ids(ATHLETE_ID)) == {"i1"}


def test_track_activity_ids(bucket: FakeBucket) -> None:
    bucket.put(archive_keys(ATHLETE_ID, "i1").track)
    bucket.put(archive_keys(ATHLETE_ID, "i2").streams)  # no positions
    bucket.put(archive_keys(OTHER_ATHLETE, "i3").track)
    assert asyncio.run(bucket.store.track_activity_ids(ATHLETE_ID)) == {"i1"}


def test_read_track_features(bucket: FakeBucket) -> None:
    for activity_id in ("i1", "i2"):
        bucket.put_json(archive_keys(ATHLETE_ID, activity_id).track, {"id": activity_id})
    features = asyncio.run(bucket.store.read_track_features(ATHLETE_ID, ["i2", "missing", "i1"]))
    assert [json.loads(feature) for feature in features] == [{"id": "i2"}, {"id": "i1"}]


def test_get_range(bucket: FakeBucket) -> None:
    bucket.put("athletes/a/tracks.pmtiles", b"0123456789")
    whole = asyncio.run(bucket.store.get_range("athletes/a/tracks.pmtiles", None))
    part = asyncio.run(bucket.store.get_range("athletes/a/tracks.pmtiles", "bytes=2-5"))
    assert whole is not None and part is not None
    assert (whole.body, whole.content_range) == (b"0123456789", None)
    assert (part.body, part.content_range, part.etag) == (b"2345", "bytes 2-5/10", whole.etag)
    assert asyncio.run(bucket.store.get_range("athletes/a/missing.pmtiles", "bytes=0-1")) is None
    with pytest.raises(InvalidRangeError):
        asyncio.run(bucket.store.get_range("athletes/a/tracks.pmtiles", "bytes=20-30"))


def test_delete_activity(bucket: FakeBucket) -> None:
    for activity_id in ("i1", "i2"):
        for key in (keys := archive_keys(ATHLETE_ID, activity_id)).activity, keys.streams, keys.track:
            bucket.put(key)
    asyncio.run(bucket.store.delete_activity(ATHLETE_ID, "i1"))
    assert all("/i2/" in key for key in bucket.keys()) and len(bucket.keys()) == 3


def test_delete_athlete_removes_both_layouts(bucket: FakeBucket) -> None:
    bucket.put(KEYS.activity)
    bucket.put(f"activities/{ATHLETE_ID}/metadata/1.json")
    bucket.put(f"activities/{ATHLETE_ID}/tracks/1.gpx")
    bucket.put(f"activities/{OTHER_ATHLETE}/tracks/2.gpx")
    assert asyncio.run(bucket.store.delete_athlete(ATHLETE_ID)) == 3
    assert bucket.keys() == {f"activities/{OTHER_ATHLETE}/tracks/2.gpx"}


def test_legacy_activity_ids(bucket: FakeBucket) -> None:
    bucket.put(f"activities/{ATHLETE_ID}/metadata/1.json")
    bucket.put(f"activities/{ATHLETE_ID}/tracks/1.gpx")
    bucket.put(f"activities/{ATHLETE_ID}/metadata/2.json")
    assert asyncio.run(bucket.store.legacy_activity_ids(ATHLETE_ID)) == {"1", "2"}


def test_build() -> None:
    store = BlobStore.build(BlobStorageConfig(access_key_id="a", secret_access_key="b"))
    assert store.bucket == "skintrackr-storage"
    assert store.s3.meta.endpoint_url == "https://t3.storage.dev"
