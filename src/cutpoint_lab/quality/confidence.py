from __future__ import annotations

from typing import Any

from .report import create_issue

LOW_CONFIDENCE_THRESHOLD = 0.55


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _confidence(token: Any) -> float | None:
    raw = _value(token, "confidence")
    if raw is None or isinstance(raw, bool):
        return None
    try:
        return float(raw)
    except (TypeError, ValueError):
        return None


def scan(segments: list[Any]) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    for segment in segments:
        tokens = list(_value(segment, "tokens", []) or [])
        if not any(_confidence(token) is not None for token in tokens):
            continue

        run: list[tuple[int, Any, float]] = []

        def flush() -> None:
            if not run:
                return
            values = [item[2] for item in run]
            mean = sum(values) / len(values)
            start = run[0][0]
            end = run[-1][0]
            text = "".join(str(_value(item[1], "text", "")) for item in run)
            issues.append(
                create_issue(
                    segment_id=str(_value(segment, "id", _value(segment, "segment_id", ""))),
                    kind="low_confidence",
                    span={
                        "text": text,
                        "token_start": start,
                        "token_end": end,
                    },
                    confidence=mean,
                    reason=(
                        f"连续 {len(run)} 个词的平均置信度 {mean:.3f} "
                        f"低于阈值 {LOW_CONFIDENCE_THRESHOLD:.2f}"
                    ),
                    source="confidence",
                )
            )
            run.clear()

        for index, token in enumerate(tokens):
            confidence = _confidence(token)
            if confidence is not None and confidence < LOW_CONFIDENCE_THRESHOLD:
                run.append((index, token, confidence))
            else:
                flush()
        flush()
    return issues


__all__ = ["LOW_CONFIDENCE_THRESHOLD", "scan"]
