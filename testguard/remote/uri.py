from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, kw_only=True)
class RemoteUri:
    scheme: str
    bucket: str
    prefix: str


def parse_remote_uri(remote_uri: str) -> RemoteUri:
    """
    Parse a remote URI for TestGuard uploads.

    Currently supported:
      - s3://<bucket>/<optional-prefix>

    The returned prefix never starts with "/".
    """
    remote_uri = remote_uri.strip()
    if not remote_uri:
        raise ValueError("remote_uri is empty")

    if remote_uri.startswith("s3://"):
        remainder = remote_uri[len("s3://") :]
        parts = remainder.split("/", 1)
        bucket = parts[0].strip()
        assert bucket, "S3 bucket is missing"

        prefix = ""
        if len(parts) == 2:
            prefix = parts[1].strip().strip("/")
        return RemoteUri(scheme="s3", bucket=bucket, prefix=prefix)

    raise ValueError(f"Unsupported remote URI scheme: {remote_uri!r}")
