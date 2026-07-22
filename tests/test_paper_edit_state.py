import unittest

from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.paper_edit.state import (
    apply_editor_rows,
    build_editor_state,
    build_plan_from_editor_rows,
    transcript_to_payload,
)


def _sample_transcript() -> Transcript:
    return Transcript(
        source_video="source.mp4",
        duration_ms=8000,
        selected_segment_ids=["seg_001", "seg_002", "seg_003"],
        segments=[
            TranscriptSegment(
                id="seg_001",
                start_ms=1000,
                end_ms=2500,
                text="开场寒暄。",
                tokens=[
                    TranscriptToken(text="开场", start_ms=1100, end_ms=1400),
                    TranscriptToken(text="寒暄。", start_ms=1500, end_ms=2200),
                ],
            ),
            TranscriptSegment(
                id="seg_002",
                start_ms=2800,
                end_ms=4200,
                text="这里是一个关键观点。",
                tokens=[
                    TranscriptToken(text="关键", start_ms=2900, end_ms=3200),
                    TranscriptToken(text="观点。", start_ms=3300, end_ms=4000),
                ],
            ),
            TranscriptSegment(
                id="seg_003",
                start_ms=5000,
                end_ms=6200,
                text="缺少词级时间戳。",
                tokens=[],
            ),
        ],
    )


class PaperEditStateTests(unittest.TestCase):
    def test_builds_rows_with_ai_default_selection_from_candidates(self):
        candidates = {
            "recommended_candidate_ids": ["clip_001"],
            "candidates": [{"id": "clip_001", "segment_ids": ["seg_002"]}],
        }

        state = build_editor_state(_sample_transcript(), candidates_payload=candidates)

        self.assertEqual([row["id"] for row in state["rows"]], ["seg_001", "seg_002", "seg_003"])
        self.assertEqual([row["checked"] for row in state["rows"]], [False, True, False])
        self.assertEqual(state["word_timestamps"]["segments_with_words"], 2)
        self.assertEqual(state["word_timestamps"]["selected_without_words"], [])
        self.assertEqual(state["selected_duration_ms"], 1400)

    def test_apply_editor_rows_updates_text_and_selected_ids(self):
        edited = apply_editor_rows(
            _sample_transcript(),
            [
                {"id": "seg_001", "checked": False, "text": "删掉这句。"},
                {"id": "seg_002", "checked": True, "text": "改后的关键观点。"},
            ],
        )

        payload = transcript_to_payload(edited)

        self.assertEqual(edited.selected_segment_ids, ["seg_002"])
        self.assertEqual(edited.segments[1].text, "改后的关键观点。")
        self.assertEqual(edited.segments[0].text, "删掉这句。")
        self.assertEqual(payload["segments"][1]["tokens"][0]["text"], "关键")

    def test_build_plan_requires_word_timestamps_for_selected_rows(self):
        with self.assertRaisesRegex(ValueError, "word-level timestamps"):
            build_plan_from_editor_rows(
                _sample_transcript(),
                [{"id": "seg_003", "checked": True, "text": "缺少词级时间戳。"}],
                require_word_timestamps=True,
            )

    def test_rows_payload_exposes_word_tokens(self):
        state = build_editor_state(_sample_transcript())
        row = state["rows"][0]
        self.assertEqual([token["text"] for token in row["tokens"]], ["开场", "寒暄。"])
        self.assertEqual(row["tokens"][0]["start_ms"], 1100)
        self.assertEqual(state["rows"][2]["tokens"], [])

    def test_rows_payload_preserves_optional_token_confidence(self):
        transcript = _sample_transcript()
        transcript.segments[0].tokens[0] = TranscriptToken(
            text="开场",
            start_ms=1100,
            end_ms=1400,
            confidence=0.87,
        )

        row = build_editor_state(transcript)["rows"][0]

        self.assertEqual(row["tokens"][0]["confidence"], 0.87)
        self.assertNotIn("confidence", row["tokens"][1])

    def test_trim_narrows_segment_to_kept_token_boundaries(self):
        edited = apply_editor_rows(
            _sample_transcript(),
            [{"id": "seg_001", "checked": True, "text": "寒暄。", "trim": {"start_token": 1, "end_token": 1}}],
        )
        segment = edited.segments[0]
        self.assertEqual(segment.start_ms, 1500)
        self.assertEqual(segment.end_ms, 2200)
        self.assertEqual([token.text for token in segment.tokens], ["寒暄。"])

    def test_cuts_preserve_text_corrections_without_changing_tokens(self):
        transcript = Transcript(
            source_video="source.mp4",
            duration_ms=2000,
            selected_segment_ids=["seg_001"],
            segments=[
                TranscriptSegment(
                    id="seg_001",
                    start_ms=0,
                    end_ms=1800,
                    text="嗯WEB CODING继续",
                    tokens=[
                        TranscriptToken(text="嗯", start_ms=0, end_ms=200),
                        TranscriptToken(text="WEB CODING", start_ms=250, end_ms=1000),
                        TranscriptToken(text="继续", start_ms=1100, end_ms=1700),
                    ],
                )
            ],
        )

        edited = apply_editor_rows(
            transcript,
            [
                {
                    "id": "seg_001",
                    "checked": True,
                    "text": "嗯vibe coding继续",
                    "cuts": [{"start_token": 0, "end_token": 0}],
                }
            ],
        )

        self.assertEqual(edited.segments[0].text, "vibe coding继续")
        self.assertEqual(
            [token.text for token in edited.segments[0].tokens],
            ["WEB CODING", "继续"],
        )

    def test_invalid_trim_falls_back_to_full_segment(self):
        for trim in (
            {"start_token": 5, "end_token": 9},   # 越界后 start>end
            {"start_token": 1, "end_token": 0},   # start>end
            {"start_token": "x"},                  # 非法类型
        ):
            edited = apply_editor_rows(
                _sample_transcript(),
                [{"id": "seg_001", "checked": True, "text": "开场寒暄。", "trim": trim}],
            )
            segment = edited.segments[0]
            self.assertEqual((segment.start_ms, segment.end_ms), (1000, 2500), trim)
            self.assertEqual(len(segment.tokens), 2, trim)

    def test_trim_on_tokenless_segment_is_ignored(self):
        edited = apply_editor_rows(
            _sample_transcript(),
            [{"id": "seg_003", "checked": True, "text": "缺少词级时间戳。", "trim": {"start_token": 0, "end_token": 0}}],
        )
        segment = edited.segments[2]
        self.assertEqual((segment.start_ms, segment.end_ms), (5000, 6200))

    def test_build_plan_uses_token_boundaries_for_selected_rows(self):
        edited, plan = build_plan_from_editor_rows(
            _sample_transcript(),
            [{"id": "seg_002", "checked": True, "text": "改后的关键观点。"}],
            require_word_timestamps=True,
        )

        self.assertEqual(edited.selected_segment_ids, ["seg_002"])
        self.assertEqual(plan["selected_segment_ids"], ["seg_002"])
        self.assertEqual(plan["ranges"][0]["adjustment_reason"], "token_padding")
        self.assertEqual(plan["ranges"][0]["start_ms"], 2740)
        self.assertEqual(plan["ranges"][0]["end_ms"], 4240)


if __name__ == "__main__":
    unittest.main()
