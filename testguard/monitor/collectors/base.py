from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Protocol, Set


@dataclass(frozen=True)
class Target:
    root_pid: int


@dataclass(frozen=True)
class SampleFragment:
    values: Dict[str, float]


class CollectorAdapter(Protocol):
    name: str

    def capabilities(self) -> Set[str]: ...
    def overhead_hint(self) -> str: ...
    def sample(self, target: Target) -> SampleFragment: ...
