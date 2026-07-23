from __future__ import annotations

import copy
import secrets
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..io import read_json, write_json


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds")


def _issue_id() -> str:
    return secrets.token_hex(4)


def empty_report() -> dict[str, Any]:
    return {
        "generated_at": None,
        "issues": [],
        "stats": {},
        "meta": {},
    }


def create_issue(
    *,
    segment_id: str,
    kind: str,
    span: dict[str, Any],
    confidence: float | None,
    reason: str,
    source: str,
    suggestion: str | None = None,
) -> dict[str, Any]:
    issue: dict[str, Any] = {
        "id": _issue_id(),
        "segment_id": str(segment_id),
        "kind": str(kind),
        "span": copy.deepcopy(span),
        "confidence": confidence,
        "reason": str(reason),
        "source": str(source),
        "status": "open",
    }
    if suggestion is not None:
        issue["suggestion"] = str(suggestion)
    return issue


def _match_key(issue: dict[str, Any]) -> tuple[str, str, str]:
    span = issue.get("span")
    span_text = span.get("text") if isinstance(span, dict) else ""
    return (
        str(issue.get("segment_id") or ""),
        str(issue.get("kind") or ""),
        str(span_text or ""),
    )


def _fresh_issue(raw: dict[str, Any]) -> dict[str, Any]:
    issue = copy.deepcopy(raw)
    issue["id"] = _issue_id()
    issue["status"] = "open"
    return issue


def _span_position(issue: dict[str, Any]) -> tuple[int, int] | None:
    span = issue.get("span")
    if not isinstance(span, dict):
        return None
    token_start = span.get("token_start")
    token_end = span.get("token_end")
    if (
        isinstance(token_start, int)
        and not isinstance(token_start, bool)
        and isinstance(token_end, int)
        and not isinstance(token_end, bool)
    ):
        return token_start, token_end
    return None


def _take_match(
    matches: list[dict[str, Any]],
    issue: dict[str, Any],
) -> dict[str, Any] | None:
    if not matches:
        return None
    position = _span_position(issue)
    if position is not None:
        for index, candidate in enumerate(matches):
            if _span_position(candidate) == position:
                return matches.pop(index)
    return matches.pop(0)


def _stats(issues: list[dict[str, Any]]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for issue in issues:
        kind = str(issue.get("kind") or "")
        if kind:
            counts[kind] = counts.get(kind, 0) + 1
    return counts


def merge_report(
    old: dict[str, Any] | None,
    new_issues: list[dict[str, Any]],
) -> dict[str, Any]:
    previous = old if isinstance(old, dict) else {}
    old_by_key: dict[tuple[str, str, str], list[dict[str, Any]]] = {}
    for issue in previous.get("issues") or []:
        if isinstance(issue, dict):
            old_by_key.setdefault(_match_key(issue), []).append(issue)
    merged: list[dict[str, Any]] = []
    for raw in new_issues:
        if not isinstance(raw, dict):
            continue
        issue = _fresh_issue(raw)
        matches = old_by_key.get(_match_key(issue)) or []
        matched = _take_match(matches, issue)
        if matched is not None:
            issue["id"] = str(matched.get("id") or issue["id"])
            old_status = str(matched.get("status") or "open")
            issue["status"] = (
                old_status if old_status in {"resolved", "ignored"} else "open"
            )
        merged.append(issue)
    meta = previous.get("meta")
    return {
        "generated_at": _now_iso(),
        "issues": merged,
        "stats": _stats(merged),
        "meta": copy.deepcopy(meta) if isinstance(meta, dict) else {},
    }


def load_report(project_dir: str | Path) -> dict[str, Any]:
    path = Path(project_dir) / "quality_report.json"
    if not path.is_file():
        return empty_report()
    payload = read_json(path)
    if not isinstance(payload, dict):
        return empty_report()
    return payload


def save_report(project_dir: str | Path, report: dict[str, Any]) -> Path:
    path = Path(project_dir) / "quality_report.json"
    write_json(path, report)
    return path


def refresh_stats(report: dict[str, Any]) -> dict[str, Any]:
    report["stats"] = _stats(
        [issue for issue in report.get("issues") or [] if isinstance(issue, dict)]
    )
    return report


__all__ = [
    "create_issue",
    "empty_report",
    "load_report",
    "merge_report",
    "refresh_stats",
    "save_report",
]
