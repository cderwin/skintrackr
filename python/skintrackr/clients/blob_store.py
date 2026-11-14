"""BlobStore: every S3-compatible blob-storage operation, and archiving activities there."""

import asyncio
from typing import TYPE_CHECKING, Self

import boto3
from attrs import frozen

from skintrackr.clients.strava import StravaClient
from skintrackr.config import BlobStorageConfig
from skintrackr.logging import get_logger
from skintrackr.models import converter

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

logger = get_logger(__name__)


@frozen
class PersistedActivity:
    """Where an archived activity was written.

    Attributes:
        metadata_key: Key of the activity's details (JSON).
        track_key: Key of the activity's track (GPX).
    """

    metadata_key: str
    track_key: str


def activity_keys(athlete_id: int, activity_id: int) -> PersistedActivity:
    """The keys an activity is archived under (the same as the Go server's)."""
    return PersistedActivity(
        metadata_key=f"activities/{athlete_id}/metadata/{activity_id}.json",
        track_key=f"activities/{athlete_id}/tracks/{activity_id}.gpx",
    )


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

    async def put(self, key: str, data: bytes, content_type: str) -> None:
        """Write an object, replacing any existing one at `key`."""
        await asyncio.to_thread(self.s3.put_object, Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)
        logger.debug("wrote blob", extra={"bucket": self.bucket, "key": key, "bytes": len(data)})

    async def persist_activity(self, strava: StravaClient, activity_id: str) -> PersistedActivity:
        """Archive an activity: fetch its details and GPX track from Strava and upload both.

        Args:
            strava: Client acting for the activity's owner.
            activity_id: Strava activity id.

        Returns:
            Where the files were written (see `activity_keys`).

        Raises:
            StravaError: Fetching the activity or its track failed.
            botocore.exceptions.ClientError: An upload failed.
        """
        activity = await strava.get_activity(activity_id)
        track = await strava.export_activity_gpx(activity)
        keys = activity_keys(activity.athlete.id, activity.id)
        await self.put(keys.metadata_key, converter.dumps(activity).encode(), "application/json")
        await self.put(keys.track_key, track, "application/gpx+xml")
        logger.info(
            "persisted activity",
            extra={"athlete_id": activity.athlete.id, "activity_id": activity.id, "bucket": self.bucket},
        )
        return keys
