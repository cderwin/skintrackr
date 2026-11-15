import asyncio

import pytest
from botocore.exceptions import ClientError

from skintrackr.clients.blob_store import BlobStore, archive_keys
from skintrackr.clients.strava import StravaError
from skintrackr.config import BlobStorageConfig
from tests.conftest import ACTIVITY, ACTIVITY_ID, ATHLETE_ID, STREAMS, FakeBucket, FakeStrava

KEYS = archive_keys(ATHLETE_ID, ACTIVITY_ID)


def archive(bucket: FakeBucket, strava: FakeStrava, activity_id: int = ACTIVITY_ID):
    return asyncio.run(bucket.store.archive_activity(strava.client("t"), ATHLETE_ID, activity_id))


def test_archive_keys() -> None:
    prefix = f"athletes/{ATHLETE_ID}/activities/{ACTIVITY_ID}"
    assert (KEYS.activity, KEYS.streams) == (f"{prefix}/activity.json.gz", f"{prefix}/streams.json.gz")


def test_archive_activity_stores_strava_json_gzipped(strava: FakeStrava, bucket: FakeBucket) -> None:
    strava.add_activity()
    summary = archive(bucket, strava)

    assert bucket.keys() == {KEYS.activity, KEYS.streams}
    assert bucket.json(KEYS.activity) == ACTIVITY  # as received, nothing dropped
    assert bucket.json(KEYS.streams) == STREAMS
    head = bucket.s3.head_object(Bucket=bucket.NAME, Key=KEYS.activity)
    assert (head["ContentType"], head["ContentEncoding"]) == ("application/json", "gzip")
    # every stream type is requested
    assert "heartrate" in strava.last("GET", f"/api/v3/activities/{ACTIVITY_ID}/streams").url.params["keys"]
    assert (summary.id, summary.name, summary.total_elevation_gain) == (ACTIVITY_ID, "Morning Skin", 0.8)


def test_archive_manual_activity_without_streams(strava: FakeStrava, bucket: FakeBucket) -> None:
    strava.add("GET", f"/api/v3/activities/{ACTIVITY_ID}", ACTIVITY)  # streams answer 404
    archive(bucket, strava)
    assert bucket.json(KEYS.streams) == []


def test_archive_activity_strava_error(strava: FakeStrava, bucket: FakeBucket) -> None:
    with pytest.raises(StravaError):
        archive(bucket, strava, 404)
    assert bucket.keys() == set()


def test_archive_activity_upload_error(strava: FakeStrava, bucket: FakeBucket) -> None:
    strava.add_activity()
    bucket.fail_writes()
    with pytest.raises(ClientError):
        archive(bucket, strava)


def test_read_activity(strava: FakeStrava, bucket: FakeBucket) -> None:
    assert asyncio.run(bucket.store.read_activity(ATHLETE_ID, ACTIVITY_ID)) is None
    strava.add_activity()
    archive(bucket, strava)
    stored = asyncio.run(bucket.store.read_activity(ATHLETE_ID, ACTIVITY_ID))
    assert stored is not None
    activity, streams = stored
    assert activity.name == "Morning Skin"
    assert [s.type for s in streams] == ["latlng", "altitude", "time", "distance"]


def test_archived_activity_ids_count_only_complete_archives(bucket: FakeBucket) -> None:
    bucket.put(archive_keys(ATHLETE_ID, 1).activity)
    bucket.put(archive_keys(ATHLETE_ID, 1).streams)
    bucket.put(archive_keys(ATHLETE_ID, 2).activity)  # streams not written yet
    bucket.put(archive_keys(ATHLETE_ID + 1, 3).streams)  # another athlete
    assert asyncio.run(bucket.store.archived_activity_ids(ATHLETE_ID)) == {1}


def test_delete_activity(bucket: FakeBucket) -> None:
    for activity_id in (1, 2):
        bucket.put(archive_keys(ATHLETE_ID, activity_id).activity)
        bucket.put(archive_keys(ATHLETE_ID, activity_id).streams)
    asyncio.run(bucket.store.delete_activity(ATHLETE_ID, 1))
    assert bucket.keys() == {archive_keys(ATHLETE_ID, 2).activity, archive_keys(ATHLETE_ID, 2).streams}


def test_delete_athlete_removes_both_layouts(bucket: FakeBucket) -> None:
    bucket.put(KEYS.activity)
    bucket.put(f"activities/{ATHLETE_ID}/metadata/1.json")
    bucket.put(f"activities/{ATHLETE_ID}/tracks/1.gpx")
    bucket.put(f"activities/{ATHLETE_ID + 1}/tracks/2.gpx")
    assert asyncio.run(bucket.store.delete_athlete(ATHLETE_ID)) == 3
    assert bucket.keys() == {f"activities/{ATHLETE_ID + 1}/tracks/2.gpx"}


def test_legacy_activity_ids(bucket: FakeBucket) -> None:
    bucket.put(f"activities/{ATHLETE_ID}/metadata/1.json")
    bucket.put(f"activities/{ATHLETE_ID}/tracks/1.gpx")
    bucket.put(f"activities/{ATHLETE_ID}/metadata/2.json")
    assert asyncio.run(bucket.store.legacy_activity_ids(ATHLETE_ID)) == {1, 2}


def test_build() -> None:
    store = BlobStore.build(BlobStorageConfig(access_key_id="a", secret_access_key="b"))
    assert store.bucket == "skintrackr-storage"
    assert store.s3.meta.endpoint_url == "https://t3.storage.dev"
