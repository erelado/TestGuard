from __future__ import annotations

from typing import Optional


def format_duration_seconds(duration_seconds: Optional[float]) -> str:
    if duration_seconds is None:
        return "-"
    if duration_seconds < 1.0:
        return f"{duration_seconds:.3f}"
    if duration_seconds < 10.0:
        return f"{duration_seconds:.2f}"
    return f"{duration_seconds:.1f}"


def format_exit_code(exit_code: Optional[int]) -> str:
    return "-" if exit_code is None else str(exit_code)


def format_signal(signal_number: Optional[int]) -> str:
    return "-" if signal_number is None else str(signal_number)


def format_bytes(num: Optional[float]) -> str:
    if num is None:
        return "-"
    value = float(num)
    units = ["B", "KiB", "MiB", "GiB", "TiB"]
    unit_index = 0
    while value >= 1024.0 and unit_index < len(units) - 1:
        value /= 1024.0
        unit_index += 1
    return f"{value:.2f}{units[unit_index]}"


def format_percent(percent_value: Optional[float]) -> str:
    if percent_value is None:
        return "-"
    sign = "+" if percent_value >= 0 else ""
    return f"{sign}{percent_value:.1f}%"
