from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, kw_only=True)
class Thresholds:
    """
    Policy thresholds for the Governor.

    Canonical names are human-readable (seconds, bytes, bytes_per_second).

    A value of 0 disables a byte threshold.
    A value of None disables an optional threshold (runtime limit, disk write rate).
    """

    # Canonical fields (preferred)
    warn_memory_bytes: int = 0
    max_memory_bytes: int = 0
    max_runtime_seconds: Optional[float] = None

    disk_write_rate_bytes_per_second: Optional[float] = None
    disk_write_sustain_seconds: float = 3.0

    # Legacy aliases (accepted as input, prefer canonical everywhere else)
    warn_rss_bytes: Optional[int] = None
    max_rss_bytes: Optional[int] = None
    max_runtime_s: Optional[float] = None

    disk_write_rate_bytes_s: Optional[float] = None
    disk_write_sustain_s: Optional[float] = None

    def __post_init__(self) -> None:
        # Fill canonical fields from legacy fields when canonical values are not explicitly set.
        if self.warn_memory_bytes == 0 and self.warn_rss_bytes is not None:
            object.__setattr__(self, "warn_memory_bytes", int(self.warn_rss_bytes))

        if self.max_memory_bytes == 0 and self.max_rss_bytes is not None:
            object.__setattr__(self, "max_memory_bytes", int(self.max_rss_bytes))

        if self.max_runtime_seconds is None and self.max_runtime_s is not None:
            object.__setattr__(self, "max_runtime_seconds", float(self.max_runtime_s))

        if self.disk_write_rate_bytes_per_second is None and self.disk_write_rate_bytes_s is not None:
            object.__setattr__(self, "disk_write_rate_bytes_per_second", float(self.disk_write_rate_bytes_s))

        if self.disk_write_sustain_s is not None:
            object.__setattr__(self, "disk_write_sustain_seconds", float(self.disk_write_sustain_s))

        # Normalize negatives to "disabled" or safe values.
        if self.warn_memory_bytes < 0:
            object.__setattr__(self, "warn_memory_bytes", 0)
        if self.max_memory_bytes < 0:
            object.__setattr__(self, "max_memory_bytes", 0)
        if self.disk_write_sustain_seconds < 0:
            object.__setattr__(self, "disk_write_sustain_seconds", 0.0)
