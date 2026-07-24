from __future__ import annotations

import copy
import logging
import re
import unicodedata
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from ._common import (
    alias_map,
    now_iso,
    resolve_id,
    segment_duration_ms,
    segment_id,
    segment_text,
)
from ._workers import plan_workers
from .content_map import analyze_content_map
from .quotes import analyze_quote_candidates, merge_topic_candidates

logger = logging.getLogger(__name__)

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
    workers = min(plan_workers(), topics_total)
    logger.info(
        "plan topics start: topics=%s workers=%s split=%s",
        topics_total,
        workers,
        request["split_topics"],
    )
    quote_document: dict[str, Any] | None = None
    best_by_topic: dict[str, dict[str, Any]] = {}
    _progress(
        progress_fn,
        stage="quotes",
        detail=f"并行给 {topics_total} 个主题挑金句…",
        topics_total=topics_total,
        topics_done=0,
    )
    quote_map = {
        "status": "draft",
        "topics": topics,
    }
    quote_results: list[dict[str, Any] | None] = [None for _ in topics]
    quote_errors: list[Exception | None] = [None for _ in topics]

    def analyze_topic_quotes(
        topic_index: int,
        topic: dict[str, Any],
    ) -> dict[str, Any]:
        topic_id = str(topic.get("id") or f"t{topic_index + 1}")
        return analyze_quote_candidates(
            quote_map,
            source_segments,
            chat_json_fn=chat_json_fn,
            assemble_prompt_fn=lambda: assemble_prompt_fn(
                "quote_candidates"
            ),
            topic_id=topic_id,
            model=model,
        )

    # LlmClient.chat_json 的配置读取有锁，请求体与 HTTP 连接均为调用内局部变量；
    # PromptStore.assemble 只读文件，因此同步 CLI 与异步 Web 入口可安全共享 callable。
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="pe-plan-quotes",
    ) as executor:
        future_indexes = {
            executor.submit(analyze_topic_quotes, index, topic): index
            for index, topic in enumerate(topics)
        }
        for future in as_completed(future_indexes):
            index = future_indexes[future]
            try:
                quote_results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 单主题金句失败按契约降级。
                quote_errors[index] = exc

    for index, topic in enumerate(topics, start=1):
        topic_id = str(topic.get("id") or f"t{index}")
        result = quote_results[index - 1]
        error = quote_errors[index - 1]
        if error is not None:
            warnings.append(
                f"主题「{_topic_label(topic, index)}」金句分析失败：{error}"
            )
            continue
        if result is None:
            continue
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
    _progress(
        progress_fn,
        stage="quotes",
        detail="金句分析完成，准备并行筛句…",
        topics_total=topics_total,
        topics_done=0,
    )
    write_quote_candidates_fn(copy.deepcopy(quote_document))

    cuts: list[str] = []
    _progress(
        progress_fn,
        stage="select",
        detail=f"并行处理 {topics_total} 个主题：已完成 0 个…",
        topics_total=topics_total,
        topics_done=0,
    )
    selection_specs = []
    for index, topic in enumerate(topics, start=1):
        topic_segments = [
            by_id[str(raw_id)]
            for raw_id in topic.get("segment_ids") or []
            if str(raw_id) in by_id
        ]
        selection_specs.append(
            (index, topic, topic_segments, _topic_label(topic, index))
        )

    def select_topic(spec):
        index, topic, topic_segments, label = spec
        topic_id = str(topic.get("id") or f"t{index}")
        raw = _select_topic(
            topic,
            topic_segments,
            request=request,
            chat_json_fn=chat_json_fn,
            assemble_prompt_fn=assemble_prompt_fn,
        )
        effective_label = label or _intent_summary(
            request["intent"],
            request["intent_extra"],
        )
        best = best_by_topic.get(topic_id)
        edl = _build_edl(
            topic_segments,
            raw,
            best=best,
            label=effective_label,
            request=request,
        )
        return topic_id, effective_label, edl, best

    selection_results = [None for _ in topics]
    selection_errors: list[Exception | None] = [None for _ in topics]
    completed = 0
    with ThreadPoolExecutor(
        max_workers=workers,
        thread_name_prefix="pe-plan-select",
    ) as executor:
        future_indexes = {
            executor.submit(select_topic, spec): index
            for index, spec in enumerate(selection_specs)
        }
        for future in as_completed(future_indexes):
            index = future_indexes[future]
            try:
                selection_results[index] = future.result()
            except Exception as exc:  # noqa: BLE001 - 单主题筛选失败按契约降级。
                selection_errors[index] = exc
            completed += 1
            _progress(
                progress_fn,
                stage="select",
                detail=(
                    f"并行处理 {topics_total} 个主题："
                    f"已完成 {completed} 个…"
                ),
                topics_total=topics_total,
                topics_done=completed,
            )

    for result_index, spec in enumerate(selection_specs):
        index, _topic, _topic_segments, label = spec
        error = selection_errors[result_index]
        if error is not None:
            warnings.append(f"主题「{label}」筛选失败，已跳过：{error}")
            continue
        try:
            topic_id, label, edl, best = selection_results[result_index]
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
        except Exception as exc:  # noqa: BLE001 - 单主题落盘失败跳过，其他主题继续。
            warnings.append(f"主题「{label}」筛选失败，已跳过：{exc}")

    write_quote_candidates_fn(copy.deepcopy(quote_document))
    if not cuts:
        raise PlanPipelineError(
            "全部主题的句子筛选均失败",
            warnings=warnings,
        )
    logger.info(
        "plan topics done: topics=%s cuts=%s warnings=%s",
        topics_total,
        len(cuts),
        len(warnings),
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
    char_budget_line = _topic_char_budget_line(
        segments,
        duration_min_s=request["duration_min_s"],
        duration_max_s=request["duration_max_s"],
    )
    extra = (
        "\n\n## 本主题出片要求\n\n"
        f"主题：{topic_name or '整片'}\n"
        + "\n".join(intent_lines)
        + f"\n- 保留句总时长应落在 {min_s}–{max_s} 秒区间，宁紧勿超。"
        + char_budget_line
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
            if not isinstance(raw.get("drop"), list):
                raise ValueError("句子筛选 AI 返回值缺少 drop 数组")
            return raw
        except Exception as exc:  # noqa: BLE001 - 每主题最多重试一次。
            last_error = exc
    assert last_error is not None
    raise last_error


def _topic_char_budget_line(
    segments: list[Any],
    *,
    duration_min_s: int | float,
    duration_max_s: int | float,
) -> str:
    duration_ms = sum(segment_duration_ms(segment) for segment in segments)
    character_count = sum(
        _effective_character_count(segment_text(segment))
        for segment in segments
    )
    if duration_ms <= 0 or character_count <= 0:
        return ""
    characters_per_second = character_count / (duration_ms / 1000)
    minimum = _round_to_tens(float(duration_min_s) * characters_per_second)
    maximum = _round_to_tens(float(duration_max_s) * characters_per_second)
    return (
        "\n- 按本片语速换算，保留句总字数应落在约 "
        f"{minimum}–{maximum} 字。"
    )


def _effective_character_count(text: str) -> int:
    return sum(
        1
        for character in text
        if not character.isspace()
        and not unicodedata.category(character).startswith("P")
    )


def _round_to_tens(value: float) -> int:
    return int(value / 10 + 0.5) * 10


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
    drops: dict[str, dict[str, Any]] = {}
    for item in raw.get("drop") or []:
        # 协议只回纯 id 字符串（通用剪辑方案，无理由）；容忍旧 {id, ...} 对象格式。
        if isinstance(item, dict):
            raw_id = item.get("id")
        elif isinstance(item, str):
            raw_id, item = item, {}
        else:
            continue
        canonical = resolve_id(raw_id, aliases)
        if canonical is None:
            continue
        drops[canonical] = item
    kept = {item for item in ids if item not in drops}
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
        drop = drops.get(item_id)
        if drop is not None and drop.get("reason"):
            row["reason"] = str(drop["reason"])[:15]
        if item_id == best_segment_id:
            row["role"] = "quote"
            row["locked"] = True
        rows.append(row)

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
            "title_suggestions": [],
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
