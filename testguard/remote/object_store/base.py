from __future__ import annotations

from typing import Iterable, Optional, Protocol


class ObjectStore(Protocol):
    """
    Minimal object-store interface

    This is intentionally small so other providers (Azure Blob, GCS, etc.) can be added without touching core logic
    """

    def put_bytes(self, key: str, data: bytes, *, content_type: Optional[str] = None) -> None: ...
    def get_bytes(self, key: str) -> bytes: ...
    def exists(self, key: str) -> bool: ...
    def delete(self, key: str) -> None: ...
