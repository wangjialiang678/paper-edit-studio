from __future__ import annotations

import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.cli import ingest_media, run_export, run_select
from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.ai_selector import AiSelector
from cutpoint_lab.studio.workspace import Workspace


REPO_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = REPO_ROOT / "prompts"


def _tools_available() -> bool:
    return shutil.which("ffmpeg") is not None and shutil.which("ffprobe") is not None


def _transcript_payload(source_video: str) -> dict:
    return {
        "source_video": source_video,
        "duration_ms": 1000,
        "selected_segment_ids": ["sentence_0001", "sentence_0002", "sentence_0003"],
        "segments": [
            {
                "id": "sentence_0001",
                "start_ms": 40,
                "end_ms": 280,
                "text": "先讲核心观点。",
                "tokens": [
                    {"text": "先讲核心观点。", "start_ms": 60, "end_ms": 260},
                ],
            },
            {
                "id": "sentence_0002",
                "start_ms": 350,
                "end_ms": 600,
                "text": "这句只是重复。",
                "tokens": [
                    {"text": "这句只是重复。", "start_ms": 370, "end_ms": 580},
                ],
            },
            {
                "id": "sentence_0003",
                "start_ms": 680,
                "end_ms": 920,
                "text": "最后给出方法。",
                "tokens": [
                    {"text": "最后给出方法。", "start_ms": 700, "end_ms": 900},
                ],
            },
        ],
    }


class FakeAsrRunner:
    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str):
        return {
            "transcript": _transcript_payload(source_video),
            "vad": {
                "duration_ms": 1000,
                "speech_intervals": [
                    {"start_ms": 30, "end_ms": 610, "confidence": 0.99},
                    {"start_ms": 670, "end_ms": 930, "confidence": 0.98},
                ],
                "source": "fake",
            },
        }


class FakeLlmClient:
    def available(self) -> bool:
        return True

    def chat_json(self, system, user, **kwargs):
        return {
            "summary": "保留观点和方法，删除重复句。",
            "decisions": [
                {
                    "segment_id": "sentence_0001",
                    "keep": True,
                    "reason": "开场核心观点",
                    "labels": ["hook", "insight"],
                },
                {
                    "segment_id": "sentence_0002",
                    "keep": False,
                    "reason": "与上一句重复",
                    "labels": ["repeat"],
                },
                {
                    "segment_id": "sentence_0003",
                    "keep": True,
                    "reason": "包含可执行方法",
                    "labels": ["method"],
                },
            ],
        }


def _selector() -> AiSelector:
    return AiSelector(PROMPTS_DIR, client=FakeLlmClient())


def _make_test_video(path: Path) -> None:
    subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc=duration=1:size=160x120:rate=25",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=440:duration=1",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "28",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )


def _assert_nonempty_file(testcase: unittest.TestCase, path: Path) -> None:
    testcase.assertTrue(path.is_file(), f"missing output: {path}")
    testcase.assertGreater(path.stat().st_size, 0, f"empty output: {path}")


class SelectFlowTests(unittest.TestCase):
    def test_select_writes_full_selection_ai_state_and_redline(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            workspace = Workspace(root / "workspace")
            project = workspace.create_project(
                "CLI 选段",
                source_path=source,
                imported_via="path",
            )
            write_json(project.transcript_path, _transcript_payload(str(source)))
            redline_path = root / "selection-redline.md"

            run_select(
                project,
                selector=_selector(),
                brief="删掉重复表达",
                target_duration="1 秒内",
                redline_path=redline_path,
            )

            selection_path = project.cut_dir("default") / "edl.json"
            _assert_nonempty_file(self, selection_path)
            selection = read_json(selection_path)
            self.assertEqual(selection["source"], "cli_select")
            self.assertTrue(selection["reasons_available"])
            self.assertEqual(
                [(row["id"], row["checked"], row["text"]) for row in selection["rows"]],
                [
                    ("sentence_0001", True, "先讲核心观点。"),
                    ("sentence_0002", False, "这句只是重复。"),
                    ("sentence_0003", True, "最后给出方法。"),
                ],
            )

            ai_state = project.read_state()["ai"]["koubo_tighten"]
            self.assertEqual(ai_state["status"], "done")
            _assert_nonempty_file(self, Path(ai_state["file"]))

            _assert_nonempty_file(self, redline_path)
            redline = redline_path.read_text(encoding="utf-8")
            self.assertIn("- 原始 3 句 / 保留 2 句 / 删除 1 句", redline)
            self.assertIn("~~这句只是重复。~~ · 与上一句重复", redline)
            self.assertNotIn("~~先讲核心观点。~~", redline)


@unittest.skipUnless(_tools_available(), "ffmpeg/ffprobe not installed — skipping CLI integration test")
class CliIntegrationTests(unittest.TestCase):
    def test_offline_ingest_select_export_flow_writes_all_artifacts(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            source = root / "source.mp4"
            _make_test_video(source)
            workspace = Workspace(root / "workspace")
            project = workspace.create_project(
                "CLI 全流程",
                source_path=source,
                imported_via="path",
            )

            ingest_result = ingest_media(project, asr_runner=FakeAsrRunner())
            self.assertIn("outputs", ingest_result)
            self.assertIn("warnings", ingest_result)
            _assert_nonempty_file(self, project.transcript_path)
            _assert_nonempty_file(self, project.vad_path)
            _assert_nonempty_file(self, project.dir / "source.srt")

            redline_path = project.dir / "selection-redline.md"
            select_result = run_select(
                project,
                selector=_selector(),
                brief="保留观点和方法",
                target_duration="1 秒内",
                redline_path=redline_path,
            )
            self.assertIn("outputs", select_result)
            self.assertIn("warnings", select_result)
            _assert_nonempty_file(self, project.cut_dir("default") / "edl.json")
            _assert_nonempty_file(self, redline_path)

            export_result = run_export(project, strategy="token_padding")
            self.assertIn("outputs", export_result)
            self.assertIn("warnings", export_result)
            _assert_nonempty_file(self, project.cut_clip_plan_path("default"))
            output_video = Path(export_result["outputs"]["video"])
            output_srt = Path(export_result["outputs"]["srt"])
            self.assertEqual(output_video.suffix, ".mp4")
            self.assertEqual(output_srt.suffix, ".srt")
            _assert_nonempty_file(self, output_video)
            _assert_nonempty_file(self, output_srt)


class CliSourceGuardTests(unittest.TestCase):
    def test_cli_source_does_not_reference_removed_studio_layer(self):
        source = (REPO_ROOT / "src" / "cutpoint_lab" / "cli.py").read_text(encoding="utf-8")
        self.assertNotIn("studio", source)


if __name__ == "__main__":
    unittest.main()
