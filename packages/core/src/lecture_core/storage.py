"""Object storage: S3-compatible (SeaweedFS locally, Google Cloud Storage for the demo), or a
local directory for running the pipeline without the stack.

Key layout:
    raw/{lecture}/source.{ext}             uploaded original
    artifacts/{stage}/{cache_key}.json     stage cache entries (ADR 0001)
    artifacts/{stage}/{cache_key}/...      files a stage writes: audio, slide images
    media/{lecture}/hls/...                playback renditions (later)
    demo/lectures.json, demo/videos/...    the demo's lectures, loaded on each deploy (ADR 0010)
"""

import re
import uuid
from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path, PurePosixPath
from typing import TYPE_CHECKING

import boto3
from botocore.config import Config
from botocore.exceptions import ClientError

from lecture_core.settings import Settings

if TYPE_CHECKING:
    from mypy_boto3_s3 import S3Client

_NOT_FOUND_CODES = {"404", "NoSuchKey", "NotFound"}
# An upload in parts that's over: finished, aborted, or cleared by the bucket's lifecycle rule.
_NO_UPLOAD_CODES = {"404", "NoSuchUpload"}
_SAFE_EXTENSION = re.compile(r"\.[a-z0-9]{1,8}")

# Uploads in parts (S3's multipart uploads, which Cloud Storage and SeaweedFS speak too): every
# part but the last must be at least 5 MiB, and an upload can have up to 10,000.
MIN_PART_BYTES = 5 * 1024**2
MAX_PARTS = 10_000


def source_key(lecture_id: uuid.UUID, filename: str) -> str:
    """Where an uploaded original lives. Keeps the extension only if it looks like one."""
    suffix = PurePosixPath(filename.replace("\\", "/")).suffix.lower()
    extension = suffix if _SAFE_EXTENSION.fullmatch(suffix) else ""
    return f"raw/{lecture_id}/source{extension}"


@dataclass(frozen=True)
class ObjectInfo:
    size: int
    content_type: str | None


@dataclass(frozen=True)
class UploadedPart:
    number: int
    size: int
    etag: str


@dataclass(frozen=True)
class PartPlan:
    """How a file is cut up for an upload in parts: parts of `part_bytes`, numbered from 1, the
    last one shorter."""

    size_bytes: int
    part_bytes: int

    @classmethod
    def for_size(cls, size_bytes: int, part_bytes: int) -> "PartPlan":
        # Bigger parts when the usual size would make more than storage allows.
        return cls(size_bytes, max(part_bytes, -(-size_bytes // MAX_PARTS)))

    @property
    def count(self) -> int:
        return max(1, -(-self.size_bytes // self.part_bytes))

    def size_of(self, number: int) -> int:
        return min(self.part_bytes, self.size_bytes - (number - 1) * self.part_bytes)

    def matching(self, uploaded: list[UploadedPart]) -> dict[int, UploadedPart]:
        """The parts storage has that fit this plan, by number. Any other is sent again."""
        return {
            part.number: part
            for part in uploaded
            if 1 <= part.number <= self.count and part.size == self.size_of(part.number)
        }


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

    def start_upload(self, key: str, content_type: str) -> str:
        """Start an upload in parts, which can be resumed; its id. The client sends each part
        straight to storage (presign_part), and finish_upload joins them into the object."""
        response = self._client.create_multipart_upload(
            Bucket=self.bucket, Key=key, ContentType=content_type
        )
        return response["UploadId"]

    def presign_part(
        self, key: str, upload_id: str, number: int, size: int, expires_in_s: int
    ) -> str:
        """URL the client PUTs one part to. It's signed for the part's length, so storage
        refuses a part of any other size, and an upload can't grow past the size it was given."""
        return self._presign_client.generate_presigned_url(
            "upload_part",
            Params={
                "Bucket": self.bucket,
                "Key": key,
                "UploadId": upload_id,
                "PartNumber": number,
                "ContentLength": size,
            },
            ExpiresIn=expires_in_s,
        )

    def uploaded_parts(self, key: str, upload_id: str) -> list[UploadedPart] | None:
        """The parts storage has so far, or None once the upload is over: finished, or left
        unfinished long enough for the bucket to clear it."""
        parts: list[UploadedPart] = []
        try:
            for page in self._client.get_paginator("list_parts").paginate(
                Bucket=self.bucket, Key=key, UploadId=upload_id
            ):
                parts.extend(
                    UploadedPart(part["PartNumber"], part["Size"], part["ETag"])
                    for part in page.get("Parts", [])
                )
        except ClientError as error:
            if _code(error) in _NO_UPLOAD_CODES:
                return None
            raise
        return parts

    def finish_upload(self, key: str, upload_id: str, parts: list[UploadedPart]) -> None:
        """Join the parts, in order, into the object; storage drops any others. An upload
        that's over already is left alone: whether the object is there tells how it ended."""
        try:
            self._client.complete_multipart_upload(
                Bucket=self.bucket,
                Key=key,
                UploadId=upload_id,
                MultipartUpload={
                    "Parts": [{"PartNumber": part.number, "ETag": part.etag} for part in parts]
                },
            )
        except ClientError as error:
            if _code(error) not in _NO_UPLOAD_CODES:
                raise

    def abort_upload(self, key: str, upload_id: str) -> None:
        """Drop an unfinished upload's parts. Safe to repeat."""
        try:
            self._client.abort_multipart_upload(Bucket=self.bucket, Key=key, UploadId=upload_id)
        except ClientError as error:
            if _code(error) not in _NO_UPLOAD_CODES:
                raise

    def presign_get(self, key: str, expires_in_s: int) -> str:
        """URL a browser can fetch directly: slide images, and later the video for playback."""
        return self._presign_client.generate_presigned_url(
            "get_object", Params={"Bucket": self.bucket, "Key": key}, ExpiresIn=expires_in_s
        )

    def download_file(self, key: str, path: Path) -> None:
        """Stream an object to disk: source videos are too big to hold in memory."""
        path.parent.mkdir(parents=True, exist_ok=True)
        self._client.download_file(self.bucket, key, str(path))

    def upload_file(self, path: Path, key: str, content_type: str) -> None:
        """A file from disk, in parts when it's big (a lecture fetched from a link)."""
        self._client.upload_file(
            str(path), self.bucket, key, ExtraArgs={"ContentType": content_type}
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

    def copy(self, source: str, destination: str) -> None:
        """Within the bucket, done by the store itself: nothing is downloaded."""
        self._client.copy_object(
            Bucket=self.bucket, Key=destination, CopySource={"Bucket": self.bucket, "Key": source}
        )

    def list_keys(self, prefix: str) -> Iterator[str]:
        for page in self._client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=prefix
        ):
            for item in page.get("Contents", []):
                yield item["Key"]

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
    return _code(error) in _NOT_FOUND_CODES


def _code(error: ClientError) -> str | None:
    return error.response.get("Error", {}).get("Code")


class LocalStorage:
    """Same interface as ObjectStorage, backed by a directory. For local pipeline runs and tests."""

    def __init__(self, root: Path) -> None:
        self.root = root

    def get_bytes(self, key: str) -> bytes | None:
        path = self._path(key)
        return path.read_bytes() if path.is_file() else None

    def put_bytes(
        self, key: str, data: bytes, content_type: str = "application/octet-stream"
    ) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        # Write then rename, so a crash never leaves a half-written file under the real name.
        partial = path.with_name(path.name + ".partial")
        partial.write_bytes(data)
        partial.replace(path)

    def _path(self, key: str) -> Path:
        parts = PurePosixPath(key).parts
        if not parts or any(part in {"..", "/"} for part in parts):
            raise ValueError(f"unsafe storage key: {key!r}")
        return self.root.joinpath(*parts)
