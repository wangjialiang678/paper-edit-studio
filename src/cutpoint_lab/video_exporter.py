from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor, as_completed
import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from .hw_encoder import pick_video_encoder


_HARDWARE_ENCODERS = {"h264_videotoolbox", "h264_nvenc", "h264_qsv", "h264_amf"}


def export_video_plan(
    source_video: str | Path,
    clip_plan: dict[str, Any] | str | Path,
    output_video: str | Path,
    *,
    work_dir: str | Path | None = None,
    timeout_seconds: int = 1800,
    encoder: str | None = None,
    max_workers: int | None = None,
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
    # 默认走 libx264：实测在真实素材上，硬件编码（VideoToolbox 等）因源解码才是瓶颈而
    # 非编码，反而更慢。硬件编码保留为显式 opt-in——encoder="auto"（或 PE_EXPORT_ENCODER=auto）
    # 才探测硬件；也可直接给具体编码器名强制指定。
    choice = encoder if encoder is not None else os.environ.get("PE_EXPORT_ENCODER")
    if not choice or choice == "libx264":
        selected_encoder = "libx264"
    elif choice == "auto":
        try:
            selected_encoder = pick_video_encoder(ffmpeg)
        except Exception:
            selected_encoder = "libx264"
    else:
        selected_encoder = choice
    source_video_info = _probe_source_video(source) if selected_encoder == "h264_videotoolbox" else {}
    video_args = _video_args_for_encoder(selected_encoder, source_video_info)

    if work_dir is None:
        with tempfile.TemporaryDirectory() as tmp:
            return _export_with_hardware_fallback(
                ffmpeg,
                source,
                ranges,
                plan,
                output,
                Path(tmp),
                timeout_seconds,
                selected_encoder,
                video_args,
                max_workers,
            )
    work = Path(work_dir)
    work.mkdir(parents=True, exist_ok=True)
    return _export_with_hardware_fallback(
        ffmpeg,
        source,
        ranges,
        plan,
        output,
        work,
        timeout_seconds,
        selected_encoder,
        video_args,
        max_workers,
    )


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
    encoder: str,
    video_args: list[str],
    max_workers: int | None,
) -> dict[str, Any]:
    workers = _resolve_worker_count(encoder, len(ranges), max_workers)
    per_job_threads = _per_job_threads(encoder, workers)
    segment_files = [work / f"segment_{index:03d}.mp4" for index in range(1, len(ranges) + 1)]
    errors: list[tuple[int, Exception]] = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = {
            executor.submit(
                _export_segment,
                ffmpeg,
                source,
                item,
                segment_path,
                timeout_seconds,
                encoder=encoder,
                video_args=video_args,
                threads=per_job_threads,
            ): index
            for index, (item, segment_path) in enumerate(zip(ranges, segment_files), start=1)
        }
        cancelled = False
        for future in as_completed(futures):
            try:
                future.result()
            except Exception as error:
                errors.append((futures[future], error))
                if not cancelled:
                    for pending in futures:
                        pending.cancel()
                    cancelled = True

    if errors:
        details = "; ".join(f"segment {index}: {error}" for index, error in errors)
        raise RuntimeError(f"Segment export failed: {details}") from errors[0][1]

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
        "encoder": encoder,
        "workers": workers,
    }


def _export_with_hardware_fallback(
    ffmpeg: str,
    source: Path,
    ranges: list[dict[str, int]],
    plan: dict[str, Any],
    output: Path,
    work: Path,
    timeout_seconds: int,
    encoder: str,
    video_args: list[str],
    max_workers: int | None,
) -> dict[str, Any]:
    try:
        return _export_with_work_dir(
            ffmpeg,
            source,
            ranges,
            plan,
            output,
            work,
            timeout_seconds,
            encoder,
            video_args,
            max_workers,
        )
    except RuntimeError:
        if encoder not in _HARDWARE_ENCODERS:
            raise
        return _export_with_work_dir(
            ffmpeg,
            source,
            ranges,
            plan,
            output,
            work,
            timeout_seconds,
            "libx264",
            _video_args_for_encoder("libx264", {}),
            max_workers,
        )


def _export_segment(
    ffmpeg: str,
    source: Path,
    item: dict[str, int],
    output: Path,
    timeout_seconds: int,
    *,
    encoder: str = "libx264",
    video_args: list[str] | None = None,
    threads: int | None = None,
) -> None:
    command = build_segment_cmd(
        ffmpeg,
        source,
        item["start_ms"],
        item["end_ms"],
        output,
        encoder=encoder,
        video_args=video_args or _video_args_for_encoder(encoder, {}),
        threads=threads,
    )
    subprocess.run(
        command,
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=timeout_seconds,
    )


def build_segment_cmd(
    ffmpeg: str,
    source: str | Path,
    start_ms: int,
    end_ms: int,
    output: str | Path,
    *,
    encoder: str,
    video_args: list[str],
    threads: int | None = None,
) -> list[str]:
    """构造单段重编码命令；输入侧 seek 兼顾快速定位和帧精确。"""
    if end_ms <= start_ms:
        raise ValueError("segment end_ms must be greater than start_ms")
    if threads is not None and threads < 1:
        raise ValueError("threads must be positive")

    start_seconds = start_ms / 1000
    duration_seconds = (end_ms - start_ms) / 1000
    effective_video_args = list(video_args)
    if effective_video_args[:2] != ["-c:v", encoder]:
        effective_video_args = ["-c:v", encoder, *effective_video_args]

    command = [
        ffmpeg,
        "-hide_banner",
        "-nostdin",
        "-loglevel",
        "error",
        "-y",
        "-ss",
        f"{start_seconds:.3f}",
        "-i",
        str(source),
        "-t",
        f"{duration_seconds:.3f}",
    ]
    if threads is not None:
        command.extend(["-threads", str(threads)])
    command.extend(
        [
            "-map",
            "0:v:0?",
            "-map",
            "0:a:0?",
            *effective_video_args,
            "-c:a",
            "aac",
            "-ar",
            "48000",
            "-ac",
            "2",
            "-movflags",
            "+faststart",
            str(output),
        ]
    )
    return command


def _probe_source_video(source: Path) -> dict[str, int]:
    """读取源视频的尺寸和码率；不可用时交由 VideoToolbox 启发式码率处理。"""
    try:
        ffprobe = _required_tool("ffprobe")
        result = subprocess.run(
            [
                ffprobe,
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-show_entries",
                "stream=width,height,bit_rate",
                "-of",
                "json",
                str(source),
            ],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            timeout=60,
        )
        streams = json.loads(result.stdout).get("streams", [])
        if not streams:
            return {}
        stream = streams[0]
        info: dict[str, int] = {}
        for key in ("width", "height", "bit_rate"):
            try:
                value = int(stream[key])
            except (KeyError, TypeError, ValueError):
                continue
            if value > 0:
                info[key] = value
        return info
    except Exception:
        return {}


def _video_args_for_encoder(encoder: str, source_video_info: dict[str, int]) -> list[str]:
    if encoder == "libx264":
        return ["-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p"]
    if encoder == "h264_videotoolbox":
        bitrate = max(source_video_info.get("bit_rate", 0), _videotoolbox_minimum_bitrate(source_video_info))
        return [
            "-c:v",
            "h264_videotoolbox",
            "-b:v",
            str(bitrate),
            "-maxrate",
            str(bitrate * 3 // 2),
            "-bufsize",
            str(bitrate * 2),
            "-allow_sw",
            "0",
            "-pix_fmt",
            "yuv420p",
        ]
    if encoder == "h264_nvenc":
        return [
            "-c:v", "h264_nvenc", "-preset", "p5", "-rc", "vbr", "-cq", "20", "-b:v", "0",
            "-pix_fmt", "yuv420p",
        ]
    if encoder == "h264_qsv":
        return [
            "-c:v", "h264_qsv", "-preset", "medium", "-global_quality", "22", "-look_ahead", "1",
            "-pix_fmt", "yuv420p",
        ]
    if encoder == "h264_amf":
        return [
            "-c:v", "h264_amf", "-quality", "balanced", "-rc", "cqp", "-qp_i", "20", "-qp_p", "22",
            "-qp_b", "24", "-pix_fmt", "yuv420p",
        ]
    return ["-c:v", encoder, "-pix_fmt", "yuv420p"]


def _videotoolbox_minimum_bitrate(source_video_info: dict[str, int]) -> int:
    dimension = source_video_info.get("height", 0) or source_video_info.get("width", 0)
    if dimension and dimension <= 720:
        return 5_000_000
    if dimension and dimension <= 1080:
        return 10_000_000
    if dimension and dimension <= 1440:
        return 16_000_000
    if dimension:
        return 20_000_000
    return 10_000_000


def _resolve_worker_count(encoder: str, range_count: int, max_workers: int | None) -> int:
    configured = max_workers if max_workers is not None else os.environ.get("PE_EXPORT_WORKERS")
    if configured is not None:
        try:
            return max(1, min(range_count, int(configured)))
        except (TypeError, ValueError):
            pass

    if encoder != "libx264":
        return min(range_count, 2)
    cpu = os.cpu_count() or 4
    return max(1, min(range_count, cpu * 3 // 4))


def _per_job_threads(encoder: str, workers: int) -> int | None:
    if encoder != "libx264":
        return None
    return max(1, (os.cpu_count() or 4) // workers)


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
