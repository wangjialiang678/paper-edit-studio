from __future__ import annotations

import json
import tempfile
import threading
import time
import unittest
import urllib.error
import urllib.request
import wave
from pathlib import Path
from unittest.mock import patch
from urllib.parse import quote

from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.config import EnvStore
from cutpoint_lab.studio.server import ROUTES, StudioApplication, bind_server
from cutpoint_lab.studio.workspace import Workspace


class _UnavailableSelector:
    class _Client:
        def available(self) -> bool:
            return False

        def chat_json(self, *_args, **_kwargs):
            raise AssertionError("不可用的 selector 不应调用 LLM")

    def __init__(self):
        self.client = self._Client()

    def available(self) -> bool:
        return False


class _QualityReviewClient:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    def available(self) -> bool:
        return True

    def chat_json(self, system: str, user: str, **_kwargs):
        self.calls.append((system, user))
        return {
            "findings": [
                {
                    "segment_id": "s1",
                    "span_text": "超导",
                    "replacement": "超脑",
                    "confidence": 0.99,
                    "reason": "上下文指向已知专名，且属于近音识别错误",
                    "verdict": "auto_fix",
                },
                {
                    "segment_id": "s2",
                    "span_text": "成本",
                    "replacement": "成品",
                    "confidence": 0.62,
                    "reason": "上下文不足，交给人工确认",
                    "verdict": "ask_user",
                },
            ]
        }


class _QualityReviewSelector:
    def __init__(self):
        self.client = _QualityReviewClient()

    def available(self) -> bool:
        return True


class _OkQualityReviewClient:
    def available(self) -> bool:
        return True

    def chat_json(self, _system: str, _user: str, **_kwargs):
        return {
            "findings": [
                {
                    "segment_id": "s1",
                    "span_text": "低置信",
                    "replacement": "",
                    "confidence": 0.9,
                    "reason": "结合上下文确认原词无误",
                    "verdict": "ok",
                }
            ]
        }


class _OkQualityReviewSelector:
    def __init__(self):
        self.client = _OkQualityReviewClient()

    def available(self) -> bool:
        return True


class _BlockingOkQualityReviewClient:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def available(self) -> bool:
        return True

    def chat_json(self, _system: str, _user: str, **_kwargs):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release quality review")
        return {
            "findings": [
                {
                    "segment_id": "s1",
                    "span_text": "低置信",
                    "replacement": "",
                    "confidence": 0.95,
                    "reason": "结合上下文确认原词无误",
                    "verdict": "ok",
                }
            ]
        }


class _BlockingOkQualityReviewSelector:
    def __init__(self):
        self.client = _BlockingOkQualityReviewClient()

    def available(self) -> bool:
        return True


class _BlockingQualityReviewClient:
    def __init__(self):
        self.entered = threading.Event()
        self.release = threading.Event()

    def available(self) -> bool:
        return True

    def chat_json(self, _system: str, _user: str, **_kwargs):
        self.entered.set()
        if not self.release.wait(timeout=5):
            raise TimeoutError("test did not release quality review")
        return {
            "findings": [
                {
                    "segment_id": "s1",
                    "span_text": "超导",
                    "replacement": "超脑",
                    "confidence": 0.99,
                    "reason": "确定的近音专名纠错",
                    "verdict": "auto_fix",
                }
            ]
        }


class _BlockingQualityReviewSelector:
    def __init__(self):
        self.client = _BlockingQualityReviewClient()

    def available(self) -> bool:
        return True


class _CountingAsrRunner:
    def __init__(self):
        self.calls = 0

    def transcribe(self, _media_path: Path, _run_root: Path, *, source_video: str):
        self.calls += 1
        return {
            "transcript": {
                "source_video": source_video,
                "duration_ms": 1000,
                "selected_segment_ids": ["s1"],
                "segments": [
                    {
                        "id": "s1",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "text": f"第 {self.calls} 次 web coding",
                        "tokens": [
                            {"text": "web coding", "start_ms": 0, "end_ms": 900}
                        ],
                    }
                ],
            },
            "vad": {"duration_ms": 1000, "speech_intervals": [], "source": "fake"},
        }


class _PipelineQualityAsrRunner:
    def __init__(self, text: str):
        self.text = text

    def transcribe(self, _media_path: Path, _run_root: Path, *, source_video: str):
        return {
            "transcript": {
                "source_video": source_video,
                "duration_ms": 1000,
                "selected_segment_ids": ["s1"],
                "segments": [
                    {
                        "id": "s1",
                        "start_ms": 0,
                        "end_ms": 1000,
                        "text": self.text,
                        "tokens": [
                            {
                                "text": self.text,
                                "start_ms": 0,
                                "end_ms": 900,
                                "confidence": 0.42,
                            }
                        ],
                    }
                ],
            },
            "vad": {"duration_ms": 1000, "speech_intervals": [], "source": "fake"},
        }


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x40" * 16000)


def _request_json(url: str, *, method: str = "GET", payload: dict | None = None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method=method,
        headers={"Content-Type": "application/json"} if body is not None else {},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _request_raw_json(
    url: str,
    body: bytes,
    *,
    content_type: str = "application/octet-stream",
):
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type},
    )
    with urllib.request.urlopen(request, timeout=10) as response:
        return response.status, json.loads(response.read().decode("utf-8"))


def _wait_ready(project, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = project.read_state()
        if state.get("stage") in {"ready", "error"}:
            return state
        time.sleep(0.02)
    raise AssertionError(project.read_state())


def _wait_quality_ai(project, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = project.read_state()
        quality_ai = state.get("quality_ai") or {}
        if quality_ai.get("status") in {"done", "error"}:
            return quality_ai
        time.sleep(0.02)
    raise AssertionError(project.read_state())


def _app(
    root: Path,
    runner=None,
    *,
    env_store: EnvStore | None = None,
    selector=None,
) -> StudioApplication:
    return StudioApplication(
        Workspace(root / "workspace"),
        asr_runner=runner or _CountingAsrRunner(),
        selector=selector or _UnavailableSelector(),
        auto_ai=False,
        env_store=env_store or EnvStore(root / ".env"),
    )


class QualityRouteTests(unittest.TestCase):
    def test_route_table_contains_quality_and_retranscribe_endpoints(self):
        self.assertEqual(
            ROUTES[("GET", "/api/settings/corrections")],
            "_route_corrections",
        )
        self.assertEqual(
            ROUTES[("PUT", "/api/settings/corrections")],
            "_route_save_corrections",
        )
        self.assertEqual(
            ROUTES[("GET", "/api/projects/{id}/quality/corrections-preview")],
            "_route_corrections_preview",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/quality/apply-corrections")],
            "_route_apply_corrections",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/quality/undo/{change_id}")],
            "_route_undo_changeset",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/retranscribe")],
            "_route_retranscribe",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/quality/analyze")],
            "_route_quality_analyze",
        )
        self.assertEqual(
            ROUTES[("GET", "/api/projects/{id}/quality/report")],
            "_route_quality_report",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/quality/issues/{issue_id}")],
            "_route_quality_issue",
        )
        self.assertEqual(
            ROUTES[("POST", "/api/projects/{id}/reference")],
            "_route_reference",
        )
        self.assertEqual(
            ROUTES[("GET", "/api/projects/{id}/reference")],
            "_route_reference",
        )

    def test_corrections_dictionary_http_crud_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            url = f"http://127.0.0.1:{port}/api/settings/corrections"
            try:
                _, initial = _request_json(url)
                self.assertEqual(initial, {"pairs": []})

                replacement = {
                    "pairs": [
                        {
                            "wrong": ["web coding", "web courting"],
                            "right": "vibe coding",
                            "is_term": True,
                        }
                    ]
                }
                _, saved = _request_json(url, method="PUT", payload=replacement)
                self.assertEqual(saved, replacement)
                self.assertEqual(_request_json(url)[1], replacement)

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(url, method="PUT", payload={"pairs": [{}]})
                self.assertEqual(caught.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()

    def test_preview_apply_and_undo_roundtrip_selection_rows(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "source.wav"
            _write_wav(media)
            app = _app(root)
            project = app.workspace.create_project("quality", source_path=media, imported_via="path")
            write_json(
                project.transcript_path,
                {
                    "source_video": str(media),
                    "duration_ms": 1000,
                    "selected_segment_ids": ["s1", "s2"],
                    "segments": [
                        {"id": "s1", "start_ms": 0, "end_ms": 400, "text": "WEB CODING", "tokens": []},
                        {"id": "s2", "start_ms": 500, "end_ms": 900, "text": "不命中", "tokens": []},
                    ],
                },
            )
            selection_path = project.dir / "selection.json"
            write_json(
                selection_path,
                {
                    "rows": [
                        {"id": "s1", "text": "WEB CODING", "checked": True, "cuts": [{"start_token": 0, "end_token": 0}]},
                        {"id": "s2", "text": "不命中", "checked": False},
                    ],
                    "groups": [{"purpose": "hook", "segment_ids": ["s1"]}],
                },
            )
            app.save_corrections(
                {"pairs": [{"wrong": ["web coding"], "right": "vibe coding", "is_term": True}]}
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            project_url = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _, preview = _request_json(f"{project_url}/quality/corrections-preview")
                self.assertEqual(preview["total"], 1)
                self.assertEqual(preview["items"][0]["segment_id"], "s1")

                _, applied = _request_json(f"{project_url}/quality/apply-corrections", method="POST", payload={})
                self.assertTrue(applied["ok"])
                self.assertEqual(applied["applied"], 1)
                self.assertEqual(applied["rows"][0]["text"], "vibe coding")
                change_id = applied["changeset_id"]
                self.assertTrue((project.dir / "changesets" / f"{change_id}.json").is_file())
                saved_selection = read_json(project.cut_dir("default") / "edl.json")
                self.assertEqual(saved_selection["order"], ["s1"])
                self.assertTrue(saved_selection["rows"][0]["checked"])
                self.assertIn("cuts", saved_selection["rows"][0])

                _, undone = _request_json(f"{project_url}/quality/undo/{change_id}", method="POST", payload={})
                self.assertTrue(undone["ok"])
                self.assertEqual(undone["reverted"], 1)
                self.assertEqual(undone["rows"][0]["text"], "WEB CODING")
            finally:
                server.shutdown()
                server.server_close()


class QualityAnalysisHttpTests(unittest.TestCase):
    def _project_with_low_confidence(self, app: StudioApplication, root: Path):
        media = root / "source.wav"
        _write_wav(media)
        project = app.workspace.create_project(
            "quality-report",
            source_path=media,
            imported_via="path",
        )
        write_json(
            project.transcript_path,
            {
                "source_video": str(media),
                "duration_ms": 1200,
                "selected_segment_ids": ["s1", "s2"],
                "segments": [
                    {
                        "id": "s1",
                        "start_ms": 0,
                        "end_ms": 500,
                        "text": "第一处低置信",
                        "tokens": [
                            {
                                "text": "低置信",
                                "start_ms": 100,
                                "end_ms": 400,
                                "confidence": 0.31,
                            }
                        ],
                    },
                    {
                        "id": "s2",
                        "start_ms": 600,
                        "end_ms": 1100,
                        "text": "第二处低置信",
                        "tokens": [
                            {
                                "text": "低置信",
                                "start_ms": 700,
                                "end_ms": 1000,
                                "confidence": 0.45,
                            }
                        ],
                    },
                ],
            },
        )
        return project

    def test_analyze_without_ai_persists_report_and_issue_status_is_idempotent(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root)
            project = self._project_with_low_confidence(app, root)
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _, analyzed = _request_json(
                    f"{base}/quality/analyze",
                    method="POST",
                    payload={"ai": False},
                )
                self.assertTrue(
                    {"generated_at", "issues", "stats", "meta"}.issubset(analyzed)
                )
                self.assertEqual(len(analyzed["issues"]), 2)
                self.assertTrue(
                    all(issue["status"] == "open" for issue in analyzed["issues"])
                )
                self.assertEqual(
                    read_json(project.dir / "quality_report.json"),
                    analyzed,
                )
                self.assertEqual(_request_json(f"{base}/quality/report")[1], analyzed)

                first_id, second_id = [
                    issue["id"] for issue in analyzed["issues"]
                ]
                for _ in range(2):
                    _request_json(
                        f"{base}/quality/issues/{quote(first_id)}",
                        method="POST",
                        payload={"status": "resolved"},
                    )
                    _request_json(
                        f"{base}/quality/issues/{quote(second_id)}",
                        method="POST",
                        payload={"status": "ignored"},
                    )
                persisted = _request_json(f"{base}/quality/report")[1]
                statuses = {
                    issue["id"]: issue["status"] for issue in persisted["issues"]
                }
                self.assertEqual(statuses[first_id], "resolved")
                self.assertEqual(statuses[second_id], "ignored")

                with self.assertRaises(urllib.error.HTTPError) as caught:
                    _request_json(
                        f"{base}/quality/issues/not-a-real-issue",
                        method="POST",
                        payload={"status": "resolved"},
                    )
                self.assertEqual(caught.exception.code, 404)
            finally:
                server.shutdown()
                server.server_close()

    def test_high_confidence_ok_persists_resolution_and_review_reason(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root, selector=_OkQualityReviewSelector())
            project = self._project_with_low_confidence(app, root)

            app.analyze_quality(project, {"ai": True})
            quality_ai = _wait_quality_ai(project)

            self.assertEqual(quality_ai["status"], "done", quality_ai)
            report = read_json(project.dir / "quality_report.json")
            low_confidence = {
                issue["segment_id"]: issue
                for issue in report["issues"]
                if issue["kind"] == "low_confidence"
            }
            self.assertEqual(low_confidence["s1"]["status"], "resolved")
            self.assertTrue(
                low_confidence["s1"]["reason"].endswith(
                    "；AI 复核通过：结合上下文确认原词无误"
                )
            )
            self.assertEqual(low_confidence["s2"]["status"], "open")

    def test_high_confidence_ok_appends_to_latest_reason_after_reload(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selector = _BlockingOkQualityReviewSelector()
            app = _app(root, selector=selector)
            project = self._project_with_low_confidence(app, root)

            app.analyze_quality(project, {"ai": True})
            self.assertTrue(selector.client.entered.wait(timeout=2))
            report_path = project.dir / "quality_report.json"
            report = read_json(report_path)
            source_issue = next(
                issue
                for issue in report["issues"]
                if issue["kind"] == "low_confidence"
                and issue["segment_id"] == "s1"
            )
            source_issue["reason"] = "并发刷新后的低置信理由"
            write_json(report_path, report)
            selector.client.release.set()

            quality_ai = _wait_quality_ai(project)

            self.assertEqual(quality_ai["status"], "done", quality_ai)
            saved = read_json(report_path)
            resolved = next(
                issue
                for issue in saved["issues"]
                if issue["kind"] == "low_confidence"
                and issue["segment_id"] == "s1"
            )
            self.assertEqual(
                resolved["reason"],
                "并发刷新后的低置信理由；AI 复核通过：结合上下文确认原词无误",
            )

    def test_reference_raw_upload_and_path_import_each_roundtrip_through_get(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _app(root)
            media = root / "source.wav"
            _write_wav(media)
            project = app.workspace.create_project(
                "reference",
                source_path=media,
                imported_via="path",
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}/reference"
            try:
                raw_srt = (
                    "1\n00:00:00,000 --> 00:00:01,000\n参考字幕一\n"
                ).encode("utf-8")
                _, uploaded = _request_raw_json(
                    f"{base}?filename={quote('参考字幕.srt')}",
                    raw_srt,
                    content_type="application/x-subrip",
                )
                self.assertTrue(uploaded["ok"])
                uploaded_path = Path(uploaded["path"])
                self.assertEqual(uploaded_path.read_bytes(), raw_srt)
                current = _request_json(base)[1]
                self.assertEqual(
                    current,
                    {"exists": True, "filename": uploaded_path.name},
                )

                external = root / "external.vtt"
                external.write_text(
                    "WEBVTT\n\n00:00.000 --> 00:01.000\n参考字幕二\n",
                    encoding="utf-8",
                )
                _, imported = _request_json(
                    base,
                    method="POST",
                    payload={"path": str(external)},
                )
                self.assertTrue(imported["ok"])
                imported_path = Path(imported["path"])
                self.assertEqual(
                    imported_path.read_text(encoding="utf-8"),
                    external.read_text(encoding="utf-8"),
                )
                self.assertEqual(
                    _request_json(base)[1],
                    {"exists": True, "filename": imported_path.name},
                )
            finally:
                server.shutdown()
                server.server_close()

    def test_analyze_with_ai_updates_state_selection_report_and_changeset(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selector = _QualityReviewSelector()
            app = _app(root, selector=selector)
            media = root / "source.wav"
            _write_wav(media)
            project = app.workspace.create_project(
                "quality-ai",
                source_path=media,
                imported_via="path",
            )
            write_json(
                project.transcript_path,
                {
                    "source_video": str(media),
                    "duration_ms": 1200,
                    "selected_segment_ids": ["s1", "s2"],
                    "segments": [
                        {
                            "id": "s1",
                            "start_ms": 0,
                            "end_ms": 500,
                            "text": "今天聊超导",
                            "tokens": [
                                {
                                    "text": "超导",
                                    "start_ms": 100,
                                    "end_ms": 400,
                                    "confidence": 0.31,
                                }
                            ],
                        },
                        {
                            "id": "s2",
                            "start_ms": 600,
                            "end_ms": 1100,
                            "text": "控制成本",
                            "tokens": [
                                {
                                    "text": "成本",
                                    "start_ms": 700,
                                    "end_ms": 1000,
                                    "confidence": 0.38,
                                }
                            ],
                        },
                    ],
                },
            )
            write_json(
                project.dir / "selection.json",
                {
                    "rows": [
                        {"id": "s1", "text": "今天聊超导", "checked": True},
                        {"id": "s2", "text": "控制成本", "checked": True},
                    ]
                },
            )
            app.save_corrections(
                {
                    "pairs": [
                        {
                            "wrong": ["超倒"],
                            "right": "超脑",
                            "is_term": True,
                        }
                    ]
                }
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _, initial = _request_json(
                    f"{base}/quality/analyze",
                    method="POST",
                    payload={"ai": True},
                )
                self.assertTrue(
                    {"generated_at", "issues", "stats", "meta"}.issubset(initial)
                )
                quality_ai = _wait_quality_ai(project)
                self.assertEqual(quality_ai["status"], "done", quality_ai)
                self.assertTrue(selector.client.calls)

                selection = read_json(project.cut_dir("default") / "edl.json")
                rows = {row["id"]: row for row in selection["rows"]}
                self.assertEqual(rows["s1"]["text"], "今天聊超脑")
                self.assertEqual(rows["s2"]["text"], "控制成本")

                report = _request_json(f"{base}/quality/report")[1]
                change_id = report["meta"]["ai_changeset_id"]
                self.assertTrue(
                    (project.dir / "changesets" / f"{change_id}.json").is_file()
                )
                suspects = [
                    issue
                    for issue in report["issues"]
                    if issue["kind"] == "ai_suspect"
                ]
                self.assertEqual(len(suspects), 1)
                self.assertEqual(suspects[0]["segment_id"], "s2")
                self.assertEqual(suspects[0]["suggestion"], "成品")
            finally:
                server.shutdown()
                server.server_close()

    def test_ai_does_not_overwrite_text_edited_while_review_is_running(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            selector = _BlockingQualityReviewSelector()
            app = _app(root, selector=selector)
            media = root / "source.wav"
            _write_wav(media)
            project = app.workspace.create_project(
                "quality-ai-race",
                source_path=media,
                imported_via="path",
            )
            write_json(
                project.transcript_path,
                {
                    "source_video": str(media),
                    "duration_ms": 500,
                    "selected_segment_ids": ["s1"],
                    "segments": [
                        {
                            "id": "s1",
                            "start_ms": 0,
                            "end_ms": 500,
                            "text": "今天聊超导",
                            "tokens": [
                                {
                                    "text": "超导",
                                    "start_ms": 100,
                                    "end_ms": 400,
                                    "confidence": 0.31,
                                }
                            ],
                        }
                    ],
                },
            )
            selection_path = project.dir / "selection.json"
            write_json(
                selection_path,
                {"rows": [{"id": "s1", "text": "今天聊超导", "checked": True}]},
            )
            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            base = f"http://127.0.0.1:{port}/api/projects/{quote(project.id)}"
            try:
                _request_json(
                    f"{base}/quality/analyze",
                    method="POST",
                    payload={"ai": True},
                )
                self.assertTrue(selector.client.entered.wait(timeout=2))
                selection = read_json(selection_path)
                selection["rows"][0]["text"] = "用户正在手工修改"
                write_json(selection_path, selection)
                selector.client.release.set()

                quality_ai = _wait_quality_ai(project)
                self.assertEqual(quality_ai["status"], "done", quality_ai)
                self.assertEqual(
                    read_json(selection_path)["rows"][0]["text"],
                    "用户正在手工修改",
                )
                report = read_json(project.dir / "quality_report.json")
                self.assertNotIn("ai_changeset_id", report["meta"])
                low = next(
                    issue
                    for issue in report["issues"]
                    if issue["kind"] == "low_confidence"
                )
                self.assertEqual(low["status"], "open")
            finally:
                selector.client.release.set()
                server.shutdown()
                server.server_close()


class CacheIntegrationTests(unittest.TestCase):
    def test_settings_resolve_cache_dir_env_then_dotenv_then_workspace_default(self):
        with tempfile.TemporaryDirectory() as tmp, patch.dict("os.environ", {}, clear=False):
            root = Path(tmp)
            import os

            os.environ.pop("TRANSCRIPT_CACHE_DIR", None)
            dotenv_cache = root / "dotenv-cache"
            env_path = root / ".env"
            env_path.write_text(f"TRANSCRIPT_CACHE_DIR={dotenv_cache}\n", encoding="utf-8")
            app = _app(root, env_store=EnvStore(env_path))
            self.assertEqual(app.settings()["transcript_cache_dir"], str(dotenv_cache.resolve()))

            process_cache = root / "process-cache"
            with patch.dict(os.environ, {"TRANSCRIPT_CACHE_DIR": str(process_cache)}):
                overridden = _app(root / "process", env_store=EnvStore(env_path))
                self.assertEqual(
                    overridden.settings()["transcript_cache_dir"],
                    str(process_cache.resolve()),
                )

            default_app = _app(root / "default", env_store=EnvStore(root / "missing.env"))
            self.assertEqual(
                default_app.settings()["transcript_cache_dir"],
                str((default_app.workspace.root / "_cache" / "transcripts").resolve()),
            )

    def test_second_project_reuses_cache_and_retranscribe_forces_inner(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "source.wav"
            _write_wav(media)
            runner = _CountingAsrRunner()
            app = _app(root, runner)

            first_state = app.import_path(str(media), "first")
            first = app.workspace.get(first_state["id"])
            self.assertEqual(_wait_ready(first)["stage"], "ready")

            second_state = app.import_path(str(media), "second")
            second = app.workspace.get(second_state["id"])
            reused = _wait_ready(second)
            self.assertEqual(runner.calls, 1)
            self.assertEqual(reused["stage_message"], "复用已有字幕（内容指纹命中）")
            self.assertEqual(reused["asr"]["cache"], "hit")

            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                _, response = _request_json(
                    f"http://127.0.0.1:{port}/api/projects/{quote(second.id)}/retranscribe",
                    method="POST",
                    payload={},
                )
                self.assertTrue(response["ok"])
                self.assertEqual(_wait_ready(second)["stage"], "ready")
                self.assertEqual(runner.calls, 2)
                self.assertIn("第 2 次", read_json(second.transcript_path)["segments"][0]["text"])
            finally:
                server.shutdown()
                server.server_close()


class PipelineQualityIntegrationTests(unittest.TestCase):
    def test_pipeline_applies_dictionary_before_confidence_report_and_ready(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "source.wav"
            _write_wav(media)
            app = _app(root, _PipelineQualityAsrRunner("web coding"))
            app.save_corrections(
                {
                    "pairs": [
                        {
                            "wrong": ["web coding"],
                            "right": "vibe coding",
                            "is_term": True,
                        }
                    ]
                }
            )

            state = app.import_path(str(media), "pipeline-quality")
            project = app.workspace.get(state["id"])
            ready = _wait_ready(project)

            self.assertEqual(ready["stage"], "ready", ready)
            self.assertIn("纠错", ready["stage_message"])
            self.assertEqual(
                read_json(project.transcript_path)["segments"][0]["text"],
                "vibe coding",
            )
            changesets = list((project.dir / "changesets").glob("*.json"))
            self.assertEqual(len(changesets), 1)
            report = read_json(project.dir / "quality_report.json")
            self.assertTrue(
                {"generated_at", "issues", "stats", "meta"}.issubset(report)
            )
            self.assertTrue(
                any(issue["kind"] == "low_confidence" for issue in report["issues"])
            )

    def test_pipeline_does_not_save_changeset_when_dictionary_has_no_hit(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "source.wav"
            _write_wav(media)
            app = _app(root, _PipelineQualityAsrRunner("没有命中"))
            app.save_corrections(
                {
                    "pairs": [
                        {
                            "wrong": ["web coding"],
                            "right": "vibe coding",
                            "is_term": True,
                        }
                    ]
                }
            )

            state = app.import_path(str(media), "pipeline-no-hit")
            project = app.workspace.get(state["id"])
            ready = _wait_ready(project)

            self.assertEqual(ready["stage"], "ready", ready)
            self.assertEqual(
                read_json(project.transcript_path)["segments"][0]["text"],
                "没有命中",
            )
            changeset_dir = project.dir / "changesets"
            self.assertEqual(
                list(changeset_dir.glob("*.json")) if changeset_dir.exists() else [],
                [],
            )
            self.assertTrue((project.dir / "quality_report.json").is_file())


if __name__ == "__main__":
    unittest.main()
