from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Generic, List, TypeVar

T = TypeVar("T")


@dataclass
class RingBuffer(Generic[T]):
    """
    Fixed-capacity in-memory buffer for keeping the most recent N items.

    This is used by the monitor loop to retain only a recent window of samples (for example, the last 30 seconds),
    while automatically discarding older items. It provides O(1) append and a snapshot() method to get the current
    contents as a regular list for serialization or analysis.
    """
    capacity: int
    _items: Deque[T] = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        assert self.capacity > 0
        self._items = deque(maxlen=self.capacity)

    def append(self, item: T) -> None:
        self._items.append(item)

    def snapshot(self) -> List[T]:
        return list(self._items)

    def __len__(self) -> int:
        return len(self._items)
