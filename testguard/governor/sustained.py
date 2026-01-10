from __future__ import annotations

from typing import Optional


class SustainedCondition:
    """
    Tracks whether a boolean condition has been continuously true for a required duration.

    Typical use case: panic only after a sustained disk write rate for N seconds.
    """

    def __init__(self, required_seconds: float) -> None:
        self._required_seconds = float(required_seconds)
        self._true_since_timestamp_seconds: Optional[float] = None

    def update(self, *, is_true: bool, timestamp_seconds: float) -> bool:
        if not is_true:
            self._true_since_timestamp_seconds = None
            return False

        if self._true_since_timestamp_seconds is None:
            self._true_since_timestamp_seconds = float(timestamp_seconds)
            return False

        elapsed_seconds = float(timestamp_seconds) - float(self._true_since_timestamp_seconds)
        return elapsed_seconds >= self._required_seconds