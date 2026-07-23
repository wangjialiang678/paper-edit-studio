from __future__ import annotations

import argparse
import copy
import http.server
import json
import shutil
import sys
import threading
import time
import webbrowser
from pathlib import Path
from typing import Any

from cutpoint_lab.engine import (
    DEFAULT_API_VAULT_PATH,
    AiSelector,
    AsrRunner,
    CachingAsrRunner,
    CorrectionSet,
    EnvStore,
    LlmClient,
    Project,
    PromptStore,
    Transcript,
    Video2mdAsrRunner,
    VocabularyClient,
    VocabularyError,
    Workspace,
    align_reference,
    apply_corrections,
    backfill_cache_entry,
    build_plan_from_selection,
    export_video_plan,
    extract_audio,
    ffprobe_duration_ms,
    load_changeset,
    load_report,
    load_rms_frames,
    load_transcript,
    merge_report,
    parse_reference,
    preview_corrections,
    read_json,
    render_review_html,
    render_redline_markdown,
    resolve_transcript_cache_dir,
    resolve_secret_key,
    review_quality,
    save_changeset,
    save_report,
    save_suggestion,
    scan_confidence,
    undo_changeset,
    write_json,
    write_srt,
)

DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[2] / "prompts"
FRAME_STRATEGIES = {"rms_snap", "anchored_rms", "visual_waveform", "hybrid_valley"}
MAX_REVIEW_CONFIRM_BODY_BYTES = 10 * 1024 * 1024


class _ReviewConfirmationServer:
    """仅供一次本地确认使用的 HTTP 服务。"""

    def __init__(self, page_html: str, selection_path: Path) -> None:
        self._page = page_html.encode("utf-8")
        self._selection_path = selection_path
        self.confirmed = threading.Event()
        self._confirmation_lock = threading.Lock()

        owner = self

        class RequestHandler(http.server.BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # noqa: N802 - HTTP handler hook.
                if self.path != "/":
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.send_header("Content-Length", str(len(owner._page)))
                self.end_headers()
                self.wfile.write(owner._page)

            def do_POST(self) -> None:  # noqa: N802 - HTTP handler hook.
                if self.path != "/confirm":
                    self.send_error(404)
                    return
                owner._confirm(self)

            def log_message(self, _format: str, *_args: object) -> None:
                return

        self._server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), RequestHandler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(
            target=self._server.serve_forever,
            name="pe-review-confirm",
            daemon=True,
        )

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}/"

    def start(self) -> None:
        self._thread.start()

    def close(self) -> None:
        self._server.shutdown()
        self._server.server_close()
        self._thread.join(timeout=5)

    def _confirm(self, request: http.server.BaseHTTPRequestHandler) -> None:
        content_length = request.headers.get("Content-Length")
        try:
            body_length = int(content_length) if content_length is not None else -1
        except ValueError:
            body_length = -1
        if body_length < 0:
            self._send_json(request, 411, {"ok": False, "error": "缺少有效 Content-Length"})
            return
        if body_length > MAX_REVIEW_CONFIRM_BODY_BYTES:
            self._send_json(request, 413, {"ok": False, "error": "确认内容超过 10MB"})
            return

        try:
            payload = json.loads(request.rfile.read(body_length).decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(request, 400, {"ok": False, "error": "确认内容不是合法 JSON"})
            return
        if not isinstance(payload, dict) or not isinstance(payload.get("rows"), list) or not payload["rows"]:
            self._send_json(request, 400, {"ok": False, "error": "确认内容缺少非空 rows 列表"})
            return

        with self._confirmation_lock:
            if self.confirmed.is_set():
                self._send_json(request, 200, {"ok": True})
                return
            try:
                write_json(self._selection_path, payload)
            except OSError as exc:
                self._send_json(request, 500, {"ok": False, "error": str(exc)})
                return
            self._send_json(request, 200, {"ok": True})
            self.confirmed.set()
            threading.Thread(
                target=self._server.shutdown,
                name="pe-review-confirm-shutdown",
                daemon=True,
            ).start()

    @staticmethod
    def _send_json(
        request: http.server.BaseHTTPRequestHandler,
        status: int,
        payload: dict[str, Any],
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request.send_response(status)
        request.send_header("Content-Type", "application/json; charset=utf-8")
        request.send_header("Content-Length", str(len(body)))
        request.end_headers()
        request.wfile.write(body)
        request.wfile.flush()


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
    if converted.get("cache") == "hit":
        _progress("复用已有字幕（内容指纹命中）")
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


def run_review(
    project: Project,
    *,
    out_path: str | Path | None = None,
    serve: bool = False,
    timeout_seconds: int = 1800,
    open_browser: bool = False,
) -> dict[str, Any]:
    if not project.transcript_path.is_file():
        raise RuntimeError(f"字幕文件不存在：{project.transcript_path}")

    transcript = load_transcript(project.transcript_path)
    selection_path = project.dir / "selection.json"
    order: list[str] | None = None
    if selection_path.is_file():
        selection = read_json(selection_path)
        rows = selection.get("rows")
        if not isinstance(rows, list):
            raise ValueError(f"选择文件缺少 rows：{selection_path}")
        raw_order = selection.get("order")
        if isinstance(raw_order, list):
            order = [str(segment_id) for segment_id in raw_order]
    else:
        rows = [
            {"id": segment.id, "checked": True, "text": segment.text}
            for segment in transcript.segments
        ]

    decisions: dict[str, dict[str, Any]] | None = None
    try:
        tighten = (project.read_state().get("ai") or {}).get("koubo_tighten") or {}
        if tighten.get("status") == "done" and tighten.get("file"):
            suggestion = read_json(tighten["file"])
            decision_rows = suggestion.get("decisions")
            if isinstance(decision_rows, list):
                decisions = {
                    str(item["segment_id"]): item
                    for item in decision_rows
                    if isinstance(item, dict) and item.get("segment_id") is not None
                }
    except Exception:  # noqa: BLE001 - AI 理由缺失不影响人工确认页生成。
        decisions = None

    target = Path(out_path).expanduser() if out_path is not None else project.dir / "review.html"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(
        render_review_html(transcript, rows, decisions, order=order),
        encoding="utf-8",
    )
    outputs: dict[str, Any] = {"review_html": str(target)}
    if not serve:
        if open_browser:
            webbrowser.open(target.resolve().as_uri())
        return {"outputs": outputs, "warnings": []}

    if timeout_seconds <= 0:
        raise ValueError("确认超时时间必须大于 0 秒")
    confirmation_page = render_review_html(
        transcript,
        rows,
        decisions,
        confirm_url="/confirm",
        order=order,
    )
    server = _ReviewConfirmationServer(confirmation_page, selection_path)
    server.start()
    _progress(f"[{project.id}] 确认页面：{server.url}")
    _progress("在网页里确认后我会自动继续")
    try:
        if open_browser:
            webbrowser.open(server.url)
        if not server.confirmed.wait(timeout_seconds):
            timeout_label = f"{timeout_seconds:g}"
            raise RuntimeError(
                f"确认超时（{timeout_label}s）：可在页面点“下载 selection.json”后手动覆盖 "
                f"workspace/{project.id}/selection.json"
            )
    finally:
        server.close()

    outputs.update({"selection": str(selection_path), "confirmed": True})
    return {"outputs": outputs, "warnings": []}


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

    edited, plan = build_plan_from_selection(
        transcript,
        selection,
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
        "reordered": bool(plan.get("reordered")),
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

    review = subparsers.add_parser("review", help="生成交互式剪辑确认 HTML")
    review.add_argument("projects", nargs="*", metavar="PROJECT")
    review.add_argument("--all", action="store_true", help="处理工作区内全部项目")
    review.add_argument("--out", default=None, metavar="PATH", help="单项目 HTML 输出路径")
    review.add_argument("--serve", action="store_true", help="启动本机确认服务并等待网页确认")
    review.add_argument(
        "--timeout",
        type=int,
        default=1800,
        metavar="SECONDS",
        help="--serve 的确认等待秒数（默认：1800）",
    )
    review.add_argument("--open", action="store_true", help="生成后在浏览器中打开")
    _add_shared_arguments(review)

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

    corrections = subparsers.add_parser("corrections", help="管理字幕纠错词典")
    correction_commands = corrections.add_subparsers(
        dest="corrections_command", required=True
    )
    corrections_list = correction_commands.add_parser("list", help="列出纠错词典")
    _add_shared_arguments(corrections_list)
    corrections_add = correction_commands.add_parser("add", help="添加纠错词对")
    corrections_add.add_argument("pair", metavar="错词=>正词")
    corrections_add.add_argument("--term", action="store_true", help="标记正词为专有名词")
    _add_shared_arguments(corrections_add)

    check = subparsers.add_parser("check", help="分析已有项目的字幕质量")
    check.add_argument("project", metavar="PROJECT")
    _add_shared_arguments(check)

    fix = subparsers.add_parser("fix", help="应用字幕纠错")
    fix.add_argument("project", metavar="PROJECT")
    fix_mode = fix.add_mutually_exclusive_group(required=True)
    fix_mode.add_argument("--dict-only", action="store_true", help="仅应用纠错词典")
    fix_mode.add_argument("--auto", action="store_true", help="运行 AI 复核并应用高置信纠错")
    fix.add_argument("--yes", action="store_true", help="不询问直接应用")
    _add_shared_arguments(fix)

    reference = subparsers.add_parser("reference", help="登记外部参考字幕")
    reference.add_argument("project", metavar="PROJECT")
    reference.add_argument("subtitle", metavar="SUBTITLE")
    _add_shared_arguments(reference)

    undo = subparsers.add_parser("undo", help="撤销一次字幕修改")
    undo.add_argument("project", metavar="PROJECT")
    undo.add_argument("change_id", metavar="CHANGE_ID")
    _add_shared_arguments(undo)

    cache = subparsers.add_parser("cache", help="管理转写内容指纹缓存")
    cache_commands = cache.add_subparsers(dest="cache_command", required=True)
    cache_backfill = cache_commands.add_parser("backfill", help="登记现有项目的转写缓存")
    _add_shared_arguments(cache_backfill)
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


def _cached_asr_runner(workspace: Workspace) -> AsrRunner:
    cache_dir = resolve_transcript_cache_dir(workspace.root, EnvStore())
    return CachingAsrRunner(Video2mdAsrRunner(), cache_dir)


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
        asr_runner = _cached_asr_runner(workspace)
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


def _handle_review(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    if args.out and args.all:
        raise ValueError("--out 仅适用于显式指定的单个项目，不能与 --all 同时使用")
    if args.serve and args.all:
        raise ValueError("--serve 仅适用于显式指定的单个项目，不能与 --all 同时使用")
    project_ids = _project_ids(workspace, args.projects, args.all)
    if not project_ids:
        raise ValueError("请指定 PROJECT，或使用 --all")
    if args.out and len(project_ids) != 1:
        raise ValueError("--out 仅适用于单个项目")
    if args.serve and len(project_ids) != 1:
        raise ValueError("--serve 仅适用于单个项目")
    if args.serve and args.timeout <= 0:
        raise ValueError("--timeout 必须是大于 0 的秒数")

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
            _progress(f"[{project.id}] 开始生成剪辑确认页")
            payload = run_review(
                project,
                out_path=args.out,
                serve=args.serve,
                timeout_seconds=args.timeout,
                open_browser=args.open,
            )
            result["outputs"] = payload["outputs"]
            result["warnings"] = payload["warnings"]
            _progress(f"[{project.id}] 剪辑确认页生成完成")
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
        asr_runner = _cached_asr_runner(workspace)
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


def _corrections_path(workspace: Workspace) -> Path:
    return workspace.root / "_settings" / "corrections.json"


def _parse_correction_pair(raw: str) -> tuple[str, str]:
    wrong, separator, right = raw.partition("=>")
    wrong = wrong.strip()
    right = right.strip()
    if separator != "=>" or not wrong or not right:
        raise ValueError('纠错词对格式应为 "错词=>正词"')
    return wrong, right


def _handle_corrections(
    args: argparse.Namespace, workspace: Workspace
) -> list[dict[str, Any]]:
    path = _corrections_path(workspace)
    correction_set = CorrectionSet.load(path)
    if args.corrections_command == "add":
        wrong, right = _parse_correction_pair(args.pair)
        correction_set.add_pair(wrong, right, is_term=args.term)
        correction_set.save(path)
    elif not args.json_output:
        for pair in correction_set.pairs:
            for wrong in pair["wrong"]:
                _progress(f"{wrong} => {pair['right']}")

    result = _new_result(project_id=None, source=None)
    result.update(correction_set.to_dict())
    result["outputs"] = {"corrections": str(path)}
    return [result]


def _selection_payload(project: Project) -> tuple[Path, dict[str, Any], list[dict[str, Any]]]:
    selection_path = project.dir / "selection.json"
    if selection_path.is_file():
        payload = read_json(selection_path)
        rows = payload.get("rows")
        if not isinstance(rows, list):
            raise ValueError(f"选择文件缺少 rows：{selection_path}")
        return selection_path, payload, rows

    if not project.transcript_path.is_file():
        raise ValueError(f"字幕文件不存在：{project.transcript_path}")
    transcript = load_transcript(project.transcript_path)
    selected = set(transcript.selected_segment_ids)
    rows = [
        {
            "id": segment.id,
            "checked": segment.id in selected,
            "text": segment.text,
        }
        for segment in transcript.segments
    ]
    return selection_path, {"rows": rows}, rows


def _quality_rows(project: Project) -> list[dict[str, Any]]:
    """以 transcript 的时间/token 为底，叠加 selection 的当前文字。"""
    if not project.transcript_path.is_file():
        raise ValueError(f"字幕文件不存在：{project.transcript_path}")
    transcript_payload = read_json(project.transcript_path)
    segments = transcript_payload.get("segments")
    if not isinstance(segments, list):
        raise ValueError(f"字幕文件缺少 segments：{project.transcript_path}")

    current_text: dict[str, str] = {}
    selection_path = project.dir / "selection.json"
    if selection_path.is_file():
        selection = read_json(selection_path)
        for row in selection.get("rows") or []:
            if isinstance(row, dict) and row.get("id") is not None:
                current_text[str(row["id"])] = str(row.get("text") or "")

    rows = copy.deepcopy([row for row in segments if isinstance(row, dict)])
    for row in rows:
        segment_id = str(row.get("id", row.get("segment_id", "")))
        if segment_id in current_text:
            row["text"] = current_text[segment_id]
    return rows


def _reference_path(project: Project) -> Path | None:
    candidates = sorted(
        (
            path
            for path in project.dir.glob("reference.*")
            if path.is_file() and path.suffix.lower() in {".srt", ".vtt"}
        ),
        key=lambda path: path.stat().st_mtime_ns,
        reverse=True,
    )
    return candidates[0] if candidates else None


def _analyze_project(project: Project) -> dict[str, Any]:
    rows = _quality_rows(project)
    issues = scan_confidence(rows)
    reference = _reference_path(project)
    if reference is not None:
        cues = parse_reference(reference.read_text(encoding="utf-8-sig"))
        issues.extend(align_reference(rows, cues))
    report = merge_report(load_report(project.dir), issues)
    if reference is not None:
        report["meta"]["reference_file"] = reference.name
    save_report(project.dir, report)
    return report


def _print_quality_report(report: dict[str, Any]) -> None:
    stats = report.get("stats") or {}
    if stats:
        _progress("质检统计：" + "，".join(f"{kind}={count}" for kind, count in stats.items()))
    else:
        _progress("质检统计：未发现问题")
    for issue in (report.get("issues") or [])[:20]:
        span = issue.get("span") or {}
        _progress(
            f"[{issue.get('segment_id')}] {issue.get('kind')} "
            f"「{span.get('text', '')}」：{issue.get('reason', '')}"
        )


def _handle_check(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    project = workspace.get(args.project)
    if project is None:
        raise ValueError(f"项目不存在：{args.project}")
    report = _analyze_project(project)
    if not args.json_output:
        _print_quality_report(report)
    result = _project_result(project)
    result.update(report)
    result["outputs"] = {
        "quality_report": str(project.dir / "quality_report.json")
    }
    return [result]


def _project_result(project: Project) -> dict[str, Any]:
    return _new_result(
        project_id=project.id,
        source=str(project.source_path) if project.source_path else None,
    )


def _print_correction_preview(preview: list[dict[str, Any]]) -> None:
    for item in preview:
        _progress(
            f"[{item['segment_id']}] {item['wrong']} => {item['right']} "
            f"× {item['count']}：{item['context']}"
        )


def _confirmed(args: argparse.Namespace) -> bool:
    if args.yes:
        return True
    _progress("应用上述纠错？[y/N]")
    return sys.stdin.readline().strip().lower() in {"y", "yes"}


def _handle_fix(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    project = workspace.get(args.project)
    if project is None:
        raise ValueError(f"项目不存在：{args.project}")
    if args.auto:
        return _handle_ai_fix(args, workspace, project)

    selection_path, selection, rows = _selection_payload(project)
    correction_set = CorrectionSet.load(_corrections_path(workspace))
    preview = preview_corrections(rows, correction_set)
    _print_correction_preview(preview)

    result = _project_result(project)
    result["applied"] = 0
    result["rows"] = rows
    if not preview or not _confirmed(args):
        return [result]

    new_rows, changeset = apply_corrections(rows, correction_set)
    changeset_path = save_changeset(project.dir, changeset)
    updated_selection = dict(selection)
    updated_selection["rows"] = new_rows
    write_json(selection_path, updated_selection)

    result["applied"] = sum(int(item["count"]) for item in preview)
    result["changeset_id"] = changeset["change_id"]
    result["rows"] = new_rows
    result["outputs"] = {
        "selection": str(selection_path),
        "changeset": str(changeset_path),
    }
    return [result]


def _print_ai_preview(findings: list[dict[str, Any]]) -> None:
    for finding in findings:
        if finding.get("verdict") != "auto_fix":
            continue
        _progress(
            f"[{finding['segment_id']}] {finding['span_text']} => "
            f"{finding.get('replacement', '')}（{finding.get('confidence')}）"
        )


def _handle_ai_fix(
    args: argparse.Namespace,
    workspace: Workspace,
    project: Project,
) -> list[dict[str, Any]]:
    report = _analyze_project(project)
    quality_rows = _quality_rows(project)
    client = LlmClient(
        env_store=EnvStore(),
        api_vault_path=DEFAULT_API_VAULT_PATH,
    )
    if not client.available():
        raise ValueError("缺少 LLM API Key，无法运行 AI 复核")
    prompt_store = PromptStore(
        DEFAULT_PROMPTS_DIR,
        workspace.root / "_settings" / "prompts",
    )
    correction_set = CorrectionSet.load(_corrections_path(workspace))
    corrections_rights = [
        str(pair["right"])
        for pair in correction_set.pairs
        if pair.get("right")
    ]
    findings, changeset, new_issues = review_quality(
        quality_rows,
        report["issues"],
        chat_json_fn=client.chat_json,
        assemble_prompt_fn=lambda: prompt_store.assemble("quality_review"),
        known_terms=_cli_known_terms(),
        corrections_rights=corrections_rights,
    )
    _print_ai_preview(findings)
    ask_user = [
        issue
        for issue in new_issues
        if issue.get("kind") in {"ai_suspect", "term_candidate"}
    ]
    for issue in ask_user:
        suggestion = (
            f" => {issue['suggestion']}" if issue.get("suggestion") else ""
        )
        _progress(
            f"[{issue['segment_id']}] 待人工确认"
            f"「{issue['span']['text']}」{suggestion}"
        )

    auto_findings = [
        finding for finding in findings if finding.get("verdict") == "auto_fix"
    ]
    apply_auto = bool(changeset and auto_findings) and _confirmed(args)
    _selection_path, _selection, original_rows = _selection_payload(project)
    result = _project_result(project)
    result.update(
        {
            "applied": 0,
            "ask_user": ask_user,
            "rows": original_rows,
        }
    )
    applied_segment_ids: set[str] = set()
    changeset_path: Path | None = None
    if apply_auto and changeset is not None:
        selection_path, selection, selection_rows = _selection_payload(project)
        pending_changes = {
            str(change.get("segment_id") or ""): change
            for change in changeset.get("changes") or []
            if isinstance(change, dict)
        }
        new_rows = copy.deepcopy(selection_rows)
        for row in new_rows:
            segment_id = str(row.get("id", row.get("segment_id", "")))
            change = pending_changes.get(segment_id)
            if change is None or row.get("text") != change.get("old"):
                continue
            row["text"] = str(change.get("new") or "")
            applied_segment_ids.add(segment_id)
        applied_changes = [
            change
            for change in changeset.get("changes") or []
            if str(change.get("segment_id") or "") in applied_segment_ids
        ]
        if applied_changes:
            applied_count = sum(
                1
                for finding in auto_findings
                if str(finding.get("segment_id") or "")
                in applied_segment_ids
            )
            changeset["changes"] = applied_changes
            changeset["label"] = f"AI 自动纠错 {applied_count} 处"
            selection["rows"] = new_rows
            write_json(selection_path, selection)
            changeset_path = save_changeset(project.dir, changeset)
            result.update(
                {
                    "applied": applied_count,
                    "changeset_id": changeset["change_id"],
                    "rows": new_rows,
                    "outputs": {
                        "selection": str(selection_path),
                        "changeset": str(changeset_path),
                        "quality_report": str(
                            project.dir / "quality_report.json"
                        ),
                    },
                }
            )

    skipped_auto_keys = {
        (
            str(finding.get("segment_id") or ""),
            str(finding.get("span_text") or ""),
        )
        for finding in auto_findings
        if str(finding.get("segment_id") or "") not in applied_segment_ids
    }
    for issue in report["issues"]:
        key = (
            str(issue.get("segment_id") or ""),
            str((issue.get("span") or {}).get("text") or ""),
        )
        if key in skipped_auto_keys:
            issue["status"] = "open"

    combined = merge_report(report, [*report["issues"], *new_issues])
    combined["meta"]["ai_findings"] = findings
    if changeset_path is not None and changeset is not None:
        combined["meta"]["ai_changeset_id"] = changeset["change_id"]
    save_report(project.dir, combined)
    return [result]


def _cli_known_terms() -> list[str]:
    env_store = EnvStore()
    vocabulary_id, _ = env_store.effective("ASR_BASE_VOCABULARY_ID")
    if not vocabulary_id:
        return []
    api_key, _ = resolve_secret_key(
        "DASHSCOPE_API_KEY",
        env_store,
        api_vault_path=DEFAULT_API_VAULT_PATH,
    )
    if not api_key:
        return []
    try:
        details = VocabularyClient(api_key).query(vocabulary_id)
    except (ValueError, VocabularyError):
        _progress("热词表暂不可用，AI 复核继续使用纠错词典")
        return []
    return [
        str(item["text"])
        for item in details.get("vocabulary") or []
        if isinstance(item, dict) and item.get("text")
    ]


def _handle_reference(
    args: argparse.Namespace,
    workspace: Workspace,
) -> list[dict[str, Any]]:
    project = workspace.get(args.project)
    if project is None:
        raise ValueError(f"项目不存在：{args.project}")
    source = Path(args.subtitle).expanduser().resolve()
    if not source.is_file():
        raise ValueError(f"参考字幕不存在：{source}")
    if source.suffix.lower() not in {".srt", ".vtt"}:
        raise ValueError("参考字幕只支持 SRT/VTT")
    target = project.dir / f"reference{source.suffix}"
    shutil.copyfile(source, target)
    result = _project_result(project)
    result["filename"] = target.name
    result["outputs"] = {"reference": str(target)}
    return [result]


def _handle_undo(args: argparse.Namespace, workspace: Workspace) -> list[dict[str, Any]]:
    project = workspace.get(args.project)
    if project is None:
        raise ValueError(f"项目不存在：{args.project}")

    selection_path, selection, rows = _selection_payload(project)
    changeset = load_changeset(project.dir, args.change_id)
    restored_rows, report = undo_changeset(rows, changeset)
    updated_selection = dict(selection)
    updated_selection["rows"] = restored_rows
    write_json(selection_path, updated_selection)

    result = _project_result(project)
    result.update(report)
    result["change_id"] = args.change_id
    result["rows"] = restored_rows
    result["outputs"] = {"selection": str(selection_path)}
    return [result]


def _handle_cache_backfill(
    args: argparse.Namespace, workspace: Workspace
) -> list[dict[str, Any]]:
    cache_dir = resolve_transcript_cache_dir(workspace.root, EnvStore())
    registered = 0
    skipped = 0
    entries: list[dict[str, Any]] = []

    for summary in workspace.list_projects():
        project_id = str(summary["id"])
        project = workspace.get(project_id)
        source = project.source_path if project is not None else None
        missing = []
        if source is None or not source.is_file():
            missing.append("source")
        if project is None or not project.transcript_path.is_file():
            missing.append("transcript")
        if project is None or not project.vad_path.is_file():
            missing.append("vad")
        if missing:
            skipped += 1
            entries.append(
                {"project_id": project_id, "status": "skipped", "missing": missing}
            )
            continue

        try:
            cached = backfill_cache_entry(
                source,
                project.transcript_path,
                project.vad_path,
                cache_dir,
            )
            if cached["created"]:
                registered += 1
                status = "registered"
            else:
                skipped += 1
                status = "skipped"
            entries.append({"project_id": project_id, "status": status, **cached})
        except Exception as exc:  # noqa: BLE001 - 回填时单项失败不影响其他项目。
            skipped += 1
            entries.append(
                {"project_id": project_id, "status": "skipped", "error": str(exc)}
            )

    result = _new_result(project_id=None, source=None)
    result.update(
        {
            "registered": registered,
            "skipped": skipped,
            "entries": entries,
            "outputs": {"cache_dir": str(cache_dir)},
        }
    )
    return [result]


def main(argv: list[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    workspace = Workspace(args.workspace)
    try:
        if args.command == "transcribe":
            results = _handle_transcribe(args, workspace)
        elif args.command == "select":
            results = _handle_select(args, workspace)
        elif args.command == "review":
            results = _handle_review(args, workspace)
        elif args.command == "export":
            results = _handle_export(args, workspace)
        elif args.command == "run":
            results = _handle_run(args, workspace)
        elif args.command == "corrections":
            results = _handle_corrections(args, workspace)
        elif args.command == "check":
            results = _handle_check(args, workspace)
        elif args.command == "fix":
            results = _handle_fix(args, workspace)
        elif args.command == "reference":
            results = _handle_reference(args, workspace)
        elif args.command == "undo":
            results = _handle_undo(args, workspace)
        elif args.command == "cache":
            results = _handle_cache_backfill(args, workspace)
        else:
            raise ValueError(f"未知命令：{args.command}")
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
