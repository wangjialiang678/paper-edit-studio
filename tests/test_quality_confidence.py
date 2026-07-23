from __future__ import annotations

import unittest

from cutpoint_lab.models import TranscriptSegment, TranscriptToken
from cutpoint_lab.quality.confidence import LOW_CONFIDENCE_THRESHOLD, scan


class ConfidenceScanTests(unittest.TestCase):
    def test_consecutive_low_tokens_are_merged_with_inclusive_indexes(self):
        issues = scan(
            [
                {
                    "id": "sentence_0001",
                    "text": "这是超导项目",
                    "tokens": [
                        {"text": "这是", "confidence": 0.9},
                        {"text": "超", "confidence": 0.4},
                        {"text": "导", "confidence": 0.5},
                        {"text": "项目", "confidence": 0.8},
                    ],
                }
            ]
        )

        self.assertEqual(len(issues), 1)
        issue = issues[0]
        self.assertEqual(issue["kind"], "low_confidence")
        self.assertEqual(issue["source"], "confidence")
        self.assertEqual(
            issue["span"],
            {"text": "超导", "token_start": 1, "token_end": 2},
        )
        self.assertAlmostEqual(issue["confidence"], 0.45)
        self.assertIn("0.55", issue["reason"])
        self.assertEqual(issue["status"], "open")

    def test_threshold_is_strict_and_missing_confidence_breaks_runs(self):
        issues = scan(
            [
                {
                    "id": "s1",
                    "tokens": [
                        {"text": "低", "confidence": LOW_CONFIDENCE_THRESHOLD - 0.01},
                        {"text": "缺"},
                        {"text": "边", "confidence": LOW_CONFIDENCE_THRESHOLD},
                        {"text": "低", "confidence": 0.1},
                    ],
                }
            ]
        )

        self.assertEqual(
            [issue["span"] for issue in issues],
            [
                {"text": "低", "token_start": 0, "token_end": 0},
                {"text": "低", "token_start": 3, "token_end": 3},
            ],
        )

    def test_segments_without_any_confidence_are_skipped(self):
        self.assertEqual(
            scan(
                [
                    {
                        "id": "legacy",
                        "text": "旧项目",
                        "tokens": [{"text": "旧"}, {"text": "项目"}],
                    }
                ]
            ),
            [],
        )

    def test_accepts_transcript_segment_models(self):
        segment = TranscriptSegment(
            id="sentence_0007",
            start_ms=0,
            end_ms=500,
            text="AI",
            tokens=[
                TranscriptToken("A", 0, 200, 0.2),
                TranscriptToken("I", 200, 400, 0.3),
            ],
        )

        issues = scan([segment])

        self.assertEqual(issues[0]["segment_id"], "sentence_0007")
        self.assertEqual(issues[0]["span"]["text"], "AI")


if __name__ == "__main__":
    unittest.main()
