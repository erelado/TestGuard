from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Optional

from testguard.remote.object_store.base import ObjectStore


@dataclass(frozen=True, kw_only=True)
class S3Location:
    bucket: str
    prefix: str = ""  # store-level prefix, for example "testguard"


class S3ObjectStore(ObjectStore):
    """
    S3 implementation of the ObjectStore interface.

    Design goals:
    - Keep TestGuard stdlib-only by default via lazy boto3 import.
    - Support an optional store prefix so many runs can share one bucket safely.
    - Allow injecting an S3 client for unit tests (no boto3, no network).
    """

    def __init__(self, *, bucket: str, prefix: str = "", client: object = None) -> None:
        assert bucket
        normalized_prefix = self._normalize_prefix(prefix)
        self._location = S3Location(bucket=bucket, prefix=normalized_prefix)
        self._client = client

    @staticmethod
    def _normalize_prefix(prefix: str) -> str:
        assert prefix is not None
        prefix = str(prefix).strip()
        prefix = prefix.strip("/")
        return prefix

    def _full_key(self, *, key: str) -> str:
        assert key and not key.startswith("/")
        key = str(key).lstrip("/")
        if not self._location.prefix:
            return key
        return f"{self._location.prefix}/{key}"

    def _strip_store_prefix(self, *, full_key: str) -> str:
        """
        Convert an S3 key back into a store-relative key.
        This keeps list_keys compatible with get_bytes/put_bytes.
        """
        if not self._location.prefix:
            return full_key
        prefix_with_slash = f"{self._location.prefix}/"
        if full_key.startswith(prefix_with_slash):
            return full_key[len(prefix_with_slash):]
        return full_key

    def _get_client(self):
        if self._client is None:
            try:
                import boto3  # type: ignore
            except ImportError as exc:
                raise RuntimeError(
                    "S3 remote requires boto3. Install with: uv pip install -e '.[aws]'"
                ) from exc
            self._client = boto3.client("s3")
        return self._client

    def put_bytes(self, *, key: str, data: bytes, content_type: Optional[str] = None) -> None:
        assert key and not key.startswith("/")
        assert isinstance(data, (bytes, bytearray))

        client = self._get_client()
        full_key = self._full_key(key=key)

        kwargs = {
            "Bucket": self._location.bucket,
            "Key": full_key,
            "Body": bytes(data),
        }
        if content_type:
            kwargs["ContentType"] = content_type
        client.put_object(**kwargs)

    def get_bytes(self, *, key: str) -> bytes:
        assert key and not key.startswith("/")

        client = self._get_client()
        full_key = self._full_key(key=key)

        response = client.get_object(Bucket=self._location.bucket, Key=full_key)
        body = response["Body"].read()
        assert isinstance(body, (bytes, bytearray))
        return bytes(body)

    def list_keys(self, *, prefix: str) -> Iterable[str]:
        """
        List keys under the given prefix (store-relative).

        Returned keys are store-relative, so they can be passed directly to get_bytes/put_bytes.
        """
        assert prefix is not None
        prefix = str(prefix).lstrip("/")

        client = self._get_client()
        paginator = client.get_paginator("list_objects_v2")

        query_prefix = self._full_key(key=prefix) if prefix else self._location.prefix
        if query_prefix is None:
            query_prefix = ""

        for page in paginator.paginate(Bucket=self._location.bucket, Prefix=query_prefix):
            for item in page.get("Contents", []) or []:
                key = item.get("Key")
                if not key:
                    continue
                yield self._strip_store_prefix(full_key=str(key))
