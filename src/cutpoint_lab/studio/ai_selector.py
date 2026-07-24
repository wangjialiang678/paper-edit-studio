from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Transcript
from .llm_client import LlmClient, LlmError
from .prompt_store import PromptStore

logger = logging.getLogger("studio.ai")

# koubo 模式逐句决策可安全分块。
# 每块句数上限：逐句决策的输出规模与句数成正比，思考型大模型（kimi-k3 等）
# 约 1s/句，300 句会撞上 DashScope 网关约 298s 的响应流超时（HTTP 504）。
KOUBO_CHUNK_SIZE = 100
SELECTION_MODES = {"koubo_tighten"}
RETIRED_SELECTION_MODES = {"topic_slicing", "highlight_remix"}

HARD_CONSTRAINTS = (
    "\n\n【系统级硬约束（优先级最高）】"
    "只能引用输入中已有的 segment_id；禁止输出任何时间戳；"
    "只输出一个 JSON object，不含其他文字。"
)


@dataclass(frozen=True)
class Suggestion:
    mode: str
    payload: dict[str, Any]
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return {"mode": self.mode, "warnings": self.warnings, **self.payload}


class AiSelector:
    def __init__(
        self,
        prompts_dir: str | Path,
        client: LlmClient | None = None,
        *,
        workspace_root: str | Path | None = None,
        prompt_store: PromptStore | None = None,
    ):
        self.prompts_dir = Path(prompts_dir)
        override_dir = Path(workspace_root) / "_settings" / "prompts" if workspace_root is not None else None
        self.prompt_store = prompt_store or PromptStore(self.prompts_dir, override_dir)
        self.client = client if client is not None else LlmClient()

    def available(self) -> bool:
        return self.client.available()

    def suggest(
        self,
        transcript: Transcript,
        mode: str,
        *,
        brief: str = "",
        target_duration: str = "",
    ) -> Suggestion:
        if mode in RETIRED_SELECTION_MODES:
            raise ValueError(f"{mode} 已并入 AI 出剪辑方案")
        if mode not in SELECTION_MODES:
            raise ValueError(f"未知 AI 模式：{mode}")
        system = self._render_system(mode, brief=brief, target_duration=target_duration)
        payload, warnings = self._suggest_koubo(system, transcript)
        _attach_durations(payload, transcript)
        return Suggestion(mode=mode, payload=payload, warnings=warnings)

    def _suggest_koubo(self, system: str, transcript: Transcript) -> tuple[dict[str, Any], list[str]]:
        segments = transcript.segments
        decisions = {
            segment.id: {
                "segment_id": segment.id,
                "keep": True,
                "reason": "",
                "labels": [],
            }
            for segment in segments
        }
        warnings: list[str] = []
        summaries: list[str] = []
        for offset in range(0, len(segments), KOUBO_CHUNK_SIZE):
            chunk = segments[offset : offset + KOUBO_CHUNK_SIZE]
            raw = None
            for attempt in (1, 2):
                try:
                    raw = self.client.chat_json(system, _digest(chunk))
                    if not isinstance(raw, dict) or not isinstance(
                        raw.get("drop"),
                        list,
                    ):
                        raise LlmError("口播筛选 AI 返回值缺少 drop 数组")
                    break
                except LlmError as exc:
                    # 模型偶发吐垃圾/断流：单块重试一次，仍失败降级为该块默认保留，
                    # 不让一块坏输出毁掉整次分析。
                    logger.warning("koubo chunk@%s attempt %s failed: %s", offset, attempt, exc)
                    if attempt == 2:
                        warnings.append(
                            f"第 {offset // KOUBO_CHUNK_SIZE + 1} 块 AI 调用失败（{exc}），"
                            f"该块 {len(chunk)} 句已默认保留"
                        )
            if raw is None:
                continue
            aliases = _alias_map([segment.id for segment in chunk])
            for item in raw["drop"]:
                if not isinstance(item, dict):
                    continue
                segment_id = _resolve_id(item.get("id"), aliases)
                if segment_id is None:
                    warnings.append(f"忽略未知/越界 segment_id：{item.get('id')}")
                    continue
                reason = str(item.get("reason") or "")
                if len(reason) > 15:
                    warnings.append(
                        f"{segment_id} 的删除理由超过 15 字，已截断"
                    )
                decisions[segment_id] = {
                    "segment_id": segment_id,
                    "keep": False,
                    "reason": reason[:15],
                    "labels": [],
                }
            if raw.get("summary"):
                summaries.append(str(raw["summary"]))
        ordered = [decisions[segment.id] for segment in segments]
        payload = {
            "summary": " / ".join(summaries),
            "decisions": ordered,
            "keep_segment_ids": [item["segment_id"] for item in ordered if item["keep"]],
        }
        return payload, warnings

    def _render_system(self, mode: str, *, brief: str, target_duration: str) -> str:
        template = self.prompt_store.assemble(mode)
        brief_block = f"\n## 用户补充要求\n\n{brief.strip()}\n" if brief.strip() else ""
        rendered = template.replace("{{USER_BRIEF}}", brief_block)
        rendered = rendered.replace("{{TARGET_DURATION}}", target_duration.strip() or "未指定")
        return rendered + HARD_CONSTRAINTS


def save_suggestion(ai_dir: Path, suggestion: Suggestion) -> Path:
    ai_dir.mkdir(parents=True, exist_ok=True)
    path = ai_dir / f"{suggestion.mode}-{time.strftime('%Y%m%d-%H%M%S')}.json"
    import json

    path.write_text(json.dumps(suggestion.to_dict(), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def _digest(segments) -> str:
    lines = ["以下是字幕句子（格式：[segment_id] 起-止 文本）："]
    for segment in segments:
        lines.append(f"[{segment.id}] {_clock(segment.start_ms)}-{_clock(segment.end_ms)} {segment.text}")
    return "\n".join(lines)


def _clock(value_ms: int) -> str:
    minutes, remainder = divmod(max(0, int(value_ms)), 60_000)
    seconds = remainder // 1000
    return f"{minutes:02d}:{seconds:02d}"


def _normalize_topics(raw: dict[str, Any], known_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    known = _alias_map(known_ids)
    order = {segment_id: index for index, segment_id in enumerate(known_ids)}
    warnings: list[str] = []
    topics = []
    for index, topic in enumerate(raw.get("topics") or [], start=1):
        segment_ids = _filter_ids(topic.get("segment_ids"), known, warnings)
        segment_ids.sort(key=lambda item: order[item])
        if not segment_ids:
            warnings.append(f"主题 {topic.get('topic_id') or index} 无有效句子，已丢弃")
            continue
        best = topic.get("best_clip") or {}
        clip_ids = [item for item in _filter_ids(best.get("segment_ids"), known, warnings) if item in set(segment_ids)]
        clip_ids.sort(key=lambda item: order[item])
        if not clip_ids:
            clip_ids = segment_ids
        hook = _resolve_id(best.get("hook_segment_id"), known) or ""
        if hook not in clip_ids:
            hook = clip_ids[0]
        topics.append(
            {
                "topic_id": str(topic.get("topic_id") or f"topic_{index:02d}"),
                "title": str(topic.get("title") or f"主题 {index}"),
                "summary": str(topic.get("summary") or ""),
                "segment_ids": segment_ids,
                "best_clip": {
                    "segment_ids": clip_ids,
                    "hook_segment_id": hook,
                    "skipped_segment_ids": _filter_ids(best.get("skipped_segment_ids"), known, warnings),
                    "reason": str(best.get("reason") or ""),
                },
            }
        )
    payload = {
        "overview": str(raw.get("overview") or ""),
        "topics": topics,
        "unassigned_segment_ids": _filter_ids(raw.get("unassigned_segment_ids"), known, warnings),
    }
    return payload, warnings


def _normalize_remix(raw: dict[str, Any], known_ids: list[str]) -> tuple[dict[str, Any], list[str]]:
    known = _alias_map(known_ids)
    order = {segment_id: index for index, segment_id in enumerate(known_ids)}
    warnings: list[str] = []
    quotes = []
    for quote in raw.get("golden_quotes") or []:
        segment_id = _resolve_id(quote.get("segment_id"), known)
        if segment_id is None:
            warnings.append(f"忽略未知金句 segment_id：{quote.get('segment_id')}")
            continue
        strength = quote.get("strength")
        quotes.append(
            {
                "segment_id": segment_id,
                "quote": str(quote.get("quote") or ""),
                "strength": int(strength) if isinstance(strength, (int, float)) else 3,
                "reason": str(quote.get("reason") or ""),
            }
        )
    clips = []
    for clip in raw.get("clips") or []:
        purpose = str(clip.get("purpose") or "")
        if purpose not in {"hook", "body", "echo"}:
            warnings.append(f"忽略未知 clip purpose：{purpose}")
            continue
        segment_ids = _filter_ids(clip.get("segment_ids"), known, warnings)
        if purpose == "body":
            segment_ids.sort(key=lambda item: order[item])
        if not segment_ids:
            warnings.append(f"{purpose} 片段无有效句子，已丢弃")
            continue
        clips.append({"purpose": purpose, "segment_ids": segment_ids, "note": str(clip.get("note") or "")})
    payload = {
        "golden_quotes": quotes,
        "clips": clips,
        "title_suggestions": [str(item) for item in (raw.get("title_suggestions") or [])],
    }
    return payload, warnings


def _filter_ids(raw_ids: Any, known: dict[str, str], warnings: list[str]) -> list[str]:
    result = []
    seen = set()
    for raw in raw_ids or []:
        segment_id = _resolve_id(raw, known)
        if segment_id is None:
            warnings.append(f"忽略未知 segment_id：{raw}")
            continue
        if segment_id in seen:
            continue
        seen.add(segment_id)
        result.append(segment_id)
    return result


def _alias_map(ids: list[str]) -> dict[str, str]:
    """规范 id 及其常见变体 → 规范 id 的映射。

    模型偶尔把 sentence_0055 简写成 0055 / 55 / sentence_55（实测 glm-5.2-fast-preview），
    这些变体在已知集合内可确定性还原；产生歧义的别名直接丢弃（宁缺勿错）。
    """
    AMBIGUOUS = object()
    aliases: dict[str, Any] = {}

    def put(alias: str, target: str) -> None:
        if not alias:
            return
        current = aliases.get(alias)
        if current is None:
            aliases[alias] = target
        elif current is not AMBIGUOUS and current != target:
            aliases[alias] = AMBIGUOUS

    for segment_id in ids:
        put(segment_id, segment_id)
        match = re.search(r"(\d+)$", segment_id)
        if match:
            digits = match.group(1)
            prefix = segment_id[: match.start()]
            put(digits, segment_id)
            put(str(int(digits)), segment_id)
            put(prefix + str(int(digits)), segment_id)
    return {alias: target for alias, target in aliases.items() if target is not AMBIGUOUS}


def _resolve_id(raw: Any, aliases: dict[str, str]) -> str | None:
    """把模型输出的 segment_id（含变体写法）还原成规范 id；无法确定时返回 None。

    兜底一层：前缀被模型写坏（实测 glm 产出过 性_0021、游戏_id_0080）时，
    提取尾部数字段在别名表里再查一次——编号在集合内唯一，仍是确定性还原。
    """
    if raw is None:
        return None
    text = str(raw).strip()
    direct = aliases.get(text) or aliases.get(text.lower())
    if direct:
        return direct
    match = re.search(r"(\d+)\s*$", text)
    if match:
        return aliases.get(match.group(1)) or aliases.get(str(int(match.group(1))))
    return None


def _attach_durations(payload: dict[str, Any], transcript: Transcript) -> None:
    durations = {segment.id: max(0, segment.end_ms - segment.start_ms) for segment in transcript.segments}

    def total(ids: list[str]) -> int:
        return sum(durations.get(segment_id, 0) for segment_id in ids)

    if "keep_segment_ids" in payload:
        payload["keep_duration_ms"] = total(payload["keep_segment_ids"])
    for topic in payload.get("topics") or []:
        topic["duration_ms"] = total(topic["segment_ids"])
        topic["best_clip"]["duration_ms"] = total(topic["best_clip"]["segment_ids"])
    if payload.get("clips"):
        payload["clips_duration_ms"] = total(
            [segment_id for clip in payload["clips"] for segment_id in clip["segment_ids"]]
        )
        for clip in payload["clips"]:
            clip["duration_ms"] = total(clip["segment_ids"])
