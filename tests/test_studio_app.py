import io
import json
import tempfile
import threading
import time
import unittest
import urllib.request
import wave
from pathlib import Path
from urllib.parse import quote

from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.models import Transcript, TranscriptSegment, TranscriptToken
from cutpoint_lab.studio.ai_selector import AiSelector
from cutpoint_lab.studio.pipeline import PipelineManager
from cutpoint_lab.studio.plans import apply_manual_nudges, build_ordered_plan, silence_gaps
from cutpoint_lab.studio.server import StudioApplication, bind_server
from cutpoint_lab.studio.workspace import Workspace

PROMPTS_DIR = Path(__file__).resolve().parents[1] / "prompts"


def _write_wav(path: Path, seconds: float = 1.0) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        frame_count = int(16000 * seconds)
        handle.writeframes(b"\x00\x40" * frame_count)


def _transcript_payload(source: str) -> dict:
    return {
        "source_video": source,
        "duration_ms": 1000,
        "selected_segment_ids": ["sentence_0001", "sentence_0002"],
        "segments": [
            {
                "id": "sentence_0001",
                "start_ms": 0,
                "end_ms": 400,
                "text": "第一句",
                "tokens": [{"text": "第一句", "start_ms": 10, "end_ms": 350}],
            },
            {
                "id": "sentence_0002",
                "start_ms": 600,
                "end_ms": 950,
                "text": "第二句",
                "tokens": [{"text": "第二句", "start_ms": 620, "end_ms": 900}],
            },
        ],
    }


class FakeAsrRunner:
    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str):
        return {
            "transcript": _transcript_payload(source_video),
            "vad": {"duration_ms": 1000, "speech_intervals": [], "source": "fake"},
        }


class FakeSelectorClient:
    def available(self) -> bool:
        return True

    def chat_json(self, system, user, **_kwargs):
        return {
            "summary": "测试",
            "decisions": [
                {"segment_id": "sentence_0001", "keep": True, "reason": "观点", "labels": ["insight"]},
                {"segment_id": "sentence_0002", "keep": False, "reason": "口水", "labels": ["filler"]},
            ],
        }


def _make_app(root: Path, *, auto_ai: bool = False) -> StudioApplication:
    return StudioApplication(
        Workspace(root / "ws"),
        prompts_dir=PROMPTS_DIR,
        asr_runner=FakeAsrRunner(),
        selector=AiSelector(PROMPTS_DIR, client=FakeSelectorClient()),
        auto_ai=auto_ai,
    )


def _wait_stage(project, stages: set[str], timeout_s: float = 20.0) -> str:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        stage = project.read_state().get("stage")
        if stage in stages:
            return stage
        time.sleep(0.05)
    raise AssertionError(f"等待阶段超时：{project.read_state()}")


class WorkspaceTests(unittest.TestCase):
    def test_create_and_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            workspace = Workspace(Path(tmp) / "ws")
            project = workspace.create_project("测试视频", source_path=Path("/tmp/a.mp4"), imported_via="path")
            self.assertEqual(project.read_state()["stage"], "imported")
            listed = workspace.list_projects()
            self.assertEqual(len(listed), 1)
            self.assertEqual(listed[0]["name"], "测试视频")
            self.assertIs(workspace.get(project.id), project)


class PipelineTests(unittest.TestCase):
    def test_pipeline_reaches_ready_with_fake_asr(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "input.wav"
            _write_wav(media)
            workspace = Workspace(root / "ws")
            project = workspace.create_project("样片", source_path=media, imported_via="path")
            pipeline = PipelineManager(FakeAsrRunner())
            pipeline.start(project)
            stage = _wait_stage(project, {"ready", "error"})
            self.assertEqual(stage, "ready", project.read_state().get("error"))
            self.assertTrue(project.transcript_path.exists())
            self.assertTrue(project.analysis_wav_path.exists())
            self.assertEqual(project.read_state()["asr"]["segment_count"], 2)

    def test_pipeline_error_recorded(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace = Workspace(root / "ws")
            project = workspace.create_project("坏源", source_path=root / "missing.mp4", imported_via="path")
            pipeline = PipelineManager(FakeAsrRunner())
            pipeline.start(project)
            stage = _wait_stage(project, {"error"})
            self.assertEqual(stage, "error")
            self.assertIn("不存在", project.read_state()["error"])


class PlansTests(unittest.TestCase):
    def _transcript(self) -> Transcript:
        segments = [
            TranscriptSegment(
                id="s1", start_ms=0, end_ms=1000, text="一",
                tokens=[TranscriptToken(text="一", start_ms=50, end_ms=900)],
            ),
            TranscriptSegment(
                id="s2", start_ms=2000, end_ms=3000, text="二",
                tokens=[TranscriptToken(text="二", start_ms=2050, end_ms=2900)],
            ),
            TranscriptSegment(
                id="s3", start_ms=5000, end_ms=6000, text="三",
                tokens=[TranscriptToken(text="三", start_ms=5050, end_ms=5900)],
            ),
        ]
        return Transcript(source_video="a.mp4", duration_ms=7000, selected_segment_ids=["s1", "s2", "s3"], segments=segments)

    def test_silence_gaps(self):
        gaps = silence_gaps(self._transcript())
        self.assertEqual([gap["gap_ms"] for gap in gaps], [1000, 2000])
        self.assertEqual(gaps[0]["after_segment_id"], "s1")

    def test_ordered_plan_preserves_group_order_and_allows_repeat(self):
        plan = build_ordered_plan(
            self._transcript(),
            [
                {"purpose": "hook", "segment_ids": ["s3"]},
                {"purpose": "body", "segment_ids": ["s1", "s2"]},
                {"purpose": "echo", "segment_ids": ["s3"]},
            ],
            strategy="token_padding",
        )
        self.assertTrue(plan["ordered"])
        purposes = [item["group_purpose"] for item in plan["ranges"]]
        self.assertEqual(purposes[0], "hook")
        self.assertEqual(purposes[-1], "echo")
        self.assertGreater(plan["ranges"][0]["start_ms"], plan["ranges"][1]["end_ms"] - 1)

    def test_ordered_plan_rejects_unknown_ids(self):
        with self.assertRaises(ValueError):
            build_ordered_plan(self._transcript(), [{"purpose": "hook", "segment_ids": ["nope"]}])

    def test_manual_nudges_shift_range_edges_and_clamp(self):
        plan = {
            "ranges": [
                {"start_ms": 1000, "end_ms": 3000, "source_segment_ids": ["s1", "s2"], "adjustment_reason": "token_padding"},
                {"start_ms": 5000, "end_ms": 5200, "source_segment_ids": ["s3"]},
            ]
        }
        apply_manual_nudges(
            plan,
            {
                "s1": {"start_ms": -120},          # range1 首段：start 左移
                "s2": {"end_ms": 80},              # range1 末段：end 右移
                "s3": {"start_ms": 500, "end_ms": -500},  # 会把片段挤没 → clamp 最小时长
            },
        )
        first, second = plan["ranges"]
        self.assertEqual(first["start_ms"], 880)
        self.assertEqual(first["end_ms"], 3080)
        self.assertEqual(first["adjustment_reason"], "token_padding+manual")
        self.assertEqual(second["start_ms"], 5500)
        self.assertEqual(second["end_ms"], 5500 + 80)
        self.assertEqual(second["adjustment_reason"], "manual")

    def test_manual_nudge_ignored_for_mid_range_segment(self):
        plan = {"ranges": [{"start_ms": 1000, "end_ms": 3000, "source_segment_ids": ["s1", "s2", "s3"]}]}
        apply_manual_nudges(plan, {"s2": {"start_ms": -200, "end_ms": 200}})
        self.assertEqual(plan["ranges"][0]["start_ms"], 1000)
        self.assertEqual(plan["ranges"][0]["end_ms"], 3000)


class StudioApplicationTests(unittest.TestCase):
    def test_editor_injects_suggested_cuts_and_restores_saved_cuts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "input.wav"
            _write_wav(media)
            app = _make_app(root)
            state = app.import_path(str(media), "样片")
            project = app.workspace.get(state["id"])
            _wait_stage(project, {"ready"})
            write_json(
                project.transcript_path,
                {
                    "source_video": str(media),
                    "duration_ms": 1000,
                    "selected_segment_ids": ["sentence_0001"],
                    "segments": [
                        {
                            "id": "sentence_0001",
                            "start_ms": 0,
                            "end_ms": 500,
                            "text": "嗯开始",
                            "tokens": [
                                {"text": "嗯", "start_ms": 20, "end_ms": 100},
                                {"text": "开始", "start_ms": 150, "end_ms": 450},
                            ],
                        },
                        {
                            "id": "sentence_0002",
                            "start_ms": 600,
                            "end_ms": 900,
                            "text": "无词时间",
                            "tokens": [],
                        },
                    ],
                },
            )

            editor = app.editor_state(project)
            self.assertEqual(editor["rows"][0]["suggested_cuts"][0]["kind"], "filler")
            self.assertEqual(editor["rows"][1]["suggested_cuts"], [])

            rows = [
                {
                    "id": "sentence_0001",
                    "checked": True,
                    "text": "开始",
                    "cuts": [{"start_token": 0, "end_token": 0}],
                },
                {"id": "sentence_0002", "checked": False, "text": "无词时间"},
            ]
            app.save_plan(project, {"rows": rows, "strategy": "token_padding"})
            restored = app.editor_state(project)
            self.assertEqual(
                restored["rows"][0]["cuts"],
                [{"start_token": 0, "end_token": 0}],
            )

    def test_selection_persists_a_full_row_set_for_partial_payloads(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "input.wav"
            _write_wav(media)
            app = _make_app(root)
            state = app.import_path(str(media), "样片")
            project = app.workspace.get(state["id"])
            _wait_stage(project, {"ready"})

            app.save_plan(
                project,
                {
                    "rows": [
                        {"id": "sentence_0001", "checked": True, "text": "第一句"}
                    ],
                    "strategy": "token_padding",
                },
            )

            selection = read_json(project.cut_dir("default") / "edl.json")
            self.assertEqual(
                [row["id"] for row in selection["rows"]],
                ["sentence_0001", "sentence_0002"],
            )
            self.assertFalse(selection["rows"][1]["checked"])

    def test_import_editor_plan_ai_flow(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "input.wav"
            _write_wav(media)
            app = _make_app(root)
            state = app.import_path(str(media), "样片")
            project = app.workspace.get(state["id"])
            _wait_stage(project, {"ready"})

            editor = app.editor_state(project)
            self.assertEqual(len(editor["rows"]), 2)
            self.assertTrue(editor["strategies"])
            self.assertEqual(editor["rows"][0]["tokens"][0]["text"], "第一句")

            rows = [{"id": row["id"], "checked": True, "text": row["text"]} for row in editor["rows"]]
            plan_result = app.save_plan(project, {"rows": rows, "strategy": "token_padding"})
            self.assertTrue(plan_result["plan"]["ranges"])
            self.assertTrue((project.cut_dir("default") / "edl.json").exists())

            # 带 nudge 的保存：range 边缘按手动偏移移动，并回读进编辑器状态。
            rows_nudged = [dict(row) for row in rows]
            rows_nudged[0]["nudge"] = {"start_ms": -60}
            nudged = app.save_plan(project, {"rows": rows_nudged, "strategy": "token_padding"})
            base_start = plan_result["plan"]["ranges"][0]["start_ms"]
            self.assertEqual(nudged["plan"]["ranges"][0]["start_ms"], max(0, base_start - 60))
            self.assertIn("manual", nudged["plan"]["ranges"][0]["adjustment_reason"])
            editor_after = app.editor_state(project)
            self.assertEqual(editor_after["rows"][0].get("nudge"), {"start_ms": -60})

            ordered = app.save_plan(
                project,
                {
                    "rows": rows,
                    "strategy": "token_padding",
                    "groups": [
                        {"purpose": "hook", "segment_ids": ["sentence_0002"]},
                        {"purpose": "body", "segment_ids": ["sentence_0001"]},
                    ],
                },
            )
            self.assertTrue(ordered["plan"]["ordered"])
            self.assertEqual(ordered["plan"]["ranges"][0]["group_purpose"], "hook")

            app.start_ai(project, {"mode": "koubo_tighten"})
            deadline = time.time() + 10
            while time.time() < deadline:
                suggestion = app.ai_suggestion(project, "koubo_tighten")
                if suggestion.get("status") == "done":
                    break
                time.sleep(0.05)
            self.assertEqual(suggestion["status"], "done")
            self.assertIn("sentence_0001", suggestion["keep_segment_ids"])

    def test_auto_ai_writes_default_selection(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            media = root / "input.wav"
            _write_wav(media)
            app = _make_app(root, auto_ai=True)
            state = app.import_path(str(media), "样片")
            project = app.workspace.get(state["id"])
            _wait_stage(project, {"ready"})
            selection = read_json(project.cut_dir("default") / "edl.json")
            checked = {row["id"]: row["checked"] for row in selection["rows"]}
            self.assertTrue(checked["sentence_0001"])
            self.assertFalse(checked["sentence_0002"])
            editor = app.editor_state(project)
            row2 = next(row for row in editor["rows"] if row["id"] == "sentence_0002")
            self.assertFalse(row2["checked"])
            self.assertEqual(row2["ai_keep"], False)

    def test_import_upload_streams_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _make_app(root)

            class _NoopPipeline:
                def start(self, project):
                    return None

                def is_running(self, project):
                    return False

            app.pipeline = _NoopPipeline()
            body = b"fake-video-bytes" * 100
            state = app.import_upload("我的 视频.mp4", io.BytesIO(body), len(body))
            saved = Path(state["source"]["path"])
            self.assertTrue(saved.exists())
            self.assertEqual(saved.read_bytes(), body)
            self.assertEqual(saved.name, "我的 视频.mp4")

    def test_import_path_rejects_missing_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            app = _make_app(Path(tmp))
            with self.assertRaises(ValueError):
                app.import_path(str(Path(tmp) / "nope.mp4"), None)


class HttpRoutingTests(unittest.TestCase):
    """回归：中文文件名 → 项目 ID 含中文 → 浏览器会百分号编码 URL，路由必须解码。"""

    def test_cjk_project_id_resolves_via_http(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            app = _make_app(root)
            media = root / "中文名测试视频.wav"
            _write_wav(media)
            state = app.import_path(str(media), None)
            project = app.workspace.get(state["id"])
            _wait_stage(project, {"ready", "error"})

            server, port = bind_server(app, host="127.0.0.1", port=0)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                url = f"http://127.0.0.1:{port}/api/projects/{quote(state['id'])}"
                with urllib.request.urlopen(url, timeout=5) as response:
                    payload = json.loads(response.read().decode("utf-8"))
                self.assertEqual(payload.get("id"), state["id"])
                self.assertEqual(payload.get("stage"), "ready")
            finally:
                server.shutdown()
                server.server_close()



class DefaultPortTests(unittest.TestCase):
    def test_default_port_is_zero_not_8765(self):
        import contextlib
        from unittest.mock import MagicMock, patch

        from cutpoint_lab.studio import server as server_module

        captured_port = []

        def spy_bind(app, *, host, port):
            captured_port.append(port)
            raise SystemExit(0)

        with patch.object(server_module, "bind_server", side_effect=spy_bind), \
             patch.object(server_module, "StudioApplication", return_value=MagicMock()), \
             patch.object(server_module, "Video2mdAsrRunner", return_value=MagicMock()), \
             patch.object(server_module, "Workspace", return_value=MagicMock()):
            with self.assertRaises(SystemExit):
                server_module.main([])
        self.assertEqual(captured_port[0], 0, "default port must be 0 (OS auto-assign), not 8765")

        buf = io.StringIO()
        with contextlib.suppress(SystemExit), contextlib.redirect_stdout(buf):
            server_module.main(["--help"])
        self.assertNotIn("8765", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
