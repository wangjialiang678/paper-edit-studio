from __future__ import annotations

import io
import json
import re
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

from cutpoint_lab.cli import main, run_review
from cutpoint_lab.io import load_transcript, write_json
from cutpoint_lab.paper_edit.review_html import render_review_html
from cutpoint_lab.studio.workspace import Workspace


def _transcript_payload(source_video: str = "source.mp4") -> dict:
    return {
        "source_video": source_video,
        "duration_ms": 3000,
        "selected_segment_ids": ["sentence_0001", "sentence_0002", "sentence_0003"],
        "segments": [
            {
                "id": "sentence_0001",
                "start_ms": 0,
                "end_ms": 900,
                "text": "先说一个核心观点。",
                "tokens": [
                    {"text": "先说", "start_ms": 50, "end_ms": 250},
                    {"text": "一个", "start_ms": 280, "end_ms": 430},
                    {"text": "核心观点。", "start_ms": 450, "end_ms": 850},
                ],
            },
            {
                "id": "sentence_0002",
                "start_ms": 1000,
                "end_ms": 2100,
                "text": "嗯这里需要修边。",
                "tokens": [
                    {"text": "嗯", "start_ms": 1020, "end_ms": 1130},
                    {"text": "这里需要", "start_ms": 1160, "end_ms": 1550},
                    {"text": "修边。", "start_ms": 1580, "end_ms": 2050},
                ],
            },
            {
                "id": "sentence_0003",
                "start_ms": 2200,
                "end_ms": 2900,
                "text": "缺少词级时间戳。",
                "tokens": [],
            },
        ],
    }


def _embedded_data(html: str) -> dict:
    match = re.search(
        r'<script id="review-data" type="application/json">(.*?)</script>',
        html,
        re.DOTALL,
    )
    if match is None:
        raise AssertionError("review data script not found")
    return json.loads(match.group(1))


class RenderReviewHtmlTests(unittest.TestCase):
    def test_renders_self_contained_rows_and_normalizes_initial_cuts(self):
        transcript = load_transcript(_transcript_payload())
        selection_rows = [
            {
                "id": "sentence_0001",
                "checked": True,
                "text": "先说一个核心观点。",
                "cuts": [{"start_token": 1, "end_token": 1}],
                "nudge": {"start_ms": 10, "end_ms": -20},
            },
            {
                "id": "sentence_0002",
                "checked": False,
                "text": "嗯这里需要修边。",
                "trim": {"start_token": 1, "end_token": 1},
            },
            {
                "id": "sentence_0003",
                "checked": True,
                "text": "安全</script>文本",
            },
        ]
        decisions = {
            "sentence_0001": {
                "keep": True,
                "reason": "开场核心观点",
                "labels": ["hook"],
            }
        }

        html = render_review_html(transcript, selection_rows, decisions)

        self.assertIn("<!doctype html>", html)
        self.assertIn('class="token', html)
        self.assertIn('"source":"review_html"', html)
        self.assertNotIn("https://", html)
        self.assertNotIn("安全</script>文本", html)
        self.assertIn(r"安全<\/script>文本", html)

        data = _embedded_data(html)
        self.assertEqual(data["source"], "review_html")
        self.assertEqual(len(data["rows"]), 3)
        self.assertEqual(
            data["rows"][0]["cuts"],
            [{"start_token": 1, "end_token": 1}],
        )
        self.assertEqual(
            data["rows"][1]["cuts"],
            [
                {"start_token": 0, "end_token": 0},
                {"start_token": 2, "end_token": 2},
            ],
        )
        self.assertNotIn("trim", data["rows"][1])
        self.assertEqual(
            data["rows"][0]["nudge"],
            {"start_ms": 10, "end_ms": -20},
        )
        self.assertEqual(data["rows"][0]["reason"], "开场核心观点")
        self.assertEqual(data["rows"][2]["tokens"], [])


class ReviewCliTests(unittest.TestCase):
    def test_run_review_writes_html_and_reads_ai_decisions(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            workspace = Workspace(root / "workspace")
            project = workspace.create_project(
                "CLI 确认",
                source_path=source,
                imported_via="path",
            )
            write_json(project.transcript_path, _transcript_payload(str(source)))
            write_json(
                project.dir / "selection.json",
                {
                    "rows": [
                        {
                            "id": "sentence_0001",
                            "checked": True,
                            "text": "改过的核心观点。",
                            "cuts": [{"start_token": 0, "end_token": 0}],
                        },
                        {
                            "id": "sentence_0002",
                            "checked": False,
                            "text": "嗯这里需要修边。",
                        },
                        {
                            "id": "sentence_0003",
                            "checked": True,
                            "text": "缺少词级时间戳。",
                        },
                    ]
                },
            )
            suggestion_path = project.ai_dir / "koubo.json"
            write_json(
                suggestion_path,
                {
                    "decisions": [
                        {
                            "segment_id": "sentence_0001",
                            "keep": True,
                            "reason": "值得保留",
                            "labels": ["insight"],
                        }
                    ]
                },
            )
            project.update_state(
                ai={
                    "koubo_tighten": {
                        "status": "done",
                        "file": str(suggestion_path),
                    }
                }
            )

            manifest = run_review(project)

            review_path = Path(manifest["outputs"]["review_html"])
            self.assertEqual(review_path, project.dir / "review.html")
            self.assertTrue(review_path.is_file())
            self.assertGreater(review_path.stat().st_size, 0)
            self.assertEqual(manifest["warnings"], [])
            self.assertIn("值得保留", review_path.read_text(encoding="utf-8"))

    def test_run_review_defaults_to_all_selected_when_selection_is_missing(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace(root / "workspace")
            project = workspace.create_project(
                "默认全选",
                source_path=root / "source.mp4",
                imported_via="path",
            )
            write_json(project.transcript_path, _transcript_payload())

            manifest = run_review(project, out_path=root / "custom-review.html")

            data = _embedded_data(
                Path(manifest["outputs"]["review_html"]).read_text(encoding="utf-8")
            )
            self.assertEqual([row["checked"] for row in data["rows"]], [True, True, True])

    def test_review_help_is_available(self):
        with redirect_stdout(io.StringIO()):
            with self.assertRaises(SystemExit) as caught:
                main(["review", "--help"])
        self.assertEqual(caught.exception.code, 0)

    def test_review_out_rejects_all_mode(self):
        with tempfile.TemporaryDirectory() as tmp:
            output = io.StringIO()
            with redirect_stdout(output):
                exit_code = main(
                    [
                        "review",
                        "--all",
                        "--out",
                        str(Path(tmp) / "review.html"),
                        "--workspace",
                        str(Path(tmp) / "workspace"),
                        "--json",
                    ]
                )

            self.assertEqual(exit_code, 1)
            manifest = json.loads(output.getvalue())
            self.assertIn("--out", manifest["results"][0]["error"])


if __name__ == "__main__":
    unittest.main()
