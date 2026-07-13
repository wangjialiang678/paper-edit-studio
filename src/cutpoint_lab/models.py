from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


def is_fallback_reason(reason: str) -> bool:
    return "fallback" in reason or reason in {"segment_padding", "mixed_fallback"}


@dataclass(frozen=True)
class TranscriptToken:
    text: str
    start_ms: int
    end_ms: int
    confidence: float | None = None

    @property
    def is_valid(self) -> bool:
        return self.start_ms >= 0 and self.end_ms > self.start_ms and bool(self.text.strip())


@dataclass(frozen=True)
class TranscriptSegment:
    id: str
    start_ms: int
    end_ms: int
    text: str = ""
    tokens: list[TranscriptToken] = field(default_factory=list)

    @property
    def valid_tokens(self) -> list[TranscriptToken]:
        return sorted((token for token in self.tokens if token.is_valid), key=lambda token: token.start_ms)


@dataclass(frozen=True)
class Transcript:
    segments: list[TranscriptSegment]
    selected_segment_ids: list[str]
    source_video: str | None = None
    duration_ms: int | None = None

    def selected_segments(self) -> list[TranscriptSegment]:
        segment_ids = {segment.id for segment in self.segments}
        missing = [segment_id for segment_id in self.selected_segment_ids if segment_id not in segment_ids]
        if missing:
            raise ValueError(f"selected_segment_ids contain unknown ids: {', '.join(missing)}")
        selected = set(self.selected_segment_ids)
        return sorted((seg for seg in self.segments if seg.id in selected), key=lambda seg: seg.start_ms)


@dataclass(frozen=True)
class CutRange:
    start_ms: int
    end_ms: int
    original_start_ms: int
    original_end_ms: int
    source_segment_ids: list[str]
    adjustment_reason: str
    confidence: float

    @property
    def duration_ms(self) -> int:
        return max(0, self.end_ms - self.start_ms)

    def to_dict(self) -> dict[str, Any]:
        return {
            "start_ms": self.start_ms,
            "end_ms": self.end_ms,
            "original_start_ms": self.original_start_ms,
            "original_end_ms": self.original_end_ms,
            "source_segment_ids": self.source_segment_ids,
            "adjustment_reason": self.adjustment_reason,
            "confidence": round(self.confidence, 3),
        }


@dataclass(frozen=True)
class ClipPlan:
    strategy: str
    ranges: list[CutRange]

    @property
    def metrics(self) -> dict[str, int]:
        reason_counts: dict[str, int] = {}
        for cut in self.ranges:
            reason_counts[cut.adjustment_reason] = reason_counts.get(cut.adjustment_reason, 0) + 1
        return {
            "range_count": len(self.ranges),
            "total_duration_ms": sum(cut.duration_ms for cut in self.ranges),
            "fallback_count": sum(1 for cut in self.ranges if is_fallback_reason(cut.adjustment_reason)),
            "boundary_risk_count": sum(1 for cut in self.ranges if cut.end_ms <= cut.start_ms),
            **{f"reason_{key}": value for key, value in reason_counts.items()},
        }

    def to_dict(self) -> dict[str, Any]:
        return {
            "strategy": self.strategy,
            "ranges": [cut.to_dict() for cut in self.ranges],
            "metrics": self.metrics,
        }


@dataclass(frozen=True)
class VadInterval:
    start_ms: int
    end_ms: int
    confidence: float | None = None

    @property
    def is_valid(self) -> bool:
        return self.start_ms >= 0 and self.end_ms > self.start_ms


@dataclass(frozen=True)
class VadData:
    duration_ms: int | None
    speech_intervals: list[VadInterval | dict[str, Any]]

    def normalized_speech(self, merge_gap_ms: int = 80) -> list[VadInterval]:
        intervals = [
            item
            if isinstance(item, VadInterval)
            else VadInterval(
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            )
            for item in self.speech_intervals
        ]
        valid = sorted(intervals, key=lambda item: item.start_ms)
        merged: list[VadInterval] = []
        for item in valid:
            start = max(0, item.start_ms)
            if self.duration_ms is not None:
                start = min(start, self.duration_ms)
            end = item.end_ms if self.duration_ms is None else min(self.duration_ms, item.end_ms)
            if end <= start:
                continue
            current = VadInterval(start, end, item.confidence)
            if not merged or current.start_ms > merged[-1].end_ms + merge_gap_ms:
                merged.append(current)
                continue
            prev = merged[-1]
            merged[-1] = VadInterval(
                start_ms=prev.start_ms,
                end_ms=max(prev.end_ms, current.end_ms),
                confidence=max(prev.confidence or 0.0, current.confidence or 0.0) or None,
            )
        return merged


@dataclass(frozen=True)
class SpeakerSegment:
    speaker: str
    start_ms: int
    end_ms: int
    confidence: float | None = None

    @property
    def is_valid(self) -> bool:
        return bool(self.speaker.strip()) and self.start_ms >= 0 and self.end_ms > self.start_ms


@dataclass(frozen=True)
class OverlapSegment:
    start_ms: int
    end_ms: int
    speakers: list[str] = field(default_factory=list)

    @property
    def is_valid(self) -> bool:
        return self.start_ms >= 0 and self.end_ms > self.start_ms


@dataclass(frozen=True)
class SpeakerData:
    duration_ms: int | None
    speaker_segments: list[SpeakerSegment | dict[str, Any]]
    overlap_segments: list[OverlapSegment | dict[str, Any]] = field(default_factory=list)

    def normalized_segments(self) -> list[SpeakerSegment]:
        segments = [
            item
            if isinstance(item, SpeakerSegment)
            else SpeakerSegment(
                speaker=str(item["speaker"]),
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                confidence=float(item["confidence"]) if item.get("confidence") is not None else None,
            )
            for item in self.speaker_segments
        ]
        return sorted((segment for segment in segments if segment.is_valid), key=lambda item: (item.start_ms, item.end_ms, item.speaker))

    def normalized_overlaps(self) -> list[OverlapSegment]:
        overlaps = [
            item
            if isinstance(item, OverlapSegment)
            else OverlapSegment(
                start_ms=int(item["start_ms"]),
                end_ms=int(item["end_ms"]),
                speakers=[str(value) for value in item.get("speakers", [])],
            )
            for item in self.overlap_segments
        ]
        return sorted((overlap for overlap in overlaps if overlap.is_valid), key=lambda item: (item.start_ms, item.end_ms))

    def is_overlapped(self, time_ms: int) -> bool:
        return any(overlap.start_ms <= time_ms < overlap.end_ms for overlap in self.normalized_overlaps())

    def dominant_speaker(self, start_ms: int, end_ms: int) -> str | None:
        totals: dict[str, int] = {}
        for segment in self.normalized_segments():
            overlap = max(0, min(end_ms, segment.end_ms) - max(start_ms, segment.start_ms))
            if overlap <= 0:
                continue
            totals[segment.speaker] = totals.get(segment.speaker, 0) + overlap
        if not totals:
            return None
        return max(totals.items(), key=lambda item: item[1])[0]

    def speaker_bounds(self, speaker: str, start_ms: int, end_ms: int) -> tuple[int | None, int | None]:
        relevant = []
        for segment in self.normalized_segments():
            if segment.speaker != speaker:
                continue
            overlap = max(0, min(end_ms, segment.end_ms) - max(start_ms, segment.start_ms))
            if overlap > 0 or segment.start_ms <= start_ms <= segment.end_ms or segment.start_ms <= end_ms <= segment.end_ms:
                relevant.append(segment)
        if not relevant:
            return None, None
        return min(segment.start_ms for segment in relevant), max(segment.end_ms for segment in relevant)
