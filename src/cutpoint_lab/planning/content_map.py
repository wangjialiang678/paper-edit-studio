from __future__ import annotations

import json
import logging
from collections.abc import Callable
from typing import Any

from ._common import (
    alias_map,
    now_iso,
    resolve_id,
    segment_duration_ms,
    segment_id,
    segment_text,
)

CONTENT_MAP_CHUNK_SIZE = 100
CONTENT_MAP_SPLIT_THRESHOLD = 150
TOPIC_STATUSES = {"pending", "confirmed"}
MAP_STATUSES = {"draft", "confirmed"}
BACKGROUND_KINDS = {"background", "case", "event"}

logger = logging.getLogger(__name__)


def _digest(segments: list[Any]) -> str:
    lines = ["以下是字幕句子（每句格式为 [segment_id] 文本）："]
    lines.extend(
        f"[{segment_id(segment)}] {segment_text(segment)}"
        for segment in segments
    )
    return "\n".join(lines)


def _system(assemble_prompt_fn: Callable[[], str]) -> str:
    return str(assemble_prompt_fn()).replace("{{USER_BRIEF}}", "")


def _chat_with_retry(
    chat_json_fn: Callable[[str, str], dict],
    system: str,
    user: str,
) -> dict[str, Any]:
    last_error: Exception | None = None
    for attempt in (1, 2):
        try:
            result = chat_json_fn(system, user)
            if not isinstance(result, dict):
                raise ValueError("AI 返回值必须是 JSON object")
            return result
        except Exception as exc:  # noqa: BLE001 - 调用方决定最终降级。
            last_error = exc
            logger.warning(
                "content map AI attempt failed: attempt=%s error_type=%s",
                attempt,
                type(exc).__name__,
            )
    assert last_error is not None
    raise last_error


def _raw_ids(
    raw: Any,
    *,
    aliases: dict[str, str],
    known: set[str],
    warnings: list[str],
    strict: bool,
    owner: str,
) -> list[str]:
    if not isinstance(raw, list):
        if strict:
            raise ValueError(f"{owner}.segment_ids 必须是数组")
        return []
    result: list[str] = []
    seen: set[str] = set()
    for item in raw:
        if strict:
            candidate = str(item)
            canonical = candidate if candidate in known else None
        else:
            canonical = resolve_id(item, aliases)
        if canonical is None:
            if strict:
                raise ValueError(f"{owner} 引用了未知 segment_id：{item}")
            warnings.append(f"{owner} 忽略未知/越界 segment_id：{item}")
            continue
        if canonical in seen:
            continue
        seen.add(canonical)
        result.append(canonical)
    return result


def _text(raw: Any, field: str, owner: str, *, strict: bool) -> str:
    value = raw.get(field) if isinstance(raw, dict) else None
    if strict and not isinstance(value, str):
        raise ValueError(f"{owner}.{field} 必须是字符串")
    return str(value or "")


def _number(raw: Any, field: str, owner: str, *, strict: bool) -> int | float:
    value = raw.get(field) if isinstance(raw, dict) else None
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        if strict:
            raise ValueError(f"{owner}.{field} 必须是非负数")
        return 0
    return value


def _normalize(
    payload: dict[str, Any],
    segments: list[Any],
    *,
    source: str,
    model: str = "",
    strict: bool,
    generated_at: str,
    inherited_warnings: list[str] | None = None,
) -> dict[str, Any]:
    if not isinstance(payload, dict):
        raise ValueError("content_map 必须是 JSON object")
    source_segments = list(segments or [])
    known_ids = [segment_id(segment) for segment in source_segments]
    known = set(known_ids)
    aliases = alias_map(known_ids)
    durations = {
        segment_id(segment): segment_duration_ms(segment)
        for segment in source_segments
    }
    warnings = list(inherited_warnings or [])
    meta = payload.get("meta")
    if isinstance(meta, dict) and isinstance(meta.get("warnings"), list):
        warnings.extend(str(item) for item in meta["warnings"])

    status = payload.get("status", "draft")
    if status not in MAP_STATUSES:
        if strict:
            raise ValueError("content_map.status 只能是 draft 或 confirmed")
        status = "draft"

    result_claims: list[dict[str, Any]] = []
    claim_ids: set[str] = set()
    raw_claims = payload.get("claims") or []
    if strict and not isinstance(raw_claims, list):
        raise ValueError("claims 必须是数组")
    for index, raw in enumerate(raw_claims if isinstance(raw_claims, list) else [], start=1):
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(f"claims[{index - 1}] 必须是对象")
            continue
        item_id = str(raw.get("id") or f"c{index}")
        if item_id in claim_ids:
            if strict:
                raise ValueError(f"claims id 重复：{item_id}")
            repaired = f"c{index}"
            suffix = 2
            while repaired in claim_ids:
                repaired = f"c{index}-{suffix}"
                suffix += 1
            warnings.append(f"claim id 重复：{item_id}，已改为 {repaired}")
            item_id = repaired
        claim_ids.add(item_id)
        owner = f"claim {item_id}"
        result_claims.append(
            {
                "id": item_id,
                "text": _text(raw, "text", owner, strict=strict),
                "segment_ids": _raw_ids(
                    raw.get("segment_ids"),
                    aliases=aliases,
                    known=known,
                    warnings=warnings,
                    strict=strict,
                    owner=owner,
                ),
                "reason": _text(raw, "reason", owner, strict=strict),
            }
        )

    result_backgrounds: list[dict[str, Any]] = []
    background_ids: set[str] = set()
    raw_backgrounds = payload.get("backgrounds") or []
    if strict and not isinstance(raw_backgrounds, list):
        raise ValueError("backgrounds 必须是数组")
    for index, raw in enumerate(
        raw_backgrounds if isinstance(raw_backgrounds, list) else [],
        start=1,
    ):
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(f"backgrounds[{index - 1}] 必须是对象")
            continue
        item_id = str(raw.get("id") or f"b{index}")
        if item_id in background_ids:
            if strict:
                raise ValueError(f"backgrounds id 重复：{item_id}")
            repaired = f"b{index}"
            suffix = 2
            while repaired in background_ids:
                repaired = f"b{index}-{suffix}"
                suffix += 1
            warnings.append(
                f"background id 重复：{item_id}，已改为 {repaired}"
            )
            item_id = repaired
        background_ids.add(item_id)
        owner = f"background {item_id}"
        kind = raw.get("kind", "background")
        if kind not in BACKGROUND_KINDS:
            if strict:
                raise ValueError(
                    f"{owner}.kind 只能是 background、case 或 event"
                )
            kind = "background"
        result_backgrounds.append(
            {
                "id": item_id,
                "text": _text(raw, "text", owner, strict=strict),
                "segment_ids": _raw_ids(
                    raw.get("segment_ids"),
                    aliases=aliases,
                    known=known,
                    warnings=warnings,
                    strict=strict,
                    owner=owner,
                ),
                "kind": kind,
            }
        )

    result_topics: list[dict[str, Any]] = []
    topic_ids: set[str] = set()
    topic_owner: dict[str, str] = {}
    raw_topics = payload.get("topics") or []
    if strict and not isinstance(raw_topics, list):
        raise ValueError("topics 必须是数组")
    for index, raw in enumerate(raw_topics if isinstance(raw_topics, list) else [], start=1):
        if not isinstance(raw, dict):
            if strict:
                raise ValueError(f"topics[{index - 1}] 必须是对象")
            continue
        item_id = str(raw.get("id") or raw.get("topic_id") or f"t{index}")
        if item_id in topic_ids:
            if strict:
                raise ValueError(f"topic id 重复：{item_id}")
            repaired = f"t{index}"
            suffix = 2
            while repaired in topic_ids:
                repaired = f"t{index}-{suffix}"
                suffix += 1
            warnings.append(f"topic id 重复：{item_id}，已改为 {repaired}")
            item_id = repaired
        topic_ids.add(item_id)
        owner = f"topic {item_id}"
        ids = _raw_ids(
            raw.get("segment_ids"),
            aliases=aliases,
            known=known,
            warnings=warnings,
            strict=strict,
            owner=owner,
        )
        unique_ids: list[str] = []
        for canonical in ids:
            first_topic = topic_owner.get(canonical)
            if first_topic is None:
                topic_owner[canonical] = item_id
                unique_ids.append(canonical)
                continue
            if strict:
                raise ValueError(
                    f"句子 {canonical} 同时归属 topic {first_topic} 和 {item_id}"
                )
            warnings.append(
                f"句子 {canonical} 已归属 topic {first_topic}，已从 {item_id} 移除"
            )
        if not unique_ids and not strict:
            warnings.append(f"topic {item_id} 没有有效句子，已丢弃")
            continue
        topic_status = raw.get("status", "pending")
        if topic_status not in TOPIC_STATUSES:
            if strict:
                raise ValueError(f"{owner}.status 只能是 pending 或 confirmed")
            topic_status = "pending"
        result_topics.append(
            {
                "id": item_id,
                "name": _text(raw, "name", owner, strict=strict)
                or _text(raw, "title", owner, strict=False)
                or f"主题 {index}",
                "summary": _text(raw, "summary", owner, strict=strict),
                "segment_ids": unique_ids,
                "duration_ms": sum(durations.get(item, 0) for item in unique_ids),
                "suggested_duration_s": _number(
                    raw,
                    "suggested_duration_s",
                    owner,
                    strict=strict,
                ),
                "status": topic_status,
            }
        )

    return {
        "generated_at": generated_at,
        "status": status,
        "claims": result_claims,
        "backgrounds": result_backgrounds,
        "topics": result_topics,
        "meta": {
            "source": source,
            "model": model or (
                str(meta.get("model") or "") if isinstance(meta, dict) else ""
            ),
            "warnings": list(dict.fromkeys(warnings)),
        },
    }


def validate_content_map(
    payload: dict[str, Any],
    segments: list[Any],
    *,
    source: str = "human",
    model: str = "",
    now_fn=None,
) -> dict[str, Any]:
    """严格校验人工整档提交，并重算所有主题时长。"""

    return _normalize(
        payload,
        segments,
        source=source,
        model=model,
        strict=True,
        generated_at=now_iso(now_fn),
    )


def _fallback_chunk(offset: int, segments: list[Any]) -> dict[str, Any]:
    return {
        "claims": [],
        "backgrounds": [],
        "topics": [
            {
                "id": f"fallback-{offset // CONTENT_MAP_CHUNK_SIZE + 1}",
                "name": f"未归类分块 {offset // CONTENT_MAP_CHUNK_SIZE + 1}",
                "summary": "该分块 AI 分析失败，保留全部句子等待人工归类",
                "segment_ids": [segment_id(segment) for segment in segments],
                "suggested_duration_s": 0,
                "status": "pending",
            }
        ],
    }


def analyze_content_map(
    segments: list[Any],
    *,
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[], str],
    model: str = "",
    now_fn=None,
) -> dict[str, Any]:
    """分析内容地图；长视频分块后再进行一次主题合并归纳。"""

    source_segments = list(segments or [])
    system = _system(assemble_prompt_fn)
    warnings: list[str] = []
    if len(source_segments) <= CONTENT_MAP_SPLIT_THRESHOLD:
        raw = _chat_with_retry(chat_json_fn, system, _digest(source_segments))
    else:
        chunks: list[dict[str, Any]] = []
        for offset in range(0, len(source_segments), CONTENT_MAP_CHUNK_SIZE):
            chunk = source_segments[offset : offset + CONTENT_MAP_CHUNK_SIZE]
            try:
                chunk_raw = _chat_with_retry(
                    chat_json_fn,
                    system,
                    _digest(chunk),
                )
            except Exception as exc:  # noqa: BLE001 - 单块降级，不泄露响应内容。
                logger.warning(
                    "content map chunk degraded: offset=%s error_type=%s",
                    offset,
                    type(exc).__name__,
                )
                warnings.append(
                    f"第 {offset // CONTENT_MAP_CHUNK_SIZE + 1} 块 AI 调用失败，已保留为待人工归类主题"
                )
                chunk_raw = _fallback_chunk(offset, chunk)
            chunks.append(
                _normalize(
                    chunk_raw,
                    chunk,
                    source="ai",
                    model=model,
                    strict=False,
                    generated_at=now_iso(now_fn),
                )
            )
        merge_user = (
            "以下是分块主题摘要。请跨块合并同一主题，并返回 content_map 协议中的 "
            "topics 数组；segment_ids 只能取输入中已有值：\n"
            + json.dumps(
                [
                    {
                        "chunk": index + 1,
                        "topics": chunk["topics"],
                    }
                    for index, chunk in enumerate(chunks)
                ],
                ensure_ascii=False,
            )
        )
        try:
            merged = _chat_with_retry(chat_json_fn, system, merge_user)
            merged_topics = merged.get("topics")
            if not isinstance(merged_topics, list):
                raise ValueError("合并调用缺少 topics")
        except Exception as exc:  # noqa: BLE001 - 合并失败降级为分块主题。
            logger.warning(
                "content map merge degraded: error_type=%s",
                type(exc).__name__,
            )
            warnings.append("跨块主题合并失败，已保留各分块主题")
            merged_topics = [
                topic
                for chunk in chunks
                for topic in chunk["topics"]
            ]
        raw = {
            "claims": [
                claim
                for chunk in chunks
                for claim in chunk["claims"]
            ],
            "backgrounds": [
                background
                for chunk in chunks
                for background in chunk["backgrounds"]
            ],
            "topics": merged_topics,
        }
    return _normalize(
        raw,
        source_segments,
        source="ai",
        model=model,
        strict=False,
        generated_at=now_iso(now_fn),
        inherited_warnings=warnings,
    )


__all__ = [
    "CONTENT_MAP_CHUNK_SIZE",
    "CONTENT_MAP_SPLIT_THRESHOLD",
    "analyze_content_map",
    "validate_content_map",
]
