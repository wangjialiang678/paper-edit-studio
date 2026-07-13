from __future__ import annotations

import json
import re
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


def _slugify(name: str, max_length: int = 24) -> str:
    slug = _SLUG_PATTERN.sub("-", name.lower()).strip("-")
    return slug[:max_length] or "video"


def _now_iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S")


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
        for sub in (project.asr_dir, project.ai_dir, project.exports_dir):
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
