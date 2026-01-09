from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import Deque, Generic, List, TypeVar

T = TypeVar("T")


@dataclass
class RingBuffer(Generic[T]):
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
