from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..models import Transcript
from .llm_client import LlmClient, LlmError
from .prompt_store import MODE_PROMPT_FILES, PromptStore

logger = logging.getLogger("studio.ai")

# koubo 模式逐句决策可安全分块；整体视角的两个模式单次调用并设上限。
KOUBO_CHUNK_SIZE = 300
GLOBAL_MODE_MAX_SEGMENTS = 1200

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
        if mode not in MODE_PROMPT_FILES:
            raise ValueError(f"未知 AI 模式：{mode}")
        system = self._render_system(mode, brief=brief, target_duration=target_duration)
        known_ids = [segment.id for segment in transcript.segments]
        if mode == "koubo_tighten":
            payload, warnings = self._suggest_koubo(system, transcript)
        else:
            if len(known_ids) > GLOBAL_MODE_MAX_SEGMENTS:
                raise LlmError(
                    f"字幕句数 {len(known_ids)} 超过单次调用上限 {GLOBAL_MODE_MAX_SEGMENTS}，请先用口播精剪缩减"
                )
            raw = self.client.chat_json(system, _digest(transcript.segments))
            if mode == "topic_slicing":
                payload, warnings = _normalize_topics(raw, known_ids)
            else:
                payload, warnings = _normalize_remix(raw, known_ids)
        _attach_durations(payload, transcript)
        return Suggestion(mode=mode, payload=payload, warnings=warnings)

    def _suggest_koubo(self, system: str, transcript: Transcript) -> tuple[dict[str, Any], list[str]]:
        segments = transcript.segments
        decisions: dict[str, dict[str, Any]] = {}
        warnings: list[str] = []
        summaries: list[str] = []
        for offset in range(0, len(segments), KOUBO_CHUNK_SIZE):
            chunk = segments[offset : offset + KOUBO_CHUNK_SIZE]
            raw = self.client.chat_json(system, _digest(chunk))
            chunk_ids = {segment.id for segment in chunk}
            for item in raw.get("decisions") or []:
                segment_id = str(item.get("segment_id") or "")
                if segment_id not in chunk_ids:
                    warnings.append(f"忽略未知/越界 segment_id：{segment_id}")
                    continue
                decisions[segment_id] = {
                    "segment_id": segment_id,
                    "keep": bool(item.get("keep", True)),
                    "reason": str(item.get("reason") or ""),
                    "labels": [str(label) for label in (item.get("labels") or [])],
                }
            if raw.get("summary"):
                summaries.append(str(raw["summary"]))
        missing = [segment.id for segment in segments if segment.id not in decisions]
        for segment_id in missing:
            decisions[segment_id] = {
                "segment_id": segment_id,
                "keep": True,
                "reason": "AI 未覆盖，默认保留",
                "labels": ["uncovered"],
            }
        if missing:
            warnings.append(f"{len(missing)} 句未被 AI 覆盖，已默认保留")
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
    known = set(known_ids)
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
        hook = str(best.get("hook_segment_id") or "")
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
    known = set(known_ids)
    order = {segment_id: index for index, segment_id in enumerate(known_ids)}
    warnings: list[str] = []
    quotes = []
    for quote in raw.get("golden_quotes") or []:
        segment_id = str(quote.get("segment_id") or "")
        if segment_id not in known:
            warnings.append(f"忽略未知金句 segment_id：{segment_id}")
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


def _filter_ids(raw_ids: Any, known: set[str], warnings: list[str]) -> list[str]:
    result = []
    seen = set()
    for raw in raw_ids or []:
        segment_id = str(raw)
        if segment_id not in known:
            warnings.append(f"忽略未知 segment_id：{segment_id}")
            continue
        if segment_id in seen:
            continue
        seen.add(segment_id)
        result.append(segment_id)
    return result


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
