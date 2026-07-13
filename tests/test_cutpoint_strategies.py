import tempfile
import unittest
import wave
from pathlib import Path

from cutpoint_lab.features import AudioFrame, validate_wav_content
from cutpoint_lab.io import load_transcript, load_vad
from cutpoint_lab.models import CutRange, VadData
from cutpoint_lab.strategies import (
    AnchoredRmsValleyStrategy,
    HybridValleyStrategy,
    RmsSnapStrategy,
    StrategyConfig,
    TokenPaddingStrategy,
    VadSnapStrategy,
    WaveformVisualSnapStrategy,
    _merge_cut_overlaps,
)


def sample_transcript():
    return load_transcript(
        {
            "source_video": "sample.mp4",
            "selected_segment_ids": ["seg_001", "seg_002"],
            "segments": [
                {
                    "id": "seg_000",
                    "start_ms": 200,
                    "end_ms": 900,
                    "text": "前一句",
                    "tokens": [
                        {"text": "前", "start_ms": 260, "end_ms": 330, "confidence": 0.95},
                        {"text": "句", "start_ms": 340, "end_ms": 430, "confidence": 0.95},
                    ],
                },
                {
                    "id": "seg_001",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "第一句",
                    "tokens": [
                        {"text": "第", "start_ms": 1080, "end_ms": 1160, "confidence": 0.95},
                        {"text": "一", "start_ms": 1180, "end_ms": 1260, "confidence": 0.95},
                        {"text": "句", "start_ms": 1780, "end_ms": 1900, "confidence": 0.95},
                    ],
                },
                {
                    "id": "seg_002",
                    "start_ms": 2300,
                    "end_ms": 3200,
                    "text": "第二句",
                    "tokens": [
                        {"text": "第", "start_ms": 2360, "end_ms": 2440, "confidence": 0.95},
                        {"text": "二", "start_ms": 2500, "end_ms": 2580, "confidence": 0.95},
                        {"text": "句", "start_ms": 3020, "end_ms": 3120, "confidence": 0.95},
                    ],
                },
            ],
        }
    )


def rms_frames(low_ranges=None, duration_ms=4500, frame_ms=20):
    low_ranges = low_ranges or []
    frames = []
    for start in range(0, duration_ms, frame_ms):
        end = start + frame_ms
        is_low = any(start >= lo and end <= hi for lo, hi in low_ranges)
        frames.append(AudioFrame(start_ms=start, end_ms=end, rms_db=-56.0 if is_low else -18.0))
    return frames


def single_segment_transcript(duration_ms=None):
    payload = {
        "selected_segment_ids": ["seg_001"],
        "segments": [
            {
                "id": "seg_001",
                "start_ms": 900,
                "end_ms": 1700,
                "text": "单句测试",
                "tokens": [
                    {"text": "开", "start_ms": 1000, "end_ms": 1100},
                    {"text": "收", "start_ms": 1500, "end_ms": 1600},
                ],
            }
        ],
    }
    if duration_ms is not None:
        payload["duration_ms"] = duration_ms
    return load_transcript(payload)


def write_test_wav(path, duration_ms=3000, low_ranges=None, sample_rate=16000):
    low_ranges = low_ranges or []
    samples = []
    total_samples = int(sample_rate * duration_ms / 1000)
    for index in range(total_samples):
        time_ms = index * 1000 / sample_rate
        is_low = any(start <= time_ms < end for start, end in low_ranges)
        sample = 0 if is_low else 8000
        samples.append(int(sample).to_bytes(2, "little", signed=True))
    with wave.open(str(path), "wb") as wav:
        wav.setnchannels(1)
        wav.setsampwidth(2)
        wav.setframerate(sample_rate)
        wav.writeframes(b"".join(samples))


class CutpointStrategyTests(unittest.TestCase):
    def test_missing_selected_segment_ids_raises_at_load_time(self):
        with self.assertRaises(ValueError):
            load_transcript(
                {
                    "segments": [{"id": "seg_001", "start_ms": 1000, "end_ms": 1400, "text": "存在"}],
                }
            )

    def test_unknown_selected_segment_id_raises(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["missing"],
                "segments": [{"id": "seg_001", "start_ms": 1000, "end_ms": 1400, "text": "存在"}],
            }
        )

        with self.assertRaises(ValueError):
            TokenPaddingStrategy().optimize(transcript)

    def test_token_padding_sorts_tokens_before_using_boundaries(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1800,
                        "text": "乱序 token",
                        "tokens": [
                            {"text": "后", "start_ms": 1600, "end_ms": 1700},
                            {"text": "前", "start_ms": 1100, "end_ms": 1200},
                        ],
                    }
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 940)
        self.assertEqual(cut.end_ms, 1940)

    def test_token_padding_clamps_to_media_duration(self):
        transcript = load_transcript(
            {
                "duration_ms": 1850,
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1800,
                        "text": "接近片尾",
                        "tokens": [
                            {"text": "尾", "start_ms": 1700, "end_ms": 1820},
                        ],
                    }
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.end_ms, 1850)

    def test_token_padding_uses_token_boundaries_and_merges_close_segments(self):
        transcript = sample_transcript()
        plan = TokenPaddingStrategy().optimize(transcript)

        self.assertEqual(plan.strategy, "token_padding")
        self.assertEqual(len(plan.ranges), 1)
        cut = plan.ranges[0]
        self.assertEqual(cut.source_segment_ids, ["seg_001", "seg_002"])
        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "token_padding")

    def test_token_padding_falls_back_to_segment_boundaries_without_tokens(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001"],
                "segments": [{"id": "seg_001", "start_ms": 1000, "end_ms": 1400, "text": "无 token"}],
            }
        )
        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 840)
        self.assertEqual(cut.end_ms, 1640)
        self.assertEqual(cut.adjustment_reason, "segment_padding")

    def test_rms_snap_moves_boundaries_to_low_energy_gaps(self):
        transcript = sample_transcript()
        frames = rms_frames(low_ranges=[(820, 1020), (3200, 3480)])

        plan = RmsSnapStrategy(frames=frames).optimize(transcript)
        cut = plan.ranges[0]

        self.assertEqual(plan.strategy, "rms_snap")
        self.assertEqual(cut.start_ms, 860)
        self.assertEqual(cut.end_ms, 3440)
        self.assertEqual(cut.adjustment_reason, "snapped_to_rms_gap")
        self.assertGreater(cut.confidence, 0.7)

    def test_rms_snap_falls_back_when_no_low_energy_gap_exists(self):
        transcript = sample_transcript()
        frames = rms_frames(low_ranges=[(0, 460)])

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_rms_snap_missing_frames_fallback_matches_token_padding_ranges(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001", "seg_002"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1200,
                        "tokens": [{"text": "one", "start_ms": 1100, "end_ms": 1200}],
                    },
                    {
                        "id": "seg_002",
                        "start_ms": 1850,
                        "end_ms": 2100,
                        "tokens": [{"text": "two", "start_ms": 1900, "end_ms": 2000}],
                    },
                ],
            }
        )

        token_ranges = TokenPaddingStrategy().optimize(transcript).ranges
        rms_ranges = RmsSnapStrategy(frames=[]).optimize(transcript).ranges

        self.assertEqual(len(rms_ranges), 2)
        self.assertEqual(
            [(cut.start_ms, cut.end_ms, cut.source_segment_ids) for cut in rms_ranges],
            [(cut.start_ms, cut.end_ms, cut.source_segment_ids) for cut in token_ranges],
        )
        self.assertEqual([cut.adjustment_reason for cut in rms_ranges], ["rms_fallback_token_padding"] * 2)

    def test_anchored_rms_valley_snaps_near_token_anchor_without_crossing_unselected_neighbors(self):
        transcript = load_transcript(
            {
                "duration_ms": 3200,
                "selected_segment_ids": ["seg_002"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 200,
                        "end_ms": 980,
                        "text": "删掉前一句",
                        "tokens": [{"text": "前", "start_ms": 850, "end_ms": 940}],
                    },
                    {
                        "id": "seg_002",
                        "start_ms": 1000,
                        "end_ms": 1900,
                        "text": "保留这一句",
                        "tokens": [
                            {"text": "保", "start_ms": 1220, "end_ms": 1300},
                            {"text": "留", "start_ms": 1710, "end_ms": 1780},
                        ],
                    },
                    {
                        "id": "seg_003",
                        "start_ms": 1960,
                        "end_ms": 2600,
                        "text": "删掉后一句",
                        "tokens": [{"text": "后", "start_ms": 1990, "end_ms": 2100}],
                    },
                ],
            }
        )
        frames = rms_frames(low_ranges=[(1100, 1160), (1830, 1890)], duration_ms=3200)

        cut = AnchoredRmsValleyStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "anchored_rms_valley")
        self.assertGreaterEqual(cut.start_ms, 1000)
        self.assertLess(cut.start_ms, 1220)
        self.assertGreater(cut.end_ms, 1780)
        self.assertLess(cut.end_ms, 1960)

    def test_waveform_visual_snap_uses_quantized_waveform_valley(self):
        transcript = single_segment_transcript(duration_ms=2600)
        frames = rms_frames(low_ranges=[(760, 900), (1760, 1900)], duration_ms=2600)

        cut = WaveformVisualSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "visual_waveform_valley")
        self.assertLess(cut.start_ms, 900)
        self.assertGreater(cut.end_ms, 1700)
        self.assertGreater(cut.confidence, 0.6)

    def test_hybrid_valley_combines_rms_and_visual_candidates(self):
        transcript = single_segment_transcript(duration_ms=2600)
        frames = rms_frames(low_ranges=[(760, 900), (1760, 1900)], duration_ms=2600)

        cut = HybridValleyStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "hybrid_valley")
        self.assertLess(cut.start_ms, 900)
        self.assertGreater(cut.end_ms, 1700)
        self.assertGreater(cut.confidence, 0.65)

    def test_rms_snap_falls_back_when_frames_are_missing(self):
        cut = RmsSnapStrategy(frames=[]).optimize(sample_transcript()).ranges[0]

        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_rms_snap_falls_back_when_dynamic_range_is_too_small(self):
        transcript = sample_transcript()
        frames = [AudioFrame(start_ms=i, end_ms=i + 20, rms_db=-30.0) for i in range(0, 4500, 20)]

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_rms_snap_rejects_start_gap_inside_token_guard(self):
        transcript = single_segment_transcript()
        frames = rms_frames(low_ranges=[(860, 1040), (1680, 1960)], duration_ms=2500)

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 840)
        self.assertEqual(cut.end_ms, 1840)
        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_rms_snap_rejects_end_gap_inside_token_guard(self):
        transcript = single_segment_transcript()
        frames = rms_frames(low_ranges=[(760, 940), (1540, 1900)], duration_ms=2500)

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 840)
        self.assertEqual(cut.end_ms, 1840)
        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_rms_snap_sorts_frames_before_detecting_gaps(self):
        transcript = single_segment_transcript()
        frames = list(reversed(rms_frames(low_ranges=[(760, 940), (1680, 1960)], duration_ms=2500)))

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 780)
        self.assertEqual(cut.end_ms, 1920)
        self.assertEqual(cut.adjustment_reason, "snapped_to_rms_gap")

    def test_rms_snap_clamps_snapped_end_to_media_duration(self):
        transcript = single_segment_transcript(duration_ms=1700)
        frames = rms_frames(low_ranges=[(760, 940), (1680, 1960)], duration_ms=2200)

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 780)
        self.assertEqual(cut.end_ms, 1700)
        self.assertEqual(cut.adjustment_reason, "snapped_to_rms_gap")

    def test_rms_snap_clamps_snapped_start_to_zero(self):
        transcript = load_transcript(
            {
                "duration_ms": 1200,
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 0,
                        "end_ms": 700,
                        "tokens": [
                            {"text": "start", "start_ms": 140, "end_ms": 220},
                            {"text": "end", "start_ms": 430, "end_ms": 500},
                        ],
                    }
                ],
            }
        )
        frames = rms_frames(low_ranges=[(0, 80), (580, 860)], duration_ms=1200)

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 0)
        self.assertEqual(cut.end_ms, 820)
        self.assertEqual(cut.adjustment_reason, "snapped_to_rms_gap")

    def test_rms_snap_does_not_bridge_missing_rms_frames_into_fake_gap(self):
        transcript = single_segment_transcript()
        frames = rms_frames(low_ranges=[(1680, 1960)], duration_ms=2500)
        frames.extend(
            [
                AudioFrame(start_ms=760, end_ms=820, rms_db=-56.0),
                AudioFrame(start_ms=900, end_ms=940, rms_db=-56.0),
            ]
        )

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_wav_content_validation_rejects_silent_audio(self):
        with tempfile.TemporaryDirectory() as tmp:
            wav_path = Path(tmp) / "silent.wav"
            write_test_wav(wav_path, low_ranges=[(0, 1000)], duration_ms=1000)

            with self.assertRaises(ValueError):
                validate_wav_content(wav_path)

    def test_mixed_snap_and_fallback_merge_reason_preserves_fallback_signal(self):
        merged = _merge_cut_overlaps(
            [
                CutRange(
                    start_ms=1000,
                    end_ms=1500,
                    original_start_ms=1100,
                    original_end_ms=1400,
                    source_segment_ids=["seg_001"],
                    adjustment_reason="snapped_to_vad_gap",
                    confidence=0.8,
                ),
                CutRange(
                    start_ms=1520,
                    end_ms=1900,
                    original_start_ms=1550,
                    original_end_ms=1800,
                    source_segment_ids=["seg_002"],
                    adjustment_reason="vad_fallback_token_padding",
                    confidence=0.45,
                ),
            ],
            StrategyConfig(merge_gap_ms=100),
        )

        self.assertEqual(len(merged), 1)
        self.assertEqual(merged[0].adjustment_reason, "mixed_fallback")

    def test_rms_snap_does_not_accept_gap_that_would_cut_into_tokens(self):
        transcript = sample_transcript()
        frames = rms_frames(low_ranges=[(1100, 1260), (2960, 3060)])

        cut = RmsSnapStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "rms_fallback_token_padding")

    def test_vad_snap_moves_boundaries_to_non_speech_gaps(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 1080, "end_ms": 3120},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "snapped_to_vad_gap")

    def test_vad_snap_falls_back_without_vad(self):
        transcript = sample_transcript()

        cut = VadSnapStrategy(vad=None).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")

    def test_vad_snap_normalizes_overlapping_speech_intervals(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 1080, "end_ms": 1800},
                {"start_ms": 1780, "end_ms": 3120},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 920)
        self.assertEqual(cut.end_ms, 3360)
        self.assertEqual(cut.adjustment_reason, "snapped_to_vad_gap")

    def test_vad_json_loads_speech_intervals(self):
        vad = load_vad(
            {
                "duration_ms": "4500",
                "speech_intervals": [
                    {"start_ms": "1080", "end_ms": "1900", "confidence": "0.91"},
                    {"start_ms": 2500, "end_ms": 3120},
                ],
            }
        )

        speech = vad.normalized_speech() if vad else []

        self.assertEqual(vad.duration_ms if vad else None, 4500)
        self.assertEqual([(item.start_ms, item.end_ms, item.confidence) for item in speech], [(1080, 1900, 0.91), (2500, 3120, None)])

    def test_vad_normalization_merges_jitter_gaps_under_80ms(self):
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 1080, "end_ms": 1800},
                {"start_ms": 1870, "end_ms": 3120},
            ],
        )

        speech = vad.normalized_speech(merge_gap_ms=80)

        self.assertEqual([(item.start_ms, item.end_ms) for item in speech], [(1080, 3120)])

    def test_vad_normalization_clips_intervals_to_duration(self):
        vad = VadData(
            duration_ms=1000,
            speech_intervals=[
                {"start_ms": -120, "end_ms": 300},
                {"start_ms": 900, "end_ms": 1200},
            ],
        )

        speech = vad.normalized_speech()

        self.assertEqual([(item.start_ms, item.end_ms) for item in speech], [(0, 300), (900, 1000)])

    def test_vad_snap_falls_back_when_gaps_are_too_short(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 0, "end_ms": 1020},
                {"start_ms": 1080, "end_ms": 3200},
                {"start_ms": 3260, "end_ms": 4500},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")

    def test_vad_snap_rejects_start_gap_that_violates_token_guard(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 0, "end_ms": 1030},
                {"start_ms": 1200, "end_ms": 3120},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")

    def test_vad_snap_rejects_end_gap_that_violates_token_guard(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 1080, "end_ms": 3000},
                {"start_ms": 3180, "end_ms": 4500},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")

    def test_vad_snap_falls_back_without_duration_instead_of_inventing_tail_gap(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=None,
            speech_intervals=[
                {"start_ms": 1080, "end_ms": 3120},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")

    def test_vad_snap_falls_back_for_fast_speech_with_no_gaps(self):
        transcript = sample_transcript()
        vad = VadData(
            duration_ms=4500,
            speech_intervals=[
                {"start_ms": 0, "end_ms": 4500},
            ],
        )

        cut = VadSnapStrategy(vad=vad).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "vad_fallback_token_padding")


if __name__ == "__main__":
    unittest.main()
