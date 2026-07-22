"""把剪辑决策渲染成「修订模式」Markdown 对照文件。

保留句正常显示、删除句用 `~~删除线~~` 并在行尾标注 AI 理由，
让人一眼看出整篇里哪些被剪掉、为什么剪。纯函数、无 IO，便于单测。
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

from ..models import Transcript


def _clock(value_ms: int) -> str:
    minutes, remainder = divmod(max(0, int(value_ms)), 60_000)
    seconds = remainder // 1000
    return f"{minutes:02d}:{seconds:02d}"


def _reason_for(decision: Mapping[str, Any] | None) -> str:
    """删除句行尾注释：优先用 AI 的 reason，退而用 labels，都没有则空。"""
    if not decision:
        return ""
    reason = str(decision.get("reason") or "").strip()
    if reason:
        return reason
    labels = [str(label).strip() for label in (decision.get("labels") or []) if str(label).strip()]
    return " / ".join(labels)


def render_redline_markdown(
    transcript: Transcript,
    keeps: Iterable[str],
    decisions: Mapping[str, Mapping[str, Any]] | None = None,
    *,
    title: str = "剪辑修订对照",
) -> str:
    """渲染整篇字幕的修订对照。

    - transcript：原始（未编辑）字幕，按时间顺序逐句列出。
    - keeps：保留的 segment_id 集合（删除 = 不在其中）。
    - decisions：segment_id → {reason, labels, ...}，仅用于给删除句注明理由。
    """
    keep_set = {str(item) for item in keeps}
    decisions = decisions or {}
    segments = list(transcript.segments)

    kept_count = sum(1 for segment in segments if segment.id in keep_set)
    total_count = len(segments)
    deleted_count = total_count - kept_count

    total_ms = sum(max(0, segment.end_ms - segment.start_ms) for segment in segments)
    kept_ms = sum(
        max(0, segment.end_ms - segment.start_ms)
        for segment in segments
        if segment.id in keep_set
    )
    ratio = f"（约 -{round((1 - kept_ms / total_ms) * 100)}%）" if total_ms else ""

    lines: list[str] = [
        f"# {title}",
        "",
        f"- 原始 {total_count} 句 / 保留 {kept_count} 句 / 删除 {deleted_count} 句",
        f"- 原始有效时长 {_clock(total_ms)} / 保留 {_clock(kept_ms)} {ratio}".rstrip(),
        "- 删除线 = 被剪掉；行尾 `·` 后为删除理由",
        "",
        "---",
        "",
    ]

    for segment in segments:
        stamp = f"`{_clock(segment.start_ms)}`"
        text = segment.text.strip() or "（空）"
        if segment.id in keep_set:
            lines.append(f"- {stamp} {text}")
        else:
            reason = _reason_for(decisions.get(segment.id))
            suffix = f" · {reason}" if reason else ""
            lines.append(f"- {stamp} ~~{text}~~{suffix}")

    return "\n".join(lines) + "\n"
