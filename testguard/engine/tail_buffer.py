from __future__ import annotations

from dataclasses import dataclass


@dataclass
class TailBuffer:
    """
    A bounded "tail" buffer for capturing the last N bytes of a stream

    TailBuffer is used when reading process output (stdout/stderr) so it can persist useful context without
    storing unbounded logs. As new chunks arrive, the buffer keeps at most `max_bytes` by evicting the oldest bytes,
    leaving the most recent output, which is typically the most relevant (especially right before a panic/kill)
    """

    max_bytes: int
    _data: bytearray = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        assert self.max_bytes > 0
        self._data = bytearray()

    def append(self, chunk: bytes) -> None:
        if not chunk:
            return
        self._data.extend(chunk)
        if len(self._data) > self.max_bytes:
            overflow = len(self._data) - self.max_bytes
            del self._data[:overflow]

    def get_bytes(self) -> bytes:
        return bytes(self._data)

    def get_text(self) -> str:
        return self.get_bytes().decode("utf-8", errors="replace")
