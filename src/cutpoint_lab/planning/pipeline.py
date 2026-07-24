from __future__ import annotations

import copy
import re
from collections.abc import Callable, Iterable
from typing import Any

from ._common import alias_map, now_iso, resolve_id, segment_id, segment_text
from .content_map import analyze_content_map
from .quotes import analyze_quote_candidates, merge_topic_candidates

INTENT_PRESETS = (
    {
        "key": "cut_fillers",
        "label": "删口癖 / 废话 / 重复",
        "brief": "删口癖、废话、重复表达和说错重来的句子",
        "default": True,
    },
    {
        "key": "hook_first",
        "label": "开头放钩子金句",
        "brief": "开头放钩子金句，优先挑选无需前文也能成立的最强一句",
        "default": True,
    },
    {
        "key": "keep_insights",
        "label": "保留干货观点",
        "brief": "优先保留有信息增量的观点、判断和方法",
        "default": False,
    },
    {
        "key": "keep_stories",
        "label": "保留案例 / 故事",
        "brief": "保留能支撑观点的案例、故事和关键过程",
        "default": False,
    },
    {
        "key": "cut_smalltalk",
        "label": "删寒暄 / 开场闲聊",
        "brief": "删掉寒暄、客套和与主题无关的开场闲聊",
        "default": False,
    },
    {
        "key": "keep_data",
        "label": "保留数据 / 结论",
        "brief": "优先保留具体数据、对比和明确结论",
        "default": False,
    },
)

_INTENT_BY_KEY = {str(item["key"]): item for item in INTENT_PRESETS}
_CUT_SLUG_PATTERN = re.compile(r"[^a-z0-9-]+")


class PlanPipelineError(RuntimeError):
    def __init__(self, message: str, *, warnings: list[str] | None = None):
        super().__init__(message)
        self.warnings = list(warnings or [])


class CutNameConflict(RuntimeError):
    """Cut 名称在分配后被并发任务占用，调用方可安全重试。"""


def validate_plan_request(payload: dict[str, Any]) -> dict[str, Any]:
    raw_intent = payload.get("intent", ["cut_fillers", "hook_first"])
    if not isinstance(raw_intent, list):
        raise ValueError("intent 必须是 key 数组")
    intent: list[str] = []
    for raw_key in raw_intent:
        if not isinstance(raw_key, str) or raw_key not in _INTENT_BY_KEY:
            raise ValueError(f"未知 plan intent：{raw_key}")
        if raw_key not in intent:
            intent.append(raw_key)

    intent_extra = payload.get("intent_extra", "")
    if not isinstance(intent_extra, str):
        raise ValueError("intent_extra 必须是字符串")
    duration_min_s = _positive_number(
        payload.get("duration_min_s", 180),
        "duration_min_s",
    )
    duration_max_s = _positive_number(
        payload.get("duration_max_s", 300),
        "duration_max_s",
    )
    if duration_min_s > duration_max_s:
        raise ValueError("duration_min_s 不能大于 duration_max_s")
    split_topics = payload.get("split_topics", True)
    if not isinstance(split_topics, bool):
        raise ValueError("split_topics 必须是 JSON boolean")
    return {
        "intent": intent,
        "intent_extra": intent_extra.strip(),
        "duration_min_s": duration_min_s,
        "duration_max_s": duration_max_s,
        "split_topics": split_topics,
    }


def generate_plans(
    segments: list[Any],
    *,
    intent: list[str],
    intent_extra: str,
    duration_min_s: int | float,
    duration_max_s: int | float,
    split_topics: bool,
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[str], str],
    list_cut_names_fn: Callable[[], Iterable[str]],
    create_cut_fn: Callable[[str, str, dict[str, Any]], Any],
    write_content_map_fn: Callable[[dict[str, Any]], Any],
    write_quote_candidates_fn: Callable[[dict[str, Any]], Any],
    progress_fn: Callable[[dict[str, Any]], Any] | None = None,
    model: str = "",
) -> dict[str, Any]:
    request = validate_plan_request(
        {
            "intent": intent,
            "intent_extra": intent_extra,
            "duration_min_s": duration_min_s,
            "duration_max_s": duration_max_s,
            "split_topics": split_topics,
        }
    )
    source_segments = list(segments or [])
    if not source_segments:
        raise ValueError("字幕没有可用于出方案的句子")
    by_id = {segment_id(segment): segment for segment in source_segments}
    warnings: list[str] = []

    if request["split_topics"]:
        _progress(
            progress_fn,
            stage="topics",
            detail="正在划分大主题…",
            topics_total=0,
            topics_done=0,
        )
        content_map = analyze_content_map(
            source_segments,
            chat_json_fn=chat_json_fn,
            assemble_prompt_fn=lambda: assemble_prompt_fn("content_map"),
            model=model,
        )
        content_map["status"] = "draft"
        topics = [
            topic
            for topic in content_map.get("topics") or []
            if isinstance(topic, dict) and topic.get("segment_ids")
        ]
        for topic in topics:
            # 管线会立即消费这些主题；整份地图仍保持 draft，供用户后续修订。
            topic["status"] = "confirmed"
        content_map["topics"] = topics
        if not topics:
            raise PlanPipelineError("大主题分析没有返回可用主题")
        write_content_map_fn(copy.deepcopy(content_map))
        _progress(
            progress_fn,
            stage="topics",
            detail=f"已划分 {len(topics)} 个大主题",
            topics_total=len(topics),
            topics_done=0,
        )
    else:
        content_map = None
        topics = [
            {
                "id": "whole",
                "name": "",
                "summary": _intent_summary(
                    request["intent"],
                    request["intent_extra"],
                ),
                "segment_ids": list(by_id),
                "status": "confirmed",
            }
        ]

    topics_total = len(topics)
    quote_document: dict[str, Any] | None = None
    best_by_topic: dict[str, dict[str, Any]] = {}
    _progress(
        progress_fn,
        stage="quotes",
        detail=f"正在给主题 1/{topics_total} 挑金句…",
        topics_total=topics_total,
        topics_done=0,
    )
    quote_map = {
        "status": "draft",
        "topics": topics,
    }
    for index, topic in enumerate(topics, start=1):
        topic_id = str(topic.get("id") or f"t{index}")
        try:
            result = analyze_quote_candidates(
                quote_map,
                source_segments,
                chat_json_fn=chat_json_fn,
                assemble_prompt_fn=lambda: assemble_prompt_fn(
                    "quote_candidates"
                ),
                topic_id=topic_id,
                model=model,
            )
            quote_document = merge_topic_candidates(
                quote_document,
                result,
                topic_id,
            )
            best = next(
                (
                    candidate
                    for candidate in quote_document.get("candidates") or []
                    if isinstance(candidate, dict)
                    and str(candidate.get("topic_id") or "") == topic_id
                ),
                None,
            )
            if best is not None:
                best_by_topic[topic_id] = best
        except Exception as exc:  # noqa: BLE001 - 单主题金句失败按契约降级。
            warnings.append(
                f"主题「{_topic_label(topic, index)}」金句分析失败：{exc}"
            )
        _progress(
            progress_fn,
            stage="quotes",
            detail=(
                f"已完成主题 {index}/{topics_total} 的金句分析"
                if index == topics_total
                else f"正在给主题 {index + 1}/{topics_total} 挑金句…"
            ),
            topics_total=topics_total,
            topics_done=index,
        )

    if quote_document is None:
        quote_document = {
            "generated_at": now_iso(),
            "candidates": [],
            "meta": {
                "source": "ai",
                "model": model,
                "warnings": list(warnings),
            },
        }
    write_quote_candidates_fn(copy.deepcopy(quote_document))

    cuts: list[str] = []
    _progress(
        progress_fn,
        stage="select",
        detail=f"正在筛选主题 1/{topics_total} 的句子…",
        topics_total=topics_total,
        topics_done=0,
    )
    for index, topic in enumerate(topics, start=1):
        topic_id = str(topic.get("id") or f"t{index}")
        topic_segments = [
            by_id[str(raw_id)]
            for raw_id in topic.get("segment_ids") or []
            if str(raw_id) in by_id
        ]
        label = _topic_label(topic, index)
        try:
            raw = _select_topic(
                topic,
                topic_segments,
                request=request,
                chat_json_fn=chat_json_fn,
                assemble_prompt_fn=assemble_prompt_fn,
            )
            label = (
                str(raw.get("topic_name") or "").strip()
                if not request["split_topics"]
                else label
            ) or label or _intent_summary(
                request["intent"],
                request["intent_extra"],
            )
            best = best_by_topic.get(topic_id)
            edl = _build_edl(
                topic_segments,
                raw,
                best=best,
                label=label,
                request=request,
            )
            base_name = (
                _topic_cut_name(topic_id, index)
                if request["split_topics"]
                else "ai-plan"
            )
            cut_name = _create_unique_cut(
                base_name,
                label,
                edl,
                list_cut_names_fn=list_cut_names_fn,
                create_cut_fn=create_cut_fn,
            )
            cuts.append(cut_name)
            if best is not None:
                best_id = str(best.get("id") or "")
                for candidate in quote_document.get("candidates") or []:
                    if (
                        isinstance(candidate, dict)
                        and str(candidate.get("id") or "") == best_id
                    ):
                        candidate["status"] = "accepted"
                        break
        except Exception as exc:  # noqa: BLE001 - 单主题筛选失败跳过，其他主题继续。
            warnings.append(f"主题「{label}」筛选失败，已跳过：{exc}")
        _progress(
            progress_fn,
            stage="select",
            detail=(
                f"已完成主题 {index}/{topics_total} 的句子筛选"
                if index == topics_total
                else f"正在筛选主题 {index + 1}/{topics_total} 的句子…"
            ),
            topics_total=topics_total,
            topics_done=index,
        )

    write_quote_candidates_fn(copy.deepcopy(quote_document))
    if not cuts:
        raise PlanPipelineError(
            "全部主题的句子筛选均失败",
            warnings=warnings,
        )
    return {
        "cuts": cuts,
        "warnings": warnings,
        "content_map": content_map,
        "quote_candidates": quote_document,
    }


def next_cut_name(base: str, existing: Iterable[str]) -> str:
    occupied = {str(name) for name in existing}
    if base not in occupied:
        return base
    suffix = 2
    while True:
        marker = f"-{suffix}"
        candidate = f"{base[: 32 - len(marker)].rstrip('-')}{marker}"
        if candidate not in occupied:
            return candidate
        suffix += 1


def _select_topic(
    topic: dict[str, Any],
    segments: list[Any],
    *,
    request: dict[str, Any],
    chat_json_fn: Callable[[str, str], dict],
    assemble_prompt_fn: Callable[[str], str],
) -> dict[str, Any]:
    if not segments:
        raise ValueError("主题没有有效字幕句子")
    intent_lines = [
        f"- {_INTENT_BY_KEY[key]['brief']}"
        for key in request["intent"]
    ]
    if request["intent_extra"]:
        intent_lines.append(f"- 用户补充：{request['intent_extra']}")
    min_s = _format_number(request["duration_min_s"])
    max_s = _format_number(request["duration_max_s"])
    topic_name = str(topic.get("name") or "")
    extra = (
        "\n\n## 本主题出片要求\n\n"
        f"主题：{topic_name or '整片'}\n"
        + "\n".join(intent_lines)
        + f"\n- 保留句总时长应落在 {min_s}–{max_s} 秒区间，宁紧勿超。"
        "\n- 除逐句 decisions 外，再返回 title_suggestions（1–2 条）"
        "和 topic_name（整片模式下概括模型识别到的大主题名）。"
    )
    system = str(assemble_prompt_fn("koubo_tighten"))
    system = system.replace("{{USER_BRIEF}}", extra)
    system = system.replace(
        "{{TARGET_DURATION}}",
        f"{min_s}–{max_s} 秒",
    )
    if extra not in system:
        system += extra
    user = "\n".join(
        ["以下是本主题字幕（每句格式为 [segment_id] 文本）："]
        + [
            f"[{segment_id(segment)}] {segment_text(segment)}"
            for segment in segments
        ]
    )
    last_error: Exception | None = None
    for _attempt in (1, 2):
        try:
            raw = chat_json_fn(system, user)
            if not isinstance(raw, dict):
                raise ValueError("句子筛选 AI 返回值必须是 JSON object")
            if not isinstance(raw.get("decisions"), list):
                raise ValueError("句子筛选 AI 返回值缺少 decisions 数组")
            return raw
        except Exception as exc:  # noqa: BLE001 - 每主题最多重试一次。
            last_error = exc
    assert last_error is not None
    raise last_error


def _build_edl(
    segments: list[Any],
    raw: dict[str, Any],
    *,
    best: dict[str, Any] | None,
    label: str,
    request: dict[str, Any],
) -> dict[str, Any]:
    ids = [segment_id(segment) for segment in segments]
    aliases = alias_map(ids)
    decisions: dict[str, dict[str, Any]] = {}
    for item in raw.get("decisions") or []:
        if not isinstance(item, dict):
            continue
        canonical = resolve_id(item.get("segment_id"), aliases)
        if canonical is None:
            continue
        decisions[canonical] = item
    kept = {
        item
        for item in ids
        if item not in decisions
        or not isinstance(decisions[item].get("keep"), bool)
        or decisions[item]["keep"]
    }
    best_segment_id = (
        str(best.get("segment_id") or "")
        if isinstance(best, dict)
        else ""
    )
    if best_segment_id in aliases.values():
        kept.add(best_segment_id)
    order = [item for item in ids if item in kept]
    if best_segment_id in order:
        order.insert(0, best_segment_id)

    rows: list[dict[str, Any]] = []
    for segment in segments:
        item_id = segment_id(segment)
        row: dict[str, Any] = {
            "id": item_id,
            "checked": item_id in kept,
            "text": segment_text(segment),
        }
        decision = decisions.get(item_id)
        if decision is not None and decision.get("reason"):
            row["reason"] = str(decision["reason"])
        if item_id == best_segment_id:
            row["role"] = "quote"
            row["locked"] = True
        rows.append(row)

    suggestions: list[str] = []
    for item in raw.get("title_suggestions") or []:
        text = str(item).strip()
        if text and text not in suggestions:
            suggestions.append(text)
        if len(suggestions) == 2:
            break
    minimum = request["duration_min_s"]
    maximum = request["duration_max_s"]
    return {
        "label": label,
        "rows": rows,
        "order": order,
        "brief": {
            "claim": label,
            "intent": list(request["intent"]),
            "intent_extra": request["intent_extra"],
            "target_duration_s": _clean_number((minimum + maximum) / 2),
            "tolerance_s": _clean_number((maximum - minimum) / 2),
            "title_suggestions": suggestions,
        },
        "source": "ai_plan_pipeline",
    }


def _create_unique_cut(
    base_name: str,
    label: str,
    edl: dict[str, Any],
    *,
    list_cut_names_fn: Callable[[], Iterable[str]],
    create_cut_fn: Callable[[str, str, dict[str, Any]], Any],
) -> str:
    for _attempt in range(100):
        name = next_cut_name(base_name, list_cut_names_fn())
        try:
            create_cut_fn(name, label, edl)
            return name
        except CutNameConflict:
            continue
    raise RuntimeError(f"无法为 {base_name} 分配可用 Cut 名称")


def _topic_cut_name(topic_id: str, index: int) -> str:
    slug = _CUT_SLUG_PATTERN.sub("-", topic_id.lower()).strip("-")
    return f"topic-{slug or index}"[:32].rstrip("-")


def _topic_label(topic: dict[str, Any], index: int) -> str:
    return str(topic.get("name") or topic.get("summary") or f"主题 {index}").strip()


def _intent_summary(intent: list[str], intent_extra: str) -> str:
    if intent_extra.strip():
        return intent_extra.strip()[:80]
    labels = [
        str(_INTENT_BY_KEY[key]["label"])
        for key in intent
        if key in _INTENT_BY_KEY
    ]
    return "、".join(labels) or "AI 剪辑方案"


def _positive_number(value: Any, field: str) -> int | float:
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        raise ValueError(f"{field} 必须是大于 0 的数字")
    return _clean_number(value)


def _clean_number(value: int | float) -> int | float:
    number = float(value)
    return int(number) if number.is_integer() else number


def _format_number(value: int | float) -> str:
    return str(_clean_number(value))


def _progress(
    progress_fn: Callable[[dict[str, Any]], Any] | None,
    *,
    stage: str,
    detail: str,
    topics_total: int,
    topics_done: int,
) -> None:
    if progress_fn is not None:
        progress_fn(
            {
                "stage": stage,
                "detail": detail,
                "topics_total": topics_total,
                "topics_done": topics_done,
            }
        )


__all__ = [
    "CutNameConflict",
    "INTENT_PRESETS",
    "PlanPipelineError",
    "generate_plans",
    "next_cut_name",
    "validate_plan_request",
]
