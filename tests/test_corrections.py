from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.quality import (
    CorrectionSet,
    apply_corrections,
    load_changeset,
    preview_corrections,
    save_changeset,
    undo_changeset,
)


def _correction_set(
    wrong: str = "web coding",
    right: str = "vibe coding",
    *,
    is_term: bool = True,
) -> CorrectionSet:
    corrections = CorrectionSet()
    corrections.add_pair(wrong, right, is_term=is_term)
    return corrections


class CorrectionSetPersistenceTests(unittest.TestCase):
    def test_load_missing_file_returns_empty_set(self):
        with tempfile.TemporaryDirectory() as tmp:
            missing = Path(tmp) / "workspace" / "_settings" / "corrections.json"
            loaded = CorrectionSet.load(missing)
            roundtrip = Path(tmp) / "roundtrip.json"

            loaded.save(roundtrip)

            self.assertEqual(json.loads(roundtrip.read_text(encoding="utf-8")), {"pairs": []})

    def test_add_pair_deduplicates_wrong_and_merges_aliases_for_same_right(self):
        with tempfile.TemporaryDirectory() as tmp:
            corrections = CorrectionSet()
            corrections.add_pair("web coding", "vibe coding", is_term=True)
            corrections.add_pair("web coding", "vibe coding", is_term=True)
            corrections.add_pair("web courting", "vibe coding", is_term=True)
            path = Path(tmp) / "workspace" / "_settings" / "corrections.json"

            corrections.save(path)

            self.assertEqual(
                json.loads(path.read_text(encoding="utf-8")),
                {
                    "pairs": [
                        {
                            "wrong": ["web coding", "web courting"],
                            "right": "vibe coding",
                            "is_term": True,
                        }
                    ]
                },
            )

    def test_save_creates_parent_directories_and_load_roundtrips(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "workspace" / "_settings" / "corrections.json"
            corrections = _correction_set("超导", "超脑")

            corrections.save(path)
            loaded = CorrectionSet.load(path)
            copied_path = Path(tmp) / "copy" / "corrections.json"
            loaded.save(copied_path)

            self.assertTrue(path.is_file())
            self.assertEqual(
                json.loads(copied_path.read_text(encoding="utf-8")),
                {
                    "pairs": [
                        {"wrong": ["超导"], "right": "超脑", "is_term": True}
                    ]
                },
            )

    def test_load_rejects_malformed_dictionary_structure(self):
        invalid_payloads = [
            [],
            {},
            {"pairs": "not-a-list"},
            {"pairs": [{}]},
            {"pairs": [{"wrong": [], "right": "vibe coding", "is_term": True}]},
            {
                "pairs": [
                    {"wrong": ["web coding", 7], "right": "vibe coding", "is_term": True}
                ]
            },
            {"pairs": [{"wrong": ["web coding"], "right": "", "is_term": True}]},
            {
                "pairs": [
                    {"wrong": ["web coding"], "right": "vibe coding", "is_term": 1}
                ]
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "corrections.json"
            for payload in invalid_payloads:
                with self.subTest(payload=payload):
                    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")
                    with self.assertRaises(ValueError):
                        CorrectionSet.load(path)


class PreviewCorrectionsTests(unittest.TestCase):
    def test_preview_counts_case_insensitive_ascii_literal_matches_per_segment(self):
        text = "WEB CODING 和 Web Coding 都需要修正。"
        rows = [
            {"id": "sentence_0001", "text": text, "checked": True},
            {"id": "sentence_0002", "text": "这里没有命中。", "checked": False},
        ]

        preview = preview_corrections(rows, _correction_set())

        self.assertEqual(
            preview,
            [
                {
                    "segment_id": "sentence_0001",
                    "wrong": "web coding",
                    "right": "vibe coding",
                    "count": 2,
                    "context": text,
                }
            ],
        )

    def test_preview_nfkc_normalizes_full_width_text_and_treats_wrong_as_literal(self):
        rows = [
            {
                "id": "sentence_0001",
                "text": "这里是ＷＥＢ　ＣＯＤＩＮＧ，不是 webXcoding。",
            },
            {"id": "sentence_0002", "text": "C++ 和 Cx 是不同的。"},
        ]
        corrections = _correction_set()
        corrections.add_pair("C++", "C plus plus", is_term=False)

        preview = preview_corrections(rows, corrections)

        self.assertEqual(
            [(item["segment_id"], item["wrong"], item["count"]) for item in preview],
            [
                ("sentence_0001", "web coding", 1),
                ("sentence_0002", "C++", 1),
            ],
        )
        self.assertEqual(preview[0]["context"], rows[0]["text"])
        self.assertEqual(preview[1]["context"], rows[1]["text"])


class ApplyAndUndoCorrectionsTests(unittest.TestCase):
    def test_apply_uses_original_text_once_without_cascading_rules(self):
        corrections = CorrectionSet()
        corrections.add_pair("超导", "超脑")
        corrections.add_pair("超脑", "超人")

        new_rows, changeset = apply_corrections(
            [{"id": "sentence_0001", "text": "超导和超脑"}],
            corrections,
        )

        self.assertEqual(new_rows[0]["text"], "超脑和超人")
        self.assertEqual(changeset["label"], "纠错词典 2 处")

    def test_apply_only_changes_row_text_and_records_one_change_per_touched_row(self):
        rows = [
            {
                "id": "sentence_0001",
                "text": "WEB CODING 与 web coding",
                "checked": True,
                "tokens": [
                    {"text": "WEB CODING", "start_ms": 0, "end_ms": 300}
                ],
            },
            {
                "id": "sentence_0002",
                "text": "ＷＥＢ　ＣＯＤＩＮＧ 也是错词",
                "checked": False,
                "start_ms": 400,
                "end_ms": 800,
            },
            {"id": "sentence_0003", "text": "无关文本", "checked": True},
        ]
        original_rows = json.loads(json.dumps(rows, ensure_ascii=False))

        new_rows, changeset = apply_corrections(rows, _correction_set())

        self.assertEqual(new_rows[0]["text"], "vibe coding 与 vibe coding")
        self.assertEqual(new_rows[1]["text"], "vibe coding 也是错词")
        self.assertEqual(new_rows[2], rows[2])
        self.assertEqual(rows, original_rows, "apply_corrections must not mutate caller rows")
        for before, after in zip(rows, new_rows):
            self.assertEqual(
                {key: value for key, value in after.items() if key != "text"},
                {key: value for key, value in before.items() if key != "text"},
            )

        self.assertEqual(changeset["label"], "纠错词典 3 处")
        self.assertEqual(
            changeset["changes"],
            [
                {
                    "segment_id": "sentence_0001",
                    "field": "text",
                    "old": "WEB CODING 与 web coding",
                    "new": "vibe coding 与 vibe coding",
                },
                {
                    "segment_id": "sentence_0002",
                    "field": "text",
                    "old": "ＷＥＢ　ＣＯＤＩＮＧ 也是错词",
                    "new": "vibe coding 也是错词",
                },
            ],
        )
        self.assertIsInstance(changeset["applied_at"], str)
        self.assertTrue(changeset["applied_at"])
        self.assertRegex(changeset["change_id"], r"^\d{8,}.*-[0-9a-f]{4,}$")

    def test_undo_roundtrips_an_applied_changeset(self):
        rows = [
            {"id": "sentence_0001", "text": "WEB CODING", "checked": True},
            {"id": "sentence_0002", "text": "不命中", "checked": False},
        ]
        applied_rows, changeset = apply_corrections(rows, _correction_set())

        restored_rows, report = undo_changeset(applied_rows, changeset)

        self.assertEqual(restored_rows, rows)
        self.assertEqual(report["reverted"], 1)
        self.assertEqual(report["skipped"], [])

    def test_undo_skips_and_reports_row_whose_current_text_no_longer_matches_new(self):
        rows = [{"id": "sentence_0001", "text": "web coding", "checked": True}]
        applied_rows, changeset = apply_corrections(rows, _correction_set())
        manually_edited = [{**applied_rows[0], "text": "用户后续手动编辑"}]

        restored_rows, report = undo_changeset(manually_edited, changeset)

        self.assertEqual(restored_rows, manually_edited)
        self.assertEqual(report["reverted"], 0)
        self.assertEqual(len(report["skipped"]), 1)
        self.assertEqual(report["skipped"][0]["segment_id"], "sentence_0001")


class ChangeSetPersistenceTests(unittest.TestCase):
    def test_save_and_load_changeset_use_project_changesets_directory(self):
        with tempfile.TemporaryDirectory() as tmp:
            project_dir = Path(tmp) / "workspace" / "project-1"
            _, changeset = apply_corrections(
                [{"id": "sentence_0001", "text": "web coding"}],
                _correction_set(),
            )

            saved_path = save_changeset(project_dir, changeset)

            expected_path = project_dir / "changesets" / f"{changeset['change_id']}.json"
            self.assertEqual(saved_path, expected_path)
            self.assertTrue(expected_path.is_file())
            self.assertEqual(load_changeset(project_dir, changeset["change_id"]), changeset)


if __name__ == "__main__":
    unittest.main()
