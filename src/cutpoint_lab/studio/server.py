from __future__ import annotations

import argparse
import json
import logging
import mimetypes
import shutil
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, unquote, urlparse

from ..export.subtitles import write_srt
from ..export.video import export_video_plan
from ..features import AudioFrame, load_rms_frames
from ..io import load_transcript, load_vad, read_json, write_json
from ..paper_edit.state import build_editor_state, build_plan_from_editor_rows
from .ai_selector import AiSelector, save_suggestion
from .asr_runner import ShellAsrRunner
from .pipeline import PipelineManager
from .plans import apply_manual_nudges, build_ordered_plan, silence_gaps
from .workspace import Project, Workspace

logger = logging.getLogger("studio.server")

STATIC_DIR = Path(__file__).parent / "static"
DEFAULT_PROMPTS_DIR = Path(__file__).resolve().parents[3] / "prompts"

STUDIO_STRATEGIES = [
    "hybrid_valley",
    "token_padding",
    "anchored_rms",
    "visual_waveform",
    "rms_snap",
    "vad_snap",
]
FRAME_STRATEGIES = {"rms_snap", "anchored_rms", "visual_waveform", "hybrid_valley"}
AI_MODES = ["koubo_tighten", "topic_slicing", "highlight_remix"]


class StudioApplication:
    def __init__(
        self,
        workspace: Workspace,
        *,
        prompts_dir: Path = DEFAULT_PROMPTS_DIR,
        asr_runner=None,
        selector: AiSelector | None = None,
        auto_ai: bool = True,
    ):
        self.workspace = workspace
        self.selector = selector or AiSelector(prompts_dir)
        self.pipeline = PipelineManager(
            asr_runner or ShellAsrRunner(),
            auto_ai=self._auto_ai if auto_ai else None,
        )
        self._frames_cache: dict[str, list[AudioFrame]] = {}
        self._ai_threads: dict[str, threading.Thread] = {}
        self._export_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    # ---------- 导入 ----------
    def import_path(self, raw_path: str, name: str | None = None) -> dict[str, Any]:
        source = Path(raw_path).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"文件不存在：{source}")
        project = self.workspace.create_project(name or source.stem, source_path=source, imported_via="path")
        self.pipeline.start(project)
        return project.read_state()

    def import_upload(self, filename: str, body_stream, content_length: int) -> dict[str, Any]:
        # filename 来自 parse_qs，已完成 URL 解码；这里不再 unquote，避免含 % 的文件名被二次解码。
        safe_name = Path(filename).name
        if not safe_name:
            raise ValueError("缺少文件名")
        if content_length <= 0:
            raise ValueError("上传内容为空")
        # 先落到临时名，成功后再建项目，避免半截文件成为项目源。
        project = self.workspace.create_project(Path(safe_name).stem, source_path=Path("/dev/null"), imported_via="upload")
        project.uploads_dir.mkdir(parents=True, exist_ok=True)
        target = project.uploads_dir / safe_name
        remaining = content_length
        with target.open("wb") as file_obj:
            while remaining > 0:
                chunk = body_stream.read(min(1024 * 1024, remaining))
                if not chunk:
                    raise ValueError("上传中断")
                file_obj.write(chunk)
                remaining -= len(chunk)
        state = project.read_state()
        state_source = state.get("source") or {}
        state_source.update({"path": str(target), "filename": safe_name})
        project.update_state(source=state_source)
        self.pipeline.start(project)
        return project.read_state()

    def retry(self, project: Project) -> dict[str, Any]:
        if self.pipeline.is_running(project):
            raise ValueError("流水线正在运行")
        project.set_stage("imported", "重新处理", error=None)
        self.pipeline.start(project)
        return project.read_state()

    # ---------- 编辑器 ----------
    def project_detail(self, project: Project) -> dict[str, Any]:
        state = project.read_state()
        state["transcript_ready"] = project.transcript_ready()
        state["pipeline_running"] = self.pipeline.is_running(project)
        return state

    def editor_state(self, project: Project) -> dict[str, Any]:
        if not project.transcript_ready():
            raise ValueError("字幕尚未就绪")
        transcript = load_transcript(project.transcript_path)
        state = build_editor_state(
            transcript,
            transcript_path=str(project.transcript_path),
            source_video=self._source_str(project),
        )
        self._apply_saved_selection(project, state["rows"])
        return {
            "project": self.project_detail(project),
            "rows": state["rows"],
            "duration_ms": state.get("duration_ms"),
            "silence_gaps": silence_gaps(transcript),
            "strategies": list(STUDIO_STRATEGIES),
            "ai": self._ai_overview(project),
        }

    def save_plan(self, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
        strategy = str(payload.get("strategy") or STUDIO_STRATEGIES[0])
        if strategy not in STUDIO_STRATEGIES:
            raise ValueError(f"未知切点策略：{strategy}")
        rows = payload.get("rows") or []
        if not isinstance(rows, list):
            raise ValueError("rows 必须是数组")
        transcript = self._transcript_with_source(project)
        groups = payload.get("groups")
        if groups:
            from ..paper_edit.state import apply_editor_rows

            edited = apply_editor_rows(transcript, rows) if rows else transcript
            plan = build_ordered_plan(
                edited,
                groups,
                strategy=strategy,
                frames=self._frames(project, strategy),
                vad=self._vad(project, strategy),
            )
        else:
            edited, plan = build_plan_from_editor_rows(
                transcript,
                rows,
                strategy=strategy,
                frames=self._frames(project, strategy),
                vad=self._vad(project, strategy),
            )
        apply_manual_nudges(plan, _nudges_from_rows(rows))
        if rows:
            write_json(project.dir / "selection.json", {"rows": rows, "groups": groups or None})
        write_json(project.dir / "clip_plan.json", plan)
        srt_path = project.dir / "edited_preview.srt"
        write_srt(edited, plan, srt_path)
        return {"ok": True, "plan": plan, "paths": {"clip_plan": str(project.dir / "clip_plan.json"), "subtitle": str(srt_path)}}

    # ---------- 导出 ----------
    def start_export(self, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
        with self._lock:
            thread = self._export_threads.get(project.id)
            if thread and thread.is_alive():
                raise ValueError("导出任务已在运行")
        plan_result = self.save_plan(project, payload)
        plan = plan_result["plan"]
        stamp = time.strftime("%Y%m%d-%H%M%S")
        output_video = project.exports_dir / f"edited-{stamp}.mp4"
        output_srt = project.exports_dir / f"edited-{stamp}.srt"
        transcript = self._transcript_with_source(project)
        project.update_state(export={"status": "running", "started_at": stamp})

        def _run() -> None:
            try:
                source = project.source_path
                if source is None or not source.exists():
                    raise RuntimeError("源媒体不存在")
                manifest = export_video_plan(source, plan, output_video, work_dir=project.exports_dir / f"segments-{stamp}")
                write_srt(transcript, plan, output_srt)
                shutil.rmtree(project.exports_dir / f"segments-{stamp}", ignore_errors=True)
                project.update_state(
                    export={
                        "status": "done",
                        "video": str(output_video),
                        "video_name": output_video.name,
                        "srt": str(output_srt),
                        "srt_name": output_srt.name,
                        "duration_ms": manifest.get("duration_ms"),
                        "range_count": manifest.get("range_count"),
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    }
                )
            except Exception as exc:  # noqa: BLE001 - 后台导出线程需把异常落回 state。
                logger.exception("export %s failed", project.id)
                project.update_state(export={"status": "error", "error": str(exc)})

        thread = threading.Thread(target=_run, daemon=True, name=f"export-{project.id}")
        with self._lock:
            self._export_threads[project.id] = thread
        thread.start()
        return {"ok": True, "export": {"status": "running"}}

    # ---------- AI ----------
    def start_ai(self, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
        mode = str(payload.get("mode") or "koubo_tighten")
        if mode not in AI_MODES:
            raise ValueError(f"未知 AI 模式：{mode}")
        if not self.selector.available():
            raise ValueError("缺少 LLM API Key，无法运行 AI 选段")
        if not project.transcript_ready():
            raise ValueError("字幕尚未就绪")
        with self._lock:
            thread = self._ai_threads.get(f"{project.id}:{mode}")
            if thread and thread.is_alive():
                raise ValueError("该模式的 AI 任务已在运行")
        brief = str(payload.get("brief") or "")
        target_duration = str(payload.get("target_duration") or "")
        self._set_ai_state(project, mode, {"status": "running", "started_at": time.strftime("%H:%M:%S")})

        def _run() -> None:
            try:
                transcript = load_transcript(project.transcript_path)
                suggestion = self.selector.suggest(transcript, mode, brief=brief, target_duration=target_duration)
                path = save_suggestion(project.ai_dir, suggestion)
                self._set_ai_state(
                    project,
                    mode,
                    {
                        "status": "done",
                        "file": str(path),
                        "warnings": suggestion.warnings,
                        "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                    },
                )
            except Exception as exc:  # noqa: BLE001 - AI 线程异常落回 state 供前端展示。
                logger.exception("ai %s/%s failed", project.id, mode)
                self._set_ai_state(project, mode, {"status": "error", "error": str(exc)})

        thread = threading.Thread(target=_run, daemon=True, name=f"ai-{project.id}-{mode}")
        with self._lock:
            self._ai_threads[f"{project.id}:{mode}"] = thread
        thread.start()
        return {"ok": True, "mode": mode, "status": "running"}

    def ai_suggestion(self, project: Project, mode: str) -> dict[str, Any]:
        entry = (project.read_state().get("ai") or {}).get(mode) or {}
        if entry.get("status") != "done" or not entry.get("file"):
            return {"mode": mode, "status": entry.get("status", "idle"), "error": entry.get("error")}
        payload = read_json(Path(entry["file"]))
        payload["status"] = "done"
        return payload

    def _auto_ai(self, project: Project) -> None:
        """ASR 完成后自动跑一次口播精剪；成功且用户尚未手动选择时，作为默认勾选。"""
        if not self.selector.available():
            raise RuntimeError("缺少 LLM API Key")
        transcript = load_transcript(project.transcript_path)
        suggestion = self.selector.suggest(transcript, "koubo_tighten")
        path = save_suggestion(project.ai_dir, suggestion)
        self._set_ai_state(
            project,
            "koubo_tighten",
            {"status": "done", "file": str(path), "warnings": suggestion.warnings, "auto": True},
        )
        selection_path = project.dir / "selection.json"
        if not selection_path.exists():
            keeps = set(suggestion.payload.get("keep_segment_ids") or [])
            reasons = {item["segment_id"]: item for item in suggestion.payload.get("decisions") or []}
            rows = [
                {"id": segment.id, "checked": segment.id in keeps, "text": segment.text}
                for segment in transcript.segments
            ]
            write_json(selection_path, {"rows": rows, "source": "auto_ai", "reasons_available": bool(reasons)})

    # ---------- 内部 ----------
    def _ai_overview(self, project: Project) -> dict[str, Any]:
        state = project.read_state().get("ai") or {}
        overview: dict[str, Any] = {"available": self.selector.available(), "modes": {}}
        for mode in AI_MODES:
            entry = dict(state.get(mode) or {})
            entry.pop("file", None)
            overview["modes"][mode] = entry or {"status": "idle"}
        return overview

    def _apply_saved_selection(self, project: Project, rows: list[dict[str, Any]]) -> None:
        selection_path = project.dir / "selection.json"
        decisions = self._koubo_decisions(project)
        if selection_path.exists():
            saved = {str(row.get("id")): row for row in (read_json(selection_path).get("rows") or [])}
            for row in rows:
                update = saved.get(row["id"])
                if update is not None:
                    row["checked"] = bool(update.get("checked", row["checked"]))
                    row["text"] = str(update.get("text", row["text"]))
                    if update.get("trim"):
                        row["trim"] = update["trim"]
                    if update.get("nudge"):
                        row["nudge"] = update["nudge"]
        for row in rows:
            decision = decisions.get(row["id"])
            if decision:
                row["ai_keep"] = decision.get("keep")
                row["ai_reason"] = decision.get("reason")
                row["ai_labels"] = decision.get("labels") or []

    def _koubo_decisions(self, project: Project) -> dict[str, dict[str, Any]]:
        entry = (project.read_state().get("ai") or {}).get("koubo_tighten") or {}
        if entry.get("status") != "done" or not entry.get("file"):
            return {}
        try:
            payload = read_json(Path(entry["file"]))
        except (OSError, json.JSONDecodeError):
            return {}
        return {str(item.get("segment_id")): item for item in payload.get("decisions") or []}

    def _set_ai_state(self, project: Project, mode: str, entry: dict[str, Any]) -> None:
        state = project.read_state()
        ai_state = state.get("ai") or {}
        ai_state[mode] = entry
        project.update_state(ai=ai_state)

    def _source_str(self, project: Project) -> str | None:
        source = project.source_path
        return str(source) if source else None

    def _transcript_with_source(self, project: Project):
        transcript = load_transcript(project.transcript_path)
        source = self._source_str(project)
        if source:
            transcript = type(transcript)(
                source_video=source,
                duration_ms=transcript.duration_ms,
                selected_segment_ids=transcript.selected_segment_ids,
                segments=transcript.segments,
            )
        return transcript

    def _frames(self, project: Project, strategy: str) -> list[AudioFrame]:
        if strategy not in FRAME_STRATEGIES:
            return []
        return self._frames_for(project)

    def _frames_for(self, project: Project) -> list[AudioFrame]:
        cached = self._frames_cache.get(project.id)
        if cached is not None:
            return cached
        wav = project.analysis_wav_path
        if not wav.exists():
            raise ValueError("分析音频缺失，请先重跑流水线")
        frames = load_rms_frames(wav, frame_ms=10)
        self._frames_cache[project.id] = frames
        return frames

    def rms_slice(self, project: Project, start_ms: int, end_ms: int) -> dict[str, Any]:
        """给微调面板的波形条：区间内 10ms 步长的归一化 RMS。"""
        if end_ms <= start_ms:
            raise ValueError("end_ms 必须大于 start_ms")
        if end_ms - start_ms > 120_000:
            raise ValueError("波形区间过长（上限 120s）")
        frames = [
            frame for frame in self._frames_for(project)
            if frame.start_ms >= start_ms - 10 and frame.start_ms < end_ms
        ]
        amplitudes = [10 ** (frame.rms_db / 20) for frame in frames]
        peak = max(amplitudes, default=0.0) or 1.0
        return {
            "start_ms": frames[0].start_ms if frames else start_ms,
            "end_ms": end_ms,
            "step_ms": 10,
            "values": [round(value / peak, 4) for value in amplitudes],
        }

    def _vad(self, project: Project, strategy: str):
        if strategy != "vad_snap":
            return None
        if not project.vad_path.exists():
            raise ValueError("VAD 数据缺失，请先重跑流水线")
        return load_vad(project.vad_path)


def _nudges_from_rows(rows: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    nudges: dict[str, dict[str, Any]] = {}
    for row in rows or []:
        nudge = row.get("nudge")
        if isinstance(nudge, dict) and (nudge.get("start_ms") or nudge.get("end_ms")):
            nudges[str(row.get("id"))] = nudge
    return nudges


def _decode_path_parts(raw_path: str) -> list[str]:
    """URL path → 各段解码后的列表。

    浏览器会把中文项目 ID 百分号编码；http.server 又把原始字节按 latin-1 解码。
    两种来源都还原成真实 UTF-8 字符串，否则中文文件名的项目全部 404。
    """
    try:
        raw_path = raw_path.encode("iso-8859-1").decode("utf-8")
    except (UnicodeEncodeError, UnicodeDecodeError):
        pass
    return [unquote(part) for part in raw_path.split("/") if part]


def _handler_factory(app: StudioApplication):
    class StudioHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定。
            parsed = urlparse(self.path)
            parts = _decode_path_parts(parsed.path)
            try:
                if parsed.path in ("/", "/index.html"):
                    return self._send_file(STATIC_DIR / "index.html")
                if parts and parts[0] == "static" and len(parts) == 2:
                    return self._send_file(STATIC_DIR / Path(parts[1]).name)
                if parsed.path == "/favicon.ico":
                    self.send_response(204)
                    self.send_header("Content-Length", "0")
                    self.end_headers()
                    return
                if parsed.path == "/api/projects":
                    return self._send_json({"projects": app.workspace.list_projects()})
                if parts[:1] == ["api"] and len(parts) >= 3 and parts[1] == "projects":
                    project = self._project(parts[2])
                    if len(parts) == 3:
                        return self._send_json(app.project_detail(project))
                    if parts[3] == "editor":
                        return self._send_json(app.editor_state(project))
                    if parts[3] == "rms":
                        query = parse_qs(parsed.query)
                        start_ms = int((query.get("start_ms") or ["0"])[0])
                        end_ms = int((query.get("end_ms") or ["0"])[0])
                        return self._send_json(app.rms_slice(project, start_ms, end_ms))
                    if parts[3] == "ai" and len(parts) == 5:
                        return self._send_json(app.ai_suggestion(project, parts[4]))
                if parts[:1] == ["media"] and len(parts) >= 3:
                    project = self._project(parts[1])
                    if parts[2] == "source":
                        source = project.source_path
                        if not source or not source.exists():
                            return self._send_error_json(404, "源媒体不存在")
                        return _serve_file_with_range(self, source)
                    if parts[2] == "exports" and len(parts) == 4:
                        target = project.exports_dir / Path(parts[3]).name
                        if not target.exists():
                            return self._send_error_json(404, "导出文件不存在")
                        return _serve_file_with_range(self, target, as_download=True)
                self._send_error_json(404, "not found")
            except ValueError as exc:
                self._send_error_json(400, str(exc))
            except Exception as exc:  # noqa: BLE001 - 本地工具返回可读错误。
                logger.exception("GET %s failed", self.path)
                self._send_error_json(500, str(exc))

        def do_POST(self) -> None:  # noqa: N802 - http.server 约定。
            parsed = urlparse(self.path)
            parts = _decode_path_parts(parsed.path)
            try:
                if parsed.path == "/api/projects/import-path":
                    body = self._read_json_body()
                    return self._send_json(app.import_path(str(body.get("path") or ""), body.get("name")))
                if parsed.path == "/api/projects/upload":
                    query = parse_qs(parsed.query)
                    filename = (query.get("filename") or [""])[0]
                    length = int(self.headers.get("Content-Length", "0") or 0)
                    return self._send_json(app.import_upload(filename, self.rfile, length))
                if parts[:2] == ["api", "projects"] and len(parts) == 4:
                    project = self._project(parts[2])
                    action = parts[3]
                    if action == "retry":
                        return self._send_json(app.retry(project))
                    body = self._read_json_body()
                    if action == "plan":
                        return self._send_json(app.save_plan(project, body))
                    if action == "export":
                        return self._send_json(app.start_export(project, body))
                if parts[:2] == ["api", "projects"] and len(parts) == 5 and parts[4] == "suggest":
                    project = self._project(parts[2])
                    body = self._read_json_body()
                    return self._send_json(app.start_ai(project, body))
                self._send_error_json(404, "not found")
            except ValueError as exc:
                self._send_error_json(400, str(exc))
            except Exception as exc:  # noqa: BLE001 - 本地工具返回可读错误。
                logger.exception("POST %s failed", self.path)
                self._send_error_json(500, str(exc))

        def log_message(self, format: str, *args: Any) -> None:  # noqa: A002 - 基类签名。
            return

        def _project(self, project_id: str) -> Project:
            project = app.workspace.get(project_id)
            if project is None:
                raise ValueError(f"项目不存在：{project_id}")
            return project

        def _read_json_body(self) -> dict[str, Any]:
            length = int(self.headers.get("Content-Length", "0") or 0)
            if length <= 0:
                return {}
            return json.loads(self.rfile.read(length).decode("utf-8"))

        def _send_file(self, path: Path) -> None:
            if not path.is_file():
                return self._send_error_json(404, f"文件不存在：{path.name}")
            body = path.read_bytes()
            content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
            self.send_response(200)
            self.send_header("Content-Type", f"{content_type}; charset=utf-8" if content_type.startswith("text/") else content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_json(self, payload: dict[str, Any], status: int = 200) -> None:
            body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "application/json; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def _send_error_json(self, status: int, message: str) -> None:
            self._send_json({"ok": False, "error": message}, status=status)

    return StudioHandler


def _serve_file_with_range(handler: BaseHTTPRequestHandler, path: Path, *, as_download: bool = False) -> None:
    size = path.stat().st_size
    range_header = handler.headers.get("Range", "")
    start = 0
    end = size - 1
    status = 200
    if range_header.startswith("bytes="):
        status = 206
        range_value = range_header.removeprefix("bytes=").split(",", 1)[0]
        raw_start, _, raw_end = range_value.partition("-")
        if raw_start:
            start = int(raw_start)
        if raw_end:
            end = int(raw_end)
        end = min(end, size - 1)
        start = min(max(0, start), end)
    length = end - start + 1
    content_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    handler.send_response(status)
    handler.send_header("Content-Type", content_type)
    handler.send_header("Accept-Ranges", "bytes")
    handler.send_header("Content-Length", str(length))
    if as_download:
        handler.send_header("Content-Disposition", f'attachment; filename="{path.name}"')
    if status == 206:
        handler.send_header("Content-Range", f"bytes {start}-{end}/{size}")
    handler.end_headers()
    with path.open("rb") as file_obj:
        file_obj.seek(start)
        remaining = length
        while remaining > 0:
            chunk = file_obj.read(min(1024 * 256, remaining))
            if not chunk:
                break
            try:
                handler.wfile.write(chunk)
            except (BrokenPipeError, ConnectionResetError):
                return
            remaining -= len(chunk)


def bind_server(app: StudioApplication, *, host: str, port: int) -> tuple[ThreadingHTTPServer, int]:
    handler = _handler_factory(app)
    if port == 0:
        server = ThreadingHTTPServer((host, 0), handler)
        return server, int(server.server_address[1])
    last_error: OSError | None = None
    for candidate in range(port, port + 20):
        try:
            return ThreadingHTTPServer((host, candidate), handler), candidate
        except OSError as exc:
            last_error = exc
    raise OSError(f"could not bind to {host}:{port}-{port + 19}") from last_error


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="studio_web", description="Paper Edit Studio 本地服务")
    parser.add_argument("--workspace", default="workspace", help="项目工作区目录")
    parser.add_argument("--prompts-dir", default=str(DEFAULT_PROMPTS_DIR))
    parser.add_argument("--asr-script", default=None, help="覆盖默认 ASR 脚本路径")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--no-auto-ai", action="store_true", help="ASR 后不自动跑口播精剪")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    asr_runner = ShellAsrRunner(Path(args.asr_script)) if args.asr_script else ShellAsrRunner()
    app = StudioApplication(
        Workspace(args.workspace),
        prompts_dir=Path(args.prompts_dir),
        asr_runner=asr_runner,
        auto_ai=not args.no_auto_ai,
    )
    server, actual_port = bind_server(app, host=args.host, port=args.port)
    url = f"http://{args.host}:{actual_port}/"
    print(url, flush=True)
    if args.open:
        webbrowser.open(url)
    try:
        server.serve_forever()
    finally:
        server.server_close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
