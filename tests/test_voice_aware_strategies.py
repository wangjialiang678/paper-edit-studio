import unittest

from cutpoint_lab.features import AudioFrame
from cutpoint_lab.io import load_speaker_data, load_transcript
from cutpoint_lab.models import SpeakerData
from cutpoint_lab.paper_edit.state import build_plan_from_editor_rows
from cutpoint_lab.strategies import SpeakerAwareValleyStrategy, VoiceEnhancedRmsStrategy


def _frames(low_ranges=None, duration_ms=3200, frame_ms=20):
    low_ranges = low_ranges or []
    frames = []
    for start in range(0, duration_ms, frame_ms):
        end = start + frame_ms
        low = any(start >= lo and end <= hi for lo, hi in low_ranges)
        frames.append(AudioFrame(start_ms=start, end_ms=end, rms_db=-54.0 if low else -18.0))
    return frames


def _speaker_transcript():
    return load_transcript(
        {
            "duration_ms": 3200,
            "selected_segment_ids": ["seg_002"],
            "segments": [
                {
                    "id": "seg_001",
                    "start_ms": 100,
                    "end_ms": 900,
                    "text": "上一个人",
                    "tokens": [{"text": "前", "start_ms": 700, "end_ms": 820}],
                },
                {
                    "id": "seg_002",
                    "start_ms": 1000,
                    "end_ms": 1850,
                    "text": "保留这一句",
                    "tokens": [
                        {"text": "保", "start_ms": 1120, "end_ms": 1220},
                        {"text": "留", "start_ms": 1680, "end_ms": 1780},
                    ],
                },
                {
                    "id": "seg_003",
                    "start_ms": 1900,
                    "end_ms": 2600,
                    "text": "下一个人",
                    "tokens": [{"text": "后", "start_ms": 1930, "end_ms": 2050}],
                },
            ],
        }
    )


def _speaker_data():
    return load_speaker_data(
        {
            "duration_ms": 3200,
            "speaker_segments": [
                {"speaker": "SPEAKER_00", "start_ms": 100, "end_ms": 900, "confidence": 0.8},
                {"speaker": "SPEAKER_01", "start_ms": 980, "end_ms": 1860, "confidence": 0.9},
                {"speaker": "SPEAKER_00", "start_ms": 1880, "end_ms": 2600, "confidence": 0.85},
            ],
            "overlap_segments": [{"start_ms": 1840, "end_ms": 1900, "speakers": ["SPEAKER_01", "SPEAKER_00"]}],
        }
    )


class VoiceAwareStrategyTests(unittest.TestCase):
    def test_load_speaker_data_normalizes_segments_and_overlap(self):
        speaker_data = _speaker_data()

        self.assertIsInstance(speaker_data, SpeakerData)
        self.assertEqual([item.speaker for item in speaker_data.normalized_segments()], ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00"])
        self.assertTrue(speaker_data.is_overlapped(1850))
        self.assertEqual(speaker_data.dominant_speaker(1000, 1850), "SPEAKER_01")

    def test_speaker_aware_fallback_does_not_bleed_into_next_speaker(self):
        transcript = _speaker_transcript()
        speaker_data = _speaker_data()

        cut = SpeakerAwareValleyStrategy(frames=[], speaker_data=speaker_data).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "speaker_aware_fallback_token_padding")
        self.assertGreaterEqual(cut.start_ms, 920)
        self.assertLessEqual(cut.end_ms, 1860)

    def test_speaker_aware_fallback_does_not_start_inside_leading_overlap(self):
        transcript = _speaker_transcript()
        speaker_data = load_speaker_data(
            {
                "duration_ms": 3200,
                "speaker_segments": [
                    {"speaker": "SPEAKER_00", "start_ms": 100, "end_ms": 1080},
                    {"speaker": "SPEAKER_01", "start_ms": 980, "end_ms": 1860},
                ],
                "overlap_segments": [{"start_ms": 980, "end_ms": 1080, "speakers": ["SPEAKER_00", "SPEAKER_01"]}],
            }
        )

        cut = SpeakerAwareValleyStrategy(frames=[], speaker_data=speaker_data).optimize(transcript).ranges[0]

        self.assertGreaterEqual(cut.start_ms, 1080)

    def test_speaker_aware_requires_speaker_timeline(self):
        with self.assertRaisesRegex(ValueError, "speaker timeline"):
            SpeakerAwareValleyStrategy(frames=[], speaker_data=None).optimize(_speaker_transcript())

    def test_speaker_aware_valley_uses_local_energy_without_crossing_overlap(self):
        transcript = _speaker_transcript()
        speaker_data = _speaker_data()
        frames = _frames(low_ranges=[(1000, 1080), (1800, 1840)], duration_ms=3200)

        cut = SpeakerAwareValleyStrategy(frames=frames, speaker_data=speaker_data).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "speaker_aware_valley")
        self.assertGreaterEqual(cut.start_ms, 980)
        self.assertLess(cut.start_ms, 1120)
        self.assertGreater(cut.end_ms, 1780)
        self.assertLess(cut.end_ms, 1840)

    def test_voice_enhanced_rms_has_distinct_strategy_reason(self):
        transcript = _speaker_transcript()
        frames = _frames(low_ranges=[(940, 1040), (1800, 1880)], duration_ms=3200)

        cut = VoiceEnhancedRmsStrategy(frames=frames).optimize(transcript).ranges[0]

        self.assertEqual(cut.adjustment_reason, "voice_enhanced_rms_valley")
        self.assertLess(cut.start_ms, 1120)
        self.assertGreater(cut.end_ms, 1780)

    def test_paper_edit_plan_can_use_speaker_aware_strategy(self):
        transcript = _speaker_transcript()
        edited, plan = build_plan_from_editor_rows(
            transcript,
            [{"id": "seg_002", "checked": True, "text": "保留这一句"}],
            strategy="speaker_aware_valley",
            speaker_data=_speaker_data(),
            require_word_timestamps=True,
        )

        self.assertEqual(edited.selected_segment_ids, ["seg_002"])
        self.assertEqual(plan["strategy"], "speaker_aware_valley")
        self.assertEqual(plan["ranges"][0]["adjustment_reason"], "speaker_aware_fallback_token_padding")


if __name__ == "__main__":
    unittest.main()
