from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from ..features import AudioFrame
from ..models import SpeakerData, Transcript, TranscriptSegment, TranscriptToken, VadData
from ..strategies import (
    AnchoredRmsValleyStrategy,
    HybridValleyStrategy,
    RmsSnapStrategy,
    SpeakerAwareValleyStrategy,
    TokenPaddingStrategy,
    VadSnapStrategy,
    VoiceEnhancedRmsStrategy,
    WaveformVisualSnapStrategy,
)


def build_editor_state(
    transcript: Transcript,
    *,
    transcript_path: str | None = None,
    source_video: str | None = None,
    candidates_payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    selected_ids = _default_selected_segment_ids(transcript, candidates_payload)
    selected_set = set(selected_ids)
    rows = [_row_payload(segment, checked=segment.id in selected_set, index=index) for index, segment in enumerate(transcript.segments, start=1)]
    selected_without_words = [
        row["id"] for row in rows if row["checked"] and row["token_count"] == 0
    ]
    return {
        "source_video": source_video or transcript.source_video,
        "transcript_path": transcript_path,
        "duration_ms": transcript.duration_ms,
        "rows": rows,
        "selected_segment_ids": [row["id"] for row in rows if row["checked"]],
        "selected_duration_ms": sum(row["end_ms"] - row["start_ms"] for row in rows if row["checked"]),
        "word_timestamps": {
            "required_for_export": True,
            "segment_count": len(rows),
            "segments_with_words": sum(1 for row in rows if row["token_count"] > 0),
            "selected_without_words": selected_without_words,
        },
    }


def apply_editor_rows(
    transcript: Transcript,
    rows_payload: Iterable[dict[str, Any]],
) -> Transcript:
    updates = {str(row.get("id")): row for row in rows_payload if row.get("id") is not None}
    selected_ids = []
    segments = []
    for segment in transcript.segments:
        update = updates.get(segment.id, {})
        checked = bool(update.get("checked", False))
        text = str(update.get("text", segment.text))
        start_ms, end_ms, tokens = _apply_trim(segment, update.get("trim"))
        if checked:
            selected_ids.append(segment.id)
        segments.append(
            TranscriptSegment(
                id=segment.id,
                start_ms=start_ms,
                end_ms=end_ms,
                text=text,
                tokens=tokens,
            )
        )
    return Transcript(
        source_video=transcript.source_video,
        duration_ms=transcript.duration_ms,
        selected_segment_ids=selected_ids,
        segments=segments,
    )


def _apply_trim(
    segment: TranscriptSegment,
    trim: dict[str, Any] | None,
) -> tuple[int, int, list[TranscriptToken]]:
    """按 valid_tokens 索引裁掉句首/句尾的词；非法或无效 trim 一律忽略回退整句。"""
    tokens = list(segment.tokens)
    valid = segment.valid_tokens
    if not trim or not valid:
        return segment.start_ms, segment.end_ms, tokens
    try:
        start_index = int(trim.get("start_token", 0))
        end_index = int(trim.get("end_token", len(valid) - 1))
    except (TypeError, ValueError):
        return segment.start_ms, segment.end_ms, tokens
    start_index = max(0, start_index)
    end_index = min(len(valid) - 1, end_index)
    if start_index > end_index:
        return segment.start_ms, segment.end_ms, tokens
    if start_index == 0 and end_index == len(valid) - 1:
        return segment.start_ms, segment.end_ms, tokens
    kept = valid[start_index : end_index + 1]
    return kept[0].start_ms, kept[-1].end_ms, list(kept)


def build_plan_from_editor_rows(
    transcript: Transcript,
    rows_payload: Iterable[dict[str, Any]],
    *,
    strategy: str = "token_padding",
    frames: list[AudioFrame] | None = None,
    voice_frames: list[AudioFrame] | None = None,
    vad: VadData | None = None,
    speaker_data: SpeakerData | None = None,
    require_word_timestamps: bool = True,
) -> tuple[Transcript, dict[str, Any]]:
    edited = apply_editor_rows(transcript, rows_payload)
    if not edited.selected_segment_ids:
        raise ValueError("At least one subtitle row must be selected")
    if require_word_timestamps:
        missing_words = [segment.id for segment in edited.selected_segments() if not segment.valid_tokens]
        if missing_words:
            raise ValueError(
                "Selected rows require word-level timestamps: " + ", ".join(missing_words)
            )
    clip_plan = _strategy(
        strategy=strategy,
        frames=frames or [],
        voice_frames=voice_frames or [],
        vad=vad,
        speaker_data=speaker_data,
    ).optimize(edited).to_dict()
    clip_plan.update(
        {
            "source_video": edited.source_video,
            "selected_segment_ids": list(edited.selected_segment_ids),
            "source": "paper_edit_web",
        }
    )
    return edited, clip_plan


def transcript_to_payload(transcript: Transcript) -> dict[str, Any]:
    return {
        "source_video": transcript.source_video,
        "duration_ms": transcript.duration_ms,
        "selected_segment_ids": list(transcript.selected_segment_ids),
        "segments": [
            {
                "id": segment.id,
                "start_ms": segment.start_ms,
                "end_ms": segment.end_ms,
                "text": segment.text,
                "tokens": [_token_payload(token) for token in segment.tokens],
            }
            for segment in transcript.segments
        ],
    }


def _row_payload(segment: TranscriptSegment, *, checked: bool, index: int) -> dict[str, Any]:
    valid_tokens = segment.valid_tokens
    token_count = len(valid_tokens)
    return {
        "id": segment.id,
        "index": index,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "start": _format_clock(segment.start_ms),
        "end": _format_clock(segment.end_ms),
        "text": segment.text,
        "original_text": segment.text,
        "checked": checked,
        "token_count": token_count,
        "has_word_timestamps": token_count > 0,
        "tokens": [
            {"text": token.text, "start_ms": token.start_ms, "end_ms": token.end_ms}
            for token in valid_tokens
        ],
    }


def _default_selected_segment_ids(
    transcript: Transcript,
    candidates_payload: dict[str, Any] | None,
) -> list[str]:
    if not candidates_payload:
        return list(transcript.selected_segment_ids)
    candidates = {str(item.get("id")): item for item in candidates_payload.get("candidates", [])}
    candidate_ids = [str(item) for item in candidates_payload.get("recommended_candidate_ids", [])]
    if not candidate_ids and candidates_payload.get("candidates"):
        candidate_ids = [str(candidates_payload["candidates"][0].get("id"))]
    selected = []
    seen = set()
    for candidate_id in candidate_ids:
        candidate = candidates.get(candidate_id)
        if not candidate:
            continue
        for segment_id in candidate.get("segment_ids", []):
            segment_id = str(segment_id)
            if segment_id in seen:
                continue
            seen.add(segment_id)
            selected.append(segment_id)
    known = {segment.id for segment in transcript.segments}
    return [segment_id for segment_id in selected if segment_id in known] or list(transcript.selected_segment_ids)


def _strategy(
    *,
    strategy: str,
    frames: list[AudioFrame],
    voice_frames: list[AudioFrame],
    vad: VadData | None,
    speaker_data: SpeakerData | None,
):
    if strategy == "token_padding":
        return TokenPaddingStrategy()
    if strategy == "rms_snap":
        return RmsSnapStrategy(frames=frames)
    if strategy == "anchored_rms":
        return AnchoredRmsValleyStrategy(frames=frames)
    if strategy == "visual_waveform":
        return WaveformVisualSnapStrategy(frames=frames)
    if strategy == "hybrid_valley":
        return HybridValleyStrategy(frames=frames)
    if strategy == "voice_enhanced_rms":
        if not voice_frames:
            raise ValueError("voice_enhanced_rms requires enhanced voice audio or voice RMS frames")
        return VoiceEnhancedRmsStrategy(frames=voice_frames)
    if strategy == "speaker_aware_valley":
        return SpeakerAwareValleyStrategy(frames=frames, speaker_data=speaker_data)
    if strategy == "vad_snap":
        return VadSnapStrategy(vad=vad)
    raise ValueError(f"Unknown strategy: {strategy}")


def _token_payload(token: TranscriptToken) -> dict[str, Any]:
    payload = {
        "text": token.text,
        "start_ms": token.start_ms,
        "end_ms": token.end_ms,
    }
    if token.confidence is not None:
        payload["confidence"] = token.confidence
    return payload


def _format_clock(value_ms: int) -> str:
    value_ms = max(0, int(value_ms))
    minutes, remainder = divmod(value_ms, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"
