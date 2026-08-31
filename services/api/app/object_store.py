"""Object storage provider interface (constitution: third-party services live
behind provider interfaces).

`ObjectStore` is the seam; `S3ObjectStore` (boto3 -> MinIO locally, any
S3-compatible store in production) and `InMemoryObjectStore` (tests) implement
it.
"""

import re
from typing import Protocol, runtime_checkable

import boto3
from botocore.client import Config as BotoConfig
from botocore.exceptions import ClientError

from app.config import Settings

_KEY_MAX_LENGTH = 512
_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._/-]*$")


def validate_object_key(key: str) -> str:
    """Reject keys that could traverse paths or break provider semantics.

    Baked into every implementation (security review M0, finding #4) so that
    future callers deriving keys from owner input inherit the check for free.
    """
    if not key or len(key) > _KEY_MAX_LENGTH:
        raise ValueError(f"invalid object key length: {len(key)}")
    if not _KEY_PATTERN.match(key):
        raise ValueError(f"invalid object key: {key!r}")
    if ".." in key.split("/") or "//" in key:
        raise ValueError(f"invalid object key (path traversal): {key!r}")
    return key


@runtime_checkable
class ObjectStore(Protocol):
    """Minimal M0 object-store contract."""

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        """Store bytes under key, overwriting any existing object."""
        ...

    def get(self, key: str) -> bytes:
        """Return object bytes. Raises KeyError if the object does not exist."""
        ...

    def delete(self, key: str) -> None:
        """Delete the object. Deleting a missing key is a no-op."""
        ...

    def exists(self, key: str) -> bool:
        """True if an object is stored under key."""
        ...


class InMemoryObjectStore:
    """Fake for unit tests; honors the ObjectStore contract."""

    def __init__(self) -> None:
        self._objects: dict[str, bytes] = {}

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._objects[validate_object_key(key)] = bytes(data)

    def get(self, key: str) -> bytes:
        validate_object_key(key)
        try:
            return self._objects[key]
        except KeyError:
            raise KeyError(f"object not found: {key}") from None

    def delete(self, key: str) -> None:
        self._objects.pop(validate_object_key(key), None)

    def exists(self, key: str) -> bool:
        return validate_object_key(key) in self._objects


class S3ObjectStore:
    """S3-compatible implementation (MinIO in dev, Hetzner object storage later)."""

    def __init__(
        self,
        *,
        endpoint_url: str,
        access_key: str,
        secret_key: str,
        bucket: str,
        region: str = "us-east-1",
    ) -> None:
        self._bucket = bucket
        self._client = boto3.client(
            "s3",
            endpoint_url=endpoint_url,
            aws_access_key_id=access_key,
            aws_secret_access_key=secret_key,
            region_name=region,
            config=BotoConfig(
                signature_version="s3v4",
                s3={"addressing_style": "path"},
                connect_timeout=5,
                read_timeout=10,
                retries={"max_attempts": 2},
            ),
        )

    @classmethod
    def from_settings(cls, settings: Settings) -> "S3ObjectStore":
        return cls(
            endpoint_url=settings.s3_endpoint_url,
            access_key=settings.s3_access_key,
            secret_key=settings.s3_secret_key,
            bucket=settings.s3_bucket,
            region=settings.s3_region,
        )

    def ensure_bucket(self) -> None:
        """Create the bucket if it does not exist (idempotent, dev convenience)."""
        try:
            self._client.head_bucket(Bucket=self._bucket)
        except ClientError:
            self._client.create_bucket(Bucket=self._bucket)

    def put(self, key: str, data: bytes, content_type: str = "application/octet-stream") -> None:
        self._client.put_object(
            Bucket=self._bucket,
            Key=validate_object_key(key),
            Body=data,
            ContentType=content_type,
        )

    def get(self, key: str) -> bytes:
        validate_object_key(key)
        try:
            response = self._client.get_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("NoSuchKey", "404"):
                raise KeyError(f"object not found: {key}") from exc
            raise
        return response["Body"].read()

    def delete(self, key: str) -> None:
        self._client.delete_object(Bucket=self._bucket, Key=validate_object_key(key))

    def exists(self, key: str) -> bool:
        validate_object_key(key)
        try:
            self._client.head_object(Bucket=self._bucket, Key=key)
        except ClientError as exc:
            code = exc.response.get("Error", {}).get("Code", "")
            if code in ("404", "NoSuchKey"):
                return False
            raise
        return True

    def health_check(self) -> None:
        """Cheap liveness probe: bucket listing on the configured bucket."""
        self._client.head_bucket(Bucket=self._bucket)
