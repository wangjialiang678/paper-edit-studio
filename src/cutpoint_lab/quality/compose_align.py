from __future__ import annotations

import logging
from collections.abc import Callable
from difflib import SequenceMatcher
from typing import Any

from .ai_review import _alias_map, _number, _resolve_id
from .align_reference import _normalized

AUTO_THRESHOLD = 0.85
AI_THRESHOLD = 0.5
AI_CONFIDENCE_THRESHOLD = 0.85
MAX_WINDOW_SIZE = 3

logger = logging.getLogger(__name__)


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _segment_id(segment: Any) -> str:
    return str(_value(segment, "id", _value(segment, "segment_id", "")) or "")


def _segment_text(segment: Any) -> str:
    return str(_value(segment, "text", "") or "")


def _paragraphs(script_text: str) -> list[str]:
    if not isinstance(script_text, str):
        raise ValueError("成片文稿必须是文本")
    lines = script_text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    return [line.strip() for line in lines.split("\n") if _normalized(line.strip())]


def _similarity(left: str, right: str) -> float:
    normalized_left = _normalized(left)
    normalized_right = _normalized(right)
    if not normalized_left or not normalized_right:
        return 0.0
    return SequenceMatcher(None, normalized_left, normalized_right).ratio()


def _window_text(segments: list[Any], start: int, end: int) -> str:
    return "".join(_segment_text(segment) for segment in segments[start : end + 1])


def _best_match(segments: list[Any], paragraph: str) -> tuple[list[Any], float]:
    if not segments:
        return [], 0.0
    expanded: list[tuple[float, int, int]] = []
    for seed in range(len(segments)):
        start = end = seed
        score = _similarity(_segment_text(segments[seed]), paragraph)
        while end - start + 1 < MAX_WINDOW_SIZE:
            candidates: list[tuple[float, int, int]] = []
            if start > 0:
                candidates.append(
                    (
                        _similarity(_window_text(segments, start - 1, end), paragraph),
                        start - 1,
                        end,
                    )
                )
            if end + 1 < len(segments):
                candidates.append(
                    (
                        _similarity(_window_text(segments, start, end + 1), paragraph),
                        start,
                        end + 1,
                    )
                )
            if not candidates:
                break
            candidate_score, candidate_start, candidate_end = max(
                candidates,
                key=lambda item: item[0],
            )
            if candidate_score <= score:
                break
            score = candidate_score
            start, end = candidate_start, candidate_end
        expanded.append((score, start, end))

    best_score, best_start, best_end = max(expanded, key=lambda item: item[0])
    return segments[best_start : best_end + 1], best_score


def _valid_tokens(segment: Any) -> list[Any]:
    tokens = list(_value(segment, "tokens", []) or [])

    def valid(token: Any) -> bool:
        text = str(_value(token, "text", "") or "")
        try:
            start_ms = int(_value(token, "start_ms", -1))
            end_ms = int(_value(token, "end_ms", -1))
        except (TypeError, ValueError):
            return False
        return bool(text.strip()) and start_ms >= 0 and end_ms > start_ms

    return sorted(
        (token for token in tokens if valid(token)),
        key=lambda token: int(_value(token, "start_ms", 0)),
    )


def _located_token_indexes(
    matched_segments: list[Any],
    paragraph: str,
) -> dict[str, set[int]] | None:
    source_characters: list[str] = []
    source_positions: list[tuple[str, int]] = []
    token_counts: dict[str, int] = {}
    tokens_by_segment: dict[str, list[Any]] = {}
    punctuation_indexes: dict[str, set[int]] = {}
    for segment in matched_segments:
        segment_id = _segment_id(segment)
        tokens = _valid_tokens(segment)
        if not segment_id or not tokens:
            return None
        token_counts[segment_id] = len(tokens)
        tokens_by_segment[segment_id] = tokens
        punctuation_indexes[segment_id] = set()
        for token_index, token in enumerate(tokens):
            normalized_token = _normalized(str(_value(token, "text", "") or ""))
            if not normalized_token:
                punctuation_indexes[segment_id].add(token_index)
            source_characters.extend(normalized_token)
            source_positions.extend((segment_id, token_index) for _ in normalized_token)

    source = "".join(source_characters)
    target = _normalized(paragraph)
    if not source or not target:
        return None
    if source == target:
        return {
            segment_id: set(range(token_count))
            for segment_id, token_count in token_counts.items()
        }

    kept: dict[str, set[int]] = {segment_id: set() for segment_id in token_counts}
    matched_target_characters = 0
    for tag, source_start, source_end, target_start, target_end in SequenceMatcher(
        None, source, target
    ).get_opcodes():
        if tag == "equal":
            matched_target_characters += target_end - target_start
            for source_index in range(source_start, source_end):
                segment_id, token_index = source_positions[source_index]
                kept[segment_id].add(token_index)
        elif tag != "delete":
            return None
    if matched_target_characters != len(target) or any(not indexes for indexes in kept.values()):
        return None

    for segment_id, indexes in kept.items():
        first = min(indexes)
        last = max(indexes)
        indexes.update(
            index
            for index in punctuation_indexes[segment_id]
            if first < index < last
        )
    located_text = "".join(
        _normalized(str(_value(token, "text", "") or ""))
        for segment in matched_segments
        for token_index, token in enumerate(tokens_by_segment[_segment_id(segment)])
        if token_index in kept[_segment_id(segment)]
    )
    if located_text != target:
        return None
    return kept


def _deleted_ranges(token_count: int, kept: set[int]) -> list[dict[str, int]]:
    deleted = [index for index in range(token_count) if index not in kept]
    if not deleted:
        return []
    ranges: list[dict[str, int]] = []
    start = previous = deleted[0]
    for index in deleted[1:]:
        if index == previous + 1:
            previous = index
            continue
        ranges.append({"start_token": start, "end_token": previous})
        start = previous = index
    ranges.append({"start_token": start, "end_token": previous})
    return ranges


def _summary(text: str, limit: int = 80) -> str:
    collapsed = " ".join(text.split())
    return collapsed if len(collapsed) <= limit else collapsed[: limit - 1] + "…"


def _ai_prompt(paragraph_index: int, paragraph: str, segments: list[Any]) -> str:
    lines = [
        f"待裁决文稿段落（paragraph_index={paragraph_index}）：",
        paragraph,
        "",
        "可选字幕清单：",
    ]
    lines.extend(
        f"[{_segment_id(segment)}] {_segment_text(segment)}"
        for segment in segments
        if _segment_id(segment)
    )
    return "\n".join(lines)


def _ai_match(
    paragraph_index: int,
    paragraph: str,
    segments: list[Any],
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[], str] | None,
) -> tuple[list[str] | None, str]:
    system = str(assemble_prompt_fn()) if assemble_prompt_fn is not None else ""
    system = system.replace("{{USER_BRIEF}}", "")
    try:
        raw = chat_json_fn(system, _ai_prompt(paragraph_index, paragraph, segments))
    except Exception as exc:  # noqa: BLE001 - 单段 AI 失败降级进对齐报告。
        logger.warning(
            "compose align AI failed: paragraph=%s error_type=%s",
            paragraph_index,
            type(exc).__name__,
        )
        return None, "AI 裁决调用失败"

    raw_matches = raw.get("matches") if isinstance(raw, dict) else None
    if not isinstance(raw_matches, list):
        return None, "AI 未返回有效裁决"
    aliases = _alias_map([_segment_id(segment) for segment in segments])
    diagnostic = "AI 未返回该段裁决"
    for item in raw_matches:
        if not isinstance(item, dict):
            continue
        raw_index = item.get("paragraph_index")
        if (
            not isinstance(raw_index, int)
            or isinstance(raw_index, bool)
            or raw_index != paragraph_index
        ):
            continue
        raw_ids = item.get("segment_ids")
        if not isinstance(raw_ids, list):
            diagnostic = "AI 裁决缺少 segment_ids"
            continue
        if not raw_ids:
            return None, "AI 确认原字幕无对应内容"
        resolved = [_resolve_id(raw_id, aliases) for raw_id in raw_ids]
        if any(segment_id is None for segment_id in resolved):
            diagnostic = "AI 返回未知 segment_id，已拒绝"
            continue
        confidence = _number(item.get("confidence"))
        if confidence is None or confidence < AI_CONFIDENCE_THRESHOLD:
            diagnostic = "AI 裁决置信度不足"
            continue
        reason = str(item.get("reason") or "").strip()
        note = f"AI 裁决：{reason}" if reason else "AI 高置信裁决"
        return [str(segment_id) for segment_id in resolved], note
    return None, diagnostic


def compose(
    segments: list[Any],
    script_text: str,
    *,
    chat_json_fn: Callable[[str, str], dict] | None = None,
    assemble_prompt_fn: Callable[[], str] | None = None,
) -> dict[str, Any]:
    """把外部成片文稿忠实对齐回 transcript，产出 EDL 与逐段报告。"""
    source_segments = list(segments or [])
    paragraphs = _paragraphs(script_text)
    logger.info(
        "compose align started: segments=%s paragraphs=%s ai=%s",
        len(source_segments),
        len(paragraphs),
        chat_json_fn is not None,
    )
    segment_by_id = {
        _segment_id(segment): segment
        for segment in source_segments
        if _segment_id(segment)
    }
    order: list[str] = []
    report_paragraphs: list[dict[str, Any]] = []
    kept_by_segment: dict[str, set[int]] = {}
    preserve_whole: set[str] = set()

    for paragraph_index, paragraph in enumerate(paragraphs):
        lexical_segments, similarity = _best_match(source_segments, paragraph)
        matched_ids: list[str] = []
        status = "unmatched"
        if similarity >= AUTO_THRESHOLD:
            matched_ids = [_segment_id(segment) for segment in lexical_segments]
            status = "auto"
            note = "自动对齐"
        elif similarity >= AI_THRESHOLD and chat_json_fn is not None:
            matched_ids, note = _ai_match(
                paragraph_index,
                paragraph,
                source_segments,
                chat_json_fn,
                assemble_prompt_fn,
            )
            matched_ids = matched_ids or []
            status = "ai" if matched_ids else "unmatched"
        elif similarity >= AI_THRESHOLD:
            note = "相似度处于灰区，未启用 AI 裁决"
        else:
            note = "原视频中没有这段话"

        if matched_ids:
            matched_segments = [segment_by_id[segment_id] for segment_id in matched_ids]
            located = _located_token_indexes(matched_segments, paragraph)
            if located is None:
                preserve_whole.update(matched_ids)
                note += "；未能定位句内词范围，已整句保留"
            else:
                for segment_id, indexes in located.items():
                    kept_by_segment.setdefault(segment_id, set()).update(indexes)
            order.extend(matched_ids)

        report_paragraphs.append(
            {
                "index": paragraph_index,
                "text": _summary(paragraph),
                "status": status,
                "segment_ids": matched_ids,
                "similarity": round(similarity, 4),
                "note": note,
            }
        )
        logger.info(
            "compose align paragraph: index=%s status=%s similarity=%.4f ids=%s",
            paragraph_index,
            status,
            similarity,
            matched_ids,
        )

    selected = set(order)
    rows: list[dict[str, Any]] = []
    for segment in source_segments:
        segment_id = _segment_id(segment)
        row: dict[str, Any] = {
            "id": segment_id,
            "checked": segment_id in selected,
            "text": _segment_text(segment),
        }
        if segment_id in selected and segment_id not in preserve_whole:
            tokens = _valid_tokens(segment)
            kept = kept_by_segment.get(segment_id)
            if tokens and kept:
                cuts = _deleted_ranges(len(tokens), kept)
                if cuts:
                    row["cuts"] = cuts
        rows.append(row)

    stats = {
        "total": len(report_paragraphs),
        "auto": sum(item["status"] == "auto" for item in report_paragraphs),
        "ai": sum(item["status"] == "ai" for item in report_paragraphs),
        "unmatched": sum(item["status"] == "unmatched" for item in report_paragraphs),
    }
    logger.info("compose align completed: stats=%s order_items=%s", stats, len(order))
    return {
        "edl": {"rows": rows, "order": order},
        "report": {"paragraphs": report_paragraphs, "stats": stats},
    }


__all__ = [
    "AI_CONFIDENCE_THRESHOLD",
    "AI_THRESHOLD",
    "AUTO_THRESHOLD",
    "compose",
]
