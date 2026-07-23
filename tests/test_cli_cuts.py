from __future__ import annotations

import contextlib
import io
import json
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.cli import _build_parser, main
from cutpoint_lab.studio.workspace import Workspace


class CutCliTests(unittest.TestCase):
    def test_relevant_commands_accept_cut_argument(self):
        parser = _build_parser()
        cases = [
            (["select", "project", "--cut", "draft-1"], "draft-1"),
            (["review", "project", "--cut", "draft-1"], "draft-1"),
            (["export", "project", "--cut", "draft-1"], "draft-1"),
            (["run", "video.mp4", "--cut", "draft-1"], "draft-1"),
            (["check", "project", "--cut", "draft-1"], "draft-1"),
            (["fix", "project", "--dict-only", "--cut", "draft-1"], "draft-1"),
            (["reference", "project", "ref.srt", "--cut", "draft-1"], "draft-1"),
            (["undo", "project", "change", "--cut", "draft-1"], "draft-1"),
        ]
        for argv, expected in cases:
            with self.subTest(argv=argv):
                self.assertEqual(parser.parse_args(argv).cut, expected)

    def test_cuts_create_and_list_are_available_through_cli(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace(root / "workspace")
            project = workspace.create_project("cli", source_path=root / "source.mp4", imported_via="test")

            output = io.StringIO()
            with contextlib.redirect_stdout(output):
                code = main(
                    [
                        "cuts",
                        project.id,
                        "--create",
                        "draft-1",
                        "--workspace",
                        str(workspace.root),
                        "--json",
                    ]
                )
            self.assertEqual(code, 0)
            created = json.loads(output.getvalue())
            self.assertTrue(created["ok"])
            self.assertEqual(
                [item["name"] for item in created["results"][0]["cuts"]],
                ["default", "draft-1"],
            )


if __name__ == "__main__":
    unittest.main()
