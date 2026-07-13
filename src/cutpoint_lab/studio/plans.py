from __future__ import annotations

from typing import Any

from ..features import AudioFrame
from ..models import SpeakerData, Transcript, VadData
from ..paper_edit.state import _strategy as make_strategy

MIN_SILENCE_GAP_MS = 300


def silence_gaps(transcript: Transcript, *, min_gap_ms: int = MIN_SILENCE_GAP_MS) -> list[dict[str, Any]]:
    """相邻字幕句之间的无声/停顿段，供 UI 以"无声 X.XXs"标记展示。"""
    gaps = []
    ordered = sorted(transcript.segments, key=lambda segment: segment.start_ms)
    for previous, current in zip(ordered, ordered[1:]):
        gap_ms = current.start_ms - previous.end_ms
        if gap_ms >= min_gap_ms:
            gaps.append(
                {
                    "after_segment_id": previous.id,
                    "start_ms": previous.end_ms,
                    "end_ms": current.start_ms,
                    "gap_ms": gap_ms,
                }
            )
    head_gap = ordered[0].start_ms if ordered else 0
    if head_gap >= min_gap_ms:
        gaps.insert(
            0,
            {"after_segment_id": None, "start_ms": 0, "end_ms": head_gap, "gap_ms": head_gap},
        )
    return gaps


MIN_RANGE_MS = 80


def apply_manual_nudges(plan: dict[str, Any], nudges_by_segment: dict[str, dict[str, Any]]) -> dict[str, Any]:
    """把用户对句首/句尾切点的手动毫秒偏移叠加到策略输出上。

    只在真实存在切点的地方生效：range 首段的 start、末段的 end。
    句子被合并进 range 中段时该处没有切点，偏移自然忽略。
    """
    if not nudges_by_segment:
        return plan
    for item in plan.get("ranges") or []:
        ids = item.get("source_segment_ids") or []
        if not ids:
            continue
        start_nudge = int((nudges_by_segment.get(str(ids[0])) or {}).get("start_ms") or 0)
        end_nudge = int((nudges_by_segment.get(str(ids[-1])) or {}).get("end_ms") or 0)
        if not start_nudge and not end_nudge:
            continue
        start_ms = int(item["start_ms"])
        end_ms = int(item["end_ms"])
        if start_nudge:
            start_ms = max(0, start_ms + start_nudge)
        if end_nudge:
            end_ms = end_ms + end_nudge
        if end_ms - start_ms < MIN_RANGE_MS:
            # 手动偏移把片段挤没了：保住最小时长，优先尊重 start。
            end_ms = start_ms + MIN_RANGE_MS
        item["start_ms"] = start_ms
        item["end_ms"] = end_ms
        reason = str(item.get("adjustment_reason") or "")
        item["adjustment_reason"] = f"{reason}+manual" if reason else "manual"
    return plan


def build_ordered_plan(
    transcript: Transcript,
    ordered_groups: list[dict[str, Any]],
    *,
    strategy: str = "token_padding",
    frames: list[AudioFrame] | None = None,
    voice_frames: list[AudioFrame] | None = None,
    vad: VadData | None = None,
    speaker_data: SpeakerData | None = None,
    require_word_timestamps: bool = True,
) -> dict[str, Any]:
    """按分组顺序构建剪辑计划（金句前置/重复强调等乱序结构）。

    每组内部按原文时间顺序、组与组之间按传入顺序拼接；
    导出端 export_video_plan 按 ranges 顺序 concat，天然支持乱序与重复。
    """
    if not ordered_groups:
        raise ValueError("ordered_groups 不能为空")
    known = {segment.id for segment in transcript.segments}
    optimizer = make_strategy(
        strategy=strategy,
        frames=frames or [],
        voice_frames=voice_frames or [],
        vad=vad,
        speaker_data=speaker_data,
    )
    ranges: list[dict[str, Any]] = []
    normalized_groups = []
    for index, group in enumerate(ordered_groups, start=1):
        segment_ids = [str(item) for item in group.get("segment_ids") or []]
        unknown = [segment_id for segment_id in segment_ids if segment_id not in known]
        if unknown:
            raise ValueError(f"分组 {index} 含未知 segment_id：{', '.join(unknown)}")
        if not segment_ids:
            continue
        if require_word_timestamps:
            missing = [
                segment.id
                for segment in transcript.segments
                if segment.id in set(segment_ids) and not segment.valid_tokens
            ]
            if missing:
                raise ValueError("以下句子缺少词级时间戳，无法安全切分：" + ", ".join(missing))
        group_transcript = Transcript(
            source_video=transcript.source_video,
            duration_ms=transcript.duration_ms,
            selected_segment_ids=segment_ids,
            segments=transcript.segments,
        )
        group_plan = optimizer.optimize(group_transcript).to_dict()
        group_ranges = group_plan.get("ranges") or []
        for item in group_ranges:
            item = dict(item)
            item["group_index"] = index
            item["group_purpose"] = str(group.get("purpose") or "")
            ranges.append(item)
        normalized_groups.append(
            {
                "purpose": str(group.get("purpose") or ""),
                "segment_ids": segment_ids,
                "note": str(group.get("note") or ""),
            }
        )
    if not ranges:
        raise ValueError("剪辑计划为空：所有分组都没有有效句子")
    return {
        "strategy": strategy,
        "ordered": True,
        "groups": normalized_groups,
        "ranges": ranges,
        "source_video": transcript.source_video,
    }
