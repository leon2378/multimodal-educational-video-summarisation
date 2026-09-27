"""S3-compatible object storage: SeaweedFS locally, S3 or R2 in the cloud.

Key layout (docs/blueprint.md, section 8):
    raw/{lecture}/source.{ext}           uploaded original
    media/{lecture}/hls/...              playback renditions        (Phase 2)
    media/{lecture}/audio.flac           16 kHz mono audio          (Phase 2)
    frames/{lecture}/...                 sampled frames             (Phase 2)
    slides/{lecture}/{n}.jpg             unique slide images        (Phase 2)
    artifacts/{stage}/{cache_key}.json   stage cache entries
"""

import re
import uuid
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from lecture_core.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
_SAFE_EXTENSION = re.compile(r"\.[a-z0-9]{1,8}")


def source_key(lecture_id: uuid.UUID, filename: str) -> str:
    """Where an uploaded original lives. Keeps the extension only if it looks like one."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    extension = suffix if _SAFE_EXTENSION.fullmatch(suffix) else ""
    return f"raw/{lecture_id}/source{extension}"


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str | None


class ObjectStorage:
    """Thin wrapper over boto3. Its calls block, so run them in a thread from async code."""

    def __init__(self, settings: Settings) -> None:
        self.bucket = settings.s3_bucket
        self._client = _make_client(settings, settings.s3_endpoint_url)
        self._presign_client = _make_client(
            settings, settings.s3_public_endpoint_url or settings.s3_endpoint_url
        )

    def ping(self) -> None:
        self._client.head_bucket(Bucket=self.bucket)

    def presign_put(self, key: str, content_type: str, expires_in_s: int) -> str:
        """URL the client PUTs the file to directly, so uploads never pass through the API."""
        return self._presign_client.generate_presigned_url(
            "put_object",
            Params={"Bucket": self.bucket, "Key": key, "ContentType": content_type},
            ExpiresIn=expires_in_s,
        )

    def head(self, key: str) -> ObjectInfo | None:
        try:
            response = self._client.head_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_not_found(error):
                return None
            raise
        return ObjectInfo(size=response["ContentLength"], content_type=response.get("ContentType"))

    def get_bytes(self, key: str) -> bytes | None:
        try:
            response = self._client.get_object(Bucket=self.bucket, Key=key)
        except ClientError as error:
            if _is_not_found(error):
                return None
            raise
        return response["Body"].read()

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        self._client.put_object(Bucket=self.bucket, Key=key, Body=data, ContentType=content_type)

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self.bucket, Key=key)


def _make_client(settings: Settings, endpoint_url: str | None) -> "S3Client":
    config = Config(
        signature_version="s3v4",
        # Custom endpoints (SeaweedFS on localhost) need path-style URLs.
        s3={"addressing_style": "path" if endpoint_url else "auto"},
        # boto3 >= 1.36 adds CRC checksums by default, which S3-compatible stores don't all accept.
        request_checksum_calculation="when_required",
        response_checksum_validation="when_required",
        retries={"mode": "standard"},
    )
    return boto3.client(
        "s3",
        endpoint_url=endpoint_url,
        region_name=settings.s3_region,
        aws_access_key_id=settings.s3_access_key_id,
        aws_secret_access_key=settings.s3_secret_access_key.get_secret_value(),
        config=config,
    )


def _is_not_found(error: ClientError) -> bool:
    return error.response.get("Error", {}).get("Code") in _NOT_FOUND_CODES
