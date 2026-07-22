/* 句内微调面板：词块划线删除（含句内气口）、波形切点拖拽、毫秒微移、接缝试听。

   数据模型（与后端契约一致，索引均基于 row.tokens=valid_tokens）：
   - row.trim  = {start_token, end_token}   句首/句尾裁剪（保留区间）
   - row.cuts  = [{start_token, end_token}] 句内删除的词区间（升序、互不重叠）
   - row.nudge = {start_ms, end_ms}         句首/句尾切点毫秒偏移（±1000）
   - row.suggested_cuts = [{start_token, end_token, kind, text}] 后端气口检测建议 */
import { el, state, pb, api, setStatus, escapeHtml, autoGrow, prefs } from "./shared.js";
import { auditionParts, segmentBaseId } from "./player.js";
import { scheduleAutosave, flushPlanNow } from "./plan.js";

const trimUi = { rowId: null, edge: "start", wave: null, drag: null };

const NUDGE_LIMIT_MS = 1000;
const JUNCTION_CONTEXT_MS = 1200;

// ---------- 删除集合：trim + cuts 的统一视图 ----------

export function trimBounds(row) {
  const count = (row.tokens || []).length;
  const trim = row.trim || {};
  const start = Math.min(Math.max(0, trim.start_token ?? 0), Math.max(0, count - 1));
  const end = Math.min(Math.max(start, trim.end_token ?? count - 1), Math.max(0, count - 1));
  return { start, end };
}

function struckSet(row) {
  const struck = new Set();
  const { start, end } = trimBounds(row);
  (row.tokens || []).forEach((_, index) => {
    if (index < start || index > end) struck.add(index);
  });
  for (const cut of row.cuts || []) {
    for (let i = cut.start_token; i <= cut.end_token; i++) struck.add(i);
  }
  return struck;
}

function joinTokens(tokens) {
  let out = "";
  for (const token of tokens) {
    // 仅字母之间补空格；数字序列（"1"/"7"/"0"/"0"）须连写还原成 "1700"。
    if (out && /[A-Za-z]$/.test(out) && /^[A-Za-z]/.test(token.text)) out += " ";
    out += token.text;
  }
  return out;
}

/* 把删除集合规范化回 row.trim / row.cuts / row.text。整句删空时拒绝并提示。 */
function applyStruck(row, struck) {
  const count = row.tokens.length;
  const keptIndexes = [];
  for (let i = 0; i < count; i++) if (!struck.has(i)) keptIndexes.push(i);
  if (!keptIndexes.length) {
    setStatus("不能删光整句：想整句删除请直接取消该句勾选。", "warn");
    return false;
  }
  const first = keptIndexes[0];
  const last = keptIndexes[keptIndexes.length - 1];
  if (first === 0 && last === count - 1) delete row.trim;
  else row.trim = { start_token: first, end_token: last };
  const cuts = [];
  let runStart = null;
  for (let i = first; i <= last; i++) {
    if (struck.has(i)) {
      if (runStart === null) runStart = i;
    } else if (runStart !== null) {
      cuts.push({ start_token: runStart, end_token: i - 1 });
      runStart = null;
    }
  }
  if (cuts.length) row.cuts = cuts;
  else delete row.cuts;
  row.text = joinTokens(keptIndexes.map((i) => row.tokens[i]));
  const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(row.id)}"]`);
  const textarea = node && node.querySelector(".row-text");
  if (textarea) { textarea.value = row.text; autoGrow(textarea); }
  scheduleAutosave();
  return true;
}

export function rowCutMs(row, edge) {
  const { start, end } = trimBounds(row);
  const nudge = row.nudge || {};
  if (edge === "start") return row.tokens[start].start_ms + (nudge.start_ms || 0);
  return row.tokens[end].end_ms + (nudge.end_ms || 0);
}

/* 一键应用该句所有气口建议；返回应用的区间数。 */
export function applySuggestedCuts(row) {
  const suggestions = row.suggested_cuts || [];
  if (!suggestions.length) return 0;
  const struck = struckSet(row);
  let applied = 0;
  for (const span of suggestions) {
    let fresh = false;
    for (let i = span.start_token; i <= span.end_token; i++) {
      if (!struck.has(i)) { struck.add(i); fresh = true; }
    }
    if (fresh) applied += 1;
  }
  if (applied && !applyStruck(row, struck)) return 0;
  return applied;
}

// ---------- 接缝试听：播「上一段结尾 → 跳切 → 下一段开头」 ----------

function rowRangeIndexes(row) {
  const indexes = [];
  pb.ranges.forEach((item, index) => {
    if ((item.source_segment_ids || []).some((id) => segmentBaseId(id) === row.id)) indexes.push(index);
  });
  return indexes;
}

function junctionPartsAround(prevRange, nextRange) {
  const parts = [];
  if (prevRange) {
    parts.push({ startMs: Math.max(prevRange.start_ms, prevRange.end_ms - JUNCTION_CONTEXT_MS), endMs: prevRange.end_ms });
  }
  if (nextRange) {
    parts.push({ startMs: nextRange.start_ms, endMs: Math.min(nextRange.end_ms, nextRange.start_ms + JUNCTION_CONTEXT_MS) });
  }
  return parts;
}

/* 先把计划落库（拿到策略+nudge 后的真实切点），再按最新 ranges 试听接缝。 */
async function auditionEdgeJunction(row, edge) {
  try { await flushPlanNow(); } catch (error) { setStatus(`保存失败：${error.message}`, "warn"); return; }
  const indexes = rowRangeIndexes(row);
  if (!indexes.length) {
    // 行未进成片（未勾选等）：退化为原片切点前后各 0.8s。
    const cut = rowCutMs(row, edge);
    auditionParts([{ startMs: Math.max(0, cut - 800), endMs: cut + 800 }], "该句不在成片中，试听原片切点前后 0.8s。");
    return;
  }
  let parts;
  if (edge === "start") {
    const index = indexes[0];
    const ids = pb.ranges[index].source_segment_ids || [];
    if (segmentBaseId(ids[0]) !== row.id) {
      setStatus("该句与上一句在成片中连续（中间没有删除内容），句首没有切点，微调不会生效。", "warn");
      return;
    }
    parts = junctionPartsAround(index > 0 ? pb.ranges[index - 1] : null, pb.ranges[index]);
  } else {
    const index = indexes[indexes.length - 1];
    const ids = pb.ranges[index].source_segment_ids || [];
    if (segmentBaseId(ids[ids.length - 1]) !== row.id) {
      setStatus("该句与下一句在成片中连续（中间没有删除内容），句尾没有切点，微调不会生效。", "warn");
      return;
    }
    parts = junctionPartsAround(pb.ranges[index], index + 1 < pb.ranges.length ? pb.ranges[index + 1] : null);
  }
  const cutIndex = edge === "start" ? indexes[0] : indexes[indexes.length - 1];
  const cutMs = edge === "start" ? pb.ranges[cutIndex].start_ms : pb.ranges[cutIndex].end_ms;
  auditionParts(parts, `接缝试听：${edge === "start" ? "句首" : "句尾"}切点当前在 ${(cutMs / 1000).toFixed(2)}s（＋=切点后移、多带相邻内容；−=提前切。切点若在静音里，±几十毫秒听不出属正常）。`);
}

/* 句内剪切区间的接缝试听：优先用真实 ranges 中该句相邻子段，缺省用词边界近似。 */
async function auditionCutSpan(row, span) {
  const cutStart = row.tokens[span.start_token].start_ms;
  const cutEnd = row.tokens[span.end_token].end_ms;
  try { await flushPlanNow(); } catch { /* 保存失败时仍用近似试听 */ }
  const subRanges = rowRangeIndexes(row).map((i) => pb.ranges[i]);
  const mid = (cutStart + cutEnd) / 2;
  for (let i = 0; i + 1 < subRanges.length; i++) {
    if (subRanges[i].end_ms <= mid + 200 && subRanges[i + 1].start_ms >= mid - 200) {
      auditionParts(junctionPartsAround(subRanges[i], subRanges[i + 1]), "接缝试听（句内剪切）：删除段前 → 跳切 → 删除段后。");
      return;
    }
  }
  auditionParts(
    [
      { startMs: Math.max(0, cutStart - JUNCTION_CONTEXT_MS), endMs: cutStart },
      { startMs: cutEnd, endMs: cutEnd + JUNCTION_CONTEXT_MS },
    ],
    "接缝试听（句内剪切，词边界近似）。"
  );
}

function maybeAudition(run) {
  if (prefs.autoAudition) run();
}

// ---------- 面板渲染 ----------

export function toggleTrimPanel(row, rowDiv) {
  const existing = el.rows.querySelector(".trim-panel");
  const wasOpen = trimUi.rowId === row.id;
  if (existing) existing.remove();
  trimUi.rowId = null;
  trimUi.wave = null;
  if (wasOpen) return;
  trimUi.rowId = row.id;
  trimUi.edge = "start";
  const panel = document.createElement("div");
  panel.className = "trim-panel";
  rowDiv.appendChild(panel);
  renderTrimPanel(row);
  loadWave(row);
}

function trimPanelNode() {
  return el.rows.querySelector(".trim-panel");
}

function nudgeLabel(row) {
  const nudge = row.nudge || {};
  const parts = [];
  if (nudge.start_ms) parts.push(`句首 ${nudge.start_ms > 0 ? "+" : ""}${nudge.start_ms}ms`);
  if (nudge.end_ms) parts.push(`句尾 ${nudge.end_ms > 0 ? "+" : ""}${nudge.end_ms}ms`);
  return parts.join(" · ") || "切点未微移";
}

export function renderTrimPanel(row) {
  const panel = trimPanelNode();
  if (!panel || trimUi.rowId !== row.id) return;
  const struck = struckSet(row);
  const suggested = new Set();
  for (const span of row.suggested_cuts || []) {
    for (let i = span.start_token; i <= span.end_token; i++) suggested.add(i);
  }
  const chips = row.tokens.map((token, index) => {
    const classes = ["chip"];
    if (struck.has(index)) classes.push("cut");
    else if (suggested.has(index)) classes.push("suggest");
    return `<span class="${classes.join(" ")}" data-i="${index}" title="点击=删除/恢复该词">${escapeHtml(token.text)}</span>`;
  }).join("");
  const pendingSuggestions = (row.suggested_cuts || []).filter((span) => {
    for (let i = span.start_token; i <= span.end_token; i++) if (!struck.has(i)) return true;
    return false;
  }).length;
  panel.innerHTML = `
    <div class="trim-chips">${chips}</div>
    <canvas class="trim-wave" height="44"></canvas>
    <div class="trim-controls">
      <span class="trim-hint">点词块＝删除/恢复；红虚线块＝检测到的气口；波形竖线可拖动</span>
      <div class="edge-toggle">
        <button class="mode-btn ${trimUi.edge === "start" ? "active" : ""}" data-edge="start">句首</button>
        <button class="mode-btn ${trimUi.edge === "end" ? "active" : ""}" data-edge="end">句尾</button>
      </div>
      <div class="nudge-btns">
        <button class="btn small" data-nudge="-50">−50ms</button>
        <button class="btn small" data-nudge="-10">−10ms</button>
        <button class="btn small" data-nudge="10">+10ms</button>
        <button class="btn small" data-nudge="50">+50ms</button>
      </div>
      <span class="nudge-label">${nudgeLabel(row)}</span>
    </div>
    <div class="trim-controls">
      ${pendingSuggestions ? `<button class="btn small accent" data-act="apply-suggest">剪气口（${pendingSuggestions} 处建议）</button>` : ""}
      <button class="btn small" data-act="listen">▶ 试听接缝</button>
      <label class="trim-auto"><input type="checkbox" data-act="auto" ${prefs.autoAudition ? "checked" : ""}>调整后自动试听</label>
      <button class="btn small" data-act="reset">重置本句</button>
    </div>`;
  panel.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number(chip.dataset.i);
      const next = struckSet(row);
      const striking = !next.has(index);
      if (striking) next.add(index);
      else next.delete(index);
      if (!applyStruck(row, next)) return;
      renderTrimPanel(row);
      drawWave(row);
      if (striking) {
        const span = findCutSpanContaining(row, index);
        if (span) maybeAudition(() => auditionCutSpan(row, span));
        else maybeAudition(() => auditionEdgeJunction(row, index <= trimBounds(row).start ? "start" : "end"));
      }
    });
  });
  panel.querySelectorAll("[data-edge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      trimUi.edge = button.dataset.edge;
      renderTrimPanel(row);
      drawWave(row);
    });
  });
  panel.querySelectorAll("[data-nudge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      applyNudgeDelta(row, trimUi.edge, Number(button.dataset.nudge));
      maybeAudition(() => auditionEdgeJunction(row, trimUi.edge));
    });
  });
  const applyBtn = panel.querySelector('[data-act="apply-suggest"]');
  if (applyBtn) applyBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    const applied = applySuggestedCuts(row);
    renderTrimPanel(row);
    drawWave(row);
    if (applied) setStatus(`已剪除 ${applied} 处气口，自动保存中。`);
  });
  panel.querySelector('[data-act="listen"]').addEventListener("click", (event) => {
    event.stopPropagation();
    auditionEdgeJunction(row, trimUi.edge);
  });
  panel.querySelector('[data-act="auto"]').addEventListener("change", (event) => {
    prefs.autoAudition = event.target.checked;
    setStatus(prefs.autoAudition ? "已开启：调整后自动试听接缝。" : "已关闭自动试听，可用「试听接缝」手动触发。");
  });
  panel.querySelector('[data-act="reset"]').addEventListener("click", (event) => {
    event.stopPropagation();
    delete row.trim;
    delete row.nudge;
    delete row.cuts;
    row.text = row.original_text || row.text;
    const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(row.id)}"]`);
    const textarea = node && node.querySelector(".row-text");
    if (textarea) { textarea.value = row.text; autoGrow(textarea); }
    scheduleAutosave();
    renderTrimPanel(row);
    drawWave(row);
  });
  bindWaveDrag(row, panel.querySelector(".trim-wave"));
  drawWave(row);
}

function findCutSpanContaining(row, index) {
  return (row.cuts || []).find((span) => index >= span.start_token && index <= span.end_token) || null;
}

function applyNudgeDelta(row, edge, delta) {
  const nudge = { start_ms: 0, end_ms: 0, ...(row.nudge || {}) };
  const key = edge === "start" ? "start_ms" : "end_ms";
  nudge[key] = Math.max(-NUDGE_LIMIT_MS, Math.min(NUDGE_LIMIT_MS, (nudge[key] || 0) + delta));
  if (!nudge.start_ms && !nudge.end_ms) delete row.nudge;
  else row.nudge = nudge;
  scheduleAutosave();
  renderTrimPanel(row);
}

// ---------- 波形：绘制 + 竖线拖拽 ----------

async function loadWave(row) {
  try {
    const startMs = Math.max(0, row.start_ms - 600);
    const endMs = row.end_ms + 600;
    trimUi.wave = await api(`/api/projects/${state.projectId}/rms?start_ms=${startMs}&end_ms=${endMs}`);
    drawWave(row);
  } catch (error) {
    console.error(error);
  }
}

function waveGeometry(canvas) {
  const wave = trimUi.wave;
  if (!wave || !wave.values.length) return null;
  const width = canvas.clientWidth || (canvas.parentElement && canvas.parentElement.clientWidth) || 600;
  const span = wave.end_ms - wave.start_ms || 1;
  return {
    wave, width, span,
    toX: (ms) => ((ms - wave.start_ms) / span) * width,
    toMs: (x) => wave.start_ms + (x / width) * span,
  };
}

export function drawWave(row) {
  const panel = trimPanelNode();
  const canvas = panel && panel.querySelector(".trim-wave");
  if (!canvas || trimUi.rowId !== row.id) return;
  const geo = waveGeometry(canvas);
  const width = canvas.clientWidth || (canvas.parentElement && canvas.parentElement.clientWidth) || 600;
  canvas.width = width;
  canvas.height = 44;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, 44);
  if (!geo) return;
  const { wave, toX } = geo;
  const step = width / wave.values.length;
  ctx.fillStyle = "#3a4257";
  wave.values.forEach((value, index) => {
    const height = Math.max(1, value * 40);
    ctx.fillRect(index * step, 44 - height, Math.max(1, step - 0.5), height);
  });
  const startCut = rowCutMs(row, "start");
  const endCut = rowCutMs(row, "end");
  ctx.fillStyle = "rgba(94, 129, 244, 0.15)";
  ctx.fillRect(toX(startCut), 0, Math.max(0, toX(endCut) - toX(startCut)), 44);
  ctx.fillStyle = "rgba(228, 90, 90, 0.28)";
  for (const span of row.cuts || []) {
    const cutStart = toX(row.tokens[span.start_token].start_ms);
    const cutEnd = toX(row.tokens[span.end_token].end_ms);
    ctx.fillRect(cutStart, 0, Math.max(1, cutEnd - cutStart), 44);
  }
  for (const [cut, color] of [[startCut, "#5e81f4"], [endCut, "#e2c04e"]]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(toX(cut), 0);
    ctx.lineTo(toX(cut), 44);
    ctx.stroke();
  }
}

const DRAG_HIT_PX = 8;

function bindWaveDrag(row, canvas) {
  if (!canvas) return;
  const edgeAtX = (x) => {
    const geo = waveGeometry(canvas);
    if (!geo) return null;
    const startX = geo.toX(rowCutMs(row, "start"));
    const endX = geo.toX(rowCutMs(row, "end"));
    const distStart = Math.abs(x - startX);
    const distEnd = Math.abs(x - endX);
    if (Math.min(distStart, distEnd) > DRAG_HIT_PX) return null;
    return distStart <= distEnd ? "start" : "end";
  };
  canvas.addEventListener("pointermove", (event) => {
    if (trimUi.drag) return;
    const rect = canvas.getBoundingClientRect();
    canvas.style.cursor = edgeAtX(event.clientX - rect.left) ? "col-resize" : "default";
  });
  canvas.addEventListener("pointerdown", (event) => {
    const rect = canvas.getBoundingClientRect();
    const edge = edgeAtX(event.clientX - rect.left);
    if (!edge) return;
    event.preventDefault();
    event.stopPropagation();
    trimUi.edge = edge;
    trimUi.drag = { edge };
    try { canvas.setPointerCapture(event.pointerId); } catch { /* 合成事件/指针已释放时无捕获，拖拽仍可用 */ }
    const { start, end } = trimBounds(row);
    const anchorMs = edge === "start" ? row.tokens[start].start_ms : row.tokens[end].end_ms;
    const move = (e) => {
      const x = Math.min(rect.width, Math.max(0, e.clientX - rect.left));
      const geo = waveGeometry(canvas);
      if (!geo) return;
      // 拖到的位置换算成相对词边界的 nudge，10ms 步进（与 RMS 分辨率一致）。
      const raw = geo.toMs(x) - anchorMs;
      const clamped = Math.max(-NUDGE_LIMIT_MS, Math.min(NUDGE_LIMIT_MS, Math.round(raw / 10) * 10));
      const nudge = { start_ms: 0, end_ms: 0, ...(row.nudge || {}) };
      nudge[edge === "start" ? "start_ms" : "end_ms"] = clamped;
      if (!nudge.start_ms && !nudge.end_ms) delete row.nudge;
      else row.nudge = nudge;
      const label = trimPanelNode() && trimPanelNode().querySelector(".nudge-label");
      if (label) label.textContent = nudgeLabel(row);
      drawWave(row);
    };
    const up = () => {
      canvas.removeEventListener("pointermove", move);
      canvas.removeEventListener("pointerup", up);
      canvas.removeEventListener("pointercancel", up);
      trimUi.drag = null;
      scheduleAutosave();
      renderTrimPanel(row);
      maybeAudition(() => auditionEdgeJunction(row, edge));
    };
    canvas.addEventListener("pointermove", move);
    canvas.addEventListener("pointerup", up);
    canvas.addEventListener("pointercancel", up);
  });
}
