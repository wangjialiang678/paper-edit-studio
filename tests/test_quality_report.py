from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.quality.report import (
    empty_report,
    load_report,
    merge_report,
    save_report,
)


def _issue(
    *,
    segment_id: str = "sentence_0001",
    kind: str = "low_confidence",
    text: str = "超导",
    status: str = "open",
) -> dict:
    return {
        "id": "incoming-id",
        "segment_id": segment_id,
        "kind": kind,
        "span": {"text": text, "token_start": 1, "token_end": 2},
        "confidence": 0.42,
        "reason": "低于阈值",
        "source": "confidence",
        "status": status,
    }


class QualityReportTests(unittest.TestCase):
    def test_merge_preserves_matching_id_and_terminal_status(self):
        old_issue = _issue(status="ignored")
        old_issue["id"] = "stable-id"
        old = {
            "generated_at": "yesterday",
            "issues": [old_issue],
            "stats": {"low_confidence": 1},
            "meta": {"ai_changeset_id": "change-1"},
        }
        refreshed = _issue()
        refreshed["reason"] = "重新扫描后的原因"
        added = _issue(
            segment_id="sentence_0002",
            kind="ref_mismatch",
            text="参考原文",
        )
        added["source"] = "reference"

        report = merge_report(old, [refreshed, added])

        self.assertEqual(report["issues"][0]["id"], "stable-id")
        self.assertEqual(report["issues"][0]["status"], "ignored")
        self.assertEqual(report["issues"][0]["reason"], "重新扫描后的原因")
        self.assertNotEqual(report["issues"][1]["id"], "incoming-id")
        self.assertEqual(report["issues"][1]["status"], "open")
        self.assertEqual(
            report["stats"],
            {"low_confidence": 1, "ref_mismatch": 1},
        )
        self.assertEqual(report["meta"], {"ai_changeset_id": "change-1"})
        self.assertNotEqual(report["generated_at"], "yesterday")

    def test_merge_does_not_reuse_id_when_span_text_changes(self):
        old_issue = _issue(text="超导", status="resolved")
        old_issue["id"] = "old-id"

        report = merge_report(
            {
                "generated_at": "old",
                "issues": [old_issue],
                "stats": {},
                "meta": {},
            },
            [_issue(text="超跑")],
        )

        self.assertNotEqual(report["issues"][0]["id"], "old-id")
        self.assertEqual(report["issues"][0]["status"], "open")

    def test_duplicate_matching_spans_keep_distinct_ids_one_to_one(self):
        first = _issue(status="ignored")
        first["id"] = "first-id"
        second = _issue(status="resolved")
        second["id"] = "second-id"
        old = {
            "generated_at": "old",
            "issues": [first, second],
            "stats": {"low_confidence": 2},
            "meta": {},
        }

        report = merge_report(old, [_issue(), _issue()])

        self.assertEqual(
            [(item["id"], item["status"]) for item in report["issues"]],
            [("first-id", "ignored"), ("second-id", "resolved")],
        )

    def test_duplicate_span_prefers_matching_token_position(self):
        first = _issue(status="ignored")
        first["id"] = "first-id"
        second = _issue(status="resolved")
        second["id"] = "second-id"
        second["span"]["token_start"] = 5
        second["span"]["token_end"] = 6
        remaining = _issue()
        remaining["span"]["token_start"] = 5
        remaining["span"]["token_end"] = 6

        report = merge_report(
            {
                "generated_at": "old",
                "issues": [first, second],
                "stats": {"low_confidence": 2},
                "meta": {},
            },
            [remaining],
        )

        self.assertEqual(report["issues"][0]["id"], "second-id")
        self.assertEqual(report["issues"][0]["status"], "resolved")

    def test_missing_report_is_empty_and_save_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workspace" / "project"

            self.assertEqual(load_report(project_dir), empty_report())

            report = merge_report(empty_report(), [_issue()])
            output = save_report(project_dir, report)

            self.assertEqual(output, project_dir / "quality_report.json")
            self.assertEqual(load_report(project_dir), report)


if __name__ == "__main__":
    unittest.main()
