"""跨平台 ffmpeg H.264 硬件编码器探测。"""

from __future__ import annotations

import subprocess
import sys


def list_ffmpeg_encoders(ffmpeg_bin: str) -> set[str]:
    """返回当前 ffmpeg 构建声明支持的视频编码器名称。"""
    try:
        result = subprocess.run(
            [ffmpeg_bin, "-hide_banner", "-encoders"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=5.0,
        )
    except (OSError, subprocess.SubprocessError):
        return set()

    if result.returncode != 0:
        return set()

    encoders: set[str] = set()
    for line in result.stdout.splitlines():
        fields = line.split()
        if len(fields) < 2 or fields[1] == "=":
            continue
        flags = fields[0]
        if len(flags) == 6 and flags.startswith("V"):
            encoders.add(fields[1])
    return encoders


def probe_encoder(ffmpeg_bin: str, name: str, timeout: float = 5.0) -> bool:
    """用单帧黑色视频验证编码器能在当前驱动和硬件上实际初始化。"""
    command = [
        ffmpeg_bin,
        "-hide_banner",
        "-loglevel",
        "error",
        "-f",
        "lavfi",
        "-i",
        "color=c=black:s=128x128:d=1",
        "-frames:v",
        "1",
        "-c:v",
        name,
        "-f",
        "null",
        "-",
    ]
    try:
        result = subprocess.run(
            command,
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError):
        return False
    return result.returncode == 0


def pick_video_encoder(ffmpeg_bin: str, *, override: str | None = None) -> str:
    """选择可用硬编；不能确认可用时始终回退到现有 libx264 路径。"""
    if override:
        return override

    candidates = {
        "darwin": ["h264_videotoolbox"],
        "win32": ["h264_nvenc", "h264_qsv", "h264_amf"],
    }.get(sys.platform, [])
    if not candidates:
        return "libx264"

    try:
        advertised = list_ffmpeg_encoders(ffmpeg_bin)
        for name in candidates:
            if name in advertised and probe_encoder(ffmpeg_bin, name):
                return name
    except Exception:
        return "libx264"
    return "libx264"
