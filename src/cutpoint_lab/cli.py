from __future__ import annotations

import argparse
import json
import shutil
import sys
import time
from pathlib import Path
from typing import Any

from cutpoint_lab.engine import (
    DEFAULT_API_VAULT_PATH,
    AiSelector,
    AsrRunner,
    EnvStore,
    LlmClient,
    Project,
    Transcript,
    Video2mdAsrRunner,
    Workspace,
    build_plan_from_editor_rows,
    export_video_plan,
    extract_audio,
    ffprobe_duration_ms,
    load_rms_frames,
    load_transcript,
    read_json,
    render_redline_markdown,
    save_suggestion,
    write_json,
    write_srt,
)

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
FRAME_STRATEGIES = {"rms_snap", "anchored_rms", "visual_waveform", "hybrid_valley"}


def _format_srt_time(value_ms: int) -> str:
    value_ms = max(0, int(round(value_ms)))
    hours, remainder = divmod(value_ms, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1_000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def _write_full_srt(transcript: Transcript, output_path: Path) -> None:
    lines: list[str] = []
    for index, segment in enumerate(transcript.segments, start=1):
        lines.extend(
            [
                str(index),
                f"{_format_srt_time(segment.start_ms)} --> {_format_srt_time(segment.end_ms)}",
                segment.text.strip(),
                "",
            ]
        )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text("\n".join(lines), encoding="utf-8")


def ingest_media(project: Project, *, asr_runner: AsrRunner) -> dict[str, Any]:
    source = project.source_path
    if source is None or not source.is_file():
        raise RuntimeError(f"源媒体不存在：{source}")

    project.set_stage("probing", "读取媒体信息")
    duration_ms = ffprobe_duration_ms(source)
    project.update_state(duration_ms=duration_ms)

    project.set_stage("extracting_audio", "提取 16kHz 分析音频")
    if not project.analysis_wav_path.exists():
        extract_audio(source, project.analysis_wav_path)

    project.set_stage("transcribing", "语音识别中")
    converted = asr_runner.transcribe(source, project.asr_dir, source_video=str(source))
    write_json(project.transcript_path, converted["transcript"])
    write_json(project.vad_path, converted["vad"])
    transcript = load_transcript(project.transcript_path)
    full_srt_path = project.dir / f"{source.stem}.srt"
    _write_full_srt(transcript, full_srt_path)
    project.update_state(asr={"segment_count": len(transcript.segments)})
    project.set_stage("ready", "字幕就绪，可以开始剪辑")

    return {
        "outputs": {
            "transcript": str(project.transcript_path),
            "vad": str(project.vad_path),
            "full_srt": str(full_srt_path),
            "analysis_wav": str(project.analysis_wav_path),
        },
        "warnings": [],
    }


def run_select(
    project: Project,
    *,
    selector: AiSelector,
    brief: str = "",
    target_duration: str = "",
    redline_path: str | Path | None = None,
) -> dict[str, Any]:
    if not selector.available():
        raise RuntimeError("缺少 LLM API Key，无法运行 AI 选段")
    if not project.transcript_path.is_file():
        raise RuntimeError(f"字幕文件不存在：{project.transcript_path}")

    transcript = load_transcript(project.transcript_path)
    suggestion = selector.suggest(
        transcript,
        "koubo_tighten",
        brief=brief or "",
        target_duration=target_duration or "",
    )
    suggestion_path = save_suggestion(project.ai_dir, suggestion)
    keeps = set(suggestion.payload.get("keep_segment_ids") or [])
    decisions = {
        str(item["segment_id"]): item
        for item in (suggestion.payload.get("decisions") or [])
        if isinstance(item, dict) and item.get("segment_id") is not None
    }
    rows = [
        {"id": segment.id, "checked": segment.id in keeps, "text": segment.text}
        for segment in transcript.segments
    ]
    selection_path = project.dir / "selection.json"
    write_json(
        selection_path,
        {
            "rows": rows,
            "source": "cli_select",
            "reasons_available": bool(decisions),
        },
    )

    state = project.read_state()
    ai_state = state.get("ai") or {}
    ai_state["koubo_tighten"] = {
        "status": "done",
        "file": str(suggestion_path),
        "warnings": list(suggestion.warnings),
    }
    project.update_state(ai=ai_state)

    outputs = {
        "suggestion": str(suggestion_path),
        "selection": str(selection_path),
    }
    if redline_path is not None:
        target = Path(redline_path).expanduser()
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(
            render_redline_markdown(transcript, keeps, decisions),
            encoding="utf-8",
        )
        outputs["redline"] = str(target)

    return {"outputs": outputs, "warnings": list(suggestion.warnings)}


def run_export(
    project: Project,
    *,
    strategy: str = "hybrid_valley",
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    source = project.source_path
    if source is None or not source.is_file():
        raise RuntimeError(f"源媒体不存在：{source}")
    if not project.transcript_path.is_file():
        raise RuntimeError(f"字幕文件不存在：{project.transcript_path}")

    base = load_transcript(project.transcript_path)
    transcript = Transcript(
        source_video=str(source),
        duration_ms=base.duration_ms,
        selected_segment_ids=base.selected_segment_ids,
        segments=base.segments,
    )
    selection_path = project.dir / "selection.json"
    selection = read_json(selection_path)
    rows = selection.get("rows")
    if not isinstance(rows, list):
        raise ValueError(f"选择文件缺少 rows：{selection_path}")

    warnings: list[str] = []
    effective_strategy = strategy
    frames = []
    if strategy in FRAME_STRATEGIES and project.analysis_wav_path.exists():
        frames = load_rms_frames(project.analysis_wav_path, frame_ms=10)
    if strategy in FRAME_STRATEGIES and not frames:
        effective_strategy = "token_padding"
        warnings.append(
            f"切点策略 {strategy} 缺少可用 RMS 数据，已回退到 token_padding"
        )

    edited, plan = build_plan_from_editor_rows(
        transcript,
        rows,
        strategy=effective_strategy,
        frames=frames,
        vad=None,
    )
    clip_plan_path = project.dir / "clip_plan.json"
    write_json(clip_plan_path, plan)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    output_video = project.exports_dir / f"edited-{stamp}.mp4"
    output_srt = project.exports_dir / f"edited-{stamp}.srt"
    work_dir = project.exports_dir / f"segments-{stamp}"
    try:
        manifest = export_video_plan(source, plan, output_video, work_dir=work_dir)
        write_srt(edited, plan, output_srt)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    completed_at = time.strftime("%Y-%m-%dT%H:%M:%S")
    project.update_state(
        export={
            "status": "done",
            "video": str(output_video),
            "srt": str(output_srt),
            "duration_ms": manifest.get("duration_ms"),
            "range_count": manifest.get("range_count"),
            "completed_at": completed_at,
        }
    )

    outputs = {
        "clip_plan": str(clip_plan_path),
        "video": str(output_video),
        "srt": str(output_srt),
    }
    if out_dir is not None:
        target_dir = Path(out_dir).expanduser().resolve()
        target_dir.mkdir(parents=True, exist_ok=True)
        copied_video = target_dir / output_video.name
        copied_srt = target_dir / output_srt.name
        if copied_video.resolve() != output_video.resolve():
            shutil.copy2(output_video, copied_video)
        if copied_srt.resolve() != output_srt.resolve():
            shutil.copy2(output_srt, copied_srt)
        outputs["copied_video"] = str(copied_video)
        outputs["copied_srt"] = str(copied_srt)

    return {"outputs": outputs, "warnings": warnings}


def run(
    project: Project,
    *,
    asr_runner: AsrRunner,
    selector: AiSelector,
    brief: str = "",
    target_duration: str = "",
    strategy: str = "hybrid_valley",
    redline_path: str | Path | None = None,
    out_dir: str | Path | None = None,
) -> dict[str, Any]:
    ingest_result = ingest_media(project, asr_runner=asr_runner)
    select_result = run_select(
        project,
        selector=selector,
        brief=brief,
        target_duration=target_duration,
        redline_path=redline_path,
    )
    export_result = run_export(project, strategy=strategy, out_dir=out_dir)
    outputs = {
        **ingest_result["outputs"],
        **select_result["outputs"],
        **export_result["outputs"],
    }
    warnings = [
        *ingest_result["warnings"],
        *select_result["warnings"],
        *export_result["warnings"],
    ]
    return {"outputs": outputs, "warnings": warnings}


def _add_shared_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--workspace", default="workspace", help="项目工作区目录")
    parser.add_argument("--json", action="store_true", dest="json_output", help="输出 JSON manifest")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pe", description="文字剪视频的无头批处理 CLI")
    subparsers = parser.add_subparsers(dest="command", required=True)

    transcribe = subparsers.add_parser("transcribe", help="批量转写视频")
    transcribe.add_argument("videos", nargs="+", metavar="VIDEO")
    transcribe.add_argument("--name", default=None, help="项目名称")
    _add_shared_arguments(transcribe)

    select = subparsers.add_parser("select", help="为已有项目生成 AI 选段")
    select.add_argument("projects", nargs="*", metavar="PROJECT")
    select.add_argument("--all", action="store_true", help="处理工作区内全部项目")
    select.add_argument("--brief", default="", help="选段补充要求")
    select.add_argument("--target-duration", default="", help="目标时长")
    select.add_argument("--prompts-dir", default=str(DEFAULT_PROMPTS_DIR), help="提示词目录")
    select_redline = select.add_mutually_exclusive_group()
    select_redline.add_argument("--redline", default=None, metavar="PATH", help="单项目修订文件")
    select_redline.add_argument("--redline-dir", default=None, metavar="DIR", help="批量修订文件目录")
    _add_shared_arguments(select)

    export = subparsers.add_parser("export", help="批量导出已有项目")
    export.add_argument("projects", nargs="*", metavar="PROJECT")
    export.add_argument("--all", action="store_true", help="处理工作区内全部项目")
    export.add_argument("--strategy", default="hybrid_valley", help="切点策略")
    export.add_argument("--out", default=None, metavar="DIR", help="额外复制导出文件到此目录")
    _add_shared_arguments(export)

    run_parser = subparsers.add_parser("run", help="批量执行转写、选段和导出")
    run_parser.add_argument("videos", nargs="+", metavar="VIDEO")
    run_parser.add_argument("--brief", default="", help="选段补充要求")
    run_parser.add_argument("--target-duration", default="", help="目标时长")
    run_parser.add_argument("--strategy", default="hybrid_valley", help="切点策略")
    run_parser.add_argument("--prompts-dir", default=str(DEFAULT_PROMPTS_DIR), help="提示词目录")
    run_redline = run_parser.add_mutually_exclusive_group()
    run_redline.add_argument("--redline", action="store_true", help="生成 Markdown 修订文件")
    run_redline.add_argument("--redline-dir", default=None, metavar="DIR", help="修订文件目录")
    run_parser.add_argument("--out", default=None, metavar="DIR", help="额外复制导出文件到此目录")
    _add_shared_arguments(run_parser)
    return parser


def _selector(workspace: Workspace, prompts_dir: str | Path) -> AiSelector:
    return AiSelector(
        Path(prompts_dir),
        client=LlmClient(
            env_store=EnvStore(),
            api_vault_path=DEFAULT_API_VAULT_PATH,
        ),
        workspace_root=workspace.root,
    )


def _project_ids(workspace: Workspace, requested: list[str], include_all: bool) -> list[str]:
    project_ids = list(requested)
    if include_all:
        project_ids.extend(str(item["id"]) for item in workspace.list_projects())
    return list(dict.fromkeys(project_ids))


def _new_result(*, project_id: str | None, source: str | None) -> dict[str, Any]:
    return {
        "project_id": project_id,
        "source": source,
        "outputs": {},
        "warnings": [],
        "error": None,
    }


def _progress(message: str) -> None:
    print(message, file=sys.stderr, flush=True)


def _handle_transcribe(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        asr_runner = Video2mdAsrRunner()
    except Exception as exc:  # noqa: BLE001 - 每个输入都要记录初始化失败。
        return [
            {
                **_new_result(project_id=None, source=str(Path(raw).expanduser())),
                "error": str(exc),
            }
            for raw in args.videos
        ]

    for raw in args.videos:
        source = Path(raw).expanduser().resolve()
        result = _new_result(project_id=None, source=str(source))
        try:
            if not source.is_file():
                raise ValueError(f"文件不存在：{source}")
            project = workspace.create_project(
                args.name or source.stem,
                source_path=source,
                imported_via="cli",
            )
            result["project_id"] = project.id
            _progress(f"[{project.id}] 开始转写 {source.name}")
            payload = ingest_media(project, asr_runner=asr_runner)
            result["outputs"] = payload["outputs"]
            result["warnings"] = payload["warnings"]
            _progress(f"[{project.id}] 转写完成")
        except Exception as exc:  # noqa: BLE001 - 批量任务逐项隔离失败。
            result["error"] = str(exc)
            _progress(f"[{result['project_id'] or source.name}] 失败：{exc}")
        results.append(result)
    return results


def _redline_for_select(args: argparse.Namespace, project: Project, count: int) -> Path | None:
    if args.redline:
        if count != 1:
            raise ValueError("--redline 仅适用于单个项目；批量请使用 --redline-dir")
        return Path(args.redline).expanduser()
    if args.redline_dir:
        return Path(args.redline_dir).expanduser() / f"{project.id}.md"
    return None


def _handle_select(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    project_ids = _project_ids(workspace, args.projects, args.all)
    if not project_ids:
        raise ValueError("请指定 PROJECT，或使用 --all")
    selector = _selector(workspace, args.prompts_dir)
    results: list[dict[str, Any]] = []
    for project_id in project_ids:
        project = workspace.get(project_id)
        result = _new_result(
            project_id=project_id,
            source=str(project.source_path) if project and project.source_path else None,
        )
        try:
            if project is None:
                raise ValueError(f"项目不存在：{project_id}")
            _progress(f"[{project.id}] 开始 AI 选段")
            payload = run_select(
                project,
                selector=selector,
                brief=args.brief,
                target_duration=args.target_duration,
                redline_path=_redline_for_select(args, project, len(project_ids)),
            )
            result["outputs"] = payload["outputs"]
            result["warnings"] = payload["warnings"]
            _progress(f"[{project.id}] 选段完成")
        except Exception as exc:  # noqa: BLE001 - 批量任务逐项隔离失败。
            result["error"] = str(exc)
            _progress(f"[{project_id}] 失败：{exc}")
        results.append(result)
    return results


def _handle_export(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    project_ids = _project_ids(workspace, args.projects, args.all)
    if not project_ids:
        raise ValueError("请指定 PROJECT，或使用 --all")
    results: list[dict[str, Any]] = []
    for project_id in project_ids:
        project = workspace.get(project_id)
        result = _new_result(
            project_id=project_id,
            source=str(project.source_path) if project and project.source_path else None,
        )
        try:
            if project is None:
                raise ValueError(f"项目不存在：{project_id}")
            _progress(f"[{project.id}] 开始导出")
            payload = run_export(project, strategy=args.strategy, out_dir=args.out)
            result["outputs"] = payload["outputs"]
            result["warnings"] = payload["warnings"]
            _progress(f"[{project.id}] 导出完成")
        except Exception as exc:  # noqa: BLE001 - 批量任务逐项隔离失败。
            result["error"] = str(exc)
            _progress(f"[{project_id}] 失败：{exc}")
        results.append(result)
    return results


def _redline_for_run(args: argparse.Namespace, project: Project) -> Path | None:
    if args.redline_dir:
        return Path(args.redline_dir).expanduser() / f"{project.id}.md"
    if not args.redline:
        return None
    if args.out:
        return Path(args.out).expanduser() / f"{project.id}.md"
    return project.dir / "redline.md"


def _handle_run(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    results: list[dict[str, Any]] = []
    try:
        asr_runner = Video2mdAsrRunner()
    except Exception as exc:  # noqa: BLE001 - 每个输入都要记录初始化失败。
        return [
            {
                **_new_result(project_id=None, source=str(Path(raw).expanduser())),
                "error": str(exc),
            }
            for raw in args.videos
        ]
    selector = _selector(workspace, args.prompts_dir)

    for raw in args.videos:
        source = Path(raw).expanduser().resolve()
        result = _new_result(project_id=None, source=str(source))
        try:
            if not source.is_file():
                raise ValueError(f"文件不存在：{source}")
            project = workspace.create_project(
                source.stem,
                source_path=source,
                imported_via="cli",
            )
            result["project_id"] = project.id
            _progress(f"[{project.id}] 开始处理 {source.name}")
            payload = run(
                project,
                asr_runner=asr_runner,
                selector=selector,
                brief=args.brief,
                target_duration=args.target_duration,
                strategy=args.strategy,
                redline_path=_redline_for_run(args, project),
                out_dir=args.out,
            )
            result["outputs"] = payload["outputs"]
            result["warnings"] = payload["warnings"]
            _progress(f"[{project.id}] 全流程完成")
        except Exception as exc:  # noqa: BLE001 - 批量任务逐项隔离失败。
            result["error"] = str(exc)
            _progress(f"[{result['project_id'] or source.name}] 失败：{exc}")
        results.append(result)
    return results


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    workspace = Workspace(args.workspace)
    try:
        if args.command == "transcribe":
            results = _handle_transcribe(args, workspace)
        elif args.command == "select":
            results = _handle_select(args, workspace)
        elif args.command == "export":
            results = _handle_export(args, workspace)
        else:
            results = _handle_run(args, workspace)
    except Exception as exc:  # noqa: BLE001 - 参数关联错误也输出统一 manifest。
        results = [
            {
                **_new_result(project_id=None, source=None),
                "error": str(exc),
            }
        ]

    ok = all(item["error"] is None for item in results)
    manifest = {"ok": ok, "command": args.command, "results": results}
    if args.json_output:
        print(json.dumps(manifest, ensure_ascii=False))
    else:
        succeeded = sum(1 for item in results if item["error"] is None)
        _progress(f"{args.command}: {succeeded}/{len(results)} 项成功")
    return 0 if ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
