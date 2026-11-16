"""BlobStore: every S3-compatible blob-storage operation, and archiving activities there.

Layout (the bucket is the source of truth; the Redis activity index is derived from it):

    athletes/{athlete}/activities/{activity}/activity.json.gz   intervals.icu's activity JSON, as received
    athletes/{athlete}/activities/{activity}/streams.json.gz    intervals.icu's streams JSON, every type recorded
    athletes/{athlete}/activities/{activity}/track.geojson.gz   the simplified track (`tracks.track_feature`), if any
    athletes/{athlete}/tracks.pmtiles                           the map's vector tiles of the athlete's tracks

The track and the archive are derived from the streams, so they can be rebuilt (`skintrackr migrate build-tracks`).

The former Go server's layout (`activities/{athlete}/metadata/{id}.json` and `tracks/{id}.gpx`) is no longer
written; the `skintrackr migrate` commands delete it.
"""

import asyncio
import gzip
import re
from concurrent.futures import ThreadPoolExecutor
from typing import TYPE_CHECKING, Self

import boto3
from attrs import frozen
from botocore.exceptions import ClientError

from skintrackr.clients.intervals import IntervalsClient
from skintrackr.config import BlobStorageConfig
from skintrackr.logging import get_logger
from skintrackr.models import Activity, ActivitySummary, Stream, loads
from skintrackr.tracks import encode_feature, track_feature

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import GetObjectRequestTypeDef, ObjectIdentifierTypeDef, PutObjectRequestTypeDef

logger = get_logger(__name__)

# concurrent reads when gathering an athlete's tracks
READ_CONCURRENCY = 16


class InvalidRangeError(ValueError):
    """A ranged read asked for bytes outside the object."""


@frozen
class ArchiveKeys:
    """Where an activity is archived.

    Attributes:
        activity: intervals.icu's activity JSON, gzipped.
        streams: intervals.icu's streams JSON (every type the activity has), gzipped.
        track: The simplified track, a GeoJSON feature, gzipped (only for activities with positions).
    """

    activity: str
    streams: str
    track: str


def athlete_prefix(athlete_id: str) -> str:
    """Everything archived for an athlete."""
    return f"athletes/{athlete_id}/"


def archive_keys(athlete_id: str, activity_id: str) -> ArchiveKeys:
    """The keys an activity is archived under."""
    prefix = f"{athlete_prefix(athlete_id)}activities/{activity_id}/"
    return ArchiveKeys(
        activity=f"{prefix}activity.json.gz", streams=f"{prefix}streams.json.gz", track=f"{prefix}track.geojson.gz"
    )


def tracks_key(athlete_id: str) -> str:
    """The athlete's map tiles (PMTiles)."""
    return f"{athlete_prefix(athlete_id)}tracks.pmtiles"


def _activity_file_pattern(athlete_id: str, name: str) -> re.Pattern[str]:
    return re.compile(rf"^{re.escape(athlete_prefix(athlete_id))}activities/([^/]+)/{re.escape(name)}$")


@frozen
class RangedObject:
    """Part (or all) of an object.

    Attributes:
        body: The bytes read.
        etag: The object's ETag (changes whenever it's rewritten).
        content_range: `bytes start-end/size` for a ranged read; None for the whole object.
    """

    body: bytes
    etag: str
    content_range: str | None


def legacy_prefix(athlete_id: str) -> str:
    """The former archive layout (from the Go server): `metadata/{id}.json` and `tracks/{id}.gpx`."""
    return f"activities/{athlete_id}/"


@frozen
class BlobStore:
    """An S3-compatible bucket.

    boto3 is synchronous, so its calls run in a worker thread.

    Attributes:
        s3: S3 client.
        bucket: Bucket to write to.
    """

    s3: "S3Client"
    bucket: str

    @classmethod
    def build(cls, config: BlobStorageConfig) -> Self:
        """Create a BlobStore for the configured endpoint and bucket."""
        s3 = boto3.client(
            "s3",
            endpoint_url=config.endpoint_url,
            region_name="auto",
            aws_access_key_id=config.access_key_id,
            aws_secret_access_key=config.secret_access_key,
        )
        return cls(s3=s3, bucket=config.bucket_name)

    async def put(self, key: str, data: bytes, content_type: str, content_encoding: str | None = None) -> None:
        """Write an object, replacing any existing one at `key`."""
        request: PutObjectRequestTypeDef = {"Bucket": self.bucket, "Key": key, "Body": data, "ContentType": content_type}
        if content_encoding:
            request["ContentEncoding"] = content_encoding
        await asyncio.to_thread(lambda: self.s3.put_object(**request))
        logger.debug("wrote blob", extra={"bucket": self.bucket, "key": key, "bytes": len(data)})

    def _read(self, key: str) -> bytes | None:
        """Read an object (blocking); None if there's none at `key`."""
        try:
            response = self.s3.get_object(Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise
        return response["Body"].read()

    async def get(self, key: str) -> bytes | None:
        """Read an object; None if there's none at `key`."""
        return await asyncio.to_thread(self._read, key)

    async def get_range(self, key: str, range_header: str | None) -> RangedObject | None:
        """Read an object, or the bytes an HTTP `Range` header asks for; None if there's none at `key`.

        Raises:
            InvalidRangeError: The range is outside the object.
        """
        request: GetObjectRequestTypeDef = {"Bucket": self.bucket, "Key": key}
        if range_header:
            request["Range"] = range_header
        try:
            response = await asyncio.to_thread(lambda: self.s3.get_object(**request))
        except ClientError as e:
            code = e.response.get("Error", {}).get("Code")
            if code == "NoSuchKey":
                return None
            if code == "InvalidRange":
                raise InvalidRangeError(range_header) from e
            raise
        body = await asyncio.to_thread(response["Body"].read)
        return RangedObject(body=body, etag=response["ETag"], content_range=response.get("ContentRange"))

    async def list_keys(self, prefix: str) -> list[str]:
        """Every key under `prefix`."""

        def list_all() -> list[str]:
            pages = self.s3.get_paginator("list_objects_v2").paginate(Bucket=self.bucket, Prefix=prefix)
            return [obj["Key"] for page in pages for obj in page.get("Contents", [])]

        return await asyncio.to_thread(list_all)

    async def delete_keys(self, keys: list[str]) -> None:
        """Delete objects (missing ones are ignored)."""
        for start in range(0, len(keys), 1000):  # S3's limit per request
            batch: list[ObjectIdentifierTypeDef] = [{"Key": key} for key in keys[start : start + 1000]]
            await asyncio.to_thread(self.s3.delete_objects, Bucket=self.bucket, Delete={"Objects": batch, "Quiet": True})
        logger.info("deleted blobs", extra={"bucket": self.bucket, "count": len(keys)})

    async def delete_prefix(self, prefix: str) -> int:
        """Delete everything under `prefix`; returns how many objects were deleted."""
        keys = await self.list_keys(prefix)
        await self.delete_keys(keys)
        return len(keys)

    # --- activities

    async def archive_activity(self, intervals: IntervalsClient, athlete_id: str, activity_id: str) -> ActivitySummary:
        """Archive an activity: fetch it and every stream from intervals.icu and upload them (gzipped).

        Args:
            intervals: Client acting for the activity's owner.
            athlete_id: The owner.
            activity_id: intervals.icu activity id.

        Returns:
            The activity's index row.

        Raises:
            IntervalsError: Fetching the activity or its streams failed.
            botocore.exceptions.ClientError: An upload failed.
        """
        activity = await intervals.get_activity_json(activity_id)
        streams = await intervals.get_streams_json(activity_id)
        summary = loads(activity, ActivitySummary)
        keys = archive_keys(athlete_id, activity_id)
        await self.put(keys.activity, gzip.compress(activity), "application/json", "gzip")
        await self.save_track(athlete_id, summary, loads(streams, list[Stream]))
        # the streams go last: they mark the activity as archived in full (`archived_activity_ids`)
        await self.put(keys.streams, gzip.compress(streams), "application/json", "gzip")
        logger.info("archived activity", extra={"athlete_id": athlete_id, "activity_id": activity_id, "bucket": self.bucket})
        return summary

    async def save_track(self, athlete_id: str, activity: Activity | ActivitySummary, streams: list[Stream]) -> None:
        """Write an activity's simplified track, or delete a stale one if it has no positions."""
        key = archive_keys(athlete_id, activity.id).track
        feature = await asyncio.to_thread(track_feature, activity, streams)
        if feature is None:
            await self.delete_keys([key])
        else:
            await self.put(key, gzip.compress(encode_feature(feature)), "application/geo+json", "gzip")

    async def read_activity(self, athlete_id: str, activity_id: str) -> tuple[Activity, list[Stream]] | None:
        """An archived activity's details and streams; None if it isn't archived."""
        keys = archive_keys(athlete_id, activity_id)
        activity, streams = await self.get(keys.activity), await self.get(keys.streams)
        if activity is None or streams is None:
            return None
        return loads(gzip.decompress(activity), Activity), loads(gzip.decompress(streams), list[Stream])

    async def archived_activity_ids(self, athlete_id: str) -> set[str]:
        """Activities archived in full (their streams are written last)."""
        return await self._activity_ids_with(athlete_id, "streams.json.gz")

    async def track_activity_ids(self, athlete_id: str) -> set[str]:
        """Activities with a track."""
        return await self._activity_ids_with(athlete_id, "track.geojson.gz")

    async def _activity_ids_with(self, athlete_id: str, name: str) -> set[str]:
        pattern = _activity_file_pattern(athlete_id, name)
        keys = await self.list_keys(f"{athlete_prefix(athlete_id)}activities/")
        return {match[1] for key in keys if (match := pattern.match(key))}

    async def read_track_features(self, athlete_id: str, activity_ids: list[str]) -> list[bytes]:
        """Activities' tracks, each a GeoJSON feature on one line (missing ones are left out)."""

        def read(activity_id: str) -> bytes | None:
            data = self._read(archive_keys(athlete_id, activity_id).track)
            return None if data is None else gzip.decompress(data)

        def read_all() -> list[bytes]:
            with ThreadPoolExecutor(READ_CONCURRENCY) as pool:
                return [feature for feature in pool.map(read, activity_ids) if feature is not None]

        return await asyncio.to_thread(read_all)

    async def delete_activity(self, athlete_id: str, activity_id: str) -> None:
        """Delete an archived activity."""
        keys = archive_keys(athlete_id, activity_id)
        await self.delete_keys([keys.activity, keys.streams, keys.track])

    async def delete_athlete(self, athlete_id: str) -> int:
        """Delete everything archived for an athlete, in both layouts; returns how many objects were deleted."""
        return await self.delete_prefix(athlete_prefix(athlete_id)) + await self.delete_prefix(legacy_prefix(athlete_id))

    async def legacy_activity_ids(self, athlete_id: str) -> set[str]:
        """Activities in the former layout (`activities/{athlete}/metadata/{id}.json`)."""
        pattern = re.compile(rf"^{re.escape(legacy_prefix(athlete_id))}metadata/([^/]+)\.json$")
        return {m[1] for key in await self.list_keys(legacy_prefix(athlete_id)) if (m := pattern.match(key))}
