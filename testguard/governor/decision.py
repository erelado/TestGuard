from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass(frozen=True, kw_only=True)
class GovernorDecision:
    level: str  # "NONE" | "WARN" | "PANIC"
    policy_id: Optional[str]
    message: Optional[str]