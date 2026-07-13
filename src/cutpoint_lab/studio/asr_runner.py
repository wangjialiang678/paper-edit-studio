from __future__ import annotations

import subprocess
from pathlib import Path
from typing import Any, Protocol

from ..dashscope import convert_dashscope_transcript
from ..io import read_json

DEFAULT_ASR_SCRIPT = Path(
    "/Users/michael/projects/组件模块/audio-asr-suite/go/audio-asr-go/scripts/transcribe_media_recorded.sh"
)


class AsrRunner(Protocol):
    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str) -> dict[str, dict[str, Any]]:
        """返回 convert_dashscope_transcript 的 {transcript, vad} 结构。"""
        ...


class ShellAsrRunner:
    """封装 transcribe_media_recorded.sh：DashScope fun-asr 录音转写（词级时间戳）。

    脚本自行完成 ffmpeg 转 m4a、OSS 上传、任务轮询与产物落盘；
    密钥由脚本 source 其项目 .env 获得，这里不做注入。
    """

    def __init__(self, script_path: Path = DEFAULT_ASR_SCRIPT, *, timeout_seconds: int = 5400):
        self.script_path = Path(script_path)
        self.timeout_seconds = timeout_seconds

    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str) -> dict[str, dict[str, Any]]:
        if not self.script_path.exists():
            raise RuntimeError(f"ASR 脚本不存在：{self.script_path}")
        run_root.mkdir(parents=True, exist_ok=True)
        output_md = run_root / f"{media_path.stem}.md"
        command = [
            str(self.script_path),
            "--input",
            str(media_path),
            "--output",
            str(output_md),
            "--output-dir",
            str(run_root),
            "--yes",
        ]
        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.timeout_seconds,
        )
        if result.returncode != 0:
            tail = "\n".join((result.stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"ASR 脚本失败（exit {result.returncode}）：\n{tail}")
        raw = self._latest_raw_transcript(run_root)
        return convert_dashscope_transcript(read_json(raw), source_video=source_video)

    @staticmethod
    def _latest_raw_transcript(run_root: Path) -> Path:
        candidates = sorted(
            run_root.glob("asr-recorded-*/dashscope-transcript.json"),
            key=lambda path: path.stat().st_mtime,
        )
        if not candidates:
            raise RuntimeError(f"ASR 完成但未找到 dashscope-transcript.json：{run_root}")
        return candidates[-1]
