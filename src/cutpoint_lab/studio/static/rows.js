/* 字幕行列表：渲染、勾选、文本编辑、静默段标记、统计。 */
import { el, state, pb, fmtClock, escapeHtml, autoGrow, markActive, setStatus } from "./shared.js";
import { rangeIndexForRow, auditionRange } from "./player.js";
import { scheduleAutosave } from "./plan.js";
import { toggleTrimPanel, applySuggestedCuts } from "./trim.js";

export function renderRows() {
  el.rows.innerHTML = "";
  const silenceAfter = new Map();
  let headSilence = null;
  for (const gap of state.silences) {
    if (gap.after_segment_id) silenceAfter.set(gap.after_segment_id, gap);
    else headSilence = gap;
  }
  if (headSilence) el.rows.appendChild(silenceNode(headSilence));
  for (const row of state.rows) {
    el.rows.appendChild(rowNode(row));
    const gap = silenceAfter.get(row.id);
    if (gap) el.rows.appendChild(silenceNode(gap));
  }
  refreshStats();
}

function silenceNode(gap) {
  const div = document.createElement("div");
  div.className = "silence-row";
  div.innerHTML = `<span class="pill">无声 ${(gap.gap_ms / 1000).toFixed(2)}s</span><span>剪辑时自动移除</span>`;
  return div;
}

function rowNode(row) {
  const div = document.createElement("div");
  div.className = `subtitle-row ${row.checked ? "" : "dropped"}`;
  div.dataset.id = row.id;
  const badges = [];
  if (row.ai_keep === true) badges.push('<span class="badge keep">AI 保留</span>');
  if (row.ai_keep === false) badges.push('<span class="badge drop">AI 删除</span>');
  if ((row.ai_labels || []).includes("golden_quote")) badges.push('<span class="badge quote">金句</span>');
  if (row.trim || row.nudge || (row.cuts || []).length) badges.push('<span class="badge trimmed">✂ 已微调</span>');
  if ((row.suggested_cuts || []).length) badges.push(`<span class="badge suggest">气口 ×${row.suggested_cuts.length}</span>`);
  if (row.has_word_timestamps) badges.push('<button class="btn tiny trim-toggle" title="句内微调：删词/剪气口/拖切点">✂ 微调</button>');
  const reason = row.ai_reason ? `<div class="row-reason">${escapeHtml(row.ai_reason)}</div>` : "";
  div.innerHTML = `
    <label class="row-check"><input type="checkbox" ${row.checked ? "checked" : ""}><span>#${row.index}</span></label>
    <div class="row-time">${row.start}<br>${row.end}</div>
    <textarea class="row-text" spellcheck="false"></textarea>
    <div class="row-badges">${badges.join("")}${reason}</div>`;
  const checkbox = div.querySelector("input");
  const textarea = div.querySelector("textarea");
  textarea.value = row.text;
  requestAnimationFrame(() => autoGrow(textarea));
  checkbox.addEventListener("change", () => {
    row.checked = checkbox.checked;
    div.classList.toggle("dropped", !row.checked);
    refreshStats();
    scheduleAutosave();
  });
  textarea.addEventListener("input", () => { row.text = textarea.value; autoGrow(textarea); scheduleAutosave(); });
  const trimBtn = div.querySelector(".trim-toggle");
  if (trimBtn) trimBtn.addEventListener("click", (event) => { event.stopPropagation(); toggleTrimPanel(row, div); });
  div.addEventListener("click", (event) => {
    if (event.target.closest(".trim-panel")) return;
    const tag = event.target.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "LABEL" || tag === "SPAN" || tag === "BUTTON" || tag === "CANVAS") return;
    markActive(row.id);
    if (pb.mode === "edited") {
      if (row.checked) {
        pb.audition = null;
        const index = rangeIndexForRow(row);
        if (index >= 0) {
          pb.rangeIndex = index;
          const target = pb.ranges[index];
          el.video.currentTime = Math.max(target.start_ms, Math.min(row.start_ms, target.end_ms)) / 1000;
        } else {
          el.video.currentTime = row.start_ms / 1000;
          scheduleAutosave();
        }
        el.video.play();
      } else {
        auditionRange(row.start_ms, row.end_ms, "试听已删除句：播完自动停，按空格回到成片。");
      }
    } else {
      el.video.currentTime = row.start_ms / 1000;
      el.video.play();
    }
  });
  return div;
}

export function refreshStats() {
  let keptMs = 0;
  let keptCount = 0;
  if (state.orderedGroups) {
    const durations = new Map(state.rows.map((row) => [row.id, Math.max(0, row.end_ms - row.start_ms)]));
    for (const group of state.orderedGroups) {
      for (const segmentId of group.segment_ids) keptMs += durations.get(segmentId) || 0;
    }
    keptCount = state.orderedGroups.reduce((sum, group) => sum + group.segment_ids.length, 0);
  } else {
    for (const row of state.rows) {
      if (row.checked) { keptCount += 1; keptMs += Math.max(0, row.end_ms - row.start_ms); }
    }
  }
  el.statDuration.textContent = fmtClock(keptMs);
  el.statKept.textContent = String(keptCount);
}

/* 工具栏「一键剪气口」：对所有保留句应用后端检测建议。 */
export function applyAllSuggestedCuts() {
  let rowsTouched = 0;
  let spans = 0;
  for (const row of state.rows) {
    if (!row.checked) continue;
    const applied = applySuggestedCuts(row);
    if (applied) { rowsTouched += 1; spans += applied; }
  }
  if (rowsTouched) {
    renderRows();
    setStatus(`一键剪气口：${rowsTouched} 句共剪除 ${spans} 处（词块面板可逐处恢复）。`);
  } else {
    setStatus("没有可应用的气口建议。");
  }
}
