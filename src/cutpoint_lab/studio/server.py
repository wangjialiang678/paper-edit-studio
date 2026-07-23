from __future__ import annotations

import argparse
import copy
import json
import logging
import mimetypes
import os
import shutil
import threading
import time
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, unquote, urlparse
from urllib.request import Request, urlopen

from ..asr_cache import CachingAsrRunner
from ..export.subtitles import write_srt
from ..export.video import export_video_plan
from ..features import AudioFrame, load_rms_frames
from ..io import load_transcript, load_vad, read_json, write_json
from ..paper_edit.state import apply_editor_rows, build_editor_state, build_plan_from_editor_rows
from ..quality import (
    CorrectionSet,
    align_reference,
    apply_corrections,
    load_changeset,
    load_report,
    merge_report,
    parse_reference,
    preview_corrections,
    review_quality,
    save_changeset,
    save_report,
    scan_confidence,
    undo_changeset,
)
from .ai_selector import HARD_CONSTRAINTS, AiSelector, save_suggestion
from .asr_runner import ShellAsrRunner, Video2mdAsrRunner
from .config import (
    DEFAULT_API_VAULT_PATH,
    EnvStore,
    mask_api_key,
    resolve_llm_api_key,
    resolve_secret_key,
    resolve_transcript_cache_dir,
)
from .filler_detect import detect as detect_filler_cuts
from .llm_client import DEFAULT_BASE_URL as DEFAULT_LLM_BASE_URL, LlmClient, LlmConfig
from .pipeline import PipelineManager
from .plans import apply_manual_nudges, build_ordered_plan, silence_gaps
from .prompt_store import PromptStore
from .vocabulary import VocabularyClient, VocabularyError, VocabularyHttpError
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
        env_store: EnvStore | None = None,
        api_vault_path: str | Path = DEFAULT_API_VAULT_PATH,
        vocabulary_transport=None,
    ):
        self.workspace = workspace
        self.env_store = env_store or EnvStore()
        self.transcript_cache_dir = resolve_transcript_cache_dir(
            self.workspace.root,
            self.env_store,
        )
        self.api_vault_path = Path(api_vault_path).expanduser()
        self.vocabulary_transport = vocabulary_transport
        self.prompt_store = PromptStore(prompts_dir, self.workspace.root / "_settings" / "prompts")
        self.selector = (
            selector
            if selector is not None
            else AiSelector(
                prompts_dir,
                client=LlmClient(env_store=self.env_store, api_vault_path=self.api_vault_path),
                prompt_store=self.prompt_store,
            )
        )
        self.quality_llm = getattr(
            self.selector,
            "client",
            LlmClient(env_store=self.env_store, api_vault_path=self.api_vault_path),
        )
        self.pipeline = PipelineManager(
            CachingAsrRunner(
                asr_runner or Video2mdAsrRunner(),
                self.transcript_cache_dir,
            ),
            auto_ai=self._auto_ai if auto_ai else None,
        )
        self._frames_cache: dict[str, list[AudioFrame]] = {}
        self._ai_threads: dict[str, threading.Thread] = {}
        self._quality_threads: dict[str, threading.Thread] = {}
        self._export_threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()
        self._quality_io_lock = threading.RLock()

    # ---------- 设置与提示词 ----------
    def settings(self) -> dict[str, Any]:
        dashscope_key, dashscope_source = resolve_secret_key(
            "DASHSCOPE_API_KEY",
            self.env_store,
            api_vault_path=self.api_vault_path,
        )
        _, key_name, key_source = resolve_llm_api_key(
            self.env_store,
            api_vault_path=self.api_vault_path,
        )
        llm = LlmConfig.from_env(self.env_store, api_vault_path=self.api_vault_path)
        vocabulary_id, _ = self.env_store.effective("ASR_BASE_VOCABULARY_ID")
        return {
            "dashscope_key": {
                "masked": mask_api_key(dashscope_key),
                "source": dashscope_source,
            },
            "llm": {
                "model": llm.model,
                "base_url": llm.base_url,
                "key_name": key_name,
                "key_source": key_source,
            },
            "vocabulary_id": vocabulary_id or None,
            "env_path": str(self.env_store.path),
            "transcript_cache_dir": str(self.transcript_cache_dir),
        }

    def corrections(self) -> dict[str, Any]:
        return CorrectionSet.load(self._corrections_path()).to_dict()

    def save_corrections(self, payload: dict[str, Any]) -> dict[str, Any]:
        correction_set = CorrectionSet.from_dict(payload)
        correction_set.save(self._corrections_path())
        return correction_set.to_dict()

    def save_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        key = _validated_api_key(payload.get("key"))
        self.env_store.write_key("DASHSCOPE_API_KEY", key)
        result: dict[str, Any] = {"ok": True}
        if "DASHSCOPE_API_KEY" in os.environ:
            result["warning"] = (
                "进程环境变量 DASHSCOPE_API_KEY 会覆盖 .env，本次修改在当前会话可能不生效"
            )
        return result

    def test_api_key(self, payload: dict[str, Any]) -> dict[str, Any]:
        if "key" in payload:
            key = _validated_api_key(payload.get("key"))
        else:
            key, _ = resolve_secret_key(
                "DASHSCOPE_API_KEY",
                self.env_store,
                api_vault_path=self.api_vault_path,
            )
        if not key:
            return {"ok": False, "detail": "尚未配置 API Key", "vocab_access": None}

        request = Request(
            f"{DEFAULT_LLM_BASE_URL}/models",
            headers={"Authorization": f"Bearer {key}"},
            method="GET",
        )
        try:
            with urlopen(request, timeout=10) as response:
                response.read(1024)
        except HTTPError as exc:
            if exc.code in (401, 403):
                detail = f"API Key 无效或无访问权限（HTTP {exc.code}）"
            else:
                detail = f"服务返回 HTTP {exc.code}"
            return {"ok": False, "detail": detail, "vocab_access": None}
        except (URLError, TimeoutError, OSError) as exc:
            detail = _redacted_network_error(exc, key)
            return {"ok": False, "detail": detail, "vocab_access": None}
        vocab_access: bool | None
        try:
            self._vocabulary_client(key).list_page_one()
        except VocabularyHttpError as exc:
            vocab_access = False if exc.status in (401, 403) else None
        except VocabularyError:
            vocab_access = None
        else:
            vocab_access = True
        return {"ok": True, "detail": "API Key 验证成功", "vocab_access": vocab_access}

    def vocabulary(self) -> dict[str, Any]:
        vocabulary_id, _ = self.env_store.effective("ASR_BASE_VOCABULARY_ID")
        if not vocabulary_id:
            return {"vocabulary_id": None, "items": [], "exists": False}
        details = self._vocabulary_client().query(vocabulary_id)
        return {
            "vocabulary_id": vocabulary_id,
            "items": list(details.get("vocabulary") or []),
            "exists": True,
            "status": details.get("status"),
            "target_model": details.get("target_model"),
        }

    def save_vocabulary(self, payload: dict[str, Any]) -> dict[str, Any]:
        items = payload.get("items")
        create = payload.get("create", False)
        if not isinstance(create, bool):
            raise ValueError("create 必须是 JSON boolean")
        if create:
            client = self._vocabulary_client()
            created = client.create("pes", "fun-asr", items)
            vocabulary_id = created.get("vocabulary_id")
            if not isinstance(vocabulary_id, str) or not vocabulary_id:
                raise VocabularyError("Vocabulary 创建响应缺少 vocabulary_id")
            self.env_store.write_key("ASR_BASE_VOCABULARY_ID", vocabulary_id)
            for attempt in range(5):
                details = client.query(vocabulary_id)
                if details.get("status") == "OK":
                    return {"ok": True, "vocabulary_id": vocabulary_id}
                if attempt < 4:
                    time.sleep(1)
            raise VocabularyError("Vocabulary 创建后未在规定时间内就绪")

        vocabulary_id, _ = self.env_store.effective("ASR_BASE_VOCABULARY_ID")
        if not vocabulary_id:
            raise ValueError("尚未配置 ASR_BASE_VOCABULARY_ID，无法更新 vocabulary")
        client = self._vocabulary_client()
        client.update(vocabulary_id, items)
        details = client.query(vocabulary_id)
        status = details.get("status")
        if status != "OK":
            raise VocabularyError("Vocabulary 更新后状态不是 OK")
        return {"ok": True, "vocabulary_id": vocabulary_id, "status": status}

    def _vocabulary_client(self, key: str | None = None) -> VocabularyClient:
        if key is None:
            key, _ = resolve_secret_key(
                "DASHSCOPE_API_KEY",
                self.env_store,
                api_vault_path=self.api_vault_path,
            )
        if not key:
            raise ValueError("尚未配置 DASHSCOPE_API_KEY")
        if self.vocabulary_transport is None:
            return VocabularyClient(key)
        return VocabularyClient(key, transport=self.vocabulary_transport)

    def prompt(self, mode: str) -> dict[str, Any]:
        result = self.prompt_store.get(mode)
        result["hard_constraints"] = HARD_CONSTRAINTS
        return result

    def save_prompt(self, mode: str, payload: dict[str, Any]) -> dict[str, Any]:
        result = self.prompt_store.write(mode, payload.get("content"))
        result["hard_constraints"] = HARD_CONSTRAINTS
        return result

    def reset_prompt(self, mode: str) -> None:
        self.prompt_store.reset(mode)

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

    def retranscribe(self, project: Project) -> dict[str, Any]:
        if self.pipeline.is_running(project):
            raise ValueError("流水线正在运行")
        logger.info("retranscribe requested: project=%s force=true", project.id)
        project.set_stage("imported", "重新转写", error=None)
        self.pipeline.start(project, force=True)
        return {"ok": True, **project.read_state()}

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
        for row in state["rows"]:
            row["suggested_cuts"] = (
                detect_filler_cuts(row["tokens"])
                if row.get("has_word_timestamps")
                else []
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
            with self._quality_io_lock:
                write_json(
                    project.dir / "selection.json",
                    {
                        "rows": _full_editor_rows(transcript, rows),
                        "groups": groups or None,
                    },
                )
        write_json(project.dir / "clip_plan.json", plan)
        srt_path = project.dir / "edited_preview.srt"
        write_srt(edited, plan, srt_path)
        return {"ok": True, "plan": plan, "paths": {"clip_plan": str(project.dir / "clip_plan.json"), "subtitle": str(srt_path)}}

    # ---------- 字幕质量 ----------
    def corrections_preview(self, project: Project) -> dict[str, Any]:
        rows, _ = self._correction_rows(project)
        items = preview_corrections(rows, CorrectionSet.load(self._corrections_path()))
        return {"items": items, "total": sum(item["count"] for item in items)}

    def apply_dictionary_corrections(self, project: Project) -> dict[str, Any]:
        rows, selection = self._correction_rows(project)
        correction_set = CorrectionSet.load(self._corrections_path())
        items = preview_corrections(rows, correction_set)
        new_rows, changeset = apply_corrections(rows, correction_set)
        selection["rows"] = new_rows
        write_json(project.dir / "selection.json", selection)
        save_changeset(project.dir, changeset)
        applied = sum(item["count"] for item in items)
        logger.info(
            "corrections applied: project=%s changeset=%s replacements=%s",
            project.id,
            changeset["change_id"],
            applied,
        )
        return {
            "ok": True,
            "changeset_id": changeset["change_id"],
            "applied": applied,
            "rows": new_rows,
        }

    def undo_dictionary_changeset(self, project: Project, change_id: str) -> dict[str, Any]:
        selection_path = project.dir / "selection.json"
        if not selection_path.is_file():
            raise ValueError("尚未保存编辑状态，无法撤销")
        selection = read_json(selection_path)
        rows = selection.get("rows")
        if not isinstance(rows, list):
            raise ValueError("selection.json 的 rows 必须是数组")
        restored_rows, report = undo_changeset(
            rows,
            load_changeset(project.dir, change_id),
        )
        selection["rows"] = restored_rows
        write_json(selection_path, selection)
        logger.info(
            "corrections undone: project=%s changeset=%s reverted=%s skipped=%s",
            project.id,
            change_id,
            report["reverted"],
            len(report["skipped"]),
        )
        return {"ok": True, **report, "rows": restored_rows}

    def quality_report(self, project: Project) -> dict[str, Any]:
        with self._quality_io_lock:
            return load_report(project.dir)

    def analyze_quality(self, project: Project, payload: dict[str, Any]) -> dict[str, Any]:
        run_ai = payload.get("ai", False)
        if not isinstance(run_ai, bool):
            raise ValueError("ai 必须是 JSON boolean")
        rows = self._quality_rows(project)
        issues = scan_confidence(rows)
        reference = self._reference_path(project)
        if reference is not None:
            cues = parse_reference(reference.read_text(encoding="utf-8-sig"))
            issues.extend(align_reference(rows, cues))
        with self._quality_io_lock:
            report = merge_report(load_report(project.dir), issues)
            if reference is not None:
                report["meta"]["reference_file"] = reference.name
            save_report(project.dir, report)
        logger.info(
            "quality analyzed: project=%s issues=%s reference=%s ai=%s",
            project.id,
            len(report["issues"]),
            reference.name if reference is not None else None,
            run_ai,
        )
        if run_ai:
            self._start_quality_ai(project)
        return report

    def update_quality_issue(
        self,
        project: Project,
        issue_id: str,
        payload: dict[str, Any],
    ) -> dict[str, Any]:
        status = payload.get("status")
        if status not in {"resolved", "ignored"}:
            raise ValueError("status 只能是 resolved 或 ignored")
        with self._quality_io_lock:
            report = load_report(project.dir)
            for issue in report.get("issues") or []:
                if str(issue.get("id") or "") != issue_id:
                    continue
                issue["status"] = status
                save_report(project.dir, report)
                return report
        raise KeyError(issue_id)

    def reference_info(self, project: Project) -> dict[str, Any]:
        path = self._reference_path(project)
        return {
            "exists": path is not None,
            "filename": path.name if path is not None else None,
        }

    def save_reference_path(self, project: Project, raw_path: Any) -> dict[str, Any]:
        source = Path(str(raw_path or "")).expanduser().resolve()
        if not source.is_file():
            raise ValueError(f"参考字幕不存在：{source}")
        target = self._reference_target(project, source.name)
        shutil.copyfile(source, target)
        logger.info("reference imported: project=%s filename=%s", project.id, target.name)
        return {"ok": True, "path": str(target), **self.reference_info(project)}

    def save_reference_upload(
        self,
        project: Project,
        filename: str,
        body_stream,
        content_length: int,
    ) -> dict[str, Any]:
        target = self._reference_target(project, filename)
        if content_length <= 0:
            raise ValueError("上传内容为空")
        temporary = target.with_name(f".{target.name}.{time.time_ns()}.tmp")
        remaining = content_length
        try:
            with temporary.open("wb") as file_obj:
                while remaining > 0:
                    chunk = body_stream.read(min(1024 * 1024, remaining))
                    if not chunk:
                        raise ValueError("上传中断")
                    file_obj.write(chunk)
                    remaining -= len(chunk)
            os.replace(temporary, target)
        finally:
            temporary.unlink(missing_ok=True)
        logger.info("reference uploaded: project=%s filename=%s", project.id, target.name)
        return {"ok": True, "path": str(target), **self.reference_info(project)}

    def _start_quality_ai(self, project: Project) -> None:
        if not self.quality_llm.available():
            raise ValueError("缺少 LLM API Key，无法运行 AI 复核")

        def _run() -> None:
            try:
                rows = self._quality_rows(project)
                with self._quality_io_lock:
                    report = load_report(project.dir)
                correction_set = CorrectionSet.load(self._corrections_path())
                corrections_rights = [
                    str(pair["right"])
                    for pair in correction_set.pairs
                    if pair.get("right")
                ]
                findings, changeset, new_issues = review_quality(
                    rows,
                    report["issues"],
                    chat_json_fn=self.quality_llm.chat_json,
                    assemble_prompt_fn=lambda: self.prompt_store.assemble(
                        "quality_review"
                    ),
                    known_terms=self._known_quality_terms(),
                    corrections_rights=corrections_rights,
                )
                changeset_path: Path | None = None
                applied_segment_ids: set[str] = set()
                if changeset is not None and changeset.get("changes"):
                    with self._quality_io_lock:
                        selection_rows, selection = self._correction_rows(project)
                        pending_changes = {
                            str(change.get("segment_id") or ""): change
                            for change in changeset["changes"]
                        }
                        updated_rows = copy.deepcopy(selection_rows)
                        for row in updated_rows:
                            segment_id = str(
                                row.get("id", row.get("segment_id", ""))
                            )
                            change = pending_changes.get(segment_id)
                            if (
                                change is None
                                or row.get("text") != change.get("old")
                            ):
                                continue
                            row["text"] = str(change.get("new") or "")
                            applied_segment_ids.add(segment_id)
                        applied_changes = [
                            change
                            for change in changeset["changes"]
                            if str(change.get("segment_id") or "")
                            in applied_segment_ids
                        ]
                        if applied_changes:
                            applied_count = sum(
                                1
                                for finding in findings
                                if finding.get("verdict") == "auto_fix"
                                and str(finding.get("segment_id") or "")
                                in applied_segment_ids
                            )
                            changeset["changes"] = applied_changes
                            changeset["label"] = (
                                f"AI 自动纠错 {applied_count} 处"
                            )
                            selection["rows"] = updated_rows
                            write_json(
                                project.dir / "selection.json",
                                selection,
                            )
                            changeset_path = save_changeset(
                                project.dir,
                                changeset,
                            )
                        else:
                            changeset = None

                skipped_auto_keys = {
                    (
                        str(finding.get("segment_id") or ""),
                        str(finding.get("span_text") or ""),
                    )
                    for finding in findings
                    if finding.get("verdict") == "auto_fix"
                    and str(finding.get("segment_id") or "")
                    not in applied_segment_ids
                }
                for issue in report["issues"]:
                    issue_key = (
                        str(issue.get("segment_id") or ""),
                        str((issue.get("span") or {}).get("text") or ""),
                    )
                    if issue_key in skipped_auto_keys:
                        issue["status"] = "open"

                reviewed_status = {
                    (
                        str(issue.get("segment_id") or ""),
                        str(issue.get("kind") or ""),
                        str((issue.get("span") or {}).get("text") or ""),
                    ): str(issue.get("status") or "open")
                    for issue in report["issues"]
                }
                ok_review_reasons = {
                    (
                        str(finding.get("segment_id") or ""),
                        "low_confidence",
                        str(finding.get("span_text") or ""),
                    ): str(finding.get("reason") or "")
                    for finding in findings
                    if finding.get("verdict") == "ok"
                }
                with self._quality_io_lock:
                    latest = load_report(project.dir)
                    for issue in latest.get("issues") or []:
                        key = (
                            str(issue.get("segment_id") or ""),
                            str(issue.get("kind") or ""),
                            str((issue.get("span") or {}).get("text") or ""),
                        )
                        if (
                            issue.get("status", "open") == "open"
                            and reviewed_status.get(key) == "resolved"
                        ):
                            issue["status"] = "resolved"
                            ok_reason = ok_review_reasons.get(key)
                            if ok_reason:
                                issue["reason"] = (
                                    f"{str(issue.get('reason') or '')}"
                                    f"；AI 复核通过：{ok_reason}"
                                )
                    refreshed = merge_report(
                        latest,
                        [*latest["issues"], *new_issues],
                    )
                    refreshed["meta"]["ai_findings"] = findings
                    if changeset_path is not None and changeset is not None:
                        refreshed["meta"]["ai_changeset_id"] = changeset["change_id"]
                    save_report(project.dir, refreshed)
                done: dict[str, Any] = {
                    "status": "done",
                    "finding_count": len(findings),
                    "completed_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
                if changeset_path is not None and changeset is not None:
                    done["changeset_id"] = changeset["change_id"]
                project.update_state(quality_ai=done)
                logger.info(
                    "quality AI done: project=%s findings=%s auto_changeset=%s",
                    project.id,
                    len(findings),
                    done.get("changeset_id"),
                )
            except Exception as exc:  # noqa: BLE001 - 后台任务异常落回 state。
                logger.exception("quality AI failed: project=%s", project.id)
                project.update_state(
                    quality_ai={
                        "status": "error",
                        "error": _redact_known_secrets(str(exc)),
                    }
                )

        thread = threading.Thread(
            target=_run,
            daemon=True,
            name=f"quality-ai-{project.id}",
        )
        with self._lock:
            existing = self._quality_threads.get(project.id)
            if existing and existing.is_alive():
                raise ValueError("该项目的 AI 质检任务已在运行")
            self._quality_threads[project.id] = thread
            project.update_state(
                quality_ai={
                    "status": "running",
                    "started_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
                }
            )
            try:
                thread.start()
            except Exception:
                self._quality_threads.pop(project.id, None)
                raise

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
        rows = payload.get("rows") or []
        source_transcript = self._transcript_with_source(project)
        transcript = apply_editor_rows(source_transcript, rows) if rows else source_transcript
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
                project.update_state(export={"status": "error", "error": _redact_known_secrets(str(exc))})

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
                self._set_ai_state(project, mode, {"status": "error", "error": _redact_known_secrets(str(exc))})

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
    def _corrections_path(self) -> Path:
        return self.workspace.root / "_settings" / "corrections.json"

    def _quality_rows(self, project: Project) -> list[dict[str, Any]]:
        """以 transcript 的时间/token 为底，叠加 selection 的当前文字。"""
        if not project.transcript_path.is_file():
            raise ValueError("字幕尚未就绪")
        transcript = read_json(project.transcript_path)
        segments = transcript.get("segments")
        if not isinstance(segments, list):
            raise ValueError("transcript.json 的 segments 必须是数组")
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

    def _reference_path(self, project: Project) -> Path | None:
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

    @staticmethod
    def _reference_target(project: Project, filename: str) -> Path:
        safe_name = Path(filename).name
        suffix = Path(safe_name).suffix
        if not safe_name or suffix.lower() not in {".srt", ".vtt"}:
            raise ValueError("参考字幕只支持 SRT/VTT")
        return project.dir / f"reference{suffix}"

    def _known_quality_terms(self) -> list[str]:
        try:
            vocabulary = self.vocabulary()
        except (ValueError, VocabularyError) as exc:
            logger.warning("quality AI: vocabulary unavailable: %s", exc)
            return []
        return [
            str(item["text"])
            for item in vocabulary.get("items") or []
            if isinstance(item, dict) and item.get("text")
        ]

    def _correction_rows(
        self,
        project: Project,
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        selection_path = project.dir / "selection.json"
        if selection_path.is_file():
            selection = read_json(selection_path)
            if not isinstance(selection, dict):
                raise ValueError("selection.json 必须是 JSON 对象")
            rows = selection.get("rows")
            if not isinstance(rows, list):
                raise ValueError("selection.json 的 rows 必须是数组")
            return rows, selection

        rows = self.editor_state(project)["rows"]
        return rows, {"rows": rows}

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
                    if "cuts" in update:
                        row["cuts"] = update["cuts"]
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
        if entry.get("status") == "done" and state.get("ai_warning"):
            # 之前自动 AI 失败留下的警告在任一 AI 运行成功后即失效，随手清掉，
            # 否则编辑器每次加载都会复播旧错误。
            project.update_state(ai=ai_state, ai_warning=None)
        else:
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
        if not isinstance(row, dict):
            continue
        nudge = row.get("nudge")
        if isinstance(nudge, dict) and (nudge.get("start_ms") or nudge.get("end_ms")):
            nudges[str(row.get("id"))] = nudge
    return nudges


def _full_editor_rows(
    transcript,
    rows: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """选择文件总是全量落原句，避免局部 payload 让未提交行丢失。"""
    updates = {
        str(row.get("id")): dict(row)
        for row in rows
        if isinstance(row, dict) and row.get("id") is not None
    }
    normalized = []
    for segment in transcript.segments:
        row = updates.get(segment.id, {"id": segment.id})
        row["id"] = segment.id
        row["checked"] = bool(row.get("checked", False))
        row["text"] = str(row.get("text", segment.text))
        normalized.append(row)
    return normalized


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


def _validated_api_key(value: Any) -> str:
    if not isinstance(value, str) or not value or any(char.isspace() for char in value):
        raise ValueError("API Key 不能为空且不能包含空白字符")
    return value


def _redacted_network_error(exc: BaseException, secret: str) -> str:
    raw = str(exc).replace(secret, "[REDACTED]")[:160]
    return f"请求失败：{raw}" if raw else "请求失败，请检查网络连接"


def _redact_known_secrets(message: str) -> str:
    """兜底脱敏：任意错误文本里出现当前生效的 API key 值时替换为掩码。

    覆盖所有异常出口（HTTP 错误响应、后台任务 error 落库），
    无需猜测异常来源——只匹配已知密钥值本身。
    """
    if not message:
        return message
    store = EnvStore()
    secrets = set()
    for key_name in ("DASHSCOPE_API_KEY",):
        value, _ = store.effective(key_name)
        if value and len(value) >= 8:
            secrets.add(value)
    llm_key, _, _ = resolve_llm_api_key(store)
    if llm_key and len(llm_key) >= 8:
        secrets.add(llm_key)
    for secret in secrets:
        message = message.replace(secret, mask_api_key(secret))
    return message


ROUTES: dict[tuple[str, str], str] = {
    ("GET", "/"): "_route_index",
    ("GET", "/index.html"): "_route_index",
    ("GET", "/static/{asset}"): "_route_static",
    ("GET", "/favicon.ico"): "_route_favicon",
    ("GET", "/api/settings"): "_route_settings",
    ("GET", "/api/settings/corrections"): "_route_corrections",
    ("PUT", "/api/settings/corrections"): "_route_save_corrections",
    ("PUT", "/api/settings/apikey"): "_route_save_api_key",
    ("POST", "/api/settings/apikey/test"): "_route_test_api_key",
    ("GET", "/api/settings/vocabulary"): "_route_vocabulary",
    ("PUT", "/api/settings/vocabulary"): "_route_save_vocabulary",
    ("GET", "/api/prompts/{mode}"): "_route_prompt",
    ("PUT", "/api/prompts/{mode}"): "_route_save_prompt",
    ("DELETE", "/api/prompts/{mode}"): "_route_reset_prompt",
    ("GET", "/api/projects"): "_route_projects",
    ("POST", "/api/projects/import-path"): "_route_import_path",
    ("POST", "/api/projects/upload"): "_route_upload",
    ("GET", "/api/projects/{id}"): "_route_project",
    ("GET", "/api/projects/{id}/editor"): "_route_editor",
    ("GET", "/api/projects/{id}/editor/{rest...}"): "_route_editor",
    ("GET", "/api/projects/{id}/rms"): "_route_rms",
    ("GET", "/api/projects/{id}/rms/{rest...}"): "_route_rms",
    ("GET", "/api/projects/{id}/ai/{mode}"): "_route_ai_suggestion",
    ("GET", "/api/projects/{id}/quality/corrections-preview"): "_route_corrections_preview",
    ("GET", "/api/projects/{id}/quality/report"): "_route_quality_report",
    ("GET", "/api/projects/{id}/reference"): "_route_reference",
    ("GET", "/api/projects/{id}/{rest...}"): "_route_unknown_project_get",
    ("POST", "/api/projects/{id}/ai/suggest"): "_route_ai_suggest",
    ("POST", "/api/projects/{id}/quality/apply-corrections"): "_route_apply_corrections",
    ("POST", "/api/projects/{id}/quality/undo/{change_id}"): "_route_undo_changeset",
    ("POST", "/api/projects/{id}/quality/analyze"): "_route_quality_analyze",
    ("POST", "/api/projects/{id}/quality/issues/{issue_id}"): "_route_quality_issue",
    ("POST", "/api/projects/{id}/reference"): "_route_reference",
    ("POST", "/api/projects/{id}/retranscribe"): "_route_retranscribe",
    ("POST", "/api/projects/{id}/{action}/suggest"): "_route_ai_suggest",
    ("POST", "/api/projects/{id}/{action}"): "_route_project_action",
    ("GET", "/media/{id}/source"): "_route_media_source",
    ("GET", "/media/{id}/source/{rest...}"): "_route_media_source",
    ("GET", "/media/{id}/exports/{filename}"): "_route_media_export",
    ("GET", "/media/{id}/{rest...}"): "_route_unknown_media_get",
}


def _match_route(pattern: str, raw_path: str) -> dict[str, str] | None:
    if "{" not in pattern:
        return {} if raw_path == pattern else None
    pattern_parts = [part for part in pattern.split("/") if part]
    path_parts = _decode_path_parts(raw_path)
    params: dict[str, str] = {}
    path_index = 0
    for pattern_part in pattern_parts:
        if pattern_part.startswith("{") and pattern_part.endswith("...}"):
            if path_index >= len(path_parts):
                return None
            params[pattern_part[1:-4]] = "/".join(path_parts[path_index:])
            path_index = len(path_parts)
            break
        if path_index >= len(path_parts):
            return None
        if pattern_part.startswith("{") and pattern_part.endswith("}"):
            params[pattern_part[1:-1]] = path_parts[path_index]
        elif pattern_part != path_parts[path_index]:
            return None
        path_index += 1
    return params if path_index == len(path_parts) else None


def _resolve_route(method: str, raw_path: str) -> tuple[str, dict[str, str]] | None:
    for (route_method, pattern), handler_name in ROUTES.items():
        if route_method != method:
            continue
        params = _match_route(pattern, raw_path)
        if params is not None:
            return handler_name, params
    return None


def _handler_factory(app: StudioApplication):
    class StudioHandler(BaseHTTPRequestHandler):
        protocol_version = "HTTP/1.1"

        def do_GET(self) -> None:  # noqa: N802 - http.server 约定。
            self._dispatch("GET")

        def do_POST(self) -> None:  # noqa: N802 - http.server 约定。
            self._dispatch("POST")

        def do_PUT(self) -> None:  # noqa: N802 - http.server 约定。
            self._dispatch("PUT")

        def do_DELETE(self) -> None:  # noqa: N802 - http.server 约定。
            self._dispatch("DELETE")

        def _dispatch(self, method: str) -> None:
            parsed = urlparse(self.path)
            try:
                resolved = _resolve_route(method, parsed.path)
                if resolved is None:
                    return self._send_error_json(404, "not found")
                handler_name, params = resolved
                handler = getattr(self, handler_name)
                handler(parsed, params)
            except ValueError as exc:
                self._send_error_json(400, str(exc))
            except Exception as exc:  # noqa: BLE001 - 本地工具返回可读错误。
                logger.exception("%s %s failed", method, self.path)
                self._send_error_json(500, str(exc))

        # ---------- 路由 handlers ----------
        def _route_index(self, _parsed, _params: dict[str, str]) -> None:
            self._send_file(STATIC_DIR / "index.html")

        def _route_static(self, _parsed, params: dict[str, str]) -> None:
            self._send_file(STATIC_DIR / Path(params["asset"]).name)

        def _route_favicon(self, _parsed, _params: dict[str, str]) -> None:
            self.send_response(204)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def _route_settings(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.settings())

        def _route_corrections(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.corrections())

        def _route_save_corrections(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.save_corrections(self._read_json_body()))

        def _route_save_api_key(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.save_api_key(self._read_json_body()))

        def _route_test_api_key(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.test_api_key(self._read_json_body()))

        def _route_vocabulary(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.vocabulary())

        def _route_save_vocabulary(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json(app.save_vocabulary(self._read_json_body()))

        def _route_prompt(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.prompt(params["mode"]))

        def _route_save_prompt(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.save_prompt(params["mode"], self._read_json_body()))

        def _route_reset_prompt(self, _parsed, params: dict[str, str]) -> None:
            app.reset_prompt(params["mode"])
            self._send_json({"ok": True})

        def _route_projects(self, _parsed, _params: dict[str, str]) -> None:
            self._send_json({"projects": app.workspace.list_projects()})

        def _route_import_path(self, _parsed, _params: dict[str, str]) -> None:
            body = self._read_json_body()
            self._send_json(app.import_path(str(body.get("path") or ""), body.get("name")))

        def _route_upload(self, parsed, _params: dict[str, str]) -> None:
            query = parse_qs(parsed.query)
            filename = (query.get("filename") or [""])[0]
            length = int(self.headers.get("Content-Length", "0") or 0)
            self._send_json(app.import_upload(filename, self.rfile, length))

        def _route_project(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.project_detail(self._project(params["id"])))

        def _route_editor(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.editor_state(self._project(params["id"])))

        def _route_rms(self, parsed, params: dict[str, str]) -> None:
            query = parse_qs(parsed.query)
            start_ms = int((query.get("start_ms") or ["0"])[0])
            end_ms = int((query.get("end_ms") or ["0"])[0])
            self._send_json(app.rms_slice(self._project(params["id"]), start_ms, end_ms))

        def _route_ai_suggestion(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.ai_suggestion(self._project(params["id"]), params["mode"]))

        def _route_corrections_preview(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.corrections_preview(self._project(params["id"])))

        def _route_quality_report(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(app.quality_report(self._project(params["id"])))

        def _route_reference(self, parsed, params: dict[str, str]) -> None:
            project = self._project(params["id"])
            if self.command == "GET":
                self._send_json(app.reference_info(project))
                return
            query = parse_qs(parsed.query)
            filename = (query.get("filename") or [""])[0]
            if filename:
                length = int(self.headers.get("Content-Length", "0") or 0)
                self._send_json(
                    app.save_reference_upload(
                        project,
                        filename,
                        self.rfile,
                        length,
                    )
                )
                return
            body = self._read_json_body()
            self._send_json(app.save_reference_path(project, body.get("path")))

        def _route_unknown_project_get(self, _parsed, params: dict[str, str]) -> None:
            self._project(params["id"])
            self._send_error_json(404, "not found")

        def _route_ai_suggest(self, _parsed, params: dict[str, str]) -> None:
            project = self._project(params["id"])
            self._send_json(app.start_ai(project, self._read_json_body()))

        def _route_apply_corrections(self, _parsed, params: dict[str, str]) -> None:
            self._read_json_body()
            project = self._project(params["id"])
            self._send_json(app.apply_dictionary_corrections(project))

        def _route_undo_changeset(self, _parsed, params: dict[str, str]) -> None:
            self._read_json_body()
            project = self._project(params["id"])
            self._send_json(app.undo_dictionary_changeset(project, params["change_id"]))

        def _route_quality_analyze(self, _parsed, params: dict[str, str]) -> None:
            self._send_json(
                app.analyze_quality(
                    self._project(params["id"]),
                    self._read_json_body(),
                )
            )

        def _route_quality_issue(self, _parsed, params: dict[str, str]) -> None:
            try:
                report = app.update_quality_issue(
                    self._project(params["id"]),
                    params["issue_id"],
                    self._read_json_body(),
                )
            except KeyError:
                self._send_error_json(404, "quality issue not found")
                return
            self._send_json(report)

        def _route_retranscribe(self, _parsed, params: dict[str, str]) -> None:
            self._read_json_body()
            self._send_json(app.retranscribe(self._project(params["id"])))

        def _route_project_action(self, _parsed, params: dict[str, str]) -> None:
            project = self._project(params["id"])
            action = params["action"]
            if action == "retry":
                return self._send_json(app.retry(project))
            body = self._read_json_body()
            if action == "plan":
                return self._send_json(app.save_plan(project, body))
            if action == "export":
                return self._send_json(app.start_export(project, body))
            self._send_error_json(404, "not found")

        def _route_media_source(self, _parsed, params: dict[str, str]) -> None:
            source = self._project(params["id"]).source_path
            if not source or not source.exists():
                return self._send_error_json(404, "源媒体不存在")
            _serve_file_with_range(self, source)

        def _route_media_export(self, _parsed, params: dict[str, str]) -> None:
            project = self._project(params["id"])
            target = project.exports_dir / Path(params["filename"]).name
            if not target.exists():
                return self._send_error_json(404, "导出文件不存在")
            _serve_file_with_range(self, target, as_download=True)

        def _route_unknown_media_get(self, _parsed, params: dict[str, str]) -> None:
            self._project(params["id"])
            self._send_error_json(404, "not found")

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
            self._send_json({"ok": False, "error": _redact_known_secrets(message)}, status=status)

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
    parser.add_argument("--asr-script", default=None, help="改用旧版 OSS bash 脚本 ASR（覆盖默认 video2md 二进制）")
    parser.add_argument("--asr-binary", default=None, help="覆盖 video2md 的 mp4-md 二进制路径（默认用仓库 bin/ 下的 vendored 版本）")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--no-auto-ai", action="store_true", help="ASR 后不自动跑口播精剪")
    parser.add_argument("--open", action="store_true", help="启动后自动打开浏览器")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(name)s %(levelname)s %(message)s")
    if args.asr_script:
        asr_runner = ShellAsrRunner(Path(args.asr_script))
    else:
        asr_runner = Video2mdAsrRunner(args.asr_binary)
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
