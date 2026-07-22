from __future__ import annotations

import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Protocol

from ..dashscope import convert_dashscope_transcript
from ..io import read_json
from ..video2md import convert_video2md_transcript
from .config import EnvStore

_REPO_ROOT = Path(__file__).resolve().parents[3]

# 内置转写脚本（vendor 自 audio-asr-suite），随仓库分发；可用 --asr-script 覆盖。
DEFAULT_ASR_SCRIPT = _REPO_ROOT / "scripts" / "transcribe_media_recorded.sh"

# 内置 video2md 二进制（vendor，随仓库分发），按平台命名。
VENDORED_BIN_DIR = _REPO_ROOT / "bin"


class AsrRunner(Protocol):
    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str) -> dict[str, dict[str, Any]]:
        """返回 convert_dashscope_transcript 的 {transcript, vad} 结构。"""
        ...


class ShellAsrRunner:
    """封装 scripts/transcribe_media_recorded.sh：DashScope fun-asr 录音转写（词级时间戳）。

    脚本自行完成 ffmpeg 转 m4a、OSS 上传、任务轮询与产物落盘；
    密钥由脚本 source 仓库根 .env 获得（见 .env.example），这里不做注入。
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


def _vendored_binary_name() -> str:
    """按当前平台返回 vendored 二进制文件名（bin/ 下）。"""
    system = sys.platform
    machine = platform.machine().lower()
    if system == "darwin":
        arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
        return f"mp4-md-darwin-{arch}"
    if system == "win32":
        return "mp4-md-windows-amd64.exe"
    # 其他平台（linux 等）未 vendor，交给 PATH/自定义路径回退。
    arch = "arm64" if machine in ("arm64", "aarch64") else "amd64"
    return f"mp4-md-{system}-{arch}"


def resolve_mp4md_binary(explicit: Path | str | None = None) -> Path:
    """定位 mp4-md 二进制，顺序：显式 → env VIDEO2MD_BIN → vendored bin/ → PATH → ~/.video2md-cli/bin。"""
    candidates: list[Path] = []
    if explicit:
        candidates.append(Path(explicit))
    env_bin = os.environ.get("VIDEO2MD_BIN")
    if env_bin:
        candidates.append(Path(env_bin))

    candidates.append(VENDORED_BIN_DIR / _vendored_binary_name())

    exe = "mp4-md.exe" if sys.platform == "win32" else "mp4-md"
    on_path = shutil.which(exe)
    if on_path:
        candidates.append(Path(on_path))
    candidates.append(Path.home() / ".video2md-cli" / "bin" / exe)

    for candidate in candidates:
        if candidate.exists():
            return candidate
    raise RuntimeError(
        "找不到 mp4-md 二进制。请确认仓库 bin/ 下有对应平台的 vendored 二进制，"
        "或设置环境变量 VIDEO2MD_BIN 指向 mp4-md，或用 --asr-binary 指定路径。"
    )


class Video2mdAsrRunner:
    """封装 video2md 的 mp4-md 二进制：DashScope fun-asr，免 OSS（用 DashScope 临时文件空间）。

    只需 DASHSCOPE_API_KEY；mp4-md 自己完成 ffmpeg 抽音轨、上传、任务轮询。
    通过 --emit-json 让 mp4-md 额外落一份结构化词级时间戳 JSON，转换后喂给剪辑流水线。

    凭据来源：仓库根 .env（沿用旧习惯，见 .env.example）叠加当前进程环境；
    显式 env 优先于 .env。
    """

    def __init__(
        self,
        binary_path: Path | str | None = None,
        *,
        env_file: Path | None = None,
        timeout_seconds: int = 5400,
    ):
        self.binary_path = resolve_mp4md_binary(binary_path)
        self.env_file = env_file if env_file is not None else (_REPO_ROOT / ".env")
        self.timeout_seconds = timeout_seconds

    def transcribe(self, media_path: Path, run_root: Path, *, source_video: str) -> dict[str, dict[str, Any]]:
        media_path = Path(media_path)
        run_root = Path(run_root)
        run_root.mkdir(parents=True, exist_ok=True)

        env = self._build_env()
        if not env.get("DASHSCOPE_API_KEY"):
            raise RuntimeError("缺少 DASHSCOPE_API_KEY（在仓库根 .env 或环境变量中设置）")

        command = [
            str(self.binary_path),
            "--out-dir",
            str(run_root),
            "--emit-json",
            "--timestamps",
            "none",
            str(media_path),
        ]
        vocab = env.get("ASR_BASE_VOCABULARY_ID")
        if vocab:
            command[1:1] = ["--vocab", vocab]

        result = subprocess.run(
            command,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=self.timeout_seconds,
            env=env,
        )
        if result.returncode != 0:
            tail = "\n".join((result.stderr or "").strip().splitlines()[-8:])
            raise RuntimeError(f"mp4-md 失败（exit {result.returncode}）：\n{tail}")

        transcript_json = run_root / f"{media_path.stem}.transcript.json"
        if not transcript_json.exists():
            raise RuntimeError(f"mp4-md 完成但未找到 {transcript_json.name}：{run_root}")
        return convert_video2md_transcript(read_json(transcript_json), source_video=source_video)

    def _build_env(self) -> dict[str, str]:
        env = dict(os.environ)
        # .env 仅补齐未在进程环境里设置的键，显式环境变量优先。
        for key, value in EnvStore(self.env_file).read().items():
            env.setdefault(key, value)
        return env
