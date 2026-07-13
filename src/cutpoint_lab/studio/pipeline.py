from __future__ import annotations

import logging
import threading
from typing import Any, Callable

from ..export.video import ffprobe_duration_ms
from ..features import extract_audio
from ..io import write_json
from .asr_runner import AsrRunner
from .workspace import Project

logger = logging.getLogger("studio.pipeline")


class PipelineManager:
    """导入后的后台流水线：probe → 提取分析音频 → ASR → （可选）AI 建议 → ready。

    每个项目一个线程；阶段进度写入 state.json，前端轮询展示。
    """

    def __init__(
        self,
        asr_runner: AsrRunner,
        *,
        auto_ai: Callable[[Project], None] | None = None,
    ):
        self.asr_runner = asr_runner
        self.auto_ai = auto_ai
        self._threads: dict[str, threading.Thread] = {}
        self._lock = threading.Lock()

    def start(self, project: Project) -> None:
        with self._lock:
            existing = self._threads.get(project.id)
            if existing and existing.is_alive():
                raise RuntimeError(f"项目 {project.id} 的流水线已在运行")
            thread = threading.Thread(target=self._run, args=(project,), daemon=True, name=f"pipeline-{project.id}")
            self._threads[project.id] = thread
        thread.start()

    def is_running(self, project: Project) -> bool:
        with self._lock:
            thread = self._threads.get(project.id)
        return bool(thread and thread.is_alive())

    def _run(self, project: Project) -> None:
        source = project.source_path
        try:
            if source is None or not source.exists():
                raise RuntimeError(f"源媒体不存在：{source}")

            project.set_stage("probing", "读取媒体信息")
            duration_ms = ffprobe_duration_ms(source)
            project.update_state(duration_ms=duration_ms)
            logger.info("pipeline %s: probed %sms", project.id, duration_ms)

            project.set_stage("extracting_audio", "提取 16kHz 分析音频")
            if not project.analysis_wav_path.exists():
                extract_audio(source, project.analysis_wav_path)

            project.set_stage("transcribing", "语音识别中（DashScope fun-asr，长视频可能需要数分钟）")
            converted = self.asr_runner.transcribe(source, project.asr_dir, source_video=str(source))
            write_json(project.transcript_path, converted["transcript"])
            write_json(project.vad_path, converted["vad"])
            segment_count = len(converted["transcript"].get("segments") or [])
            project.update_state(asr={"segment_count": segment_count})
            logger.info("pipeline %s: transcribed %s segments", project.id, segment_count)

            if self.auto_ai is not None:
                project.set_stage("ai_suggesting", "AI 正在生成保留建议")
                try:
                    self.auto_ai(project)
                except Exception as exc:  # noqa: BLE001 - AI 失败不阻塞主流程，默认全选兜底。
                    logger.warning("pipeline %s: auto AI failed: %s", project.id, exc)
                    project.update_state(ai_warning=f"AI 建议失败（已默认全部保留）：{exc}")

            project.set_stage("ready", "字幕就绪，可以开始剪辑")
        except Exception as exc:  # noqa: BLE001 - 后台线程需把异常落到 state 供前端展示。
            logger.exception("pipeline %s failed", project.id)
            project.set_stage("error", "处理失败", error=str(exc))
