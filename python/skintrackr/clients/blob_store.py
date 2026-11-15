"""BlobStore: every S3-compatible blob-storage operation, and archiving activities there.

Layout (the bucket is the source of truth; the Redis activity index is derived from it):

    athletes/{athlete}/activities/{activity}/activity.json.gz   intervals.icu's activity JSON, as received
    athletes/{athlete}/activities/{activity}/streams.json.gz    intervals.icu's streams JSON, every type recorded

The former Go server's layout (`activities/{athlete}/metadata/{id}.json` and `tracks/{id}.gpx`) is no longer
written; the `skintrackr migrate` commands delete it.
"""

import asyncio
import gzip
import re
from typing import TYPE_CHECKING, Self

import boto3
from attrs import frozen
from botocore.exceptions import ClientError

from skintrackr.clients.intervals import IntervalsClient
from skintrackr.config import BlobStorageConfig
from skintrackr.logging import get_logger
from skintrackr.models import Activity, ActivitySummary, Stream, loads

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client
    from mypy_boto3_s3.type_defs import ObjectIdentifierTypeDef, PutObjectRequestTypeDef

logger = get_logger(__name__)


@frozen
class ArchiveKeys:
    """Where an activity is archived.

    Attributes:
        activity: intervals.icu's activity JSON, gzipped.
        streams: intervals.icu's streams JSON (every type the activity has), gzipped.
    """

    activity: str
    streams: str


def athlete_prefix(athlete_id: str) -> str:
    """Everything archived for an athlete."""
    return f"athletes/{athlete_id}/"


def archive_keys(athlete_id: str, activity_id: str) -> ArchiveKeys:
    """The keys an activity is archived under."""
    prefix = f"{athlete_prefix(athlete_id)}activities/{activity_id}/"
    return ArchiveKeys(activity=f"{prefix}activity.json.gz", streams=f"{prefix}streams.json.gz")


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

    async def get(self, key: str) -> bytes | None:
        """Read an object; None if there's none at `key`."""
        try:
            response = await asyncio.to_thread(self.s3.get_object, Bucket=self.bucket, Key=key)
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") == "NoSuchKey":
                return None
            raise
        return await asyncio.to_thread(response["Body"].read)

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
        keys = archive_keys(athlete_id, activity_id)
        await self.put(keys.activity, gzip.compress(activity), "application/json", "gzip")
        await self.put(keys.streams, gzip.compress(streams), "application/json", "gzip")
        logger.info("archived activity", extra={"athlete_id": athlete_id, "activity_id": activity_id, "bucket": self.bucket})
        return loads(activity, ActivitySummary)

    async def read_activity(self, athlete_id: str, activity_id: str) -> tuple[Activity, list[Stream]] | None:
        """An archived activity's details and streams; None if it isn't archived."""
        keys = archive_keys(athlete_id, activity_id)
        activity, streams = await self.get(keys.activity), await self.get(keys.streams)
        if activity is None or streams is None:
            return None
        return loads(gzip.decompress(activity), Activity), loads(gzip.decompress(streams), list[Stream])

    async def archived_activity_ids(self, athlete_id: str) -> set[str]:
        """Activities archived in full (their streams are written last)."""
        pattern = re.compile(rf"^{re.escape(athlete_prefix(athlete_id))}activities/([^/]+)/streams\.json\.gz$")
        keys = await self.list_keys(f"{athlete_prefix(athlete_id)}activities/")
        return {match[1] for key in keys if (match := pattern.match(key))}

    async def delete_activity(self, athlete_id: str, activity_id: str) -> None:
        """Delete an archived activity."""
        keys = archive_keys(athlete_id, activity_id)
        await self.delete_keys([keys.activity, keys.streams])

    async def delete_athlete(self, athlete_id: str) -> int:
        """Delete everything archived for an athlete, in both layouts; returns how many objects were deleted."""
        return await self.delete_prefix(athlete_prefix(athlete_id)) + await self.delete_prefix(legacy_prefix(athlete_id))

    async def legacy_activity_ids(self, athlete_id: str) -> set[str]:
        """Activities in the former layout (`activities/{athlete}/metadata/{id}.json`)."""
        pattern = re.compile(rf"^{re.escape(legacy_prefix(athlete_id))}metadata/([^/]+)\.json$")
        return {m[1] for key in await self.list_keys(legacy_prefix(athlete_id)) if (m := pattern.match(key))}
