from __future__ import annotations

import json
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any


def export_video_plan(
    source_video: str | Path,
    clip_plan: dict[str, Any] | str | Path,
    output_video: str | Path,
    *,
    work_dir: str | Path | None = None,
    timeout_seconds: int = 1800,
) -> dict[str, Any]:
    plan = _load_plan(clip_plan)
    source = Path(source_video)
    _validate_plan_source(plan, source)
    duration_ms = ffprobe_duration_ms(source)
    ranges = _valid_ranges(plan, duration_ms=duration_ms)
    if not ranges:
        raise ValueError("Clip plan contains no valid ranges")

    output = Path(output_video)
    output.parent.mkdir(parents=True, exist_ok=True)
    ffmpeg = _required_tool("ffmpeg")

    if work_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _export_with_work_dir(ffmpeg, source, ranges, plan, output, Path(tmp), timeout_seconds)
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    return _export_with_work_dir(ffmpeg, source, ranges, plan, output, work, timeout_seconds)


def ffprobe_duration_ms(media_path: str | Path, *, timeout_seconds: int = 60) -> int:
    ffprobe = _required_tool("ffprobe")
    result = subprocess.run(
        [
            ffprobe,
            "-v",
            "error",
            "-show_entries",
            "format=duration",
            "-of",
            "default=noprint_wrappers=1:nokey=1",
            str(media_path),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        timeout=timeout_seconds,
    )
    return round(float(result.stdout.strip()) * 1000)


def _export_with_work_dir(
    ffmpeg: str,
    source: Path,
    ranges: list[dict[str, int]],
    plan: dict[str, Any],
    output: Path,
    work: Path,
    timeout_seconds: int,
) -> dict[str, Any]:
    segment_files = []
    for index, item in enumerate(ranges, start=1):
        segment_path = work / f"segment_{index:03d}.mp4"
        _export_segment(ffmpeg, source, item, segment_path, timeout_seconds)
        segment_files.append(segment_path)

    concat_list = work / "concat.txt"
    concat_list.write_text(
        "\n".join(f"file '{_concat_escape(path)}'" for path in segment_files) + "\n",
        encoding="utf-8",
    )
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c",
            "copy",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )
    return {
        "source_video": str(source),
        "output_video": str(output),
        "strategy": plan.get("strategy"),
        "range_count": len(ranges),
        "duration_ms": ffprobe_duration_ms(output),
        "ranges": ranges,
        "segments": [str(path) for path in segment_files],
        "concat_list": str(concat_list),
    }


def _export_segment(ffmpeg: str, source: Path, item: dict[str, int], output: Path, timeout_seconds: int) -> None:
    start_seconds = item["start_ms"] / 1000
    duration_seconds = (item["end_ms"] - item["start_ms"]) / 1000
    subprocess.run(
        [
            ffmpeg,
            "-hide_banner",
            "-nostdin",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-ss",
            f"{start_seconds:.3f}",
            "-t",
            f"{duration_seconds:.3f}",
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            "-c:v",
            "libx264",
            "-preset",
            "veryfast",
            "-crf",
            "20",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )


def _load_plan(clip_plan: dict[str, Any] | str | Path) -> dict[str, Any]:
    if isinstance(clip_plan, dict):
        return clip_plan
    return json.loads(Path(clip_plan).read_text(encoding="utf-8"))


def _valid_ranges(plan: dict[str, Any], *, duration_ms: int) -> list[dict[str, int]]:
    raw_ranges = plan.get("ranges")
    if raw_ranges is None and isinstance(plan.get("clip_plan"), dict):
        raw_ranges = plan["clip_plan"].get("ranges")
    ranges = []
    for item in raw_ranges or []:
        start_ms = int(item.get("start_ms", 0))
        end_ms = int(item.get("end_ms", 0))
        if start_ms < 0 or end_ms < 0:
            raise ValueError("Clip plan ranges cannot contain negative timestamps")
        start_ms = min(start_ms, duration_ms)
        end_ms = min(end_ms, duration_ms)
        if end_ms <= start_ms:
            continue
        ranges.append({"start_ms": start_ms, "end_ms": end_ms})
    return ranges


def _validate_plan_source(plan: dict[str, Any], source: Path) -> None:
    expected = plan.get("source_video")
    if expected is None and isinstance(plan.get("clip_plan"), dict):
        expected = plan["clip_plan"].get("source_video")
    if expected and not _paths_match(expected, source):
        raise ValueError("clip plan source_video does not match source_video")


def _required_tool(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise RuntimeError(f"{name} not found")
    return path


def _concat_escape(path: Path) -> str:
    return str(path).replace("'", "'\\''")


def _paths_match(left: str | Path, right: str | Path) -> bool:
    left_path = Path(left).expanduser()
    right_path = Path(right).expanduser()
    if left_path.exists() and right_path.exists():
        return left_path.resolve() == right_path.resolve()
    return str(left_path) == str(right_path)
