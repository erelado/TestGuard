from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Dict

from testguard.monitor.collectors.base import Target


_PSI_MEMORY_PATH = Path("/proc/pressure/memory")


def parse_psi_pressure_text(pressure_text: str) -> Dict[str, float]:
    """
    Parse Linux PSI text (for example /proc/pressure/memory).

    Expected format (two lines):
      some avg10=0.00 avg60=0.00 avg300=0.00 total=123
      full avg10=0.00 avg60=0.00 avg300=0.00 total=456

    We extract avg10 for "some" and "full" as diagnostic signals.
    """
    result: Dict[str, float] = {}

    for raw_line in pressure_text.splitlines():
        line = raw_line.strip()
        if not line:
            continue

        parts = line.split()
        if len(parts) < 2:
            continue

        category = parts[0]  # "some" or "full"
        if category not in {"some", "full"}:
            continue

        kv = {}
        for token in parts[1:]:
            if "=" not in token:
                continue
            key, value = token.split("=", 1)
            kv[key] = value

        avg10_text = kv.get("avg10")
        if avg10_text is None:
            continue

        try:
            avg10_value = float(avg10_text)
        except ValueError:
            continue

        if category == "some":
            result["psi_memory_some_avg10"] = avg10_value
        else:
            result["psi_memory_full_avg10"] = avg10_value

    return result


@dataclass(frozen=True, kw_only=True)
class PsiCollector:
    name: str = "psi"

    def sample(self, target: Target) -> Dict[str, float]:
        # PSI is host level, target is unused.
        try:
            pressure_text = _PSI_MEMORY_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            return {}
        except PermissionError:
            return {}
        except OSError:
            return {}

        return parse_psi_pressure_text(pressure_text)
