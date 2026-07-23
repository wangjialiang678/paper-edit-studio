from __future__ import annotations

import copy
from collections.abc import Callable
from typing import Any

from ._common import alias_map, now_iso, resolve_id, segment_id, segment_text

QUOTE_TYPES = {"claim", "hook", "background", "question", "action"}
QUOTE_STATUSES = {"pending", "accepted", "rejected"}


def _confirmed_topics(
    content_map: dict[str, Any],
    topic_id: str | None,
) -> list[dict[str, Any]]:
    topics = [
        item
        for item in content_map.get("topics") or []
        if isinstance(item, dict) and item.get("status") == "confirmed"
    ]
    if topic_id is not None:
        topics = [
            topic
            for topic in topics
            if str(topic.get("id") or topic.get("topic_id") or "") == topic_id
        ]
    if not topics:
        raise ValueError("content_map 中没有可分析的 confirmed topic")
    return topics


def _prompt(topics: list[dict[str, Any]], segments: list[Any]) -> str:
    by_id = {segment_id(segment): segment for segment in segments}
    lines = ["以下是已确认主题及其字幕。请为每个主题给出 3–5 个金句候选："]
    for topic in topics:
        topic_id = str(topic.get("id") or topic.get("topic_id") or "")
        lines.append(
            f"\n## [{topic_id}] {str(topic.get('name') or '')}\n"
            f"摘要：{str(topic.get('summary') or '')}"
        )
        for raw_id in topic.get("segment_ids") or []:
            canonical = str(raw_id)
            segment = by_id.get(canonical)
            if segment is not None:
                lines.append(f"[{canonical}] {segment_text(segment)}")
    return "\n".join(lines)


def analyze_quote_candidates(
    content_map: dict[str, Any],
    segments: list[Any],
    *,
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[], str],
    topic_id: str | None = None,
    model: str = "",
    now_fn=None,
) -> dict[str, Any]:
    topics = _confirmed_topics(content_map, topic_id)
    source_segments = list(segments or [])
    known_ids = [segment_id(segment) for segment in source_segments]
    aliases = alias_map(known_ids)
    allowed_by_topic = {
        str(topic.get("id") or topic.get("topic_id") or ""): {
            str(item) for item in topic.get("segment_ids") or []
        }
        for topic in topics
    }
    system = str(assemble_prompt_fn()).replace("{{USER_BRIEF}}", "")
    raw = chat_json_fn(system, _prompt(topics, source_segments))
    if not isinstance(raw, dict):
        raise ValueError("金句 AI 返回值必须是 JSON object")
    raw_candidates = raw.get("candidates")
    if not isinstance(raw_candidates, list):
        raw_candidates = []

    warnings: list[str] = []
    candidates: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    candidate_ids: set[str] = set()
    per_topic: dict[str, int] = {item: 0 for item in allowed_by_topic}
    for index, item in enumerate(raw_candidates, start=1):
        if not isinstance(item, dict):
            continue
        candidate_topic = str(item.get("topic_id") or "")
        allowed = allowed_by_topic.get(candidate_topic)
        if allowed is None:
            warnings.append(f"忽略未知或未确认 topic_id：{candidate_topic}")
            continue
        canonical = resolve_id(item.get("segment_id"), aliases)
        if canonical is None or canonical not in allowed:
            warnings.append(
                f"忽略不属于 topic {candidate_topic} 的 segment_id：{item.get('segment_id')}"
            )
            continue
        candidate_type = str(item.get("type") or "")
        if candidate_type not in QUOTE_TYPES:
            warnings.append(f"忽略未知金句类型：{candidate_type}")
            continue
        key = (candidate_topic, canonical)
        if key in seen:
            warnings.append(
                f"topic {candidate_topic} 的句子 {canonical} 出现重复候选，已去重"
            )
            continue
        if per_topic[candidate_topic] >= 5:
            warnings.append(f"topic {candidate_topic} 超过 5 个候选，已截断")
            continue
        seen.add(key)
        per_topic[candidate_topic] += 1
        item_id = str(item.get("id") or f"q{index}")
        if item_id in candidate_ids:
            repaired = f"q{index}"
            suffix = 2
            while repaired in candidate_ids:
                repaired = f"q{index}-{suffix}"
                suffix += 1
            warnings.append(f"candidate id 重复：{item_id}，已改为 {repaired}")
            item_id = repaired
        candidate_ids.add(item_id)
        candidates.append(
            {
                "id": item_id,
                "topic_id": candidate_topic,
                "segment_id": canonical,
                "type": candidate_type,
                "context": str(item.get("context") or ""),
                "reason": str(item.get("reason") or ""),
                "status": "pending",
            }
        )
    for confirmed_id, count in per_topic.items():
        if count < 3:
            warnings.append(
                f"topic {confirmed_id} 仅返回 {count} 个有效候选，少于要求的 3 个"
            )
    return {
        "generated_at": now_iso(now_fn),
        "candidates": candidates,
        "meta": {
            "source": "ai",
            "model": model,
            "warnings": list(dict.fromkeys(warnings)),
        },
    }


def accept_quote(
    edl: dict[str, Any],
    candidate: dict[str, Any],
    *,
    promote: bool,
) -> dict[str, Any]:
    if not isinstance(promote, bool):
        raise ValueError("promote 必须是 JSON boolean")
    updated = copy.deepcopy(edl)
    segment_id_value = str(candidate.get("segment_id") or "")
    target = None
    rows = updated.get("rows")
    if not isinstance(rows, list):
        raise ValueError("EDL 的 rows 必须是数组")
    for row in rows:
        if isinstance(row, dict) and str(row.get("id") or "") == segment_id_value:
            target = row
            break
    if target is None:
        raise ValueError(f"EDL 中不存在候选句：{segment_id_value}")
    target["checked"] = True
    target["role"] = "quote"
    target["locked"] = True

    raw_order = updated.get("order")
    if raw_order is not None and not isinstance(raw_order, list):
        raise ValueError("EDL 的 order 必须是数组")
    order = [str(item) for item in (raw_order or [])]
    if promote:
        if not order:
            order = [
                str(row.get("id"))
                for row in rows
                if isinstance(row, dict) and bool(row.get("checked"))
            ]
        order = [item for item in order if item != segment_id_value]
        order.insert(0, segment_id_value)
        updated["order"] = order
    elif order and segment_id_value not in order:
        # order 非空时它是实际保留集；追加一次才能让 checked=true 真正进入成片。
        updated["order"] = [*order, segment_id_value]
    elif "order" not in updated:
        updated["order"] = []
    return updated


def merge_topic_candidates(
    previous: dict[str, Any] | None,
    result: dict[str, Any],
    topic_id: str,
) -> dict[str, Any]:
    """替换单个主题的候选，并保证整份文档的 candidate id 唯一。"""

    merged = copy.deepcopy(result)
    retained = [
        copy.deepcopy(item)
        for item in (previous or {}).get("candidates") or []
        if isinstance(item, dict)
        and str(item.get("topic_id") or "") != topic_id
    ]
    incoming = [
        copy.deepcopy(item)
        for item in merged.get("candidates") or []
        if isinstance(item, dict)
    ]
    candidates = [*retained, *incoming]
    used: set[str] = set()
    repairs: list[str] = []
    for index, candidate in enumerate(candidates, start=1):
        original = str(candidate.get("id") or f"q{index}")
        repaired = original
        suffix = 2
        while repaired in used:
            repaired = f"{original}-{suffix}"
            suffix += 1
        if repaired != original:
            repairs.append(f"candidate id 重复：{original}，已改为 {repaired}")
        candidate["id"] = repaired
        used.add(repaired)
    merged["candidates"] = candidates
    meta = merged.get("meta")
    if not isinstance(meta, dict):
        meta = {}
        merged["meta"] = meta
    warnings = meta.get("warnings")
    if not isinstance(warnings, list):
        warnings = []
    meta["warnings"] = list(
        dict.fromkeys([str(item) for item in warnings] + repairs)
    )
    return merged


def update_candidate_status(
    document: dict[str, Any],
    candidate_id: str,
    status: str,
) -> dict[str, Any]:
    if status not in QUOTE_STATUSES:
        raise ValueError("候选状态必须是 pending、accepted 或 rejected")
    updated = copy.deepcopy(document)
    candidates = updated.get("candidates")
    if not isinstance(candidates, list):
        raise ValueError("quote_candidates.candidates 必须是数组")
    for candidate in candidates:
        if isinstance(candidate, dict) and str(candidate.get("id") or "") == candidate_id:
            candidate["status"] = status
            return updated
    raise KeyError(candidate_id)


__all__ = [
    "QUOTE_STATUSES",
    "QUOTE_TYPES",
    "accept_quote",
    "analyze_quote_candidates",
    "merge_topic_candidates",
    "update_candidate_status",
]
