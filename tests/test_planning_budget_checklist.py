from __future__ import annotations

import copy
import unittest

from cutpoint_lab.planning.budget import (
    budget_report,
    fit_budget,
    update_brief,
)
from cutpoint_lab.planning.checklist import build_export_checklist


def _plan_builder(durations: dict[str, int]):
    def build(edl: dict) -> dict:
        rows = {
            str(row["id"]): row
            for row in edl.get("rows") or []
            if isinstance(row, dict) and row.get("id") is not None
        }
        order = edl.get("order") or []
        if order:
            selected = [str(segment_id) for segment_id in order if str(segment_id) in rows]
        else:
            selected = [
                segment_id
                for segment_id, row in rows.items()
                if bool(row.get("checked"))
            ]
        cursor = 0
        ranges = []
        for segment_id in selected:
            end = cursor + durations.get(segment_id, 0)
            ranges.append(
                {
                    "start_ms": cursor,
                    "end_ms": end,
                    "source_segment_ids": [segment_id],
                }
            )
            cursor = end
        return {"ranges": ranges}

    return build


class BudgetPlanningTests(unittest.TestCase):
    def test_report_uses_plan_ranges_and_order_duplicates_for_marginal_row_ms(self):
        edl = {
            "brief": {"target_duration_s": 4, "tolerance_s": 1},
            "order": ["q", "f", "f"],
            "rows": [
                {"id": "q", "checked": True, "role": "quote", "locked": True},
                {"id": "f", "checked": True, "role": "filler", "locked": False},
                {"id": "x", "checked": False, "role": "support", "locked": False},
            ],
        }
        report = budget_report(edl, plan_builder=_plan_builder({"q": 2000, "f": 1500}))

        self.assertEqual(report["target_s"], 4)
        self.assertEqual(report["tolerance_s"], 1)
        self.assertEqual(report["estimated_ms"], 5000)
        self.assertEqual(report["delta_ms"], 1000)
        rows = {row["id"]: row for row in report["rows"]}
        self.assertEqual(rows["q"]["ms"], 2000)
        self.assertEqual(rows["f"]["ms"], 3000)
        self.assertEqual(rows["x"]["ms"], 0)

    def test_report_without_target_and_empty_selection(self):
        report = budget_report(
            {"rows": [{"id": "s1", "checked": False}], "order": []},
            plan_builder=_plan_builder({"s1": 1000}),
        )
        self.assertIsNone(report["target_s"])
        self.assertEqual(report["estimated_ms"], 0)
        self.assertIsNone(report["delta_ms"])

    def test_fit_strict_respects_quote_and_locked_and_reports_infeasible_gap(self):
        edl = {
            "brief": {"target_duration_s": 5, "tolerance_s": 0},
            "rows": [
                {"id": "quote", "checked": True, "role": "quote", "locked": False},
                {"id": "locked", "checked": True, "role": "filler", "locked": True},
                {"id": "filler", "checked": True, "role": "filler", "locked": False},
                {"id": "support", "checked": True, "role": "support", "locked": False},
                {"id": "background", "checked": True, "role": "background", "locked": False},
                {"id": "claim", "checked": True, "role": "claim", "locked": False},
            ],
            "order": [],
        }
        durations = {
            "quote": 4000,
            "locked": 3000,
            "filler": 1000,
            "support": 2000,
            "background": 2000,
            "claim": 2000,
        }
        result = fit_budget(
            edl,
            strategy="strict",
            plan_builder=_plan_builder(durations),
        )

        self.assertEqual(
            [item["id"] for item in result["suggestions"]],
            ["filler", "support", "background", "claim"],
        )
        self.assertNotIn("quote", [item["id"] for item in result["suggestions"]])
        self.assertNotIn("locked", [item["id"] for item in result["suggestions"]])
        self.assertEqual(result["projected_ms"], 7000)
        self.assertTrue(result["infeasible"])
        self.assertEqual(result["gap_ms"], 2000)

    def test_fit_complete_only_removes_filler_and_support_and_does_not_mutate_edl(self):
        edl = {
            "brief": {"target_duration_s": 2, "tolerance_s": 0},
            "rows": [
                {"id": "f", "checked": True, "role": "filler"},
                {"id": "s", "checked": True, "role": "support"},
                {"id": "b", "checked": True, "role": "background"},
                {"id": "c", "checked": True, "role": "claim"},
            ],
            "order": [],
        }
        before = copy.deepcopy(edl)
        result = fit_budget(
            edl,
            strategy="complete",
            plan_builder=_plan_builder({"f": 1000, "s": 1000, "b": 2000, "c": 2000}),
        )
        self.assertEqual([item["id"] for item in result["suggestions"]], ["f", "s"])
        self.assertEqual(result["projected_ms"], 4000)
        self.assertEqual(result["overage_ms"], 2000)
        self.assertEqual(edl, before)

    def test_fit_keep_quotes_preserves_quote_and_claim(self):
        edl = {
            "brief": {"target_duration_s": 3, "tolerance_s": 0},
            "rows": [
                {"id": "q", "checked": True, "role": "quote"},
                {"id": "c", "checked": True, "role": "claim"},
                {"id": "f", "checked": True, "role": "filler"},
                {"id": "s", "checked": True, "role": "support"},
            ],
            "order": [],
        }
        result = fit_budget(
            edl,
            strategy="keep_quotes",
            plan_builder=_plan_builder({"q": 2000, "c": 2000, "f": 1000, "s": 1000}),
        )
        ids = [item["id"] for item in result["suggestions"]]
        self.assertEqual(ids, ["f", "s"])
        self.assertNotIn("q", ids)
        self.assertNotIn("c", ids)
        self.assertEqual(result["projected_ms"], 4000)

    def test_fit_keep_quotes_chooses_deletion_closest_to_target(self):
        edl = {
            "brief": {"target_duration_s": 4, "tolerance_s": 0},
            "rows": [
                {"id": "q", "checked": True, "role": "quote"},
                {"id": "f", "checked": True, "role": "filler"},
                {"id": "b", "checked": True, "role": "background"},
            ],
            "order": [],
        }
        result = fit_budget(
            edl,
            strategy="keep_quotes",
            plan_builder=_plan_builder({"q": 2000, "f": 1000, "b": 4000}),
        )
        self.assertEqual(
            [item["id"] for item in result["suggestions"]],
            ["b"],
        )
        self.assertEqual(result["projected_ms"], 3000)

    def test_fit_keep_quotes_uses_target_not_tolerance_ceiling_for_closest_choice(self):
        edl = {
            "brief": {"target_duration_s": 4, "tolerance_s": 2},
            "rows": [
                {"id": "q", "checked": True, "role": "quote"},
                {"id": "small", "checked": True, "role": "filler"},
                {"id": "exact", "checked": True, "role": "background"},
            ],
            "order": [],
        }
        result = fit_budget(
            edl,
            strategy="keep_quotes",
            plan_builder=_plan_builder({"q": 4000, "small": 1000, "exact": 3000}),
        )
        self.assertEqual(
            [item["id"] for item in result["suggestions"]],
            ["exact"],
        )
        self.assertEqual(result["projected_ms"], 5000)

    def test_update_brief_partially_merges_allowed_fields_and_validates_types(self):
        updated = update_brief(
            {"claim": "旧", "background": ["旧背景"], "target_duration_s": 60},
            {"claim": "新", "tolerance_s": 8, "must_keep": ["金句"]},
        )
        self.assertEqual(
            updated,
            {
                "claim": "新",
                "background": ["旧背景"],
                "target_duration_s": 60,
                "tolerance_s": 8,
                "must_keep": ["金句"],
            },
        )
        with self.assertRaisesRegex(ValueError, "target_duration_s"):
            update_brief(updated, {"target_duration_s": True})
        with self.assertRaisesRegex(ValueError, "未知 brief"):
            update_brief(updated, {"extra": "x"})


class ChecklistPlanningTests(unittest.TestCase):
    def test_checklist_skips_missing_map_and_target_and_passes_normalized_background(self):
        edl = {
            "brief": {"background": ["游戏 设计营"]},
            "rows": [
                {
                    "id": "s1",
                    "checked": True,
                    "text": "我们在「游戏设计营」里做实验。",
                    "role": "quote",
                    "locked": True,
                }
            ],
            "order": [],
        }
        result = build_export_checklist(
            edl,
            transcript_segments=[{"id": "s1", "text": "原文"}],
            content_map=None,
            budget={"target_s": None, "estimated_ms": 1000, "tolerance_s": 0},
        )
        items = {item["key"]: item for item in result["items"]}
        self.assertIsNone(items["topics_confirmed"]["ok"])
        self.assertIsNone(items["duration"]["ok"])
        self.assertTrue(items["quotes_locked"]["ok"])
        self.assertTrue(items["background_covered"]["ok"])
        self.assertTrue(result["ok"])

    def test_checklist_respects_order_flags_duration_unlocked_quote_and_missing_background(self):
        edl = {
            "brief": {"background": ["案例甲"], "target_duration_s": 2, "tolerance_s": 0},
            "rows": [
                {"id": "s1", "checked": True, "text": "案例甲", "role": "support"},
                {"id": "s2", "checked": False, "text": "另一句", "role": "quote", "locked": False},
            ],
            "order": ["s2"],
        }
        result = build_export_checklist(
            edl,
            transcript_segments=[
                {"id": "s1", "text": "案例甲"},
                {"id": "s2", "text": "另一句"},
            ],
            content_map={"status": "draft"},
            budget={"target_s": 2, "tolerance_s": 0, "estimated_ms": 3000},
        )
        items = {item["key"]: item for item in result["items"]}
        self.assertFalse(items["topics_confirmed"]["ok"])
        self.assertFalse(items["duration"]["ok"])
        self.assertFalse(items["quotes_locked"]["ok"])
        self.assertFalse(items["background_covered"]["ok"])
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
