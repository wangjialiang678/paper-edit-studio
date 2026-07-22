import json
import os
import stat
import sys
import tempfile
import unittest
from pathlib import Path

from cutpoint_lab.studio.asr_runner import (
    Video2mdAsrRunner,
    resolve_mp4md_binary,
)

FAKE_BINARY = r"""#!/usr/bin/env bash
set -euo pipefail
out_dir=""
input=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --out-dir) out_dir="$2"; shift 2 ;;
    --emit-json) shift ;;
    --timestamps) shift 2 ;;
    --vocab) shift 2 ;;
    *) input="$1"; shift ;;
  esac
done
stem="$(basename "$input")"
stem="${stem%.*}"
cat > "$out_dir/$stem.transcript.json" <<'JSON'
{
  "schema": "video2md/transcript@1",
  "source": "clip.mp4",
  "segments": [
    {"index": 1, "begin_ms": 100, "end_ms": 900, "text": "你好世界",
     "words": [
       {"begin_ms": 120, "end_ms": 300, "text": "你好", "confidence": 0.9},
       {"begin_ms": 420, "end_ms": 780, "text": "世界", "confidence": 0.8}
     ]}
  ]
}
JSON
echo "$input -> $out_dir/$stem.md"
"""


@unittest.skipIf(sys.platform == "win32", "fake bash binary not runnable on Windows CI")
class Video2mdRunnerTests(unittest.TestCase):
    def _make_fake_binary(self, tmp: Path) -> Path:
        binary = tmp / "fake-mp4-md"
        binary.write_text(FAKE_BINARY, encoding="utf-8")
        binary.chmod(binary.stat().st_mode | stat.S_IEXEC | stat.S_IRWXU)
        return binary

    def test_transcribe_runs_binary_and_converts_json(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            binary = self._make_fake_binary(tmp)
            env_file = tmp / ".env"
            env_file.write_text("DASHSCOPE_API_KEY=sk-test\n", encoding="utf-8")
            media = tmp / "clip.mp4"
            media.write_text("fake", encoding="utf-8")
            run_root = tmp / "asr"

            runner = Video2mdAsrRunner(binary, env_file=env_file)
            converted = runner.transcribe(media, run_root, source_video=str(media))

            self.assertIn("transcript", converted)
            self.assertIn("vad", converted)
            self.assertEqual(converted["transcript"]["selected_segment_ids"], ["sentence_0001"])
            self.assertEqual(converted["transcript"]["segments"][0]["text"], "你好世界")
            self.assertEqual(converted["vad"]["source"], "video2md_word_timestamps_proxy")
            self.assertEqual(converted["transcript"]["duration_ms"], 900)

    def test_missing_api_key_raises(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            binary = self._make_fake_binary(tmp)
            env_file = tmp / ".env"  # 不存在
            media = tmp / "clip.mp4"
            media.write_text("fake", encoding="utf-8")

            saved = os.environ.pop("DASHSCOPE_API_KEY", None)
            try:
                runner = Video2mdAsrRunner(binary, env_file=env_file)
                with self.assertRaises(RuntimeError) as ctx:
                    runner.transcribe(media, tmp / "asr", source_video=str(media))
                self.assertIn("DASHSCOPE_API_KEY", str(ctx.exception))
            finally:
                if saved is not None:
                    os.environ["DASHSCOPE_API_KEY"] = saved

    def test_resolve_prefers_explicit_binary(self):
        with tempfile.TemporaryDirectory() as tmp_str:
            tmp = Path(tmp_str)
            binary = tmp / "custom-mp4-md"
            binary.write_text("#!/bin/sh\n", encoding="utf-8")
            binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
            self.assertEqual(resolve_mp4md_binary(binary), binary)


if __name__ == "__main__":
    unittest.main()
