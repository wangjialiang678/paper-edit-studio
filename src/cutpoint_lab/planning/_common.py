from __future__ import annotations

import re
from datetime import datetime, timezone
from typing import Any


def value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def segment_id(segment: Any) -> str:
    return str(value(segment, "id", value(segment, "segment_id", "")) or "")


def segment_text(segment: Any) -> str:
    return str(value(segment, "text", "") or "")


def segment_duration_ms(segment: Any) -> int:
    try:
        start_ms = int(value(segment, "start_ms", 0))
        end_ms = int(value(segment, "end_ms", 0))
    except (TypeError, ValueError):
        return 0
    return max(0, end_ms - start_ms)


def alias_map(ids: list[str]) -> dict[str, str]:
    """把规范 segment id 及常见数字尾缀变体映射回规范 id。"""

    ambiguous = object()
    aliases: dict[str, Any] = {}

    def put(alias: str, target: str) -> None:
        if not alias:
            return
        current = aliases.get(alias)
        if current is None:
            aliases[alias] = target
        elif current is not ambiguous and current != target:
            aliases[alias] = ambiguous

    for canonical in ids:
        put(canonical, canonical)
        put(canonical.lower(), canonical)
        match = re.search(r"(\d+)$", canonical)
        if match is None:
            continue
        digits = match.group(1)
        prefix = canonical[: match.start()]
        put(digits, canonical)
        put(str(int(digits)), canonical)
        put(prefix + str(int(digits)), canonical)
    return {
        alias: target
        for alias, target in aliases.items()
        if target is not ambiguous
    }


def resolve_id(raw: Any, aliases: dict[str, str]) -> str | None:
    if raw is None:
        return None
    rendered = str(raw).strip()
    direct = aliases.get(rendered) or aliases.get(rendered.lower())
    if direct is not None:
        return direct
    match = re.search(r"(\d+)\s*$", rendered)
    if match is None:
        return None
    digits = match.group(1)
    return aliases.get(digits) or aliases.get(str(int(digits)))


def now_iso(now_fn=None) -> str:
    if now_fn is not None:
        return str(now_fn())
    return datetime.now(timezone.utc).isoformat(timespec="seconds")
