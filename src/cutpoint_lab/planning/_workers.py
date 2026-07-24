from __future__ import annotations

import logging
import os

DEFAULT_PLAN_WORKERS = 4

logger = logging.getLogger(__name__)


def plan_workers() -> int:
    raw = os.environ.get("PE_PLAN_WORKERS", "").strip()
    if not raw:
        return DEFAULT_PLAN_WORKERS
    try:
        workers = int(raw)
    except ValueError:
        workers = 0
    if workers < 1:
        logger.warning(
            "invalid PE_PLAN_WORKERS=%r; fallback=%s",
            raw,
            DEFAULT_PLAN_WORKERS,
        )
        return DEFAULT_PLAN_WORKERS
    return workers


__all__ = ["DEFAULT_PLAN_WORKERS", "plan_workers"]
