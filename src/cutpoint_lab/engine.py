from __future__ import annotations

from .asr_cache import CachingAsrRunner, backfill_cache_entry, sha256_file
from .features import extract_audio, load_rms_frames
from .io import load_transcript, load_vad, read_json, write_json
from .models import Transcript, TranscriptSegment, TranscriptToken
from .paper_edit.redline import render_redline_markdown
from .paper_edit.review_html import render_review_html
from .paper_edit.state import (
    apply_editor_rows,
    build_plan_from_editor_rows,
    build_plan_from_selection,
)
from .planning import (
    analyze_content_map,
    analyze_quote_candidates,
    budget_report,
    fit_budget,
    merge_topic_candidates,
)
from .quality import (
    CorrectionSet,
    align_reference,
    apply_corrections,
    compose,
    empty_report,
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
from .studio.ai_selector import AiSelector, Suggestion, save_suggestion
from .studio.asr_runner import AsrRunner, ShellAsrRunner, Video2mdAsrRunner, resolve_mp4md_binary
from .studio.config import (
    DEFAULT_API_VAULT_PATH,
    EnvStore,
    resolve_secret_key,
    resolve_transcript_cache_dir,
)
from .studio.llm_client import LlmClient, LlmConfig, LlmError
from .studio.plans import apply_manual_nudges
from .studio.prompt_store import PromptStore
from .studio.vocabulary import VocabularyClient, VocabularyError
from .studio.workspace import Project, Workspace
from .subtitle_exporter import write_srt
from .video_exporter import export_video_plan, ffprobe_duration_ms

__all__ = [
    "AiSelector",
    "AsrRunner",
    "CachingAsrRunner",
    "CorrectionSet",
    "DEFAULT_API_VAULT_PATH",
    "EnvStore",
    "LlmClient",
    "LlmConfig",
    "LlmError",
    "Project",
    "PromptStore",
    "ShellAsrRunner",
    "Suggestion",
    "Transcript",
    "TranscriptSegment",
    "TranscriptToken",
    "Video2mdAsrRunner",
    "VocabularyClient",
    "VocabularyError",
    "Workspace",
    "align_reference",
    "analyze_content_map",
    "analyze_quote_candidates",
    "apply_editor_rows",
    "apply_manual_nudges",
    "apply_corrections",
    "compose",
    "backfill_cache_entry",
    "build_plan_from_editor_rows",
    "build_plan_from_selection",
    "budget_report",
    "export_video_plan",
    "extract_audio",
    "empty_report",
    "ffprobe_duration_ms",
    "fit_budget",
    "load_rms_frames",
    "load_changeset",
    "load_report",
    "load_transcript",
    "load_vad",
    "read_json",
    "preview_corrections",
    "merge_report",
    "merge_topic_candidates",
    "parse_reference",
    "render_review_html",
    "render_redline_markdown",
    "resolve_mp4md_binary",
    "resolve_secret_key",
    "resolve_transcript_cache_dir",
    "review_quality",
    "save_suggestion",
    "save_changeset",
    "save_report",
    "scan_confidence",
    "sha256_file",
    "undo_changeset",
    "write_json",
    "write_srt",
]
