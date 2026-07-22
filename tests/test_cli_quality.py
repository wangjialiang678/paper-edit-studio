from __future__ import annotations

import hashlib
import io
import json
import tempfile
import unittest
import wave
from contextlib import redirect_stderr, redirect_stdout
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch

from cutpoint_lab.cli import main
from cutpoint_lab.io import read_json, write_json
from cutpoint_lab.studio.workspace import Workspace


def _project_payload(source: str, text: str = "WEB CODING 与 web coding") -> dict:
    return {
        "source_video": source,
        "duration_ms": 1000,
        "selected_segment_ids": ["s1", "s2"],
        "segments": [
            {
                "id": "s1",
                "start_ms": 0,
                "end_ms": 500,
                "text": text,
                "tokens": [{"text": text, "start_ms": 0, "end_ms": 480}],
            },
            {
                "id": "s2",
                "start_ms": 600,
                "end_ms": 1000,
                "text": "不命中",
                "tokens": [{"text": "不命中", "start_ms": 620, "end_ms": 980}],
            },
        ],
    }


def _write_wav(path: Path) -> None:
    with wave.open(str(path), "wb") as handle:
        handle.setnchannels(1)
        handle.setsampwidth(2)
        handle.setframerate(16000)
        handle.writeframes(b"\x00\x40" * 16000)


def _run_json(argv: list[str]):
    stdout = io.StringIO()
    stderr = io.StringIO()
    with redirect_stdout(stdout), redirect_stderr(stderr):
        code = main(argv)
    return code, json.loads(stdout.getvalue()), stderr.getvalue()


class _CountingRunner:
    def __init__(self):
        self.calls = 0

    def transcribe(self, _media_path: Path, _run_root: Path, *, source_video: str):
        self.calls += 1
        return deepcopy(
            {
                "transcript": _project_payload(source_video, text=f"第 {self.calls} 次"),
                "vad": {"duration_ms": 1000, "speech_intervals": [], "source": "fake"},
            }
        )


class CorrectionsCliTests(unittest.TestCase):
    def test_add_list_fix_and_undo_end_to_end(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            workspace = Workspace(workspace_path)
            source = root / "source.wav"
            _write_wav(source)
            project = workspace.create_project("quality", source_path=source, imported_via="cli")
            write_json(project.transcript_path, _project_payload(str(source)))
            selection_path = project.dir / "selection.json"
            write_json(
                selection_path,
                {
                    "rows": [
                        {"id": "s1", "text": "WEB CODING 与 web coding", "checked": True},
                        {"id": "s2", "text": "不命中", "checked": False},
                    ]
                },
            )

            code, added, _ = _run_json(
                [
                    "corrections",
                    "add",
                    "web coding=>vibe coding",
                    "--term",
                    "--workspace",
                    str(workspace_path),
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertTrue(added["ok"])
            self.assertEqual(added["results"][0]["pairs"][0]["wrong"], ["web coding"])

            code, listed, _ = _run_json(
                ["corrections", "list", "--workspace", str(workspace_path), "--json"]
            )
            self.assertEqual(code, 0)
            self.assertEqual(listed["results"][0]["pairs"][0]["right"], "vibe coding")

            code, fixed, stderr = _run_json(
                [
                    "fix",
                    project.id,
                    "--dict-only",
                    "--yes",
                    "--workspace",
                    str(workspace_path),
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertIn("web coding => vibe coding", stderr)
            fix_result = fixed["results"][0]
            self.assertEqual(fix_result["applied"], 2)
            self.assertEqual(read_json(selection_path)["rows"][0]["text"], "vibe coding 与 vibe coding")
            change_id = fix_result["changeset_id"]

            code, undone, _ = _run_json(
                [
                    "undo",
                    project.id,
                    change_id,
                    "--workspace",
                    str(workspace_path),
                    "--json",
                ]
            )
            self.assertEqual(code, 0)
            self.assertEqual(undone["results"][0]["reverted"], 1)
            self.assertEqual(read_json(selection_path)["rows"][0]["text"], "WEB CODING 与 web coding")

    def test_fix_without_yes_only_previews_when_user_declines(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            workspace = Workspace(workspace_path)
            source = root / "source.wav"
            _write_wav(source)
            project = workspace.create_project("preview", source_path=source, imported_via="cli")
            write_json(project.transcript_path, _project_payload(str(source)))
            selection_path = project.dir / "selection.json"
            write_json(selection_path, {"rows": [{"id": "s1", "text": "web coding", "checked": True}]})
            corrections = workspace_path / "_settings" / "corrections.json"
            write_json(
                corrections,
                {"pairs": [{"wrong": ["web coding"], "right": "vibe coding", "is_term": False}]},
            )

            with patch("sys.stdin", io.StringIO("n\n")):
                code, manifest, _ = _run_json(
                    [
                        "fix",
                        project.id,
                        "--dict-only",
                        "--workspace",
                        str(workspace_path),
                        "--json",
                    ]
                )

            self.assertEqual(code, 0)
            self.assertEqual(manifest["results"][0]["applied"], 0)
            self.assertNotIn("changeset_id", manifest["results"][0])
            self.assertEqual(read_json(selection_path)["rows"][0]["text"], "web coding")


class CacheCliTests(unittest.TestCase):
    def test_cache_backfill_registers_existing_project(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            cache_dir = root / "cache"
            workspace = Workspace(workspace_path)
            source = root / "source.bin"
            source.write_bytes(b"backfill-media")
            project = workspace.create_project("cached", source_path=source, imported_via="cli")
            transcript = _project_payload(str(source))
            vad = {"duration_ms": 1000, "speech_intervals": [], "source": "fake"}
            write_json(project.transcript_path, transcript)
            write_json(project.vad_path, vad)

            with patch.dict("os.environ", {"TRANSCRIPT_CACHE_DIR": str(cache_dir)}):
                code, manifest, _ = _run_json(
                    ["cache", "backfill", "--workspace", str(workspace_path), "--json"]
                )

            self.assertEqual(code, 0)
            result = manifest["results"][0]
            self.assertEqual(result["registered"], 1)
            digest = hashlib.sha256(source.read_bytes()).hexdigest()
            self.assertEqual(read_json(cache_dir / digest / "transcript.json"), transcript)
            self.assertEqual(read_json(cache_dir / digest / "vad.json"), vad)

    def test_transcribe_command_wraps_runner_and_hits_cache(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            workspace_path = root / "workspace"
            cache_dir = root / "cache"
            source = root / "source.wav"
            _write_wav(source)
            runner = _CountingRunner()

            with patch("cutpoint_lab.cli.Video2mdAsrRunner", return_value=runner), patch.dict(
                "os.environ", {"TRANSCRIPT_CACHE_DIR": str(cache_dir)}
            ):
                first = _run_json(
                    ["transcribe", str(source), "--workspace", str(workspace_path), "--json"]
                )
                second = _run_json(
                    ["transcribe", str(source), "--workspace", str(workspace_path), "--json"]
                )

            self.assertEqual(first[0], 0)
            self.assertEqual(second[0], 0)
            self.assertEqual(runner.calls, 1)
            self.assertIn("复用已有字幕（内容指纹命中）", second[2])


if __name__ == "__main__":
    unittest.main()
