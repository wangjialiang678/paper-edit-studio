from __future__ import annotations

import tempfile
import time
import unittest
from pathlib import Path

from cutpoint_lab.io import write_json
from cutpoint_lab.studio.config import EnvStore
from cutpoint_lab.studio.server import ROUTES, StudioApplication
from cutpoint_lab.studio.workspace import Workspace


class _FillerSweepClient:
    def __init__(self, response=None, error: Exception | None = None):
        self.response = response
        self.error = error
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, **_kwargs):
        self.calls.append((system, user))
        if self.error is not None:
            raise self.error
        return self.response


class _Selector:
    def __init__(self, client: _FillerSweepClient):
        self.client = client

    def available(self) -> bool:
        return True


def _app(root: Path, client: _FillerSweepClient) -> StudioApplication:
    return StudioApplication(
        Workspace(root / "workspace"),
        selector=_Selector(client),
        auto_ai=False,
        env_store=EnvStore(root / ".env"),
    )


def _project(app: StudioApplication, root: Path):
    project = app.workspace.create_project(
        "filler-sweep",
        source_path=root / "source.mp4",
        imported_via="test",
    )
    write_json(
        project.transcript_path,
        {
            "source_video": str(root / "source.mp4"),
            "duration_ms": 5000,
            "selected_segment_ids": ["s1", "s2", "s3", "s4", "s5"],
            "segments": [
                {
                    "id": "s1",
                    "start_ms": 0,
                    "end_ms": 1000,
                    "text": "Open AI项目吧继续",
                    "tokens": [
                        {"text": "Open", "start_ms": 0, "end_ms": 100},
                        {"text": "AI", "start_ms": 120, "end_ms": 220},
                        {"text": "项目", "start_ms": 240, "end_ms": 400},
                        {"text": "吧", "start_ms": 420, "end_ms": 500},
                        {"text": "继续", "start_ms": 520, "end_ms": 800},
                    ],
                },
                {
                    "id": "s2",
                    "start_ms": 1000,
                    "end_ms": 2000,
                    "text": "整句删除",
                    "tokens": [
                        {"text": "整句", "start_ms": 1000, "end_ms": 1300},
                        {"text": "删除", "start_ms": 1320, "end_ms": 1700},
                    ],
                },
                {
                    "id": "s3",
                    "start_ms": 2000,
                    "end_ms": 3000,
                    "text": "甲乙丙丁",
                    "tokens": [
                        {"text": "甲", "start_ms": 2000, "end_ms": 2100},
                        {"text": "乙", "start_ms": 2120, "end_ms": 2220},
                        {"text": "丙", "start_ms": 2240, "end_ms": 2340},
                        {"text": "丁", "start_ms": 2360, "end_ms": 2460},
                    ],
                },
                {
                    "id": "s4",
                    "start_ms": 3000,
                    "end_ms": 4000,
                    "text": "无词级时间戳",
                    "tokens": [],
                },
                {
                    "id": "s5",
                    "start_ms": 4000,
                    "end_ms": 5000,
                    "text": "未勾选句子",
                    "tokens": [
                        {"text": "未勾选", "start_ms": 4000, "end_ms": 4300},
                        {"text": "句子", "start_ms": 4320, "end_ms": 4600},
                    ],
                },
            ],
        },
    )
    project.write_edl(
        "default",
        {
            "rows": [
                {"id": "s1", "checked": True, "text": "Open AI项目吧继续"},
                {"id": "s2", "checked": True, "text": "整句删除"},
                {"id": "s3", "checked": True, "text": "甲乙丙丁"},
                {"id": "s4", "checked": True, "text": "无词级时间戳"},
                {"id": "s5", "checked": False, "text": "未勾选句子"},
            ]
        },
    )
    return project


def _wait_result(project, timeout: float = 3.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        entry = (project.read_state().get("ai") or {}).get("filler_sweep") or {}
        if entry.get("status") in {"done", "error"}:
            return entry
        time.sleep(0.01)
    raise AssertionError(project.read_state())


class FillerSweepApiTests(unittest.TestCase):
    def _start(self, app: StudioApplication, project) -> dict:
        self.assertTrue(
            hasattr(app, "start_filler_sweep"),
            "filler_sweep 异步任务尚未实现",
        )
        return app.start_filler_sweep(project)

    def test_routes_and_ai_overview_expose_filler_sweep(self):
        get_route = ("GET", "/api/projects/{id}/filler-sweep/report")
        post_route = ("POST", "/api/projects/{id}/filler-sweep/analyze")
        self.assertIn(get_route, ROUTES)
        self.assertIn(post_route, ROUTES)
        self.assertEqual(ROUTES[get_route], "_route_filler_sweep_report")
        self.assertEqual(ROUTES[post_route], "_route_filler_sweep_analyze")

        with tempfile.TemporaryDirectory() as tmp:
            app = _app(Path(tmp), _FillerSweepClient({"cuts": []}))
            project = _project(app, Path(tmp))
            self.assertIn("filler_sweep", app.editor_state(project)["ai"]["modes"])

    def test_validates_and_places_filler_sweep_suggestions(self):
        client = _FillerSweepClient(
            {
                "cuts": [
                    {"segment_id": "s1", "span_text": "Open AI"},
                    {"segment_id": "unknown", "span_text": "不存在"},
                    {"segment_id": "s1", "span_text": "匹配失败"},
                    {"segment_id": "s2", "span_text": "整句删除"},
                    {"segment_id": "s3", "span_text": "甲"},
                    {"segment_id": "s3", "span_text": "乙"},
                    {"segment_id": "s3", "span_text": "丙"},
                ]
            }
        )
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root, client)
            project = _project(app, root)

            started = self._start(app, project)
            self.assertEqual(started["status"], "running")
            result = _wait_result(project)

            self.assertEqual(result["status"], "done", result)
            self.assertEqual(
                result["suggestions"],
                [
                    {
                        "segment_id": "s1",
                        "start_token": 0,
                        "end_token": 1,
                        "kind": "ai",
                        "text": "Open AI",
                    }
                ],
            )
            self.assertEqual(result["dropped"], 6)
            self.assertTrue(result["updated_at"])
            self.assertEqual(app.filler_sweep_report(project), result)

            self.assertEqual(len(client.calls), 1)
            system, user = client.calls[0]
            self.assertNotIn("{{USER_BRIEF}}", system)
            self.assertIn("[s1] Open AI项目吧继续", user)
            self.assertIn("[s2] 整句删除", user)
            self.assertIn("[s3] 甲乙丙丁", user)
            self.assertNotIn("[s4]", user)
            self.assertNotIn("[s5]", user)

    def test_llm_exception_sets_error_state(self):
        client = _FillerSweepClient(error=RuntimeError("LLM unavailable"))
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root, client)
            project = _project(app, root)

            self._start(app, project)
            result = _wait_result(project)

            self.assertEqual(result["status"], "error")
            self.assertIn("LLM unavailable", result["error"])


if __name__ == "__main__":
    unittest.main()
