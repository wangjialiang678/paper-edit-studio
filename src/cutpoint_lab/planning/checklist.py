from __future__ import annotations

from typing import Any

from ..quality.align_reference import normalize_text


def _selected_rows(edl: dict[str, Any]) -> list[dict[str, Any]]:
    rows = [
        row
        for row in edl.get("rows") or []
        if isinstance(row, dict) and row.get("id") is not None
    ]
    order = edl.get("order")
    if isinstance(order, list) and order:
        selected = {str(item) for item in order}
        return [row for row in rows if str(row["id"]) in selected]
    return [row for row in rows if bool(row.get("checked"))]


def _seconds(milliseconds: int) -> str:
    return f"{milliseconds / 1000:.1f}s".replace(".0s", "s")


def build_export_checklist(
    edl: dict[str, Any],
    *,
    transcript_segments: list[Any],
    content_map: dict[str, Any] | None,
    budget: dict[str, Any],
) -> dict[str, Any]:
    if content_map is None:
        topics_item = {
            "key": "topics_confirmed",
            "ok": None,
            "detail": "无 content_map，跳过主题确认检查",
        }
    else:
        status = str(content_map.get("status") or "draft")
        topics_item = {
            "key": "topics_confirmed",
            "ok": status == "confirmed",
            "detail": f"content_map status={status}",
        }

    target_s = budget.get("target_s")
    estimated_ms = int(budget.get("estimated_ms") or 0)
    tolerance_s = budget.get("tolerance_s") or 0
    if target_s is None:
        duration_item = {
            "key": "duration",
            "ok": None,
            "detail": f"预计 {_seconds(estimated_ms)}，未设置目标时长",
        }
    else:
        target_ms = round(float(target_s) * 1000)
        tolerance_ms = round(float(tolerance_s) * 1000)
        delta_ms = estimated_ms - target_ms
        ok = abs(delta_ms) <= tolerance_ms
        if ok:
            suffix = "在容差内"
        elif delta_ms > 0:
            suffix = f"超 {_seconds(delta_ms - tolerance_ms)}"
        else:
            suffix = f"少 {_seconds(abs(delta_ms) - tolerance_ms)}"
        duration_item = {
            "key": "duration",
            "ok": ok,
            "detail": (
                f"预计 {_seconds(estimated_ms)} / 目标 {target_s}±{tolerance_s}s，"
                f"{suffix}"
            ),
        }

    selected_rows = _selected_rows(edl)
    quote_rows = [
        row
        for row in selected_rows
        if str(row.get("role") or "") == "quote"
    ]
    locked_quotes = sum(row.get("locked") is True for row in quote_rows)
    quotes_item = {
        "key": "quotes_locked",
        "ok": all(row.get("locked") is True for row in quote_rows),
        "detail": f"{locked_quotes} 个金句已锁定"
        + (
            f"，{len(quote_rows) - locked_quotes} 个未锁定"
            if locked_quotes != len(quote_rows)
            else ""
        ),
    }

    brief = edl.get("brief") if isinstance(edl.get("brief"), dict) else {}
    backgrounds = brief.get("background") or []
    if not isinstance(backgrounds, list):
        backgrounds = []
    transcript_text = {
        str(
            segment.get("id", segment.get("segment_id", ""))
            if isinstance(segment, dict)
            else getattr(segment, "id", "")
        ): str(
            segment.get("text", "")
            if isinstance(segment, dict)
            else getattr(segment, "text", "")
        )
        for segment in transcript_segments
    }
    kept_texts = [
        normalize_text(
            str(row.get("text", transcript_text.get(str(row.get("id")), "")))
        )
        for row in selected_rows
    ]
    missing = [
        str(background)
        for background in backgrounds
        if normalize_text(str(background))
        and not any(
            normalize_text(str(background)) in text
            for text in kept_texts
        )
    ]
    if not backgrounds:
        background_item = {
            "key": "background_covered",
            "ok": None,
            "detail": "brief.background 为空，跳过背景覆盖检查",
        }
    else:
        background_item = {
            "key": "background_covered",
            "ok": not missing,
            "detail": (
                "brief.background 均已覆盖"
                if not missing
                else "brief.background 中未覆盖：" + "、".join(missing)
            ),
        }

    items = [topics_item, duration_item, quotes_item, background_item]
    return {
        "items": items,
        "ok": not any(item["ok"] is False for item in items),
    }


__all__ = ["build_export_checklist"]
