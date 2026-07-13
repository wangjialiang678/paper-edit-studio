from __future__ import annotations

from dataclasses import dataclass, replace

from .features import AudioFrame
from .models import ClipPlan, CutRange, SpeakerData, Transcript, TranscriptSegment, TranscriptToken, VadData


@dataclass(frozen=True)
class StrategyConfig:
    pre_roll_ms: int = 160
    post_roll_ms: int = 240
    merge_gap_ms: int = 500
    unselected_neighbor_guard_ms: int = 20
    min_gap_ms: int = 120
    weak_gap_ms: int = 80
    merge_speech_gap_ms: int = 80
    search_before_start_ms: int = 600
    search_after_start_ms: int = 200
    search_before_end_ms: int = 200
    search_after_end_ms: int = 800
    low_energy_margin_db: float = 6.0
    noise_floor_percentile: float = 10.0
    dynamic_range_min_db: float = 10.0
    start_guard_ms: int = 60
    end_guard_ms: int = 80
    visual_column_ms: int = 40
    hybrid_agreement_window_ms: int = 120


@dataclass(frozen=True)
class BoundaryRange:
    segments: list[TranscriptSegment]
    media_duration_ms: int | None = None
    start_floor_ms: int | None = None
    end_ceiling_ms: int | None = None

    @property
    def original_start_ms(self) -> int:
        return min(segment.start_ms for segment in self.segments)

    @property
    def original_end_ms(self) -> int:
        return max(segment.end_ms for segment in self.segments)

    @property
    def source_segment_ids(self) -> list[str]:
        return [segment.id for segment in self.segments]

    @property
    def duration_ms(self) -> int | None:
        return self.media_duration_ms

    @property
    def valid_tokens(self) -> list[TranscriptToken]:
        tokens = []
        for segment in self.segments:
            tokens.extend(segment.valid_tokens)
        return sorted(tokens, key=lambda token: (token.start_ms, token.end_ms))

    @property
    def first_token_start_ms(self) -> int | None:
        tokens = self.valid_tokens
        return tokens[0].start_ms if tokens else None

    @property
    def last_token_end_ms(self) -> int | None:
        tokens = self.valid_tokens
        return max((token.end_ms for token in tokens), default=None)


@dataclass(frozen=True)
class ValleyCandidate:
    time_ms: int
    score: float
    confidence: float


class TokenPaddingStrategy:
    name = "token_padding"

    def __init__(self, config: StrategyConfig | None = None):
        self.config = config or StrategyConfig()

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = [self._cut_from_range(boundary) for boundary in _selected_ranges(transcript, self.config)]
        return ClipPlan(strategy=self.name, ranges=cuts)

    def _cut_from_range(self, boundary: BoundaryRange, reason: str | None = None, confidence: float = 0.6) -> CutRange:
        first_token_start = boundary.first_token_start_ms
        last_token_end = boundary.last_token_end_ms
        has_tokens = first_token_start is not None and last_token_end is not None
        base_start = first_token_start if first_token_start is not None else boundary.original_start_ms
        base_end = last_token_end if last_token_end is not None else boundary.original_end_ms
        start = _clamp_ms(base_start - self.config.pre_roll_ms, boundary.duration_ms)
        end = _clamp_ms(base_end + self.config.post_roll_ms, boundary.duration_ms)
        start = _respect_start_floor(start, base_start, boundary.start_floor_ms)
        end = _respect_end_ceiling(end, base_end, boundary.end_ceiling_ms)
        return CutRange(
            start_ms=start,
            end_ms=max(end, start),
            original_start_ms=boundary.original_start_ms,
            original_end_ms=boundary.original_end_ms,
            source_segment_ids=boundary.source_segment_ids,
            adjustment_reason=reason or ("token_padding" if has_tokens else "segment_padding"),
            confidence=confidence if has_tokens else min(confidence, 0.45),
        )


class RmsSnapStrategy(TokenPaddingStrategy):
    name = "rms_snap"

    def __init__(self, frames: list[AudioFrame] | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.frames = frames or []

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = []
        gaps = _low_energy_gaps(self.frames, self.config)
        for boundary in _selected_ranges(transcript, self.config):
            fallback = self._cut_from_range(boundary, reason="rms_fallback_token_padding", confidence=0.45)
            if not gaps:
                cuts.append(fallback)
                continue
            start = _snap_start(boundary, gaps, self.config, require_gap_guard=True)
            end = _snap_end(boundary, gaps, self.config, require_gap_guard=True)
            if start is None or end is None:
                cuts.append(fallback)
                continue
            cuts.append(_with_adjusted_bounds(fallback, start, end, "snapped_to_rms_gap", 0.78, boundary.duration_ms))
        return ClipPlan(strategy=self.name, ranges=cuts)


class VadSnapStrategy(TokenPaddingStrategy):
    name = "vad_snap"

    def __init__(self, vad: VadData | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.vad = vad

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = []
        gaps = _vad_gaps(self.vad, self.config) if self.vad else []
        for boundary in _selected_ranges(transcript, self.config):
            fallback = self._cut_from_range(boundary, reason="vad_fallback_token_padding", confidence=0.45)
            if not gaps:
                cuts.append(fallback)
                continue
            start = _snap_start(boundary, gaps, self.config)
            end = _snap_end(boundary, gaps, self.config)
            if start is None or end is None:
                cuts.append(fallback)
                continue
            cuts.append(_with_adjusted_bounds(fallback, start, end, "snapped_to_vad_gap", 0.82, boundary.duration_ms))
        return ClipPlan(strategy=self.name, ranges=_merge_cut_overlaps(cuts, self.config))


class AnchoredRmsValleyStrategy(TokenPaddingStrategy):
    name = "anchored_rms"
    adjustment_reason = "anchored_rms_valley"
    fallback_reason = "anchored_rms_fallback_token_padding"
    confidence = 0.74

    def __init__(self, frames: list[AudioFrame] | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.frames = _ordered_audio_frames(frames or [])

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = [_valley_cut(self, boundary, _rms_valley_candidate) for boundary in _selected_ranges(transcript, self.config)]
        return ClipPlan(strategy=self.name, ranges=cuts)


class WaveformVisualSnapStrategy(TokenPaddingStrategy):
    name = "visual_waveform"
    adjustment_reason = "visual_waveform_valley"
    fallback_reason = "visual_waveform_fallback_token_padding"
    confidence = 0.69

    def __init__(self, frames: list[AudioFrame] | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.frames = _ordered_audio_frames(frames or [])

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = [
            _valley_cut(self, boundary, _visual_waveform_valley_candidate)
            for boundary in _selected_ranges(transcript, self.config)
        ]
        return ClipPlan(strategy=self.name, ranges=cuts)


class HybridValleyStrategy(TokenPaddingStrategy):
    name = "hybrid_valley"
    adjustment_reason = "hybrid_valley"
    fallback_reason = "hybrid_valley_fallback_token_padding"
    confidence = 0.78

    def __init__(self, frames: list[AudioFrame] | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.frames = _ordered_audio_frames(frames or [])

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = [_valley_cut(self, boundary, _hybrid_valley_candidate) for boundary in _selected_ranges(transcript, self.config)]
        return ClipPlan(strategy=self.name, ranges=cuts)


class VoiceEnhancedRmsStrategy(TokenPaddingStrategy):
    name = "voice_enhanced_rms"
    adjustment_reason = "voice_enhanced_rms_valley"
    fallback_reason = "voice_enhanced_rms_fallback_token_padding"
    confidence = 0.76

    def __init__(self, frames: list[AudioFrame] | None = None, config: StrategyConfig | None = None):
        super().__init__(config)
        self.frames = _ordered_audio_frames(frames or [])

    def optimize(self, transcript: Transcript) -> ClipPlan:
        cuts = [_valley_cut(self, boundary, _rms_valley_candidate) for boundary in _selected_ranges(transcript, self.config)]
        return ClipPlan(strategy=self.name, ranges=cuts)


class SpeakerAwareValleyStrategy(TokenPaddingStrategy):
    name = "speaker_aware_valley"
    adjustment_reason = "speaker_aware_valley"
    fallback_reason = "speaker_aware_fallback_token_padding"
    confidence = 0.77

    def __init__(
        self,
        frames: list[AudioFrame] | None = None,
        speaker_data: SpeakerData | None = None,
        config: StrategyConfig | None = None,
    ):
        base_config = config or StrategyConfig()
        super().__init__(replace(base_config, end_guard_ms=min(base_config.end_guard_ms, 40)))
        self.frames = _ordered_audio_frames(frames or [])
        self.speaker_data = speaker_data

    def optimize(self, transcript: Transcript) -> ClipPlan:
        if self.speaker_data is None:
            raise ValueError("speaker_aware_valley requires speaker timeline data")
        cuts = []
        for boundary in _selected_ranges(transcript, self.config):
            adjusted = _speaker_adjusted_boundary(boundary, self.speaker_data, self.config)
            cuts.append(_valley_cut(self, adjusted, _speaker_safe_hybrid_candidate))
        return ClipPlan(strategy=self.name, ranges=cuts)


def _valley_cut(strategy, boundary: BoundaryRange, candidate_fn) -> CutRange:
    fallback = strategy._cut_from_range(boundary, reason=strategy.fallback_reason, confidence=0.45)
    if not strategy.frames:
        return fallback

    start_anchor = boundary.first_token_start_ms or boundary.original_start_ms
    end_anchor = boundary.last_token_end_ms or boundary.original_end_ms
    start_window = _candidate_window(boundary, start_anchor, strategy.config, is_start=True)
    end_window = _candidate_window(boundary, end_anchor, strategy.config, is_start=False)
    if start_window is None or end_window is None:
        return fallback

    start_candidate = candidate_fn(
        strategy.frames,
        start_anchor,
        start_window[0],
        start_window[1],
        strategy.config,
        is_start=True,
    )
    end_candidate = candidate_fn(
        strategy.frames,
        end_anchor,
        end_window[0],
        end_window[1],
        strategy.config,
        is_start=False,
    )
    if start_candidate is None or end_candidate is None:
        return fallback

    confidence = min(strategy.confidence, start_candidate.confidence, end_candidate.confidence)
    return _with_adjusted_bounds(
        fallback,
        start_candidate.time_ms,
        end_candidate.time_ms,
        strategy.adjustment_reason,
        confidence,
        boundary.duration_ms,
    )


def _candidate_window(
    boundary: BoundaryRange, anchor_ms: int, config: StrategyConfig, *, is_start: bool
) -> tuple[int, int] | None:
    if is_start:
        start = max(0, anchor_ms - config.search_before_start_ms)
        end = anchor_ms + config.search_after_start_ms
    else:
        start = max(0, anchor_ms - config.search_before_end_ms)
        end = anchor_ms + config.search_after_end_ms
    if boundary.start_floor_ms is not None:
        start = max(start, boundary.start_floor_ms)
    if boundary.end_ceiling_ms is not None:
        end = min(end, boundary.end_ceiling_ms)
    if boundary.duration_ms is not None:
        start = min(start, boundary.duration_ms)
        end = min(end, boundary.duration_ms)
    if end <= start:
        return None
    return start, end


def _speaker_adjusted_boundary(
    boundary: BoundaryRange, speaker_data: SpeakerData | None, config: StrategyConfig
) -> BoundaryRange:
    if speaker_data is None:
        return boundary
    speaker = speaker_data.dominant_speaker(boundary.original_start_ms, boundary.original_end_ms)
    if speaker is None:
        return boundary
    speaker_start, speaker_end = speaker_data.speaker_bounds(speaker, boundary.original_start_ms, boundary.original_end_ms)
    start_floor = boundary.start_floor_ms
    end_ceiling = boundary.end_ceiling_ms
    if speaker_start is not None:
        floor = max(0, speaker_start - config.unselected_neighbor_guard_ms)
        start_floor = floor if start_floor is None else max(start_floor, floor)
    if speaker_end is not None:
        ceiling = speaker_end + config.unselected_neighbor_guard_ms
        end_ceiling = ceiling if end_ceiling is None else min(end_ceiling, ceiling)
    for overlap in speaker_data.normalized_overlaps():
        if overlap.start_ms <= boundary.original_end_ms and overlap.end_ms >= boundary.original_start_ms:
            if overlap.start_ms <= boundary.original_start_ms < overlap.end_ms:
                floor = overlap.end_ms
                start_floor = floor if start_floor is None else max(start_floor, floor)
            if overlap.start_ms > boundary.original_start_ms:
                end_ceiling = overlap.start_ms if end_ceiling is None else min(end_ceiling, overlap.start_ms)
    return BoundaryRange(
        boundary.segments,
        media_duration_ms=boundary.media_duration_ms,
        start_floor_ms=start_floor,
        end_ceiling_ms=end_ceiling,
    )


def _speaker_safe_hybrid_candidate(
    frames: list[AudioFrame],
    anchor_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    config: StrategyConfig,
    *,
    is_start: bool,
) -> ValleyCandidate | None:
    return _hybrid_valley_candidate(
        frames,
        anchor_ms,
        window_start_ms,
        window_end_ms,
        config,
        is_start=is_start,
    )


def _rms_valley_candidate(
    frames: list[AudioFrame],
    anchor_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    config: StrategyConfig,
    *,
    is_start: bool,
) -> ValleyCandidate | None:
    window_frames = _frames_in_window(frames, window_start_ms, window_end_ms)
    profile = _energy_profile(window_frames, config)
    if profile is None:
        return None
    floor_db, speech_db, dynamic_range = profile
    span = max(1, window_end_ms - window_start_ms)
    candidates: list[ValleyCandidate] = []
    for frame in window_frames:
        center = _frame_center_ms(frame)
        if not _passes_anchor_guard(center, anchor_ms, config, is_start=is_start):
            continue
        level = _normalized_energy(frame.rms_db, floor_db, speech_db)
        if level > 0.45 and frame.rms_db > floor_db + config.low_energy_margin_db * 2:
            continue
        distance = abs(center - anchor_ms) / span
        score = level * 0.7 + distance * 0.3
        confidence = max(0.58, min(0.84, 0.84 - distance * 0.18 - level * 0.12 + dynamic_range / 250))
        candidates.append(ValleyCandidate(time_ms=center, score=score, confidence=confidence))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (candidate.score, abs(candidate.time_ms - anchor_ms)))


def _visual_waveform_valley_candidate(
    frames: list[AudioFrame],
    anchor_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    config: StrategyConfig,
    *,
    is_start: bool,
) -> ValleyCandidate | None:
    window_frames = _frames_in_window(frames, window_start_ms, window_end_ms)
    profile = _energy_profile(window_frames, config)
    if profile is None:
        return None
    floor_db, speech_db, _dynamic_range = profile
    columns = _waveform_columns(window_frames, window_start_ms, window_end_ms, floor_db, speech_db, config)
    if not columns:
        return None
    span = max(1, window_end_ms - window_start_ms)
    candidates: list[ValleyCandidate] = []
    for index, column in enumerate(columns):
        time_ms, level = column
        if not _passes_anchor_guard(time_ms, anchor_ms, config, is_start=is_start):
            continue
        left_level = columns[index - 1][1] if index > 0 else level
        right_level = columns[index + 1][1] if index < len(columns) - 1 else level
        contrast = max(0.0, ((left_level + right_level) / 2) - level)
        if level > 0.5 and contrast < 0.12:
            continue
        distance = abs(time_ms - anchor_ms) / span
        score = level * 0.58 + distance * 0.3 - min(contrast, 0.35) * 0.18
        confidence = max(0.55, min(0.78, 0.75 - distance * 0.14 - level * 0.1 + contrast * 0.08))
        candidates.append(ValleyCandidate(time_ms=time_ms, score=score, confidence=confidence))
    if not candidates:
        return None
    return min(candidates, key=lambda candidate: (candidate.score, abs(candidate.time_ms - anchor_ms)))


def _hybrid_valley_candidate(
    frames: list[AudioFrame],
    anchor_ms: int,
    window_start_ms: int,
    window_end_ms: int,
    config: StrategyConfig,
    *,
    is_start: bool,
) -> ValleyCandidate | None:
    rms = _rms_valley_candidate(
        frames,
        anchor_ms,
        window_start_ms,
        window_end_ms,
        config,
        is_start=is_start,
    )
    visual = _visual_waveform_valley_candidate(
        frames,
        anchor_ms,
        window_start_ms,
        window_end_ms,
        config,
        is_start=is_start,
    )
    if rms is None and visual is None:
        return None
    if rms is None:
        return ValleyCandidate(visual.time_ms, visual.score, max(0.55, visual.confidence * 0.92))
    if visual is None:
        return ValleyCandidate(rms.time_ms, rms.score, max(0.55, rms.confidence * 0.92))

    distance = abs(rms.time_ms - visual.time_ms)
    if distance <= config.hybrid_agreement_window_ms:
        total_weight = max(0.01, (1 - rms.score) + (1 - visual.score))
        time_ms = round((rms.time_ms * (1 - rms.score) + visual.time_ms * (1 - visual.score)) / total_weight)
        confidence = min(0.86, (rms.confidence + visual.confidence) / 2 + 0.06)
        score = (rms.score + visual.score) / 2
        return ValleyCandidate(time_ms, score, confidence)

    better = rms if rms.score <= visual.score else visual
    return ValleyCandidate(better.time_ms, better.score + 0.08, max(0.58, better.confidence * 0.88))


def _passes_anchor_guard(time_ms: int, anchor_ms: int, config: StrategyConfig, *, is_start: bool) -> bool:
    if is_start:
        return time_ms <= anchor_ms - config.start_guard_ms
    return time_ms >= anchor_ms + config.end_guard_ms


def _energy_profile(frames: list[AudioFrame], config: StrategyConfig) -> tuple[float, float, float] | None:
    if not frames:
        return None
    db_values = sorted(frame.rms_db for frame in frames if frame.end_ms > frame.start_ms)
    if not db_values:
        return None
    floor_db = db_values[0]
    speech_index = max(0, min(len(db_values) - 1, int(len(db_values) * 0.8)))
    speech_db = db_values[speech_index]
    dynamic_range = speech_db - floor_db
    if dynamic_range < config.dynamic_range_min_db:
        return None
    return floor_db, speech_db, dynamic_range


def _normalized_energy(rms_db: float, floor_db: float, speech_db: float) -> float:
    dynamic_range = max(1.0, speech_db - floor_db)
    return min(1.0, max(0.0, (rms_db - floor_db) / dynamic_range))


def _waveform_columns(
    frames: list[AudioFrame],
    window_start_ms: int,
    window_end_ms: int,
    floor_db: float,
    speech_db: float,
    config: StrategyConfig,
) -> list[tuple[int, float]]:
    column_ms = max(10, config.visual_column_ms)
    columns: list[tuple[int, float]] = []
    cursor = window_start_ms
    while cursor < window_end_ms:
        column_end = min(window_end_ms, cursor + column_ms)
        overlapping = _frames_in_window(frames, cursor, column_end)
        if overlapping:
            level = sum(_normalized_energy(frame.rms_db, floor_db, speech_db) for frame in overlapping) / len(overlapping)
            columns.append(((cursor + column_end) // 2, level))
        cursor = column_end
    return columns


def _ordered_audio_frames(frames: list[AudioFrame]) -> list[AudioFrame]:
    return sorted((frame for frame in frames if frame.end_ms > frame.start_ms), key=lambda frame: (frame.start_ms, frame.end_ms))


def _frames_in_window(frames: list[AudioFrame], window_start_ms: int, window_end_ms: int) -> list[AudioFrame]:
    return [frame for frame in frames if frame.end_ms > window_start_ms and frame.start_ms < window_end_ms]


def _frame_center_ms(frame: AudioFrame) -> int:
    return (frame.start_ms + frame.end_ms) // 2


def _selected_ranges(transcript: Transcript, config: StrategyConfig) -> list[BoundaryRange]:
    selected = transcript.selected_segments()
    if not selected:
        return []
    ordered_segments = sorted(transcript.segments, key=lambda segment: (segment.start_ms, segment.end_ms))
    selected_set = {segment.id for segment in selected}
    selected_ranges: list[list[TranscriptSegment]] = []
    current = [selected[0]]
    for segment in selected[1:]:
        prev = current[-1]
        if segment.start_ms - prev.end_ms <= config.merge_gap_ms:
            current.append(segment)
            continue
        selected_ranges.append(current)
        current = [segment]
    selected_ranges.append(current)
    return [
        BoundaryRange(
            group,
            transcript.duration_ms,
            start_floor_ms=_start_floor_for_group(group, ordered_segments, selected_set, config),
            end_ceiling_ms=_end_ceiling_for_group(group, ordered_segments, selected_set, config),
        )
        for group in selected_ranges
    ]


def _start_floor_for_group(
    group: list[TranscriptSegment],
    ordered_segments: list[TranscriptSegment],
    selected_set: set[str],
    config: StrategyConfig,
) -> int | None:
    first_id = group[0].id
    for index, segment in enumerate(ordered_segments):
        if segment.id != first_id:
            continue
        if index == 0:
            return None
        previous = ordered_segments[index - 1]
        if previous.id in selected_set:
            return None
        return previous.end_ms + config.unselected_neighbor_guard_ms
    return None


def _end_ceiling_for_group(
    group: list[TranscriptSegment],
    ordered_segments: list[TranscriptSegment],
    selected_set: set[str],
    config: StrategyConfig,
) -> int | None:
    last_id = group[-1].id
    for index, segment in enumerate(ordered_segments):
        if segment.id != last_id:
            continue
        if index >= len(ordered_segments) - 1:
            return None
        next_segment = ordered_segments[index + 1]
        if next_segment.id in selected_set:
            return None
        return next_segment.start_ms - config.unselected_neighbor_guard_ms
    return None


def _merge_cut_overlaps(cuts: list[CutRange], config: StrategyConfig) -> list[CutRange]:
    if not cuts:
        return []
    merged = [cuts[0]]
    for cut in cuts[1:]:
        prev = merged[-1]
        if cut.start_ms - prev.end_ms > config.merge_gap_ms:
            merged.append(cut)
            continue
        merged[-1] = CutRange(
            start_ms=min(prev.start_ms, cut.start_ms),
            end_ms=max(prev.end_ms, cut.end_ms),
            original_start_ms=min(prev.original_start_ms, cut.original_start_ms),
            original_end_ms=max(prev.original_end_ms, cut.original_end_ms),
            source_segment_ids=prev.source_segment_ids + cut.source_segment_ids,
            adjustment_reason=_merged_reason(prev.adjustment_reason, cut.adjustment_reason),
            confidence=min(prev.confidence, cut.confidence),
        )
    return merged


def _merged_reason(left: str, right: str) -> str:
    if left == right:
        return left
    if "fallback" in left or "fallback" in right or left == "segment_padding" or right == "segment_padding":
        return "mixed_fallback"
    if left.startswith("snapped_to_") or right.startswith("snapped_to_"):
        return "mixed_snap"
    return "mixed_adjustment"


def _with_adjusted_bounds(
    fallback: CutRange, start: int, end: int, reason: str, confidence: float, duration_ms: int | None = None
) -> CutRange:
    start = _clamp_ms(start, duration_ms)
    end = _clamp_ms(end, duration_ms)
    end = max(end, start)
    return CutRange(
        start_ms=start,
        end_ms=end,
        original_start_ms=fallback.original_start_ms,
        original_end_ms=fallback.original_end_ms,
        source_segment_ids=fallback.source_segment_ids,
        adjustment_reason=reason,
        confidence=confidence,
    )


def _respect_start_floor(start: int, base_start: int, floor_ms: int | None) -> int:
    if floor_ms is None:
        return start
    return max(start, min(base_start, floor_ms))


def _respect_end_ceiling(end: int, base_end: int, ceiling_ms: int | None) -> int:
    if ceiling_ms is None:
        return end
    return min(end, max(base_end, ceiling_ms))


def _clamp_ms(value: int, duration_ms: int | None = None) -> int:
    value = max(0, value)
    if duration_ms is not None:
        value = min(value, duration_ms)
    return value


def _low_energy_gaps(frames: list[AudioFrame], config: StrategyConfig) -> list[tuple[int, int]]:
    if not frames:
        return []
    ordered_frames = sorted(
        (frame for frame in frames if frame.end_ms > frame.start_ms),
        key=lambda frame: (frame.start_ms, frame.end_ms),
    )
    if not ordered_frames:
        return []
    sorted_db = sorted(frame.rms_db for frame in ordered_frames)
    index = max(0, min(len(sorted_db) - 1, int(len(sorted_db) * config.noise_floor_percentile / 100)))
    speech_index = max(0, min(len(sorted_db) - 1, int(len(sorted_db) * 0.8)))
    dynamic_range = sorted_db[speech_index] - sorted_db[index]
    if dynamic_range < config.dynamic_range_min_db:
        return []
    threshold = sorted_db[index] + config.low_energy_margin_db
    gaps: list[tuple[int, int]] = []
    start: int | None = None
    end: int | None = None
    for frame in ordered_frames:
        if frame.rms_db <= threshold:
            if start is not None and end is not None and frame.start_ms > end + 1:
                if end - start >= config.weak_gap_ms:
                    gaps.append((start, end))
                start = frame.start_ms
                end = frame.end_ms
                continue
            start = frame.start_ms if start is None else start
            end = frame.end_ms
            continue
        if start is not None and end is not None and end - start >= config.weak_gap_ms:
            gaps.append((start, end))
        start = None
        end = None
    if start is not None and end is not None and end - start >= config.weak_gap_ms:
        gaps.append((start, end))
    return gaps


def _vad_gaps(vad: VadData | None, config: StrategyConfig) -> list[tuple[int, int]]:
    if vad is None or vad.duration_ms is None:
        return []
    speech = vad.normalized_speech(merge_gap_ms=config.merge_speech_gap_ms)
    gaps: list[tuple[int, int]] = []
    cursor = 0
    for interval in speech:
        if interval.start_ms - cursor >= config.weak_gap_ms:
            gaps.append((cursor, interval.start_ms))
        cursor = max(cursor, interval.end_ms)
    if vad.duration_ms - cursor >= config.weak_gap_ms:
        gaps.append((cursor, vad.duration_ms))
    return gaps


def _snap_start(
    boundary: BoundaryRange, gaps: list[tuple[int, int]], config: StrategyConfig, require_gap_guard: bool = False
) -> int | None:
    anchor = boundary.first_token_start_ms or boundary.original_start_ms
    window_start = max(0, anchor - config.search_before_start_ms)
    window_end = anchor + config.search_after_start_ms
    candidates = _overlapping_gaps(gaps, window_start, window_end, config)
    if not candidates:
        return None
    safe_candidates = []
    for gap_start, gap_end in candidates:
        if require_gap_guard and gap_end > anchor:
            continue
        cut = max(gap_start, gap_end - config.pre_roll_ms)
        if cut <= anchor - config.start_guard_ms:
            safe_candidates.append((gap_start, gap_end, cut))
    if not safe_candidates:
        return None
    gap_start, gap_end, cut = min(safe_candidates, key=lambda gap: abs(gap[1] - anchor))
    return cut


def _snap_end(
    boundary: BoundaryRange, gaps: list[tuple[int, int]], config: StrategyConfig, require_gap_guard: bool = False
) -> int | None:
    anchor = boundary.last_token_end_ms or boundary.original_end_ms
    window_start = max(0, anchor - config.search_before_end_ms)
    window_end = anchor + config.search_after_end_ms
    candidates = _overlapping_gaps(gaps, window_start, window_end, config)
    if not candidates:
        return None
    safe_candidates = []
    for gap_start, gap_end in candidates:
        if require_gap_guard and gap_start < anchor:
            continue
        cut = min(gap_end, gap_start + config.post_roll_ms)
        if cut >= anchor + config.end_guard_ms:
            safe_candidates.append((gap_start, gap_end, cut))
    if not safe_candidates:
        return None
    gap_start, gap_end, cut = min(safe_candidates, key=lambda gap: abs(gap[0] - anchor))
    return cut


def _overlapping_gaps(
    gaps: list[tuple[int, int]], window_start: int, window_end: int, config: StrategyConfig
) -> list[tuple[int, int]]:
    candidates = []
    for start, end in gaps:
        clipped_start = max(start, window_start)
        clipped_end = min(end, window_end)
        if clipped_end - clipped_start >= config.weak_gap_ms:
            candidates.append((clipped_start, clipped_end))
    strong = [gap for gap in candidates if gap[1] - gap[0] >= config.min_gap_ms]
    return strong or candidates
