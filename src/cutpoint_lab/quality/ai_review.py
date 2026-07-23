from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from .corrections import new_changeset
from .report import create_issue

MAX_SUSPECT_SENTENCES = 40
AUTO_FIX_CONFIDENCE = 0.85
OK_RESOLVE_CONFIDENCE = 0.9
MAX_LENGTH_DELTA = 2


def _segment_id(row: dict[str, Any]) -> str:
    return str(row.get("id", row.get("segment_id", "")))


def _alias_map(ids: list[str]) -> dict[str, str]:
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

    for segment_id in ids:
        put(segment_id, segment_id)
        put(segment_id.lower(), segment_id)
        match = re.search(r"(\d+)$", segment_id)
        if match:
            digits = match.group(1)
            prefix = segment_id[: match.start()]
            put(digits, segment_id)
            put(str(int(digits)), segment_id)
            put(prefix + str(int(digits)), segment_id)
    return {
        alias: target
        for alias, target in aliases.items()
        if target is not ambiguous
    }


def _resolve_id(raw: Any, aliases: dict[str, str]) -> str | None:
    if raw is None:
        return None
    value = str(raw).strip()
    return aliases.get(value) or aliases.get(value.lower())


def _number(raw: Any) -> float | None:
    if isinstance(raw, bool):
        return None
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return None
    return value if 0.0 <= value <= 1.0 else None


def _known_terms(
    known_terms: list[str],
    corrections_rights: list[str],
) -> list[str]:
    result: list[str] = []
    seen: set[str] = set()
    for raw in [*known_terms, *corrections_rights]:
        term = str(raw).strip()
        if not term or term.casefold() in seen:
            continue
        seen.add(term.casefold())
        result.append(term)
    return result


def _marked_text(text: str, issues: list[dict[str, Any]]) -> str:
    rendered = text
    cursor = 0
    pieces: list[str] = []
    for issue in issues:
        span = str((issue.get("span") or {}).get("text") or "")
        found = rendered.find(span, cursor)
        if not span or found < 0:
            continue
        pieces.extend((rendered[cursor:found], f"『{span}』"))
        cursor = found + len(span)
    pieces.append(rendered[cursor:])
    return "".join(pieces)


def _prompt_for_chunk(
    rows: list[dict[str, Any]],
    chunk_ids: list[str],
    issues_by_segment: dict[str, list[dict[str, Any]]],
    terms: list[str],
) -> str:
    row_indexes = {_segment_id(row): index for index, row in enumerate(rows)}
    lines = [
        "请复核以下语音识别字幕。标为【存疑句】的句子才是可输出 finding 的目标；",
        "【上下文】只用于理解，禁止对其输出 finding。",
        f"已知词表：{'、'.join(terms) if terms else '（空）'}",
        "",
    ]
    included_context: set[tuple[str, str]] = set()
    for segment_id in chunk_ids:
        index = row_indexes[segment_id]
        for context_index in range(max(0, index - 1), min(len(rows), index + 2)):
            row = rows[context_index]
            row_id = _segment_id(row)
            if context_index == index:
                issue_list = issues_by_segment[segment_id]
                confidence_values = [
                    issue.get("confidence")
                    for issue in issue_list
                    if issue.get("confidence") is not None
                ]
                confidence = (
                    sum(float(value) for value in confidence_values)
                    / len(confidence_values)
                    if confidence_values
                    else 0.0
                )
                lines.append(
                    f"【存疑句 confidence={confidence:.3f}】"
                    f"[{row_id}] {_marked_text(str(row.get('text') or ''), issue_list)}"
                )
                continue
            key = (segment_id, row_id)
            if key in included_context:
                continue
            included_context.add(key)
            lines.append(f"【上下文】[{row_id}] {str(row.get('text') or '')}")
        lines.append("")
    return "\n".join(lines)


def review(
    segments: list[dict[str, Any]],
    issues: list[dict[str, Any]],
    *,
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[], str],
    known_terms: list[str],
    corrections_rights: list[str],
) -> tuple[list[dict[str, Any]], dict[str, Any] | None, list[dict[str, Any]]]:
    rows = [row for row in segments if isinstance(row, dict)]
    rows_by_id = {_segment_id(row): row for row in rows}
    issues_by_segment: dict[str, list[dict[str, Any]]] = {}
    for issue in issues:
        if (
            not isinstance(issue, dict)
            or issue.get("kind") != "low_confidence"
            or issue.get("status", "open") != "open"
        ):
            continue
        segment_id = str(issue.get("segment_id") or "")
        span_text = str((issue.get("span") or {}).get("text") or "")
        row = rows_by_id.get(segment_id)
        if row is None or not span_text or span_text not in str(row.get("text") or ""):
            continue
        issues_by_segment.setdefault(segment_id, []).append(issue)

    target_ids = [
        _segment_id(row)
        for row in rows
        if _segment_id(row) in issues_by_segment
    ]
    if not target_ids:
        return [], None, []

    system = str(assemble_prompt_fn()).replace("{{USER_BRIEF}}", "")
    terms = _known_terms(known_terms, corrections_rights)
    findings: list[dict[str, Any]] = []
    new_issues: list[dict[str, Any]] = []
    seen_targets: set[tuple[str, str]] = set()
    replacements: list[tuple[str, str, str]] = []

    for offset in range(0, len(target_ids), MAX_SUSPECT_SENTENCES):
        chunk_ids = target_ids[offset : offset + MAX_SUSPECT_SENTENCES]
        aliases = _alias_map(chunk_ids)
        raw = chat_json_fn(
            system,
            _prompt_for_chunk(rows, chunk_ids, issues_by_segment, terms),
        )
        raw_findings = raw.get("findings") if isinstance(raw, dict) else []
        if not isinstance(raw_findings, list):
            continue
        for item in raw_findings:
            if not isinstance(item, dict):
                continue
            segment_id = _resolve_id(item.get("segment_id"), aliases)
            span_text = str(item.get("span_text") or "")
            if segment_id is None or not span_text:
                continue
            source_issue = next(
                (
                    issue
                    for issue in issues_by_segment.get(segment_id, [])
                    if str((issue.get("span") or {}).get("text") or "") == span_text
                ),
                None,
            )
            target_key = (segment_id, span_text)
            if source_issue is None or target_key in seen_targets:
                continue
            row = rows_by_id[segment_id]
            if span_text not in str(row.get("text") or ""):
                continue
            verdict = str(item.get("verdict") or "")
            if verdict not in {"auto_fix", "ask_user", "ok"}:
                continue
            replacement = str(item.get("replacement") or "")
            confidence = _number(item.get("confidence"))
            reason = str(item.get("reason") or "")
            if not reason:
                continue
            if verdict == "ok" and confidence is None:
                continue
            if verdict == "ask_user" and (
                not replacement or confidence is None
            ):
                continue
            if verdict == "auto_fix" and (
                not replacement
                or abs(len(replacement) - len(span_text)) > MAX_LENGTH_DELTA
                or confidence is None
                or confidence < AUTO_FIX_CONFIDENCE
                or str(row.get("text") or "").count(span_text) != 1
            ):
                verdict = "ask_user"
            if verdict == "auto_fix" and replacement == span_text:
                verdict = "ok"

            finding = {
                "segment_id": segment_id,
                "span_text": span_text,
                "verdict": verdict,
                "replacement": replacement,
                "reason": reason,
                "confidence": confidence,
            }
            findings.append(finding)
            seen_targets.add(target_key)
            if verdict == "auto_fix":
                source_issue["status"] = "resolved"
                replacements.append((segment_id, span_text, replacement))
            elif verdict == "ask_user":
                kind = (
                    "term_candidate"
                    if "专名" in reason or "专有名词" in reason
                    else "ai_suspect"
                )
                new_issues.append(
                    create_issue(
                        segment_id=segment_id,
                        kind=kind,
                        span={"text": span_text},
                        suggestion=replacement or None,
                        confidence=confidence,
                        reason=reason,
                        source="ai",
                    )
                )
            elif confidence is not None and confidence >= OK_RESOLVE_CONFIDENCE:
                source_issue["status"] = "resolved"
                source_issue["reason"] = (
                    f"{str(source_issue.get('reason') or '')}"
                    f"；AI 复核通过：{reason}"
                )

    old_text_by_id: dict[str, str] = {}
    applied = 0
    for segment_id, span_text, replacement in replacements:
        row = rows_by_id[segment_id]
        current = str(row.get("text") or "")
        if span_text not in current:
            continue
        old_text_by_id.setdefault(segment_id, current)
        row["text"] = current.replace(span_text, replacement, 1)
        applied += 1
    if not applied:
        return findings, None, new_issues

    changes = [
        {
            "segment_id": segment_id,
            "field": "text",
            "old": old,
            "new": str(rows_by_id[segment_id].get("text") or ""),
        }
        for segment_id, old in old_text_by_id.items()
    ]
    return (
        findings,
        new_changeset(f"AI 自动纠错 {applied} 处", changes),
        new_issues,
    )


__all__ = [
    "AUTO_FIX_CONFIDENCE",
    "MAX_LENGTH_DELTA",
    "MAX_SUSPECT_SENTENCES",
    "OK_RESOLVE_CONFIDENCE",
    "review",
]
