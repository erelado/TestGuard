from __future__ import annotations

import inspect
import logging
from typing import Optional


class ContextLoggerAdapter(logging.LoggerAdapter):
    def process(self, msg, kwargs):
        extra = dict(self.extra or {})
        extra.update(kwargs.get("extra") or {})
        kwargs["extra"] = extra

        run_id = extra.get("run_id")
        policy_id = extra.get("policy_id")

        prefix_parts = []
        if run_id:
            prefix_parts.append(f"run_id={run_id}")
        if policy_id:
            prefix_parts.append(f"policy_id={policy_id}")

        prefix = f"[{' '.join(prefix_parts)}] " if prefix_parts else ""
        return prefix + str(msg), kwargs


def configure_logging(log_level: str) -> None:
    numeric_level = getattr(logging, log_level.upper(), logging.INFO)

    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s"))

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(numeric_level)


def get_named_logger(
    logger_name: str,
    *,
    run_id: Optional[str] = None,
    policy_id: Optional[str] = None,
) -> ContextLoggerAdapter:
    base_logger = logging.getLogger(logger_name)
    return ContextLoggerAdapter(base_logger, {"run_id": run_id, "policy_id": policy_id})


def get_logger(*, run_id: Optional[str] = None, policy_id: Optional[str] = None) -> ContextLoggerAdapter:
    # Intended for low frequency logs (CLI/engine). Avoid using this in the 0.5s monitor loop.
    frame = inspect.currentframe()
    assert frame is not None
    caller_frame = frame.f_back
    module_name = caller_frame.f_globals.get("__name__", "testguard") if caller_frame is not None else "testguard"
    return get_named_logger(module_name, run_id=run_id, policy_id=policy_id)
