import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.features import AudioFrame
from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken, VadData
from cutpoint_lab.paper_edit.state import apply_editor_rows, build_plan_from_editor_rows
from cutpoint_lab.studio.plans import apply_manual_nudges, build_ordered_plan
from cutpoint_lab.subtitle_exporter import write_srt


def _sample_transcript() -> Transcript:
    texts = ["Hello", "world", "um", "back", "again", "today"]
    tokens = [
        TranscriptToken(text=text, start_ms=1000 + index * 200, end_ms=1120 + index * 200)
        for index, text in enumerate(texts)
    ]
    return Transcript(
        source_video="source.mp4",
        duration_ms=5000,
        selected_segment_ids=["s1", "s2"],
        segments=[
            TranscriptSegment(
                id="s1",
                start_ms=900,
                end_ms=2300,
                text="Hello world um back again today",
                tokens=tokens,
            ),
            TranscriptSegment(
                id="s2",
                start_ms=3000,
                end_ms=3500,
                text="收尾",
                tokens=[TranscriptToken(text="收尾", start_ms=3050, end_ms=3400)],
            ),
        ],
    )


def _cut_rows() -> list[dict]:
    return [
        {
            "id": "s1",
            "checked": True,
            "text": "前端已经更新的全句文本",
            # 故意乱序，后端必须排序后拆成三段。
            "cuts": [
                {"start_token": 4, "end_token": 4},
                {"start_token": 2, "end_token": 2},
            ],
        },
        {"id": "s2", "checked": True, "text": "收尾"},
    ]


class CutsStateTests(unittest.TestCase):
    def test_cuts_split_segment_into_ordered_runs_and_expand_selected_ids(self):
        edited = apply_editor_rows(_sample_transcript(), _cut_rows())

        self.assertEqual([segment.id for segment in edited.segments], ["s1", "s1#2", "s1#3", "s2"])
        self.assertEqual(edited.selected_segment_ids, ["s1", "s1#2", "s1#3", "s2"])
        first, second, third = edited.segments[:3]
        self.assertEqual((first.start_ms, first.end_ms, first.text), (1000, 1320, "Hello world"))
        self.assertEqual((second.start_ms, second.end_ms, second.text), (1600, 1720, "back"))
        self.assertEqual((third.start_ms, third.end_ms, third.text), (2000, 2120, "today"))
        self.assertEqual([token.text for token in first.tokens], ["Hello", "world"])

    def test_trim_is_applied_before_defensively_clamped_cuts(self):
        edited = apply_editor_rows(
            _sample_transcript(),
            [
                {
                    "id": "s1",
                    "checked": True,
                    "trim": {"start_token": 1, "end_token": 5},
                    "cuts": [
                        {"start_token": 99, "end_token": 99},
                        {"start_token": -9, "end_token": 1},
                    ],
                }
            ],
        )

        parts = [segment for segment in edited.segments if segment.id.startswith("s1")]
        self.assertEqual([segment.id for segment in parts], ["s1"])
        self.assertEqual([token.text for token in parts[0].tokens], ["um", "back", "again"])
        self.assertEqual(parts[0].text, "um back again")

    def test_no_cuts_and_empty_cuts_keep_legacy_behavior_identical(self):
        rows = [
            {
                "id": "s1",
                "checked": True,
                "text": "legacy edited text",
                "trim": {"start_token": 1, "end_token": 4},
            }
        ]
        without_cuts = apply_editor_rows(_sample_transcript(), rows)
        with_empty_cuts = apply_editor_rows(
            _sample_transcript(),
            [{**rows[0], "cuts": []}],
        )

        self.assertEqual(with_empty_cuts, without_cuts)
        self.assertEqual(without_cuts.segments[0].id, "s1")
        self.assertEqual(without_cuts.segments[0].text, "legacy edited text")


class CutsPlanTests(unittest.TestCase):
    def test_plan_contains_subsplit_metadata_and_keeps_cut_runs_separate(self):
        edited, plan = build_plan_from_editor_rows(
            _sample_transcript(),
            _cut_rows(),
            strategy="token_padding",
        )

        self.assertEqual(
            plan["segment_subsplits"],
            {"s1": ["s1", "s1#2", "s1#3"]},
        )
        self.assertEqual(plan["selected_segment_ids"], edited.selected_segment_ids)
        flattened = [
            segment_id
            for item in plan["ranges"]
            for segment_id in item["source_segment_ids"]
        ]
        self.assertEqual(flattened, ["s1", "s1#2", "s1#3", "s2"])
        s1_ranges = [
            item for item in plan["ranges"] if item["source_segment_ids"][0].startswith("s1")
        ]
        self.assertEqual(len(s1_ranges), 3)
        self.assertLessEqual(s1_ranges[0]["end_ms"], 1320)
        self.assertGreaterEqual(s1_ranges[1]["start_ms"], 1600)

    def test_vad_strategy_does_not_merge_short_subsplit_gaps_back_together(self):
        rows = _cut_rows()
        rows[1]["checked"] = False
        _, plan = build_plan_from_editor_rows(
            _sample_transcript(),
            rows,
            strategy="vad_snap",
            vad=VadData(
                duration_ms=5000,
                speech_intervals=[
                    {"start_ms": 900, "end_ms": 1320},
                    {"start_ms": 1600, "end_ms": 1720},
                    {"start_ms": 2000, "end_ms": 2200},
                    {"start_ms": 3050, "end_ms": 3400},
                ],
            ),
        )

        s1_ranges = [
            item for item in plan["ranges"] if item["source_segment_ids"][0].startswith("s1")
        ]
        self.assertEqual(
            [item["source_segment_ids"] for item in s1_ranges],
            [["s1"], ["s1#2"], ["s1#3"]],
        )
        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in s1_ranges],
            [(740, 1320), (1600, 1720), (2000, 2440)],
        )
        self.assertEqual(
            [item["adjustment_reason"] for item in s1_ranges],
            ["snapped_to_vad_gap", "vad_fallback_token_padding", "snapped_to_vad_gap"],
        )

    def test_rms_strategy_does_not_snap_across_subsplit_boundaries(self):
        rows = _cut_rows()
        rows[1]["checked"] = False
        frames = [
            AudioFrame(
                start_ms=start,
                end_ms=start + 20,
                rms_db=(
                    -60.0
                    if (
                        720 <= start < 900
                        or 1320 <= start < 1600
                        or 1720 <= start < 2000
                        or 2200 <= start < 2500
                    )
                    else -10.0
                ),
            )
            for start in range(0, 5000, 20)
        ]
        _, plan = build_plan_from_editor_rows(
            _sample_transcript(),
            rows,
            strategy="rms_snap",
            frames=frames,
        )

        s1_ranges = [
            item for item in plan["ranges"] if item["source_segment_ids"][0].startswith("s1")
        ]
        self.assertEqual(
            [(item["start_ms"], item["end_ms"]) for item in s1_ranges],
            [(740, 1320), (1600, 1720), (2000, 2440)],
        )
        self.assertEqual(
            [item["adjustment_reason"] for item in s1_ranges],
            ["snapped_to_rms_gap", "rms_fallback_token_padding", "snapped_to_rms_gap"],
        )

    def test_manual_nudges_only_apply_to_true_original_sentence_edges(self):
        plan = {
            "segment_subsplits": {"s1": ["s1", "s1#2", "s1#3"]},
            "ranges": [
                {"start_ms": 1000, "end_ms": 1200, "source_segment_ids": ["s1"]},
                {"start_ms": 1500, "end_ms": 1700, "source_segment_ids": ["s1#2"]},
                {"start_ms": 2000, "end_ms": 2200, "source_segment_ids": ["s1#3"]},
            ],
        }

        apply_manual_nudges(plan, {"s1": {"start_ms": -50, "end_ms": 80}})

        first, middle, last = plan["ranges"]
        self.assertEqual((first["start_ms"], first["end_ms"]), (950, 1200))
        self.assertEqual((middle["start_ms"], middle["end_ms"]), (1500, 1700))
        self.assertEqual((last["start_ms"], last["end_ms"]), (2000, 2280))

    def test_ordered_groups_expand_original_ids_to_all_subsplit_ids(self):
        edited = apply_editor_rows(_sample_transcript(), _cut_rows())
        plan = build_ordered_plan(
            edited,
            [{"purpose": "hook", "segment_ids": ["s1"]}],
            strategy="token_padding",
        )

        flattened = [
            segment_id
            for item in plan["ranges"]
            for segment_id in item["source_segment_ids"]
        ]
        self.assertEqual(flattened, ["s1", "s1#2", "s1#3"])
        self.assertEqual(plan["groups"][0]["segment_ids"], ["s1"])
        self.assertEqual(plan["segment_subsplits"], {"s1": ["s1", "s1#2", "s1#3"]})

    def test_write_srt_uses_each_subsplit_text(self):
        edited = apply_editor_rows(_sample_transcript(), _cut_rows())
        plan = {
            "ranges": [
                {"start_ms": 1000, "end_ms": 1320, "source_segment_ids": ["s1"]},
                {"start_ms": 1600, "end_ms": 1720, "source_segment_ids": ["s1#2"]},
                {"start_ms": 2000, "end_ms": 2120, "source_segment_ids": ["s1#3"]},
            ]
        }
        with tempfile.TemporaryDirectory() as tmp:
            cues = write_srt(edited, plan, Path(tmp) / "split.srt")

        self.assertEqual([cue.source_segment_id for cue in cues], ["s1", "s1#2", "s1#3"])
        self.assertEqual([cue.text for cue in cues], ["Hello world", "back", "today"])
        self.assertEqual([(cue.start_ms, cue.end_ms) for cue in cues], [(0, 320), (320, 440), (440, 560)])

    def test_plan_without_cuts_has_empty_subsplit_metadata(self):
        _, plan = build_plan_from_editor_rows(
            _sample_transcript(),
            [
                {"id": "s1", "checked": True, "text": "Hello world um back again today"},
                {"id": "s2", "checked": True, "text": "收尾"},
            ],
        )

        self.assertEqual(plan["segment_subsplits"], {})

    def test_non_object_rows_are_ignored_instead_of_crashing(self):
        edited = apply_editor_rows(
            _sample_transcript(),
            [None, {"id": "s1", "checked": True, "text": "kept"}],  # type: ignore[list-item]
        )

        self.assertEqual(edited.selected_segment_ids, ["s1"])


if __name__ == "__main__":
    unittest.main()
