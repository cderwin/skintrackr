import asyncio

import pytest
from botocore.exceptions import ClientError

from skintrackr.clients.blob_store import BlobStore, activity_keys
from skintrackr.clients.strava import StravaError
from skintrackr.config import BlobStorageConfig
from tests.conftest import ACTIVITY, ACTIVITY_ID, ATHLETE_ID, FakeBucket, FakeStrava

METADATA_KEY = f"activities/{ATHLETE_ID}/metadata/{ACTIVITY_ID}.json"
TRACK_KEY = f"activities/{ATHLETE_ID}/tracks/{ACTIVITY_ID}.gpx"


def test_activity_keys() -> None:
    keys = activity_keys(ATHLETE_ID, ACTIVITY_ID)
    assert (keys.metadata_key, keys.track_key) == (METADATA_KEY, TRACK_KEY)


def test_persist_activity(strava: FakeStrava, bucket: FakeBucket) -> None:
    strava.add_activity()
    bucket.expect_puts(2)
    keys = asyncio.run(bucket.store.persist_activity(strava.client("t"), str(ACTIVITY_ID)))

    assert keys == activity_keys(ATHLETE_ID, ACTIVITY_ID)
    assert set(bucket.objects) == {METADATA_KEY, TRACK_KEY}
    # the metadata has exactly the fields of the Go server's StravaActivity
    assert bucket.json(METADATA_KEY) == {
        "id": ACTIVITY_ID,
        "athlete": {"id": ATHLETE_ID},
        "name": "Morning Skin",
        "distance": 6.5,
        "moving_time": 5,
        "elapsed_time": 5,
        "total_elevation_gain": 0.8,
        "type": "BackcountrySki",
        "start_date": ACTIVITY["start_date"],
        "start_latlng": [47.6062, -122.3321],
        "end_latlng": [47.60625, -122.33215],
        "description": "",
        "calories": 0.0,
        "suffer_score": 3.0,
    }
    assert bucket.objects[METADATA_KEY][1] == "application/json"
    track, content_type = bucket.objects[TRACK_KEY]
    assert content_type == "application/gpx+xml"
    assert b"<name>Morning Skin</name>" in track
    bucket.stubber.assert_no_pending_responses()


def test_persist_activity_strava_error(strava: FakeStrava, bucket: FakeBucket) -> None:
    with pytest.raises(StravaError):
        asyncio.run(bucket.store.persist_activity(strava.client("t"), "404"))
    assert bucket.objects == {}


def test_persist_activity_upload_error(strava: FakeStrava, bucket: FakeBucket) -> None:
    strava.add_activity()
    bucket.expect_put_error()
    with pytest.raises(ClientError):
        asyncio.run(bucket.store.persist_activity(strava.client("t"), str(ACTIVITY_ID)))


def test_build() -> None:
    store = BlobStore.build(BlobStorageConfig(access_key_id="a", secret_access_key="b"))
    assert store.bucket == "skintrackr-storage"
    assert store.s3.meta.endpoint_url == "https://t3.storage.dev"
