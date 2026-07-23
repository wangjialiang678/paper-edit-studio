from __future__ import annotations

import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote

from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.server import StudioApplication, bind_server
from cutpoint_lab.studio.workspace import Workspace


def _transcript(source: Path) -> dict:
    return {
        "source_video": str(source),
        "duration_ms": 1200,
        "selected_segment_ids": ["s1", "s2", "s3"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 300,
                "text": "第一句",
                "tokens": [{"text": "第一句", "start_ms": 10, "end_ms": 280}],
            },
            {
                "id": "s2",
                "start_ms": 400,
                "end_ms": 700,
                "text": "第二句",
                "tokens": [{"text": "第二句", "start_ms": 420, "end_ms": 680}],
            },
            {
                "id": "s3",
                "start_ms": 800,
                "end_ms": 1100,
                "text": "第三句",
                "tokens": [{"text": "第三句", "start_ms": 820, "end_ms": 1080}],
            },
        ],
    }


def _rows() -> list[dict]:
    return [
        {"id": "s1", "checked": True, "text": "第一句"},
        {"id": "s2", "checked": True, "text": "第二句"},
        {"id": "s3", "checked": False, "text": "第三句"},
    ]


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None) -> tuple[int, dict]:
    data = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(url, data=data, method=method)
    if data is not None:
        request.add_header("Content-Type", "application/json")
    with urllib.request.urlopen(request, timeout=3) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


class ProjectEdlStorageTests(unittest.TestCase):
    def _project(self, root: Path):
        source = root / "source.mp4"
        return Workspace(root / "workspace").create_project(
            "edl", source_path=source, imported_via="test"
        )

    def test_legacy_selection_read_then_first_write_migrates_and_backs_up(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            legacy = {"rows": _rows(), "groups": [{"segment_ids": ["s2", "s1", "s2"]}]}
            write_json(project.dir / "selection.json", legacy)

            edl = project.read_edl("default")
            self.assertEqual(edl["order"], ["s2", "s1", "s2"])
            self.assertTrue((project.dir / "selection.json").is_file())

            edl["label"] = "默认成片"
            project.write_edl("default", edl)

            saved = read_json(project.cut_dir("default") / "edl.json")
            self.assertEqual(saved["label"], "默认成片")
            self.assertEqual(saved["order"], ["s2", "s1", "s2"])
            self.assertNotIn("groups", saved)
            self.assertFalse((project.dir / "selection.json").exists())
            backups = list(project.dir.glob("selection.json.bak-*"))
            self.assertEqual(len(backups), 1)
            self.assertEqual(read_json(backups[0]), legacy)

    def test_new_edl_wins_over_legacy_and_cut_crud_isolated(self):
        with tempfile.TemporaryDirectory() as tmp:
            project = self._project(Path(tmp))
            write_json(project.dir / "selection.json", {"rows": _rows(), "label": "旧"})
            project.create_cut("topic-a", "主题 A", {"rows": _rows(), "order": ["s2", "s1"]})
            project.write_edl("default", {"rows": _rows(), "label": "新默认"})

            self.assertEqual(project.read_edl("default")["label"], "新默认")
            self.assertEqual(project.read_edl("topic-a")["order"], ["s2", "s1"])
            self.assertTrue((project.cut_dir("topic-a") / "exports").is_dir())
            self.assertEqual([item["name"] for item in project.list_cuts()], ["default", "topic-a"])
            with self.assertRaisesRegex(ValueError, "default"):
                project.delete_cut("default")
            project.delete_cut("topic-a")
            self.assertFalse(project.cut_dir("topic-a").exists())
            with self.assertRaisesRegex(ValueError, "cut"):
                project.cut_dir("Bad Name")


class CutApplicationTests(unittest.TestCase):
    def _app_and_project(self, root: Path):
        source = root / "source.mp4"
        workspace = Workspace(root / "workspace")
        app = StudioApplication(workspace, auto_ai=False)
        project = workspace.create_project("cuts", source_path=source, imported_via="test")
        write_json(project.transcript_path, _transcript(source))
        return app, project

    def test_save_plan_converts_groups_to_repeated_order_and_keeps_artifacts_in_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, project = self._app_and_project(Path(tmp))
            project.create_cut("remix", "混剪", {"rows": _rows()})

            result = app.save_plan(
                project,
                {
                    "rows": _rows(),
                    "strategy": "token_padding",
                    "groups": [
                        {"purpose": "hook", "segment_ids": ["s2"]},
                        {"purpose": "echo", "segment_ids": ["s1", "s2"]},
                    ],
                },
                cut="remix",
            )

            self.assertTrue(result["plan"]["ordered"])
            self.assertEqual(
                [
                    source_id
                    for item in result["plan"]["ranges"]
                    for source_id in item["source_segment_ids"]
                ],
                ["s2", "s1", "s2"],
            )
            self.assertEqual(
                project.read_edl("remix")["order"], ["s2", "s1", "s2"]
            )
            self.assertNotIn("groups", project.read_edl("remix"))
            self.assertTrue((project.cut_dir("remix") / "clip_plan.json").is_file())
            self.assertNotIn(
                "groups", read_json(project.cut_dir("remix") / "clip_plan.json")
            )
            self.assertFalse((project.dir / "clip_plan.json").exists())
            editor = app.editor_state(project, cut="remix")
            self.assertEqual(editor["order"], ["s2", "s1", "s2"])

            explicit = app.save_plan(
                project,
                {
                    "rows": _rows(),
                    "strategy": "token_padding",
                    "order": ["s1", "s2", "s1"],
                },
                cut="remix",
            )
            self.assertEqual(
                [
                    source_id
                    for item in explicit["plan"]["ranges"]
                    for source_id in item["source_segment_ids"]
                ],
                ["s1", "s2", "s1"],
            )
            self.assertEqual(project.read_edl("remix")["order"], ["s1", "s2", "s1"])

    def test_topic_cut_preselects_latest_topic_suggestion_best_clip(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, project = self._app_and_project(Path(tmp))
            write_json(
                project.ai_dir / "topic_slicing-20260723-010101.json",
                {
                    "topics": [
                        {
                            "topic_id": "topic_01",
                            "title": "核心主题",
                            "best_clip": {"segment_ids": ["s2", "s3"]},
                        }
                    ]
                },
            )

            created = app.create_cut(
                project,
                {"name": "topic-a", "label": "主题 A", "from": "topic:topic_01"},
            )

            self.assertEqual(created["name"], "topic-a")
            edl = project.read_edl("topic-a")
            self.assertEqual(edl["order"], [])
            self.assertEqual(
                {row["id"] for row in edl["rows"] if row["checked"]}, {"s2", "s3"}
            )

    def test_save_plan_roundtrips_valid_role_and_locked_to_editor_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, project = self._app_and_project(Path(tmp))
            rows = _rows()
            rows[0]["role"] = "quote"
            rows[0]["locked"] = True
            rows[1]["role"] = "support"
            rows[1]["locked"] = False

            app.save_plan(
                project,
                {"rows": rows, "strategy": "token_padding"},
            )

            saved = {row["id"]: row for row in project.read_edl("default")["rows"]}
            self.assertEqual(saved["s1"]["role"], "quote")
            self.assertIs(saved["s1"]["locked"], True)
            editor = {row["id"]: row for row in app.editor_state(project)["rows"]}
            self.assertEqual(editor["s1"]["role"], "quote")
            self.assertIs(editor["s1"]["locked"], True)
            self.assertEqual(editor["s2"]["role"], "support")
            self.assertIs(editor["s2"]["locked"], False)

    def test_save_plan_discards_invalid_role_and_non_boolean_locked(self):
        with tempfile.TemporaryDirectory() as tmp:
            app, project = self._app_and_project(Path(tmp))
            rows = _rows()
            rows[0]["role"] = "star"
            rows[0]["locked"] = 1
            rows[1]["role"] = "background"
            rows[1]["locked"] = "true"

            app.save_plan(
                project,
                {"rows": rows, "strategy": "token_padding"},
            )

            saved = {row["id"]: row for row in project.read_edl("default")["rows"]}
            self.assertNotIn("role", saved["s1"])
            self.assertNotIn("locked", saved["s1"])
            self.assertEqual(saved["s2"]["role"], "background")
            self.assertNotIn("locked", saved["s2"])


class CutHttpTests(unittest.TestCase):
    def test_crud_endpoints_and_default_protection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            app = StudioApplication(Workspace(root / "workspace"), auto_ai=False)
            project = app.workspace.create_project("http", source_path=source, imported_via="test")
            write_json(project.transcript_path, _transcript(source))
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                status, listed = _request_json(f"{base}/cuts")
                self.assertEqual(status, 200)
                self.assertEqual([item["name"] for item in listed["cuts"]], ["default"])
                status, created = _request_json(
                    f"{base}/cuts",
                    method="POST",
                    payload={"name": "draft-1", "label": "草稿", "from": "blank"},
                )
                self.assertEqual(status, 200)
                self.assertEqual(created["name"], "draft-1")
                self.assertEqual(_request_json(f"{base}/cuts/draft-1", method="DELETE")[0], 200)
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(f"{base}/cuts/default", method="DELETE")
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == "__main__":
    unittest.main()
