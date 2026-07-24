from __future__ import annotations

import json
import re
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import quote
from unittest.mock import patch

from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio import server as server_module
from cutpoint_lab.studio.config import EnvStore
from cutpoint_lab.studio.server import ROUTES, StudioApplication, bind_server
from cutpoint_lab.studio.workspace import Workspace


class _PlanningClient:
    def __init__(self, *, fail: bool = False):
        self.calls: list[tuple[str, str]] = []
        self.fail = fail

    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, **_kwargs):
        self.calls.append((system, user))
        if self.fail:
            raise RuntimeError("mock planning failure")
        if '"candidates"' in system:
            topic_match = re.search(r"## \[([^\]]+)\]", user)
            topic_id = topic_match.group(1) if topic_match else "t1"
            segment_ids = re.findall(r"\[(s\d+)\]", user)
            if "s2" in segment_ids:
                segment_ids = [
                    "s2",
                    *[item for item in segment_ids if item != "s2"],
                ]
            return {
                "candidates": [
                    {
                        "id": f"q{index}",
                        "topic_id": topic_id,
                        "segment_id": segment_id,
                        "type": "hook" if index == 1 else "claim",
                        "reason": "主张完整",
                    }
                    for index, segment_id in enumerate(segment_ids[:3], 1)
                ]
            }
        if '"drop"' in system:
            return {
                "summary": "全部保留",
                "drop": [],
            }
        return {
            "claims": [
                {
                    "id": "c1",
                    "text": "核心主张",
                    "segment_ids": ["s2"],
                    "reason": "可传播",
                }
            ],
            "backgrounds": [
                {
                    "id": "b1",
                    "text": "案例甲",
                    "segment_ids": ["s1"],
                    "kind": "case",
                }
            ],
            "topics": [
                {
                    "id": "t1",
                    "name": "核心主题",
                    "summary": "三句话",
                    "segment_ids": ["s1", "s2", "s3"],
                    "suggested_duration_s": 10,
                    "status": "confirmed",
                }
            ],
        }


class _PlanningSelector:
    def __init__(self, *, fail: bool = False):
        self.client = _PlanningClient(fail=fail)

    def available(self) -> bool:
        return self.client.available()


def _transcript(source: Path) -> dict:
    return {
        "source_video": str(source),
        "duration_ms": 5000,
        "selected_segment_ids": ["s1", "s2", "s3"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 1000,
                "text": "案例甲发生了",
                "tokens": [{"text": "案例甲发生了", "start_ms": 100, "end_ms": 800}],
            },
            {
                "id": "s2",
                "start_ms": 1500,
                "end_ms": 3000,
                "text": "这是核心主张",
                "tokens": [
                    {"text": "这是", "start_ms": 1600, "end_ms": 1900},
                    {"text": "核心主张", "start_ms": 2100, "end_ms": 2800},
                ],
            },
            {
                "id": "s3",
                "start_ms": 3500,
                "end_ms": 4500,
                "text": "最后行动",
                "tokens": [{"text": "最后行动", "start_ms": 3600, "end_ms": 4300}],
            },
        ],
    }


def _rows() -> list[dict]:
    return [
        {
            "id": "s1",
            "checked": True,
            "text": "案例甲发生了",
            "role": "background",
        },
        {
            "id": "s2",
            "checked": True,
            "text": "这是核心主张",
            "role": "claim",
        },
        {
            "id": "s3",
            "checked": True,
            "text": "最后行动",
            "role": "filler",
        },
    ]


def _request_json(url: str, *, method: str = "GET", payload: object | None = None):
    body = None if payload is None else json.dumps(payload, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _wait_state(project, key: str, timeout: float = 5) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = project.read_state().get(key) or {}
        if state.get("status") in {"done", "error"}:
            return state
        time.sleep(0.01)
    raise AssertionError(project.read_state())


class PlanningApiTests(unittest.TestCase):
    def _app_project(self, root: Path, *, fail: bool = False):
        source = root / "source.mp4"
        selector = _PlanningSelector(fail=fail)
        app = StudioApplication(
            Workspace(root / "workspace"),
            selector=selector,
            auto_ai=False,
            env_store=EnvStore(root / ".env"),
        )
        project = app.workspace.create_project(
            "planning",
            source_path=source,
            imported_via="test",
        )
        write_json(project.transcript_path, _transcript(source))
        project.write_edl(
            "default",
            {
                "rows": _rows(),
                "order": [],
                "brief": {
                    "background": ["案例甲"],
                    "target_duration_s": 5,
                    "tolerance_s": 1,
                },
            },
        )
        return app, project, selector

    def test_route_table_contains_all_b10_endpoints(self):
        expected = {
            ("GET", "/api/projects/{id}/content-map"): "_route_content_map",
            ("PUT", "/api/projects/{id}/content-map"): "_route_save_content_map",
            ("POST", "/api/projects/{id}/content-map/analyze"): "_route_content_map_analyze",
            (
                "POST",
                "/api/projects/{id}/content-map/topics/{tid}/create-cut",
            ): "_route_content_map_create_cut",
            ("GET", "/api/projects/{id}/quotes"): "_route_quotes",
            ("POST", "/api/projects/{id}/quotes/analyze"): "_route_quotes_analyze",
            ("POST", "/api/projects/{id}/quotes/{qid}/accept"): "_route_quote_accept",
            ("POST", "/api/projects/{id}/quotes/{qid}/reject"): "_route_quote_reject",
            ("GET", "/api/projects/{id}/budget"): "_route_budget",
            ("POST", "/api/projects/{id}/budget/fit"): "_route_budget_fit",
            ("PUT", "/api/projects/{id}/brief"): "_route_brief",
            ("GET", "/api/projects/{id}/export-checklist"): "_route_export_checklist",
        }
        for route, handler in expected.items():
            self.assertEqual(ROUTES[route], handler)

    def test_route_table_contains_unified_plan_endpoints(self):
        self.assertEqual(
            ROUTES[("GET", "/api/plan-intents")],
            "_route_plan_intents",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/plans/generate")],
            "_route_plans_generate",
        )

    def test_plan_intents_and_whole_video_generation_are_async_and_never_overwrite(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            before_default = project.read_edl("default")
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            api = f"http://127.0.0.1:{port}/api"
            base = f"{api}/projects/{quote(project.id)}"
            payload = {
                "intent": ["cut_fillers", "hook_first"],
                "intent_extra": "只保留 AI 教育",
                "duration_min_s": 180,
                "duration_max_s": 300,
                "split_topics": False,
            }
            try:
                _, presets = _request_json(f"{api}/plan-intents")
                self.assertEqual(
                    {item["key"] for item in presets["intents"]},
                    {
                        "cut_fillers",
                        "hook_first",
                        "keep_insights",
                        "keep_stories",
                        "cut_smalltalk",
                        "keep_data",
                    },
                )

                _, started = _request_json(
                    f"{base}/plans/generate",
                    method="POST",
                    payload=payload,
                )
                self.assertEqual(started["status"], "running")
                first = _wait_state(project, "plan_ai")
                self.assertEqual(first["status"], "done", first)
                self.assertEqual(first["cuts"], ["ai-plan"])
                self.assertIsNone(first["error"])

                _request_json(
                    f"{base}/plans/generate",
                    method="POST",
                    payload=payload,
                )
                second = _wait_state(project, "plan_ai")
                self.assertEqual(second["cuts"], ["ai-plan-2"])
                self.assertEqual(project.read_edl("default"), before_default)
                self.assertEqual(
                    project.read_edl("ai-plan")["order"],
                    ["s2", "s1", "s2", "s3"],
                )
                self.assertEqual(
                    project.read_edl("ai-plan")["label"],
                    "只保留 AI 教育",
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_plan_ai_state_exposes_live_stage_progress(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, selector = self._app_project(root)
            entered = threading.Event()
            release = threading.Event()
            original_chat_json = selector.client.chat_json

            def blocking_chat_json(system: str, user: str, **kwargs):
                if '"drop"' in system and "[s3]" in user:
                    entered.set()
                    if not release.wait(2):
                        raise AssertionError("测试未释放第二主题筛选")
                return original_chat_json(system, user, **kwargs)

            selector.client.chat_json = blocking_chat_json
            app.start_plan_generation(
                project,
                {
                    "intent": ["cut_fillers", "hook_first"],
                    "intent_extra": "",
                    "duration_min_s": 180,
                    "duration_max_s": 300,
                    "split_topics": True,
                },
            )
            self.assertTrue(entered.wait(2))
            running = project.read_state()["plan_ai"]
            self.assertEqual(running["status"], "running")
            self.assertEqual(running["stage"], "select")
            self.assertEqual(running["topics_total"], 1)
            self.assertEqual(running["topics_done"], 0)
            self.assertIn("并行处理 1 个主题", running["detail"])
            self.assertIn("已完成 0 个", running["detail"])
            release.set()
            done = _wait_state(project, "plan_ai")
            self.assertEqual(done["status"], "done", done)
            self.assertEqual(done["topics_done"], done["topics_total"])

    def test_plan_generation_all_topic_failures_land_error(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root, fail=True)
            app.start_plan_generation(
                project,
                {
                    "intent": ["cut_fillers"],
                    "duration_min_s": 180,
                    "duration_max_s": 300,
                    "split_topics": False,
                },
            )
            state = _wait_state(project, "plan_ai")
            self.assertEqual(state["status"], "error")
            self.assertIn("全部主题", state["error"])
            self.assertEqual(state["cuts"], [])

    def test_retired_ai_modes_return_http_410(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                for mode in ("topic_slicing", "highlight_remix"):
                    with self.subTest(mode=mode):
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            _request_json(f"{base}/ai/{mode}")
                        self.assertEqual(caught.exception.code, 410)
                        self.assertIn(
                            "已并入 AI 出剪辑方案",
                            caught.exception.read().decode("utf-8"),
                        )
            finally:
                server.shutdown()
                server.server_close()

    def test_content_map_put_get_validation_and_topic_cut_http_semantics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            payload = {
                "status": "confirmed",
                "claims": [],
                "backgrounds": [],
                "topics": [
                    {
                        "id": "t1",
                        "name": "主题一",
                        "summary": "",
                        "segment_ids": ["s1", "s2"],
                        "suggested_duration_s": 8,
                        "status": "confirmed",
                    }
                ],
            }
            try:
                _, saved = _request_json(
                    f"{base}/content-map",
                    method="PUT",
                    payload=payload,
                )
                self.assertEqual(saved["topics"][0]["duration_ms"], 2500)
                self.assertEqual(saved["meta"]["source"], "human")
                self.assertEqual(_request_json(f"{base}/content-map")[1], saved)

                duplicate = json.loads(json.dumps(payload))
                duplicate["topics"].append(
                    {
                        "id": "t2",
                        "name": "主题二",
                        "summary": "",
                        "segment_ids": ["s2"],
                        "suggested_duration_s": 3,
                        "status": "confirmed",
                    }
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(
                        f"{base}/content-map",
                        method="PUT",
                        payload=duplicate,
                    )
                self.assertEqual(caught.exception.code, 400)
                self.assertIn("s2", caught.exception.read().decode("utf-8"))

                _, created = _request_json(
                    f"{base}/content-map/topics/t1/create-cut",
                    method="POST",
                    payload={},
                )
                self.assertEqual(created["name"], "topic-t1")
                topic_edl = project.read_edl("topic-t1")
                self.assertEqual(topic_edl["brief"]["claim"], "主题一")
                self.assertEqual(
                    [row["id"] for row in topic_edl["rows"] if row["checked"]],
                    ["s1", "s2"],
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(
                        f"{base}/content-map/topics/t1/create-cut",
                        method="POST",
                        payload={},
                    )
                self.assertEqual(caught.exception.code, 409)
            finally:
                server.shutdown()
                server.server_close()

    def test_content_map_missing_is_404_and_mock_ai_sets_running_then_done(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, selector = self._app_project(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(f"{base}/content-map")
                self.assertEqual(caught.exception.code, 404)

                _, started = _request_json(
                    f"{base}/content-map/analyze",
                    method="POST",
                    payload={},
                )
                self.assertEqual(started["status"], "running")
                state = _wait_state(project, "content_map_ai")
                self.assertEqual(state["status"], "done", state)
                saved = project.read_content_map()
                self.assertEqual(saved["status"], "draft")
                self.assertEqual(saved["topics"][0]["duration_ms"], 3500)
                self.assertEqual(len(selector.client.calls), 1)
            finally:
                server.shutdown()
                server.server_close()

    def test_content_map_ai_exception_is_not_swallowed(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root, fail=True)
            app.start_content_map_analysis(project)
            state = _wait_state(project, "content_map_ai")
            self.assertEqual(state["status"], "error")
            self.assertIn("mock planning failure", state["error"])

    def test_quotes_analyze_accept_reject_and_missing_candidate_statuses(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            project.write_content_map(
                {
                    "status": "confirmed",
                    "claims": [],
                    "backgrounds": [],
                    "topics": [
                        {
                            "id": "t1",
                            "name": "主题",
                            "summary": "",
                            "segment_ids": ["s1", "s2", "s3"],
                            "duration_ms": 3500,
                            "suggested_duration_s": 10,
                            "status": "confirmed",
                        }
                    ],
                    "meta": {"source": "human", "model": "", "warnings": []},
                }
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _, started = _request_json(
                    f"{base}/quotes/analyze",
                    method="POST",
                    payload={"topic_id": "t1"},
                )
                self.assertEqual(started["status"], "running")
                self.assertEqual(_wait_state(project, "quotes_ai")["status"], "done")
                self.assertEqual(len(_request_json(f"{base}/quotes")[1]["candidates"]), 3)

                _, accepted = _request_json(
                    f"{base}/quotes/q1/accept",
                    method="POST",
                    payload={"cut": "default", "promote": True},
                )
                self.assertTrue(accepted["ok"])
                row = next(
                    row
                    for row in project.read_edl("default")["rows"]
                    if row["id"] == "s2"
                )
                self.assertEqual(row["role"], "quote")
                self.assertIs(row["locked"], True)
                self.assertEqual(project.read_edl("default")["order"][0], "s2")
                self.assertEqual(
                    project.read_quote_candidates()["candidates"][0]["status"],
                    "accepted",
                )

                _request_json(
                    f"{base}/quotes/q1/reject",
                    method="POST",
                    payload={},
                )
                self.assertEqual(
                    project.read_quote_candidates()["candidates"][0]["status"],
                    "rejected",
                )
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(
                        f"{base}/quotes/missing/accept",
                        method="POST",
                        payload={"cut": "default", "promote": False},
                    )
                self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()

    def test_targeted_quote_analysis_repairs_ids_that_collide_with_other_topics(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            project.write_content_map(
                {
                    "status": "confirmed",
                    "topics": [
                        {
                            "id": "t1",
                            "name": "主题",
                            "summary": "",
                            "segment_ids": ["s1", "s2", "s3"],
                            "status": "confirmed",
                        }
                    ],
                }
            )
            project.write_quote_candidates(
                {
                    "generated_at": "old",
                    "candidates": [
                        {
                            "id": "q1",
                            "topic_id": "other",
                            "segment_id": "s1",
                            "type": "claim",
                            "status": "accepted",
                        }
                    ],
                    "meta": {"source": "ai", "model": "old", "warnings": []},
                }
            )

            app.start_quote_analysis(project, {"topic_id": "t1"})
            self.assertEqual(_wait_state(project, "quotes_ai")["status"], "done")

            document = project.read_quote_candidates()
            ids = [item["id"] for item in document["candidates"]]
            self.assertEqual(ids, ["q1", "q1-2", "q2", "q3"])
            accepted = app.accept_quote_candidate(
                project,
                "q1-2",
                {"cut": "default", "promote": False},
            )
            self.assertEqual(accepted["candidate"]["segment_id"], "s2")

    def test_quote_analysis_does_not_write_result_after_content_map_changes(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, selector = self._app_project(root)
            content_map = {
                "status": "confirmed",
                "topics": [
                    {
                        "id": "t1",
                        "name": "旧主题",
                        "summary": "",
                        "segment_ids": ["s1", "s2", "s3"],
                        "status": "confirmed",
                    }
                ],
            }
            project.write_content_map(content_map)
            entered = threading.Event()
            release = threading.Event()
            original_chat_json = selector.client.chat_json

            def blocking_chat_json(system: str, user: str, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("测试未释放金句分析")
                return original_chat_json(system, user, **kwargs)

            selector.client.chat_json = blocking_chat_json
            app.start_quote_analysis(project, {"topic_id": "t1"})
            self.assertTrue(entered.wait(2))
            content_map["topics"][0]["name"] = "新主题"
            project.write_content_map(content_map)
            release.set()

            state = _wait_state(project, "quotes_ai")
            self.assertEqual(state["status"], "done")
            self.assertIn("内容地图", state["warning"])
            self.assertFalse(project.quote_candidates_path.exists())

    def test_save_plan_does_not_overwrite_concurrent_quote_accept(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            project.write_quote_candidates(
                {
                    "generated_at": "now",
                    "candidates": [
                        {
                            "id": "q1",
                            "topic_id": "t1",
                            "segment_id": "s2",
                            "type": "claim",
                            "status": "pending",
                        }
                    ],
                }
            )
            entered = threading.Event()
            release = threading.Event()
            accept_done = threading.Event()
            errors: list[BaseException] = []
            original_builder = server_module.build_plan_from_selection

            def blocking_builder(*args, **kwargs):
                entered.set()
                if not release.wait(2):
                    raise AssertionError("测试未释放计划构建")
                return original_builder(*args, **kwargs)

            def save() -> None:
                try:
                    app.save_plan(
                        project,
                        {
                            "rows": _rows(),
                            "strategy": "token_padding",
                        },
                    )
                except BaseException as exc:  # pragma: no cover - 由主线程断言。
                    errors.append(exc)

            def accept() -> None:
                try:
                    app.accept_quote_candidate(
                        project,
                        "q1",
                        {"cut": "default", "promote": False},
                    )
                except BaseException as exc:  # pragma: no cover - 由主线程断言。
                    errors.append(exc)
                finally:
                    accept_done.set()

            with patch(
                "cutpoint_lab.studio.server.build_plan_from_selection",
                side_effect=blocking_builder,
            ):
                save_thread = threading.Thread(target=save)
                save_thread.start()
                self.assertTrue(entered.wait(2))
                accept_thread = threading.Thread(target=accept)
                accept_thread.start()
                accept_done.wait(0.25)
                release.set()
                save_thread.join(2)
                accept_thread.join(2)

            self.assertFalse(save_thread.is_alive())
            self.assertFalse(accept_thread.is_alive())
            self.assertEqual(errors, [])
            row = next(
                item
                for item in project.read_edl("default")["rows"]
                if item["id"] == "s2"
            )
            self.assertEqual(row["role"], "quote")
            self.assertIs(row["locked"], True)

    def test_budget_fit_brief_and_checklist_are_cut_aware_and_fit_does_not_write(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            project.write_content_map(
                {
                    "status": "confirmed",
                    "claims": [],
                    "backgrounds": [],
                    "topics": [],
                    "meta": {"source": "human", "model": "", "warnings": []},
                }
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _, brief = _request_json(
                    f"{base}/brief?cut=default",
                    method="PUT",
                    payload={"target_duration_s": 2, "tolerance_s": 0},
                )
                self.assertEqual(brief["brief"]["background"], ["案例甲"])
                self.assertEqual(brief["brief"]["target_duration_s"], 2)

                _, budget = _request_json(f"{base}/budget?cut=default")
                self.assertGreater(budget["estimated_ms"], 2000)
                before = read_json(project.cut_dir("default") / "edl.json")
                _, fitted = _request_json(
                    f"{base}/budget/fit?cut=default",
                    method="POST",
                    payload={"strategy": "strict"},
                )
                self.assertIn("suggestions", fitted)
                self.assertEqual(
                    read_json(project.cut_dir("default") / "edl.json"),
                    before,
                )

                _, checklist = _request_json(
                    f"{base}/export-checklist?cut=default"
                )
                self.assertEqual(
                    [item["key"] for item in checklist["items"]],
                    [
                        "topics_confirmed",
                        "duration",
                        "quotes_locked",
                        "background_covered",
                    ],
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_b10_cut_endpoints_return_404_for_missing_cut(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            requests = [
                (f"{base}/budget?cut=missing", "GET", None),
                (
                    f"{base}/budget/fit?cut=missing",
                    "POST",
                    {"strategy": "strict"},
                ),
                (
                    f"{base}/brief?cut=missing",
                    "PUT",
                    {"target_duration_s": 10},
                ),
                (f"{base}/export-checklist?cut=missing", "GET", None),
            ]
            try:
                for url, method, payload in requests:
                    with self.subTest(url=url):
                        with self.assertRaises(urllib.error.HTTPError) as caught:
                            _request_json(url, method=method, payload=payload)
                        self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()

    def test_json_body_must_be_an_object(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = (
                f"http://127.0.0.1:{port}/api/projects/"
                f"{quote(project.id)}/quotes/analyze"
            )
            try:
                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(url, method="POST", payload=[])
                self.assertEqual(caught.exception.code, 400)
                self.assertIn("object", caught.exception.read().decode("utf-8"))
            finally:
                server.shutdown()
                server.server_close()

    def test_budget_estimate_includes_cuts_nudge_and_repeated_order(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            edl = project.read_edl("default")
            edl["order"] = ["s2", "s2"]
            rows = {row["id"]: row for row in edl["rows"]}
            rows["s2"]["cuts"] = [{"start_token": 0, "end_token": 0}]
            rows["s2"]["nudge"] = {"start_ms": -40, "end_ms": 60}
            project.write_edl("default", edl)

            report = app.duration_budget(project)

            self.assertEqual(report["estimated_ms"], 2400)
            s2 = next(row for row in report["rows"] if row["id"] == "s2")
            self.assertEqual(s2["ms"], 2400)

    def test_quotes_analyze_rejects_content_map_without_confirmed_topic(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app, project, _selector = self._app_project(root)
            project.write_content_map(
                {
                    "status": "draft",
                    "topics": [
                        {
                            "id": "t1",
                            "name": "待确认",
                            "segment_ids": ["s1"],
                            "status": "pending",
                        }
                    ],
                }
            )
            with self.assertRaisesRegex(ValueError, "confirmed"):
                app.start_quote_analysis(project, {})


if __name__ == "__main__":
    unittest.main()
