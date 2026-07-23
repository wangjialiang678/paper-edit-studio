from __future__ import annotations

import math
import re
import unicodedata
from difflib import SequenceMatcher
from typing import Any

from .report import create_issue

SIMILARITY_THRESHOLD = 0.85
_TIMING = re.compile(r"(?P<start>\S+)\s*-->\s*(?P<end>\S+)")


def _parse_time(raw: str) -> int:
    value = raw.strip().replace(",", ".")
    clock, dot, fraction = value.partition(".")
    parts = clock.split(":")
    if len(parts) == 3:
        hours, minutes, seconds = parts
    elif len(parts) == 2:
        hours = "0"
        minutes, seconds = parts
    else:
        raise ValueError(f"无法解析字幕时间：{raw}")
    milliseconds = int((fraction + "000")[:3]) if dot else 0
    return (
        int(hours) * 3_600_000
        + int(minutes) * 60_000
        + int(seconds) * 1000
        + milliseconds
    )


def parse_reference(text: str) -> list[dict[str, Any]]:
    if not isinstance(text, str):
        raise ValueError("参考字幕必须是文本")
    lines = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    cues: list[dict[str, Any]] = []
    index = 0
    while index < len(lines):
        line = lines[index].strip()
        if not line:
            index += 1
            continue
        if line.upper().startswith("WEBVTT"):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue
        if line.startswith("NOTE"):
            index += 1
            while index < len(lines) and lines[index].strip():
                index += 1
            continue

        match = _TIMING.search(line)
        if match is None and index + 1 < len(lines):
            following = _TIMING.search(lines[index + 1].strip())
            if following is not None:
                index += 1
                match = following
                line = lines[index].strip()
        if match is None:
            index += 1
            continue

        try:
            start_ms = _parse_time(match.group("start"))
            end_ms = _parse_time(match.group("end"))
        except (TypeError, ValueError):
            index += 1
            continue
        index += 1
        content: list[str] = []
        while index < len(lines):
            current = lines[index]
            if not current.strip():
                break
            if _TIMING.search(current.strip()) is not None:
                break
            if (
                current.strip().isdigit()
                and index + 1 < len(lines)
                and _TIMING.search(lines[index + 1].strip()) is not None
            ):
                break
            content.append(current.strip())
            index += 1
        rendered = "\n".join(content).strip()
        if end_ms > start_ms and rendered:
            cues.append({"start_ms": start_ms, "end_ms": end_ms, "text": rendered})
    return cues


def _value(item: Any, key: str, default: Any = None) -> Any:
    if isinstance(item, dict):
        return item.get(key, default)
    return getattr(item, key, default)


def _normalized(value: str) -> str:
    normalized = unicodedata.normalize("NFKC", value).lower()
    return "".join(
        character
        for character in normalized
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _overlap_piece(
    cue: dict[str, Any],
    segment_start: int,
    segment_end: int,
) -> str:
    cue_start = int(cue.get("start_ms", 0))
    cue_end = int(cue.get("end_ms", 0))
    overlap_start = max(segment_start, cue_start)
    overlap_end = min(segment_end, cue_end)
    text = str(cue.get("text") or "")
    if overlap_end <= overlap_start or not text:
        return ""
    duration = cue_end - cue_start
    if duration <= 0 or (overlap_start == cue_start and overlap_end == cue_end):
        return text
    start_ratio = (overlap_start - cue_start) / duration
    end_ratio = (overlap_end - cue_start) / duration
    start_index = min(len(text), max(0, math.floor(start_ratio * len(text))))
    end_index = min(len(text), max(start_index + 1, math.ceil(end_ratio * len(text))))
    return text[start_index:end_index]


def _has_word_character(value: str) -> bool:
    return any(character.isalnum() for character in value)


def _token_indexes(segment: Any, text: str) -> tuple[int, int] | None:
    if not text:
        return None
    tokens = list(_value(segment, "tokens", []) or [])
    token_texts = [str(_value(token, "text", "") or "") for token in tokens]
    joined = "".join(token_texts)
    start = joined.find(text)
    if start < 0 or joined.find(text, start + 1) >= 0:
        return None

    end = start + len(text) - 1
    offset = 0
    token_start: int | None = None
    for index, token_text in enumerate(token_texts):
        next_offset = offset + len(token_text)
        if token_start is None and start < next_offset:
            token_start = index
        if end < next_offset:
            if token_start is None:
                return None
            return token_start, index
        offset = next_offset
    return None


def align(
    segments: list[Any],
    cues: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    issues: list[dict[str, Any]] = []
    ordered_cues = sorted(
        (cue for cue in cues if isinstance(cue, dict)),
        key=lambda cue: (int(cue.get("start_ms", 0)), int(cue.get("end_ms", 0))),
    )
    for segment in segments:
        start_ms = int(_value(segment, "start_ms", 0))
        end_ms = int(_value(segment, "end_ms", 0))
        pieces = [
            _overlap_piece(cue, start_ms, end_ms)
            for cue in ordered_cues
            if min(end_ms, int(cue.get("end_ms", 0)))
            > max(start_ms, int(cue.get("start_ms", 0)))
        ]
        reference = "\n".join(piece for piece in pieces if piece)
        normalized_reference = _normalized(reference)
        if not normalized_reference:
            continue
        current = str(_value(segment, "text", "") or "")
        ratio = SequenceMatcher(
            None,
            _normalized(current),
            normalized_reference,
        ).ratio()
        if ratio < SIMILARITY_THRESHOLD:
            issues.append(
                create_issue(
                    segment_id=str(_value(segment, "id", _value(segment, "segment_id", ""))),
                    kind="ref_mismatch",
                    span={"text": current},
                    suggestion=reference,
                    confidence=ratio,
                    reason=f"与时间重叠参考字幕的文本相似度 {ratio:.3f} 低于 {SIMILARITY_THRESHOLD:.2f}",
                    source="reference",
                )
            )
            continue

        for tag, current_start, current_end, reference_start, reference_end in (
            SequenceMatcher(None, current, reference).get_opcodes()
        ):
            if tag != "replace":
                continue
            current_piece = current[current_start:current_end]
            reference_piece = reference[reference_start:reference_end]
            if len(current_piece) > 8 or len(reference_piece) > 8:
                continue
            if not (
                _has_word_character(current_piece)
                or _has_word_character(reference_piece)
            ):
                continue

            span: dict[str, Any] = {"text": current_piece}
            token_indexes = _token_indexes(segment, current_piece)
            if token_indexes is not None:
                span["token_start"], span["token_end"] = token_indexes
            issues.append(
                create_issue(
                    segment_id=str(_value(segment, "id", _value(segment, "segment_id", ""))),
                    kind="ref_mismatch",
                    span=span,
                    suggestion=reference_piece,
                    confidence=ratio,
                    reason=f"参考字幕此处为「{reference_piece}」",
                    source="reference",
                )
            )
    return issues


__all__ = ["SIMILARITY_THRESHOLD", "align", "parse_reference"]
