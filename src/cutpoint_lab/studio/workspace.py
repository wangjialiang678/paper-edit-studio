from __future__ import annotations

import json
import os
import re
import shutil
import threading
import time
import uuid
from pathlib import Path
from typing import Any

from ..io import read_json, write_json

PIPELINE_STAGES = [
    "imported",
    "probing",
    "extracting_audio",
    "transcribing",
    "ai_suggesting",
    "ready",
    "error",
]

_SLUG_PATTERN = re.compile(r"[^a-z0-9一-鿿]+")
_CUT_NAME_PATTERN = re.compile(r"^[a-z0-9-]{1,32}$")
DEFAULT_CUT = "default"


def _slugify(name: str, max_length: int = 24) -> str:
    slug = _SLUG_PATTERN.sub("-", name.lower()).strip("-")
    return slug[:max_length] or "video"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


def _cut_name(name: str) -> str:
    if not isinstance(name, str) or _CUT_NAME_PATTERN.fullmatch(name) is None:
        raise ValueError("cut 名称必须匹配 ^[a-z0-9-]{1,32}$")
    return name


def _order_from_groups(groups: Any) -> list[str]:
    order: list[str] = []
    if not isinstance(groups, list):
        return order
    for group in groups:
        if not isinstance(group, dict):
            continue
        segment_ids = group.get("segment_ids")
        if not isinstance(segment_ids, list):
            continue
        order.extend(str(segment_id) for segment_id in segment_ids)
    return order


def _normalize_edl(data: dict[str, Any]) -> dict[str, Any]:
    """把已退役的 groups 语义一次性归并为 EDL.order。"""
    result = dict(data)
    groups = result.pop("groups", None)
    if ("order" not in result or result.get("order") is None) and groups is not None:
        result["order"] = _order_from_groups(groups)
    order = result.get("order")
    if order is not None:
        if not isinstance(order, list):
            raise ValueError("EDL 的 order 必须是数组")
        result["order"] = [str(segment_id) for segment_id in order]
    return result


class Project:
    """单个剪辑项目：workspace/<id>/ 下的文件与 state.json。"""

    def __init__(self, directory: Path):
        self.dir = directory
        self._lock = threading.RLock()

    # --- 固定路径 ---
    @property
    def id(self) -> str:
        return self.dir.name

    @property
    def state_path(self) -> Path:
        return self.dir / "state.json"

    @property
    def transcript_path(self) -> Path:
        return self.dir / "transcript.json"

    @property
    def vad_path(self) -> Path:
        return self.dir / "vad.json"

    @property
    def analysis_wav_path(self) -> Path:
        return self.dir / "analysis_16k.wav"

    @property
    def asr_dir(self) -> Path:
        return self.dir / "asr"

    @property
    def ai_dir(self) -> Path:
        return self.dir / "ai"

    @property
    def exports_dir(self) -> Path:
        return self.dir / "exports"

    @property
    def cuts_dir(self) -> Path:
        return self.dir / "cuts"

    @property
    def uploads_dir(self) -> Path:
        return self.dir / "uploads"

    # --- 状态读写 ---
    def read_state(self) -> dict[str, Any]:
        with self._lock:
            if not self.state_path.exists():
                return {}
            return read_json(self.state_path)

    def update_state(self, **patch: Any) -> dict[str, Any]:
        with self._lock:
            state = self.read_state()
            state.update(patch)
            state["updated_at"] = _now_iso()
            write_json(self.state_path, state)
            return state

    def set_stage(self, stage: str, message: str = "", *, error: str | None = None) -> dict[str, Any]:
        if stage not in PIPELINE_STAGES:
            raise ValueError(f"unknown stage: {stage}")
        return self.update_state(stage=stage, stage_message=message, error=error)

    @property
    def source_path(self) -> Path | None:
        source = self.read_state().get("source") or {}
        raw = source.get("path")
        return Path(raw) if raw else None

    def transcript_ready(self) -> bool:
        return self.transcript_path.exists()

    # --- Cut / EDL ---
    def cut_dir(self, name: str = DEFAULT_CUT) -> Path:
        return self.cuts_dir / _cut_name(name)

    def cut_exports_dir(self, name: str = DEFAULT_CUT) -> Path:
        return self.cut_dir(name) / "exports"

    def cut_clip_plan_path(self, name: str = DEFAULT_CUT) -> Path:
        return self.cut_dir(name) / "clip_plan.json"

    def cut_compose_report_path(self, name: str) -> Path:
        return self.cut_dir(name) / "compose_report.json"

    def _legacy_selection_path(self) -> Path:
        return self.dir / "selection.json"

    def _edl_path(self, name: str) -> Path:
        return self.cut_dir(name) / "edl.json"

    def read_edl(self, name: str = DEFAULT_CUT) -> dict[str, Any]:
        """读取某个 Cut；default 在迁移前兼容根目录 selection.json。"""
        name = _cut_name(name)
        with self._lock:
            path = self._edl_path(name)
            if path.is_file():
                data = read_json(path)
            elif name == DEFAULT_CUT and self._legacy_selection_path().is_file():
                data = read_json(self._legacy_selection_path())
            elif name == DEFAULT_CUT:
                return {}
            else:
                raise ValueError(f"Cut 不存在：{name}")
            if not isinstance(data, dict):
                raise ValueError("EDL 必须是 JSON 对象")
            return _normalize_edl(data)

    def write_edl(self, name: str, data: dict[str, Any]) -> Path:
        """写入 EDL，并在 default 首次写入时迁移旧 selection.json。"""
        name = _cut_name(name)
        if not isinstance(data, dict):
            raise ValueError("EDL 必须是 JSON 对象")
        with self._lock:
            edl_path = self._edl_path(name)
            legacy = self._legacy_selection_path()
            if name == DEFAULT_CUT and not edl_path.exists() and legacy.is_file():
                stamp = time.strftime("%Y%m%d-%H%M%S")
                backup = self.dir / f"selection.json.bak-{stamp}"
                suffix = 1
                while backup.exists():
                    backup = self.dir / f"selection.json.bak-{stamp}-{suffix}"
                    suffix += 1
                os.replace(legacy, backup)
            payload = _normalize_edl(data)
            edl_path.parent.mkdir(parents=True, exist_ok=True)
            self.cut_exports_dir(name).mkdir(parents=True, exist_ok=True)
            write_json(edl_path, payload)
            return edl_path

    def create_cut(self, name: str, label: str | None, edl: dict[str, Any]) -> dict[str, Any]:
        name = _cut_name(name)
        if name == DEFAULT_CUT:
            raise ValueError("default 是保留的 Cut 名称")
        with self._lock:
            if self.cut_dir(name).exists():
                raise ValueError(f"Cut 已存在：{name}")
            payload = dict(edl)
            if label is not None:
                payload["label"] = str(label)
            self.write_edl(name, payload)
            return self._cut_summary(name)

    def delete_cut(self, name: str) -> None:
        name = _cut_name(name)
        if name == DEFAULT_CUT:
            raise ValueError("default Cut 不可删除")
        with self._lock:
            directory = self.cut_dir(name)
            if not directory.is_dir():
                raise ValueError(f"Cut 不存在：{name}")
            shutil.rmtree(directory)

    def list_cuts(self) -> list[dict[str, Any]]:
        with self._lock:
            names = {DEFAULT_CUT}
            if self.cuts_dir.is_dir():
                names.update(
                    path.name
                    for path in self.cuts_dir.iterdir()
                    if path.is_dir() and _CUT_NAME_PATTERN.fullmatch(path.name)
                )
            return [self._cut_summary(name) for name in sorted(names, key=lambda value: (value != DEFAULT_CUT, value))]

    def _cut_summary(self, name: str) -> dict[str, Any]:
        edl_path = self._edl_path(name)
        legacy = self._legacy_selection_path() if name == DEFAULT_CUT else None
        source = edl_path if edl_path.is_file() else legacy
        edl = self.read_edl(name)
        exports = self.cut_exports_dir(name)
        if name == DEFAULT_CUT and not exports.is_dir():
            exports = self.exports_dir
        return {
            "name": name,
            "label": edl.get("label") if isinstance(edl, dict) else None,
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(source.stat().st_mtime)) if source and source.exists() else None,
            "has_export": exports.is_dir() and any(path.is_file() for path in exports.iterdir()),
        }


class Workspace:
    """项目工作区：负责创建、列出、查找项目。"""

    def __init__(self, root: str | Path):
        self.root = Path(root).expanduser().resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._projects: dict[str, Project] = {}
        self._lock = threading.Lock()

    def create_project(self, name: str, *, source_path: Path, imported_via: str) -> Project:
        project_id = f"{time.strftime('%Y%m%d-%H%M%S')}-{_slugify(name)}-{uuid.uuid4().hex[:4]}"
        directory = self.root / project_id
        directory.mkdir(parents=True, exist_ok=False)
        project = Project(directory)
        for sub in (project.asr_dir, project.ai_dir, project.cut_exports_dir()):
            sub.mkdir(parents=True, exist_ok=True)
        project.update_state(
            id=project_id,
            name=name,
            created_at=_now_iso(),
            stage="imported",
            stage_message="已导入，等待处理",
            error=None,
            source={
                "path": str(source_path),
                "filename": source_path.name,
                "imported_via": imported_via,
            },
            ai={},
            export={"status": "idle"},
        )
        with self._lock:
            self._projects[project_id] = project
        return project

    def get(self, project_id: str) -> Project | None:
        with self._lock:
            cached = self._projects.get(project_id)
        if cached:
            return cached
        directory = self.root / project_id
        if not directory.is_dir() or not (directory / "state.json").exists():
            return None
        project = Project(directory)
        with self._lock:
            self._projects.setdefault(project_id, project)
            return self._projects[project_id]

    def list_projects(self) -> list[dict[str, Any]]:
        summaries = []
        for state_file in sorted(self.root.glob("*/state.json"), reverse=True):
            try:
                state = json.loads(state_file.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                continue
            summaries.append(
                {
                    "id": state.get("id") or state_file.parent.name,
                    "name": state.get("name"),
                    "stage": state.get("stage"),
                    "stage_message": state.get("stage_message"),
                    "error": state.get("error"),
                    "created_at": state.get("created_at"),
                    "source": state.get("source"),
                    "duration_ms": state.get("duration_ms"),
                    "transcript_ready": (state_file.parent / "transcript.json").exists(),
                }
            )
        return summaries
