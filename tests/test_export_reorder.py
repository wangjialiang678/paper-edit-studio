from __future__ import annotations

import unittest

from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.paper_edit.review_html import render_review_html
from cutpoint_lab.paper_edit.state import build_plan_from_selection


def _sample_transcript() -> Transcript:
    return Transcript(
        source_video="source.mp4",
        duration_ms=5000,
        selected_segment_ids=["a", "b", "c"],
        segments=[
            TranscriptSegment(
                id="a",
                start_ms=200,
                end_ms=900,
                text="第一段",
                tokens=[
                    TranscriptToken(text="第", start_ms=250, end_ms=350),
                    TranscriptToken(text="一", start_ms=400, end_ms=500),
                    TranscriptToken(text="段", start_ms=550, end_ms=700),
                ],
            ),
            TranscriptSegment(
                id="b",
                start_ms=1800,
                end_ms=2500,
                text="第二段",
                tokens=[
                    TranscriptToken(text="第", start_ms=1850, end_ms=1950),
                    TranscriptToken(text="二", start_ms=2000, end_ms=2100),
                    TranscriptToken(text="段", start_ms=2150, end_ms=2300),
                ],
            ),
            TranscriptSegment(
                id="c",
                start_ms=3400,
                end_ms=4300,
                text="第三段内容",
                tokens=[
                    TranscriptToken(text="第", start_ms=3450, end_ms=3550),
                    TranscriptToken(text="三", start_ms=3600, end_ms=3700),
                    TranscriptToken(text="段", start_ms=3750, end_ms=3850),
                    TranscriptToken(text="内容", start_ms=4000, end_ms=4150),
                ],
            ),
        ],
    )


def _rows() -> list[dict]:
    return [
        {"id": "a", "checked": True, "text": "第一段"},
        {"id": "b", "checked": False, "text": "第二段"},
        {"id": "c", "checked": True, "text": "第三段内容"},
    ]


def _flattened_source_ids(plan: dict) -> list[str]:
    return [
        segment_id
        for clip_range in plan["ranges"]
        for segment_id in clip_range["source_segment_ids"]
    ]


class BuildPlanFromSelectionTests(unittest.TestCase):
    def test_nonempty_order_controls_kept_segments_and_range_order(self):
        edited, plan = build_plan_from_selection(
            _sample_transcript(),
            {"rows": _rows(), "order": ["c", "a"]},
            strategy="token_padding",
        )

        self.assertEqual(_flattened_source_ids(plan), ["c", "a"])
        self.assertEqual(edited.selected_segment_ids, ["a", "c"])
        self.assertTrue(plan["reordered"])

    def test_ordered_segment_cuts_expand_subsplits_in_segment_order(self):
        rows = _rows()
        rows[2]["cuts"] = [{"start_token": 1, "end_token": 2}]

        edited, plan = build_plan_from_selection(
            _sample_transcript(),
            {"rows": rows, "order": ["c"]},
            strategy="token_padding",
        )

        self.assertEqual(
            [segment.id for segment in edited.segments if segment.id.startswith("c")],
            ["c", "c#2"],
        )
        self.assertEqual(_flattened_source_ids(plan), ["c", "c#2"])
        self.assertEqual(plan["segment_subsplits"], {"c": ["c", "c#2"]})

    def test_missing_order_uses_legacy_time_order(self):
        rows = list(reversed(_rows()))

        _edited, plan = build_plan_from_selection(
            _sample_transcript(),
            {"rows": rows},
            strategy="token_padding",
        )

        self.assertEqual(_flattened_source_ids(plan), ["a", "c"])
        self.assertFalse(plan["reordered"])

    def test_row_text_does_not_change_ranges_or_cut_points(self):
        original_rows = _rows()
        rewritten_rows = [dict(row) for row in original_rows]
        rewritten_rows[2]["text"] = "这不是原字幕，不能参与切点计算"

        _original_edited, original_plan = build_plan_from_selection(
            _sample_transcript(),
            {"rows": original_rows, "order": ["c", "a"]},
            strategy="token_padding",
        )
        rewritten_edited, rewritten_plan = build_plan_from_selection(
            _sample_transcript(),
            {"rows": rewritten_rows, "order": ["c", "a"]},
            strategy="token_padding",
        )

        self.assertEqual(rewritten_plan["ranges"], original_plan["ranges"])
        self.assertEqual(rewritten_edited.segments[2].text, rewritten_rows[2]["text"])

    def test_rows_are_required(self):
        with self.assertRaisesRegex(ValueError, "rows"):
            build_plan_from_selection(
                _sample_transcript(),
                {"order": ["a"]},
                strategy="token_padding",
            )


class ReorderedReviewHtmlTests(unittest.TestCase):
    def test_ordered_rows_render_first_and_payload_uses_visual_checked_order(self):
        page = render_review_html(
            _sample_transcript(),
            _rows(),
            order=["c", "a"],
        )

        first_c = page.index('"id":"c"')
        then_a = page.index('"id":"a"')
        then_b = page.index('"id":"b"')
        self.assertLess(first_c, then_a)
        self.assertLess(then_a, then_b)
        self.assertIn("draggable", page)
        self.assertIn('"dragstart"', page)
        self.assertIn("function visibleRows()", page)
        self.assertIn(
            "order: visibleRows().filter((row) => row.checked).map((row) => row.id)",
            page,
        )


if __name__ == "__main__":
    unittest.main()
