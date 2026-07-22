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
    def available(self) -> bool:
        return False


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


def _wait_ready(project, timeout: float = 10.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        state = project.read_state()
        if state.get("stage") in {"ready", "error"}:
            return state
        time.sleep(0.02)
    raise AssertionError(project.read_state())


def _app(root: Path, runner=None, *, env_store: EnvStore | None = None) -> StudioApplication:
    return StudioApplication(
        Workspace(root / "workspace"),
        asr_runner=runner or _CountingAsrRunner(),
        selector=_UnavailableSelector(),
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
                saved_selection = read_json(selection_path)
                self.assertEqual(saved_selection["groups"][0]["purpose"], "hook")
                self.assertTrue(saved_selection["rows"][0]["checked"])
                self.assertIn("cuts", saved_selection["rows"][0])

                _, undone = _request_json(f"{project_url}/quality/undo/{change_id}", method="POST", payload={})
                self.assertTrue(undone["ok"])
                self.assertEqual(undone["reverted"], 1)
                self.assertEqual(undone["rows"][0]["text"], "WEB CODING")
            finally:
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


if __name__ == "__main__":
    unittest.main()
