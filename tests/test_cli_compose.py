from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.cli import _build_parser, main
from cutpoint_lab.engine import compose
from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.workspace import Workspace


def _transcript(source: Path) -> dict:
    return {
        "source_video": str(source),
        "duration_ms": 600,
        "selected_segment_ids": ["s1", "s2"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 250,
                "text": "第一句",
                "tokens": [{"text": "第一句", "start_ms": 0, "end_ms": 250}],
            },
            {
                "id": "s2",
                "start_ms": 300,
                "end_ms": 550,
                "text": "第二句",
                "tokens": [{"text": "第二句", "start_ms": 300, "end_ms": 550}],
            },
        ],
    }


class ComposeCliTests(unittest.TestCase):
    def test_parser_requires_named_cut_and_engine_reexports_compose(self):
        args = _build_parser().parse_args(
            ["compose", "project", "script.md", "--cut", "external"]
        )
        self.assertEqual(args.cut, "external")
        self.assertTrue(callable(compose))

    def test_json_compose_creates_cut_and_report_from_fake_project_data(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace(root / "workspace")
            source = root / "source.mp4"
            project = workspace.create_project(
                "cli-compose", source_path=source, imported_via="test"
            )
            write_json(project.transcript_path, _transcript(source))
            script = root / "script.md"
            script.write_text("第二句\n第一句", encoding="utf-8")

            stdout = io.StringIO()
            stderr = io.StringIO()
            with contextlib.redirect_stdout(stdout), contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "compose",
                        project.id,
                        str(script),
                        "--cut",
                        "external",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            manifest = json.loads(stdout.getvalue())
            result = manifest["results"][0]
            self.assertTrue(manifest["ok"])
            self.assertEqual(result["cut"]["name"], "external")
            self.assertEqual(project.read_edl("external")["order"], ["s2", "s1"])
            self.assertEqual(
                read_json(project.cut_dir("external") / "compose_report.json"),
                result["report"],
            )
            self.assertEqual(stderr.getvalue(), "")

    def test_text_output_summarizes_counts_and_lists_unmatched_paragraphs(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace(root / "workspace")
            project = workspace.create_project(
                "cli-compose", source_path=root / "source.mp4", imported_via="test"
            )
            write_json(project.transcript_path, _transcript(root / "source.mp4"))
            script = root / "script.txt"
            script.write_text("第二句\n完全不存在", encoding="utf-8")

            stderr = io.StringIO()
            with contextlib.redirect_stderr(stderr):
                code = main(
                    [
                        "compose",
                        project.id,
                        str(script),
                        "--cut",
                        "summary",
                        "--workspace",
                        str(workspace.root),
                    ]
                )

            self.assertEqual(code, 0)
            rendered = stderr.getvalue()
            self.assertIn("auto=1", rendered)
            self.assertIn("ai=0", rendered)
            self.assertIn("unmatched=1", rendered)
            self.assertIn("完全不存在", rendered)


if __name__ == "__main__":
    unittest.main()
