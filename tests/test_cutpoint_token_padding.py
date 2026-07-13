import unittest
from pathlib import Path

from cutpoint_lab.io import load_transcript
from cutpoint_lab.strategies import TokenPaddingStrategy


class TokenPaddingAgentATests(unittest.TestCase):
    def test_loads_normal_transcript_json_fixture(self):
        fixture_path = Path(__file__).resolve().parents[1] / "examples" / "sample_transcript.json"

        transcript = load_transcript(fixture_path)

        self.assertEqual(transcript.source_video, "sample.mp4")
        self.assertEqual(transcript.selected_segment_ids, ["seg_001"])
        self.assertEqual(len(transcript.segments), 1)
        self.assertEqual(transcript.segments[0].valid_tokens[0].text, "测")

    def test_raises_for_unknown_selected_segment_id(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["missing"],
                "segments": [{"id": "seg_001", "start_ms": 1000, "end_ms": 1200}],
            }
        )

        with self.assertRaises(ValueError):
            TokenPaddingStrategy().optimize(transcript)

    def test_sorts_all_tokens_in_merged_range_before_choosing_boundaries(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001", "seg_002"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1300,
                        "tokens": [{"text": "later", "start_ms": 2200, "end_ms": 2300}],
                    },
                    {
                        "id": "seg_002",
                        "start_ms": 1350,
                        "end_ms": 1700,
                        "tokens": [{"text": "earlier", "start_ms": 900, "end_ms": 950}],
                    },
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 740)
        self.assertEqual(cut.end_ms, 2540)

    def test_filters_invalid_tokens_before_choosing_boundaries(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1800,
                        "tokens": [
                            {"text": "", "start_ms": 100, "end_ms": 200},
                            {"text": "negative", "start_ms": -1, "end_ms": 2000},
                            {"text": "reversed", "start_ms": 2600, "end_ms": 2500},
                            {"text": "first", "start_ms": 1100, "end_ms": 1200},
                            {"text": "last", "start_ms": 1600, "end_ms": 1700},
                        ],
                    }
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 940)
        self.assertEqual(cut.end_ms, 1940)

    def test_uses_token_boundaries_with_default_padding(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 1000,
                        "end_ms": 1800,
                        "tokens": [
                            {"text": "first", "start_ms": 1100, "end_ms": 1200},
                            {"text": "last", "start_ms": 1600, "end_ms": 1700},
                        ],
                    }
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 940)
        self.assertEqual(cut.end_ms, 1940)
        self.assertEqual(cut.adjustment_reason, "token_padding")

    def test_token_padding_does_not_bleed_into_unselected_neighbor_segments(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_002"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 800,
                        "end_ms": 1060,
                        "tokens": [{"text": "dropped-before", "start_ms": 820, "end_ms": 1040}],
                    },
                    {
                        "id": "seg_002",
                        "start_ms": 1080,
                        "end_ms": 1520,
                        "tokens": [
                            {"text": "kept-start", "start_ms": 1100, "end_ms": 1180},
                            {"text": "kept-end", "start_ms": 1460, "end_ms": 1500},
                        ],
                    },
                    {
                        "id": "seg_003",
                        "start_ms": 1560,
                        "end_ms": 1900,
                        "tokens": [{"text": "dropped-after", "start_ms": 1580, "end_ms": 1880}],
                    },
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 1080)
        self.assertEqual(cut.end_ms, 1540)

    def test_falls_back_to_segment_boundaries_without_tokens(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001"],
                "segments": [{"id": "seg_001", "start_ms": 1000, "end_ms": 1800}],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 840)
        self.assertEqual(cut.end_ms, 2040)
        self.assertEqual(cut.adjustment_reason, "segment_padding")

    def test_merges_selected_segments_when_original_gap_is_less_than_merge_gap(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001", "seg_002"],
                "segments": [
                    {"id": "seg_001", "start_ms": 1000, "end_ms": 1200},
                    {"id": "seg_002", "start_ms": 1600, "end_ms": 1800},
                ],
            }
        )

        plan = TokenPaddingStrategy().optimize(transcript)

        self.assertEqual(len(plan.ranges), 1)
        self.assertEqual(plan.ranges[0].source_segment_ids, ["seg_001", "seg_002"])

    def test_keeps_selected_segments_separate_when_original_gap_exceeds_merge_gap(self):
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

        plan = TokenPaddingStrategy().optimize(transcript)

        self.assertEqual(len(plan.ranges), 2)
        self.assertEqual([cut.source_segment_ids for cut in plan.ranges], [["seg_001"], ["seg_002"]])

    def test_clamps_start_and_end_to_media_bounds(self):
        transcript = load_transcript(
            {
                "duration_ms": 1800,
                "selected_segment_ids": ["seg_001"],
                "segments": [
                    {
                        "id": "seg_001",
                        "start_ms": 0,
                        "end_ms": 1800,
                        "tokens": [
                            {"text": "first", "start_ms": 100, "end_ms": 200},
                            {"text": "last", "start_ms": 1700, "end_ms": 1780},
                        ],
                    }
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.start_ms, 0)
        self.assertEqual(cut.end_ms, 1800)

    def test_preserves_traceability_reason_and_confidence(self):
        transcript = load_transcript(
            {
                "selected_segment_ids": ["seg_001", "seg_002"],
                "segments": [
                    {"id": "seg_001", "start_ms": 1000, "end_ms": 1200},
                    {"id": "seg_002", "start_ms": 1300, "end_ms": 1500},
                ],
            }
        )

        cut = TokenPaddingStrategy().optimize(transcript).ranges[0]

        self.assertEqual(cut.source_segment_ids, ["seg_001", "seg_002"])
        self.assertEqual(cut.adjustment_reason, "segment_padding")
        self.assertGreater(cut.confidence, 0)
        self.assertLessEqual(cut.confidence, 1)


if __name__ == "__main__":
    unittest.main()
