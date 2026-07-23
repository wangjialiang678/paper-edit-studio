from __future__ import annotations

import html
import json
from typing import Any

from ..models import Transcript, TranscriptSegment


def render_review_html(
    transcript: Transcript,
    selection_rows: list[dict],
    decisions: dict[str, dict] | None = None,
    *,
    title: str = "剪辑确认",
    confirm_url: str | None = None,
    order: list[str] | None = None,
) -> str:
    """把字幕和选择状态渲染为可离线使用的交互式确认页面。"""

    updates = {
        str(row.get("id")): row
        for row in selection_rows
        if isinstance(row, dict) and row.get("id") is not None
    }
    decision_map = decisions if isinstance(decisions, dict) else {}
    rows = [
        _review_row(segment, updates.get(segment.id, {}), decision_map.get(segment.id))
        for segment in transcript.segments
    ]
    if order:
        rows_by_id = {row["id"]: row for row in rows}
        ordered_rows = []
        seen_ids = set()
        for raw_segment_id in order:
            segment_id = str(raw_segment_id)
            if segment_id in seen_ids or segment_id not in rows_by_id:
                continue
            ordered_rows.append(rows_by_id[segment_id])
            seen_ids.add(segment_id)
        remaining_rows = [row for row in rows if row["id"] not in seen_ids]
        rows = (
            ordered_rows
            + [row for row in remaining_rows if row["checked"]]
            + [row for row in remaining_rows if not row["checked"]]
        )
    embedded = json.dumps(
        {
            "rows": rows,
            "source": "review_html",
            "confirm_url": confirm_url,
        },
        ensure_ascii=False,
        separators=(",", ":"),
    ).replace("</", "<\\/")

    return (
        """<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>"""
        + html.escape(str(title))
        + """</title>
  <style>
    :root {
      color-scheme: light;
      --blue: #2563eb;
      --blue-dark: #1d4ed8;
      --blue-soft: #eff6ff;
      --border: #dbe2ea;
      --muted: #64748b;
      --text: #172033;
      --surface: #ffffff;
      --page: #f5f7fa;
      --deleted: #dc2626;
    }

    * { box-sizing: border-box; }

    body {
      margin: 0;
      color: var(--text);
      background: var(--page);
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      line-height: 1.55;
    }

    button, input { font: inherit; }

    .toolbar {
      position: sticky;
      top: 0;
      z-index: 10;
      display: flex;
      align-items: center;
      justify-content: space-between;
      gap: 18px;
      min-height: 72px;
      padding: 12px max(24px, calc((100vw - 1060px) / 2));
      border-bottom: 1px solid var(--border);
      background: rgba(255, 255, 255, 0.96);
      box-shadow: 0 3px 14px rgba(15, 23, 42, 0.06);
    }

    .title-group h1 {
      margin: 0;
      font-size: 20px;
      letter-spacing: 0.01em;
    }

    .stats {
      margin-top: 2px;
      color: var(--muted);
      font-size: 14px;
    }

    .export-button {
      flex: none;
      padding: 9px 15px;
      border: 0;
      border-radius: 8px;
      color: white;
      background: var(--blue);
      cursor: pointer;
      font-weight: 600;
    }

    .export-button:hover { background: var(--blue-dark); }
    .export-button:disabled { cursor: default; opacity: 0.65; }

    .actions {
      display: flex;
      flex: none;
      align-items: center;
      gap: 8px;
    }

    .download-button {
      padding: 8px 11px;
      border: 1px solid var(--border);
      border-radius: 8px;
      color: var(--muted);
      background: var(--surface);
      cursor: pointer;
      font-size: 13px;
    }

    .download-button:hover { color: var(--text); border-color: #94a3b8; }

    .confirmation-status {
      margin-top: 3px;
      color: #15803d;
      font-size: 13px;
    }

    main {
      width: min(1060px, calc(100% - 32px));
      margin: 24px auto 56px;
    }

    .hint {
      margin: 0 0 14px;
      color: var(--muted);
      font-size: 14px;
    }

    .rows {
      overflow: hidden;
      border: 1px solid var(--border);
      border-radius: 12px;
      background: var(--surface);
    }

    .sentence-row {
      display: grid;
      grid-template-columns: 24px 28px 86px minmax(0, 1fr) minmax(120px, 220px);
      gap: 12px;
      align-items: start;
      padding: 15px 18px;
      border-bottom: 1px solid #edf0f4;
    }

    .sentence-row:last-child { border-bottom: 0; }
    .sentence-row.checked { background: var(--surface); }
    .sentence-row:not(.checked) { background: #f8fafc; }
    .sentence-row.dragging { opacity: 0.45; }

    .drag-handle {
      width: 24px;
      padding: 0;
      border: 0;
      color: #94a3b8;
      background: transparent;
      cursor: grab;
      font-size: 20px;
      line-height: 1.2;
    }

    .drag-handle:hover { color: var(--text); }
    .drag-handle:active { cursor: grabbing; }

    .sentence-row input[type="checkbox"] {
      width: 17px;
      height: 17px;
      margin: 4px 0 0;
      accent-color: var(--blue);
      cursor: pointer;
    }

    .timestamp {
      padding-top: 2px;
      color: var(--muted);
      font-size: 12px;
      font-variant-numeric: tabular-nums;
      white-space: nowrap;
    }

    .content {
      min-width: 0;
      font-size: 16px;
    }

    .sentence-row:not(.checked) .content {
      color: #94a3b8;
      text-decoration: line-through;
    }

    .tokens {
      display: block;
    }

    .token {
      display: inline;
      padding: 0;
      border: 0;
      border-radius: 3px;
      color: inherit;
      background: transparent;
      cursor: pointer;
      vertical-align: baseline;
    }

    .token:hover {
      background: var(--blue-soft);
    }

    .token.deleted {
      color: var(--deleted);
      background: #fef2f2;
      text-decoration: line-through;
      text-decoration-thickness: 1.5px;
    }

    .plain-text { padding: 3px 0; }

    .reason {
      padding-top: 2px;
      color: var(--muted);
      font-size: 12px;
      line-height: 1.45;
    }

    .labels {
      display: block;
      margin-top: 3px;
      color: #94a3b8;
    }

    @media (max-width: 760px) {
      .toolbar { align-items: flex-start; padding: 12px 16px; }
      main { width: min(100% - 20px, 1060px); margin-top: 14px; }
      .sentence-row {
        grid-template-columns: 22px 26px 76px minmax(0, 1fr);
        gap: 8px;
        padding: 13px 12px;
      }
      .reason { grid-column: 4; }
      .export-button { padding: 8px 11px; }
      .actions { gap: 5px; }
      .download-button { padding: 7px 8px; }
    }
  </style>
</head>
<body>
  <header class="toolbar">
    <div class="title-group">
      <h1>"""
        + html.escape(str(title))
        + """</h1>
      <div class="stats" id="stats" aria-live="polite"></div>
      <div class="confirmation-status" id="confirmation-status" hidden></div>
    </div>
    <div class="actions" id="actions">
      <button class="export-button" id="export-button" type="button">导出 selection.json</button>
    </div>
  </header>
  <main>
    <p class="hint">拖动 ⠿ 调整句子顺序；勾选决定保留或删除；点击词块可删除或恢复该词。</p>
    <section class="rows" id="rows" aria-label="字幕剪辑确认"></section>
  </main>
  <template id="token-template"><button type="button" class="token"></button></template>
  <script id="review-data" type="application/json">"""
        + embedded
        + """</script>
  <script>
    (() => {
      "use strict";

      const payload = JSON.parse(document.getElementById("review-data").textContent);
      const rows = payload.rows.map((row) => ({
        ...row,
        removed: new Set(
          (row.cuts || []).flatMap((cut) => {
            const indexes = [];
            for (let index = cut.start_token; index <= cut.end_token; index += 1) {
              indexes.push(index);
            }
            return indexes;
          }),
        ),
      }));
      const rowsElement = document.getElementById("rows");
      const statsElement = document.getElementById("stats");
      const tokenTemplate = document.getElementById("token-template");
      const exportButton = document.getElementById("export-button");
      const actionsElement = document.getElementById("actions");
      const confirmationStatus = document.getElementById("confirmation-status");
      const confirmUrl = payload.confirm_url;
      const rowById = new Map(rows.map((row) => [row.id, row]));
      let draggedElement = null;

      function visibleRows() {
        return [...rowsElement.querySelectorAll(".sentence-row")]
          .map((element) => rowById.get(element.dataset.rowId))
          .filter(Boolean);
      }

      function formatTimestamp(valueMs) {
        const totalSeconds = Math.max(0, Math.floor(Number(valueMs) / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}`;
      }

      function formatDuration(valueMs) {
        const totalSeconds = Math.max(0, Math.round(valueMs / 1000));
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        return minutes ? `${minutes}分${String(seconds).padStart(2, "0")}秒` : `${seconds}秒`;
      }

      function removedRanges(row) {
        const indexes = [...row.removed]
          .filter((index) => Number.isInteger(index) && index >= 0 && index < row.tokens.length)
          .sort((left, right) => left - right);
        const ranges = [];
        indexes.forEach((index) => {
          const last = ranges[ranges.length - 1];
          if (!last || index > last.end_token + 1) {
            ranges.push({ start_token: index, end_token: index });
          } else {
            last.end_token = index;
          }
        });
        return ranges;
      }

      function keptDuration(row) {
        if (!row.checked) return 0;
        if (!row.tokens.length) return Math.max(0, row.end_ms - row.start_ms);
        return row.tokens.reduce((total, token, index) => (
          row.removed.has(index) ? total : total + Math.max(0, token.end_ms - token.start_ms)
        ), 0);
      }

      function updateStats() {
        const kept = rows.filter((row) => row.checked).length;
        const duration = rows.reduce((total, row) => total + keptDuration(row), 0);
        statsElement.textContent = `保留 ${kept} / ${rows.length} 句 · 预计成片 ${formatDuration(duration)}`;
      }

      function alertNeedsToken() {
        window.alert("保留句至少需要保留一个词，请先恢复词块或取消保留整句。");
      }

      // 与 _join_token_text 一致：相邻 ASCII 字母词块间补一个不可点击空格。
      function needsAsciiSpace(previousText, currentText) {
        return /[A-Za-z]$/.test(previousText) && /^[A-Za-z]/.test(currentText);
      }

      function renderRow(row) {
        const element = document.createElement("article");
        element.className = `sentence-row${row.checked ? " checked" : ""}`;
        element.dataset.rowId = row.id;

        const dragHandle = document.createElement("button");
        dragHandle.className = "drag-handle";
        dragHandle.type = "button";
        dragHandle.draggable = true;
        dragHandle.textContent = "⠿";
        dragHandle.title = "拖动调整顺序";
        dragHandle.setAttribute("aria-label", `拖动句子 ${row.id} 调整顺序`);
        dragHandle.addEventListener("dragstart", (event) => {
          draggedElement = element;
          element.classList.add("dragging");
          event.dataTransfer.effectAllowed = "move";
          event.dataTransfer.setData("text/plain", row.id);
        });
        dragHandle.addEventListener("dragend", () => {
          element.classList.remove("dragging");
          draggedElement = null;
        });

        const checkbox = document.createElement("input");
        checkbox.type = "checkbox";
        checkbox.checked = row.checked;
        checkbox.setAttribute("aria-label", `保留句子 ${row.id}`);
        checkbox.addEventListener("change", () => {
          if (checkbox.checked && row.tokens.length && row.removed.size >= row.tokens.length) {
            checkbox.checked = false;
            alertNeedsToken();
            return;
          }
          row.checked = checkbox.checked;
          element.classList.toggle("checked", row.checked);
          updateStats();
        });

        const timestamp = document.createElement("time");
        timestamp.className = "timestamp";
        timestamp.textContent = `${formatTimestamp(row.start_ms)}–${formatTimestamp(row.end_ms)}`;

        const content = document.createElement("div");
        content.className = "content";
        if (row.tokens.length) {
          const tokens = document.createElement("div");
          tokens.className = "tokens";
          row.tokens.forEach((token, index) => {
            if (index > 0 && needsAsciiSpace(row.tokens[index - 1].text, token.text)) {
              tokens.appendChild(document.createTextNode(" "));
            }
            const tokenButton = tokenTemplate.content.firstElementChild.cloneNode(true);
            tokenButton.textContent = token.text;
            tokenButton.classList.toggle("deleted", row.removed.has(index));
            tokenButton.title = "点击删除或恢复该词";
            tokenButton.addEventListener("click", () => {
              if (row.removed.has(index)) {
                row.removed.delete(index);
              } else {
                const keptCount = row.tokens.length - row.removed.size;
                if (row.checked && keptCount <= 1) {
                  alertNeedsToken();
                  return;
                }
                row.removed.add(index);
              }
              tokenButton.classList.toggle("deleted", row.removed.has(index));
              updateStats();
            });
            tokens.appendChild(tokenButton);
          });
          content.appendChild(tokens);
        } else {
          const plainText = document.createElement("div");
          plainText.className = "plain-text";
          plainText.textContent = row.text;
          content.appendChild(plainText);
        }

        const reason = document.createElement("div");
        reason.className = "reason";
        reason.textContent = row.reason || "";
        if (row.labels.length) {
          const labels = document.createElement("span");
          labels.className = "labels";
          labels.textContent = row.labels.join(" · ");
          reason.appendChild(labels);
        }

        element.append(dragHandle, checkbox, timestamp, content, reason);
        return element;
      }

      rows.forEach((row) => rowsElement.appendChild(renderRow(row)));
      rowsElement.addEventListener("dragover", (event) => {
        if (!draggedElement) return;
        event.preventDefault();
        const target = event.target.closest(".sentence-row");
        if (!target || target === draggedElement) return;
        const bounds = target.getBoundingClientRect();
        const insertBefore = event.clientY < bounds.top + bounds.height / 2;
        rowsElement.insertBefore(
          draggedElement,
          insertBefore ? target : target.nextSibling,
        );
      });
      rowsElement.addEventListener("drop", (event) => event.preventDefault());
      updateStats();

      function selectionPayload() {
        const exportRows = visibleRows().map((row) => {
          const output = {
            id: row.id,
            checked: Boolean(row.checked),
            text: row.text,
            cuts: removedRanges(row),
          };
          if (Object.prototype.hasOwnProperty.call(row, "nudge")) {
            output.nudge = row.nudge;
          }
          return output;
        });
        return {
          rows: exportRows,
          order: visibleRows().filter((row) => row.checked).map((row) => row.id),
          source: "review_html",
        };
      }

      function downloadSelection() {
        const json = JSON.stringify(selectionPayload(), null, 2);
        const url = URL.createObjectURL(new Blob([json], { type: "application/json;charset=utf-8" }));
        const link = document.createElement("a");
        link.href = url;
        link.download = "selection.json";
        document.body.appendChild(link);
        link.click();
        link.remove();
        setTimeout(() => URL.revokeObjectURL(url), 0);
      }

      if (confirmUrl) {
        exportButton.textContent = "✓ 确认完成，继续剪辑";
        const downloadButton = document.createElement("button");
        downloadButton.className = "download-button";
        downloadButton.type = "button";
        downloadButton.textContent = "下载 selection.json";
        downloadButton.addEventListener("click", downloadSelection);
        actionsElement.appendChild(downloadButton);

        exportButton.addEventListener("click", async () => {
          exportButton.disabled = true;
          try {
            const response = await fetch(confirmUrl, {
              method: "POST",
              headers: { "Content-Type": "application/json" },
              body: JSON.stringify(selectionPayload()),
            });
            if (response.status !== 200) {
              throw new Error(`HTTP ${response.status}`);
            }
            exportButton.textContent = "✓ 已确认";
            confirmationStatus.textContent = "已确认，剪辑继续进行中，可关闭本页回到终端";
            confirmationStatus.hidden = false;
          } catch (error) {
            exportButton.disabled = false;
            window.alert("确认失败，请重试；也可下载 selection.json 后手动回传。");
          }
        });
      } else {
        exportButton.addEventListener("click", downloadSelection);
      }
    })();
  </script>
</body>
</html>
"""
    )


def _review_row(
    segment: TranscriptSegment,
    update: dict[str, Any],
    decision: dict[str, Any] | None,
) -> dict[str, Any]:
    tokens = segment.valid_tokens
    checked = bool(update.get("checked", True))
    text = str(update.get("text", segment.text))
    decision = decision if isinstance(decision, dict) else {}
    labels = decision.get("labels")
    row: dict[str, Any] = {
        "id": segment.id,
        "checked": checked,
        "text": text,
        "start_ms": segment.start_ms,
        "end_ms": segment.end_ms,
        "tokens": [
            {
                "text": token.text,
                "start_ms": token.start_ms,
                "end_ms": token.end_ms,
            }
            for token in tokens
        ],
        "cuts": _normalized_cuts(len(tokens), update.get("trim"), update.get("cuts")),
        "reason": str(decision.get("reason") or ""),
        "labels": [str(label) for label in labels] if isinstance(labels, list) else [],
    }
    if "nudge" in update:
        row["nudge"] = update["nudge"]
    return row


def _normalized_cuts(token_count: int, trim: Any, cuts: Any) -> list[dict[str, int]]:
    if token_count <= 0:
        return []
    trim_start, trim_end = _trim_bounds(token_count, trim)
    removed = set(range(0, trim_start))
    removed.update(range(trim_end + 1, token_count))
    if isinstance(cuts, list):
        for item in cuts:
            if not isinstance(item, dict):
                continue
            try:
                start = int(item.get("start_token"))
                end = int(item.get("end_token"))
            except (TypeError, ValueError):
                continue
            if start > end:
                start, end = end, start
            start = min(trim_end, max(trim_start, start))
            end = min(trim_end, max(trim_start, end))
            removed.update(range(start, end + 1))
    return _ranges(sorted(removed))


def _trim_bounds(token_count: int, trim: Any) -> tuple[int, int]:
    if not isinstance(trim, dict):
        return 0, token_count - 1
    try:
        start = int(trim.get("start_token", 0))
        end = int(trim.get("end_token", token_count - 1))
    except (TypeError, ValueError):
        return 0, token_count - 1
    start = max(0, start)
    end = min(token_count - 1, end)
    if start > end:
        return 0, token_count - 1
    return start, end


def _ranges(indexes: list[int]) -> list[dict[str, int]]:
    ranges: list[dict[str, int]] = []
    for index in indexes:
        if not ranges or index > ranges[-1]["end_token"] + 1:
            ranges.append({"start_token": index, "end_token": index})
        else:
            ranges[-1]["end_token"] = index
    return ranges
