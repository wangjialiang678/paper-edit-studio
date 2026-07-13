from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .models import Transcript, TranscriptSegment


@dataclass(frozen=True)
class SubtitleCue:
    start_ms: int
    end_ms: int
    text: str
    source_segment_id: str


def edited_subtitle_cues(transcript: Transcript, clip_plan: dict[str, Any] | str | Path) -> list[SubtitleCue]:
    plan = _load_plan(clip_plan)
    segments = {segment.id: segment for segment in transcript.segments}
    cues: list[SubtitleCue] = []
    output_cursor_ms = 0
    for clip_range in plan.get("ranges", []):
        range_start = int(clip_range["start_ms"])
        range_end = int(clip_range["end_ms"])
        range_duration = max(0, range_end - range_start)
        source_segments = _segments_for_range(transcript, clip_range, segments)
        for segment in source_segments:
            start = max(segment.start_ms, range_start)
            end = min(segment.end_ms, range_end)
            if end <= start or not segment.text.strip():
                continue
            cues.append(
                SubtitleCue(
                    start_ms=output_cursor_ms + start - range_start,
                    end_ms=output_cursor_ms + end - range_start,
                    text=segment.text.strip(),
                    source_segment_id=segment.id,
                )
            )
        output_cursor_ms += range_duration
    return cues


def write_srt(transcript: Transcript, clip_plan: dict[str, Any] | str | Path, output_path: str | Path) -> list[SubtitleCue]:
    cues = edited_subtitle_cues(transcript, clip_plan)
    output = Path(output_path)
    output.parent.mkdir(parents=True, exist_ok=True)
    lines: list[str] = []
    for index, cue in enumerate(cues, start=1):
        lines.extend(
            [
                str(index),
                f"{_format_srt_time(cue.start_ms)} --> {_format_srt_time(cue.end_ms)}",
                cue.text,
                "",
            ]
        )
    output.write_text("\n".join(lines), encoding="utf-8")
    return cues


def _segments_for_range(
    transcript: Transcript,
    clip_range: dict[str, Any],
    segments_by_id: dict[str, TranscriptSegment],
) -> list[TranscriptSegment]:
    explicit_ids = [str(item) for item in clip_range.get("source_segment_ids", [])]
    if explicit_ids:
        missing = [segment_id for segment_id in explicit_ids if segment_id not in segments_by_id]
        if missing:
            raise ValueError(f"clip range source_segment_ids contain unknown ids: {', '.join(missing)}")
        return [segments_by_id[segment_id] for segment_id in explicit_ids]
    range_start = int(clip_range["start_ms"])
    range_end = int(clip_range["end_ms"])
    return [
        segment
        for segment in transcript.segments
        if segment.end_ms > range_start and segment.start_ms < range_end
    ]


def _load_plan(clip_plan: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(clip_plan, dict):
        return clip_plan
    return json.loads(Path(clip_plan).read_text(encoding="utf-8"))


def _format_srt_time(value_ms: int) -> str:
    value_ms = max(0, int(round(value_ms)))
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"
