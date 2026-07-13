/* Paper Edit Studio 前端：项目列表 / 流水线进度 / 字幕剪辑 / AI 选段 / 预览导出 */
"use strict";

const $ = (id) => document.getElementById(id);

const el = {
  sidebar: $("sidebar"), importZone: $("importZone"), fileInput: $("fileInput"),
  pickFileBtn: $("pickFileBtn"), pathInput: $("pathInput"), importPathBtn: $("importPathBtn"),
  uploadProgress: $("uploadProgress"), uploadBar: $("uploadBar"), uploadPct: $("uploadPct"),
  projectList: $("projectList"),
  emptyView: $("emptyView"), pipelineView: $("pipelineView"), pipelineTitle: $("pipelineTitle"),
  stageList: $("stageList"), pipelineError: $("pipelineError"), pipelineErrorText: $("pipelineErrorText"),
  retryBtn: $("retryBtn"),
  editorView: $("editorView"), video: $("video"), rows: $("rows"),
  playBtn: $("playBtn"), progressBar: $("progressBar"), progressFill: $("progressFill"),
  timeLabel: $("timeLabel"), modeToggle: $("modeToggle"),
  statDuration: $("statDuration"), statKept: $("statKept"),
  statusBox: $("statusBox"), exportResult: $("exportResult"),
  orderedBanner: $("orderedBanner"), exitOrderedBtn: $("exitOrderedBtn"),
  aiPanelBtn: $("aiPanelBtn"), exportBtn: $("exportBtn"),
  aiPanel: $("aiPanel"), aiCloseBtn: $("aiCloseBtn"), aiBrief: $("aiBrief"), aiRunBtn: $("aiRunBtn"),
  aiBody: $("aiBody"),
};

const STAGE_LABELS = [
  ["imported", "已导入"],
  ["probing", "读取媒体信息"],
  ["extracting_audio", "提取音频"],
  ["transcribing", "语音识别（生成字幕）"],
  ["ai_suggesting", "AI 保留建议"],
  ["ready", "就绪"],
];
const MODE_NAMES = { koubo_tighten: "口播精剪", topic_slicing: "主题切片", highlight_remix: "金句混剪" };

const state = {
  projectId: null, project: null,
  rows: [], silences: [], sourceDurationMs: 0,
  aiOverview: { modes: {} }, aiMode: "koubo_tighten", aiData: {},
  orderedGroups: null,
  pollTimer: null, aiPollTimers: {}, exportTimer: null,
};

/* 播放引擎：mode 是持久状态——成片=按 plan.ranges 顺序跳播（默认），原片=线性播放。
   audition 是叠加在成片模式上的一次性试听（已删除句 / 切点前后），播完自动暂停。 */
const pb = {
  mode: "edited",
  ranges: [],           // 最新 plan.ranges，播放顺序即数组顺序（支持混剪乱序）
  editedTotal: 0,
  prefix: [],           // ranges 时长前缀和，用于成片时间轴映射
  rangeIndex: 0,
  audition: null,       // {startMs, endMs}
  raf: null,
};

// ---------- 基础 ----------
async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try { payload = await response.json(); } catch { /* 非 JSON 响应 */ }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

function postJson(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

function fmtClock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

function setStatus(message, kind = "") {
  el.statusBox.textContent = message;
  el.statusBox.className = `status ${kind}`.trim();
}

// ---------- 项目列表与导入 ----------
async function refreshProjects() {
  try {
    const { projects } = await api("/api/projects");
    el.projectList.innerHTML = "";
    for (const project of projects) {
      const item = document.createElement("div");
      item.className = `project-item ${project.id === state.projectId ? "active" : ""}`;
      const meta = project.error
        ? `<div class="p-meta err">失败：${escapeHtml(project.error).slice(0, 60)}</div>`
        : `<div class="p-meta">${escapeHtml(project.stage_message || project.stage || "")}</div>`;
      item.innerHTML = `<div class="p-name">${escapeHtml(project.name || project.id)}</div>${meta}`;
      item.addEventListener("click", () => selectProject(project.id));
      el.projectList.appendChild(item);
    }
  } catch (error) {
    console.error(error);
  }
}

function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

function uploadFile(file) {
  el.uploadProgress.hidden = false;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/projects/upload?filename=${encodeURIComponent(file.name)}`);
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const pct = Math.round((event.loaded / event.total) * 100);
      el.uploadBar.style.width = `${pct}%`;
      el.uploadPct.textContent = `${pct}%`;
    }
  };
  xhr.onload = () => {
    el.uploadProgress.hidden = true;
    el.uploadBar.style.width = "0";
    try {
      const payload = JSON.parse(xhr.responseText);
      if (xhr.status >= 400 || payload.ok === false) throw new Error(payload.error || "上传失败");
      refreshProjects();
      selectProject(payload.id);
    } catch (error) {
      alert(error.message);
    }
  };
  xhr.onerror = () => { el.uploadProgress.hidden = true; alert("上传失败"); };
  xhr.send(file);
}

el.pickFileBtn.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", () => { if (el.fileInput.files[0]) uploadFile(el.fileInput.files[0]); el.fileInput.value = ""; });
el.importZone.addEventListener("dragover", (event) => { event.preventDefault(); el.importZone.classList.add("dragover"); });
el.importZone.addEventListener("dragleave", () => el.importZone.classList.remove("dragover"));
el.importZone.addEventListener("drop", (event) => {
  event.preventDefault();
  el.importZone.classList.remove("dragover");
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) uploadFile(file);
});
el.importPathBtn.addEventListener("click", async () => {
  const path = el.pathInput.value.trim();
  if (!path) return;
  try {
    const payload = await postJson("/api/projects/import-path", { path });
    el.pathInput.value = "";
    refreshProjects();
    selectProject(payload.id);
  } catch (error) {
    alert(error.message);
  }
});

// ---------- 项目选择与流水线 ----------
async function selectProject(projectId) {
  state.projectId = projectId;
  state.orderedGroups = null;
  resetPlayback();
  clearTimers();
  el.exportResult.hidden = true;
  await refreshProjects();
  await pollProjectOnce();
}

function clearTimers() {
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
  if (state.exportTimer) { clearTimeout(state.exportTimer); state.exportTimer = null; }
  for (const key of Object.keys(state.aiPollTimers)) { clearTimeout(state.aiPollTimers[key]); delete state.aiPollTimers[key]; }
}

async function pollProjectOnce() {
  if (!state.projectId) return;
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    state.project = project;
    if (project.stage === "ready" && project.transcript_ready) {
      showEditor();
      return;
    }
    showPipeline(project);
    if (project.stage !== "error") {
      state.pollTimer = setTimeout(pollProjectOnce, 2000);
    }
  } catch (error) {
    showPipeline({ stage: "error", error: error.message, name: state.projectId });
  }
}

function showView(name) {
  el.emptyView.hidden = name !== "empty";
  el.pipelineView.hidden = name !== "pipeline";
  el.editorView.hidden = name !== "editor";
}

function showPipeline(project) {
  showView("pipeline");
  el.pipelineTitle.textContent = `${project.name || ""} · ${project.stage_message || "处理中…"}`;
  el.stageList.innerHTML = "";
  const stageIndex = STAGE_LABELS.findIndex(([key]) => key === project.stage);
  STAGE_LABELS.forEach(([key, label], index) => {
    const li = document.createElement("li");
    li.textContent = label;
    if (project.stage === "error") {
      if (index < stageIndex) li.className = "done";
    } else if (index < stageIndex || project.stage === "ready") {
      li.className = "done";
      li.textContent = `✓ ${label}`;
    } else if (index === stageIndex) {
      li.className = "current";
    }
    el.stageList.appendChild(li);
  });
  const failed = project.stage === "error";
  el.pipelineError.hidden = !failed;
  if (failed) el.pipelineErrorText.textContent = project.error || "未知错误";
}

el.retryBtn.addEventListener("click", async () => {
  try {
    await postJson(`/api/projects/${state.projectId}/retry`);
    pollProjectOnce();
  } catch (error) {
    alert(error.message);
  }
});

// ---------- 编辑器 ----------
async function showEditor() {
  try {
    const payload = await api(`/api/projects/${state.projectId}/editor`);
    state.rows = payload.rows || [];
    state.silences = payload.silence_gaps || [];
    state.aiOverview = payload.ai || { modes: {} };
    state.sourceDurationMs = payload.duration_ms || payload.project.duration_ms || 0;
    showView("editor");
    const mediaUrl = `/media/${state.projectId}/source`;
    if (!el.video.src.endsWith(encodeURI(mediaUrl))) el.video.src = mediaUrl;
    renderRows();
    renderAiPanel();
    pb.mode = "edited";
    pb.audition = null;
    el.modeToggle.querySelectorAll(".mode-btn").forEach((button) => {
      button.classList.toggle("active", button.dataset.mode === "edited");
    });
    try {
      await syncPlan();
    } catch { /* 无选中句或字幕缺词级时间戳时，成片范围留空即可 */ }
    updateTransport();
    const warning = payload.project.ai_warning;
    setStatus(warning ? warning : `已加载 ${state.rows.length} 句字幕。空格播放成片，点击句子从该处继续。`, warning ? "warn" : "");
    resumeAiPolling();
  } catch (error) {
    setStatus(error.message, "error");
    showView("editor");
  }
}

function renderRows() {
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
  if (row.trim || row.nudge) badges.push('<span class="badge trimmed">✂ 已微调</span>');
  if (row.has_word_timestamps) badges.push('<button class="btn tiny trim-toggle" title="句内微调：修词边界 / 毫秒移切点">✂ 微调</button>');
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

function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

// ---------- 句内微调：词级修边 + 毫秒微移 ----------
const trimUi = { rowId: null, edge: "start", wave: null };

function trimBounds(row) {
  const count = (row.tokens || []).length;
  const trim = row.trim || {};
  const start = Math.min(Math.max(0, trim.start_token ?? 0), Math.max(0, count - 1));
  const end = Math.min(Math.max(start, trim.end_token ?? count - 1), Math.max(0, count - 1));
  return { start, end };
}

function joinTokens(tokens) {
  let out = "";
  for (const token of tokens) {
    if (out && /[A-Za-z0-9]$/.test(out) && /^[A-Za-z0-9]/.test(token.text)) out += " ";
    out += token.text;
  }
  return out;
}

function rowCutMs(row, edge) {
  const { start, end } = trimBounds(row);
  const nudge = row.nudge || {};
  if (edge === "start") return row.tokens[start].start_ms + (nudge.start_ms || 0);
  return row.tokens[end].end_ms + (nudge.end_ms || 0);
}

function setRowTrim(row, edge, index) {
  const count = row.tokens.length;
  const bounds = trimBounds(row);
  if (edge === "start") bounds.start = Math.min(index, bounds.end);
  else bounds.end = Math.max(index, bounds.start);
  if (bounds.start === 0 && bounds.end === count - 1) delete row.trim;
  else row.trim = { start_token: bounds.start, end_token: bounds.end };
  row.text = joinTokens(row.tokens.slice(bounds.start, bounds.end + 1));
  const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(row.id)}"]`);
  const textarea = node && node.querySelector(".row-text");
  if (textarea) { textarea.value = row.text; autoGrow(textarea); }
  scheduleAutosave();
}

function toggleTrimPanel(row, rowDiv) {
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

function renderTrimPanel(row) {
  const panel = trimPanelNode();
  if (!panel || trimUi.rowId !== row.id) return;
  const { start, end } = trimBounds(row);
  const chips = row.tokens.map((token, index) => {
    const cut = index < start || index > end;
    return `<span class="chip ${cut ? "cut" : ""}" data-i="${index}">${escapeHtml(token.text)}</span>`;
  }).join("");
  panel.innerHTML = `
    <div class="trim-chips">${chips}</div>
    <canvas class="trim-wave" height="44"></canvas>
    <div class="trim-controls">
      <span class="trim-hint">点词块＝把最近的边界移到那个词</span>
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
      <button class="btn small" data-act="listen">▶ 试听切点</button>
      <button class="btn small" data-act="reset">重置</button>
    </div>`;
  panel.querySelectorAll(".chip").forEach((chip) => {
    chip.addEventListener("click", (event) => {
      event.stopPropagation();
      const index = Number(chip.dataset.i);
      const bounds = trimBounds(row);
      const edge = Math.abs(index - bounds.start) <= Math.abs(index - bounds.end) ? "start" : "end";
      trimUi.edge = edge;
      setRowTrim(row, edge, index);
      renderTrimPanel(row);
      auditionCut(row, edge);
    });
  });
  panel.querySelectorAll("[data-edge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      trimUi.edge = button.dataset.edge;
      renderTrimPanel(row);
    });
  });
  panel.querySelectorAll("[data-nudge]").forEach((button) => {
    button.addEventListener("click", (event) => {
      event.stopPropagation();
      const delta = Number(button.dataset.nudge);
      const nudge = { start_ms: 0, end_ms: 0, ...(row.nudge || {}) };
      const key = trimUi.edge === "start" ? "start_ms" : "end_ms";
      nudge[key] = Math.max(-1000, Math.min(1000, (nudge[key] || 0) + delta));
      if (!nudge.start_ms && !nudge.end_ms) delete row.nudge;
      else row.nudge = nudge;
      scheduleAutosave();
      renderTrimPanel(row);
      auditionCut(row, trimUi.edge);
    });
  });
  panel.querySelector('[data-act="listen"]').addEventListener("click", (event) => {
    event.stopPropagation();
    auditionCut(row, trimUi.edge);
  });
  panel.querySelector('[data-act="reset"]').addEventListener("click", (event) => {
    event.stopPropagation();
    delete row.trim;
    delete row.nudge;
    row.text = row.original_text || row.text;
    const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(row.id)}"]`);
    const textarea = node && node.querySelector(".row-text");
    if (textarea) { textarea.value = row.text; autoGrow(textarea); }
    scheduleAutosave();
    renderTrimPanel(row);
  });
  drawWave(row);
}

function auditionCut(row, edge) {
  const cut = rowCutMs(row, edge);
  auditionRange(Math.max(0, cut - 800), cut + 800, `试听${edge === "start" ? "句首" : "句尾"}切点（前后 0.8s）`);
}

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

function drawWave(row) {
  const panel = trimPanelNode();
  const canvas = panel && panel.querySelector(".trim-wave");
  if (!canvas || trimUi.rowId !== row.id) return;
  const wave = trimUi.wave;
  const width = canvas.clientWidth || (canvas.parentElement && canvas.parentElement.clientWidth) || 600;
  canvas.width = width;
  canvas.height = 44;
  const ctx = canvas.getContext("2d");
  ctx.clearRect(0, 0, width, 44);
  if (!wave || !wave.values.length) return;
  const span = wave.end_ms - wave.start_ms || 1;
  const toX = (ms) => ((ms - wave.start_ms) / span) * width;
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
  for (const [cut, color] of [[startCut, "#5e81f4"], [endCut, "#e2c04e"]]) {
    ctx.strokeStyle = color;
    ctx.lineWidth = 2;
    ctx.beginPath();
    ctx.moveTo(toX(cut), 0);
    ctx.lineTo(toX(cut), 44);
    ctx.stroke();
  }
}

function refreshStats() {
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

function markActive(id, scroll = false) {
  const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(id)}"]`);
  if (!node || node.classList.contains("active")) return;
  el.rows.querySelectorAll(".subtitle-row.active").forEach((item) => item.classList.remove("active"));
  node.classList.add("active");
  if (scroll) node.scrollIntoView({ block: "center", behavior: "smooth" });
}

// ---------- 计划与自动保存 ----------
function planBody() {
  // 切点策略不再由前端指定，服务端使用工程默认 hybrid_valley。
  return {
    rows: state.rows.map((row) => {
      const item = { id: row.id, checked: row.checked, text: row.text };
      if (row.trim) item.trim = row.trim;
      if (row.nudge && (row.nudge.start_ms || row.nudge.end_ms)) item.nudge = row.nudge;
      return item;
    }),
    groups: state.orderedGroups || undefined,
  };
}

function hasSelection() {
  if (state.orderedGroups) return state.orderedGroups.some((group) => group.segment_ids.length);
  return state.rows.some((row) => row.checked);
}

/* 保存剪辑计划并刷新成片播放范围；无选中时清空。 */
async function syncPlan() {
  if (!state.projectId || !hasSelection()) { setRanges([]); return null; }
  const payload = await postJson(`/api/projects/${state.projectId}/plan`, planBody());
  setRanges(payload.plan.ranges || []);
  return payload;
}

let autosaveTimer = null;
function scheduleAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(async () => {
    autosaveTimer = null;
    if (!state.projectId) return;
    try {
      await syncPlan();
    } catch (error) {
      setStatus(`自动保存失败：${error.message}`, "warn");
    }
  }, 800);
}

// ---------- 播放引擎：成片/原片双模式 ----------
function resetPlayback() {
  pb.audition = null;
  pb.ranges = [];
  pb.prefix = [];
  pb.editedTotal = 0;
  pb.rangeIndex = 0;
  if (pb.raf) { cancelAnimationFrame(pb.raf); pb.raf = null; }
}

function setRanges(ranges) {
  pb.ranges = ranges || [];
  pb.prefix = [];
  let total = 0;
  for (const item of pb.ranges) {
    pb.prefix.push(total);
    total += Math.max(0, item.end_ms - item.start_ms);
  }
  pb.editedTotal = total;
  if (pb.rangeIndex >= pb.ranges.length) pb.rangeIndex = 0;
  updateTransport();
}

function rangeIndexForRaw(rawMs) {
  for (let i = 0; i < pb.ranges.length; i++) {
    if (rawMs >= pb.ranges[i].start_ms - 40 && rawMs <= pb.ranges[i].end_ms + 40) return i;
  }
  let best = -1;
  let bestGap = Infinity;
  for (let i = 0; i < pb.ranges.length; i++) {
    const gap = pb.ranges[i].start_ms - rawMs;
    if (gap >= 0 && gap < bestGap) { bestGap = gap; best = i; }
  }
  return best >= 0 ? best : 0;
}

function rangeIndexForRow(row) {
  return pb.ranges.findIndex((item) => (item.source_segment_ids || []).includes(row.id));
}

function editedElapsed() {
  if (!pb.ranges.length) return 0;
  const current = pb.ranges[pb.rangeIndex];
  const rawMs = el.video.currentTime * 1000;
  const inRange = Math.min(Math.max(rawMs - current.start_ms, 0), current.end_ms - current.start_ms);
  return pb.prefix[pb.rangeIndex] + inRange;
}

function seekEdited(editedMs) {
  if (!pb.ranges.length) return;
  editedMs = Math.min(Math.max(0, editedMs), Math.max(0, pb.editedTotal - 1));
  let index = pb.ranges.length - 1;
  for (let i = 0; i < pb.ranges.length; i++) {
    const length = pb.ranges[i].end_ms - pb.ranges[i].start_ms;
    if (editedMs < pb.prefix[i] + length) { index = i; break; }
  }
  pb.rangeIndex = index;
  el.video.currentTime = (pb.ranges[index].start_ms + (editedMs - pb.prefix[index])) / 1000;
}

function tick() {
  pb.raf = null;
  if (el.video.paused) { updateTransport(); return; }
  const nowMs = el.video.currentTime * 1000;
  if (pb.audition) {
    if (nowMs >= pb.audition.endMs - 30) {
      el.video.pause();
      pb.audition = null;
      setStatus("试听结束。按空格继续播放成片。");
      updateTransport();
      return;
    }
  } else if (pb.mode === "edited" && pb.ranges.length) {
    const current = pb.ranges[pb.rangeIndex];
    if (nowMs < current.start_ms - 250 || nowMs > current.end_ms + 250) {
      pb.rangeIndex = rangeIndexForRaw(nowMs);
      const target = pb.ranges[pb.rangeIndex];
      if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
        el.video.currentTime = target.start_ms / 1000;
      }
    } else if (nowMs >= current.end_ms - 45) {
      if (pb.rangeIndex + 1 >= pb.ranges.length) {
        el.video.pause();
        setStatus("成片播放完毕。");
        updateTransport();
        return;
      }
      pb.rangeIndex += 1;
      el.video.currentTime = pb.ranges[pb.rangeIndex].start_ms / 1000;
    }
  }
  syncActiveRow();
  updateTransport();
  pb.raf = requestAnimationFrame(tick);
}

function syncActiveRow() {
  const nowMs = el.video.currentTime * 1000;
  const row = state.rows.find((item) => nowMs >= item.start_ms && nowMs <= item.end_ms);
  if (row) markActive(row.id, !el.video.paused);
}

function updateTransport() {
  const playing = !el.video.paused && !el.video.ended;
  el.playBtn.textContent = playing ? "⏸" : "▶";
  let elapsed;
  let total;
  if (pb.mode === "edited" && !pb.audition) {
    elapsed = editedElapsed();
    total = pb.editedTotal;
  } else {
    elapsed = el.video.currentTime * 1000;
    total = state.sourceDurationMs || (el.video.duration || 0) * 1000;
  }
  el.timeLabel.textContent = `${fmtClock(elapsed)} / ${fmtClock(total)}`;
  el.progressFill.style.width = total ? `${Math.min(100, (elapsed / total) * 100)}%` : "0";
}

function togglePlay() {
  if (!el.video.paused) { el.video.pause(); return; }
  pb.audition = null;
  if (pb.mode === "edited") {
    if (!pb.ranges.length) { setStatus("没有保留片段可播放，请先勾选句子。", "warn"); return; }
    if (editedElapsed() >= pb.editedTotal - 60) {
      seekEdited(0);
    } else {
      const nowMs = el.video.currentTime * 1000;
      pb.rangeIndex = rangeIndexForRaw(nowMs);
      const target = pb.ranges[pb.rangeIndex];
      if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
        el.video.currentTime = target.start_ms / 1000;
      }
    }
  }
  el.video.play();
}

function setMode(mode) {
  if (pb.mode === mode) return;
  pb.mode = mode;
  pb.audition = null;
  el.modeToggle.querySelectorAll(".mode-btn").forEach((button) => {
    button.classList.toggle("active", button.dataset.mode === mode);
  });
  if (mode === "edited" && pb.ranges.length) {
    pb.rangeIndex = rangeIndexForRaw(el.video.currentTime * 1000);
    const target = pb.ranges[pb.rangeIndex];
    const nowMs = el.video.currentTime * 1000;
    if (nowMs < target.start_ms - 40 || nowMs > target.end_ms + 40) {
      el.video.currentTime = target.start_ms / 1000;
    }
  }
  updateTransport();
  setStatus(mode === "edited" ? "成片模式：只播放保留内容。" : "原片模式：完整播放原始素材。");
}

/* 一次性试听某个区间（已删除句/切点检查），播完自动暂停，不改变当前模式。 */
function auditionRange(startMs, endMs, message) {
  pb.audition = { startMs, endMs };
  el.video.currentTime = startMs / 1000;
  el.video.play();
  if (message) setStatus(message);
}

function progressSeek(event) {
  const rect = el.progressBar.getBoundingClientRect();
  const ratio = Math.min(1, Math.max(0, (event.clientX - rect.left) / rect.width));
  pb.audition = null;
  if (pb.mode === "edited") {
    seekEdited(ratio * pb.editedTotal);
  } else {
    el.video.currentTime = (ratio * (state.sourceDurationMs || 0)) / 1000;
  }
  updateTransport();
}

el.playBtn.addEventListener("click", togglePlay);
el.video.addEventListener("click", togglePlay);
el.video.addEventListener("play", () => { if (!pb.raf) pb.raf = requestAnimationFrame(tick); updateTransport(); });
el.video.addEventListener("pause", () => { if (pb.raf) { cancelAnimationFrame(pb.raf); pb.raf = null; } updateTransport(); });
el.video.addEventListener("timeupdate", () => { if (el.video.paused) { syncActiveRow(); updateTransport(); } });
el.progressBar.addEventListener("pointerdown", (event) => {
  progressSeek(event);
  const move = (e) => progressSeek(e);
  const up = () => { window.removeEventListener("pointermove", move); window.removeEventListener("pointerup", up); };
  window.addEventListener("pointermove", move);
  window.addEventListener("pointerup", up);
});
el.modeToggle.addEventListener("click", (event) => {
  const button = event.target.closest(".mode-btn");
  if (button) setMode(button.dataset.mode);
});
document.addEventListener("keydown", (event) => {
  if (el.editorView.hidden) return;
  const target = event.target;
  if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;
  if (event.code === "Space") { event.preventDefault(); togglePlay(); }
  else if (event.key === "m" || event.key === "M") { setMode(pb.mode === "edited" ? "source" : "edited"); }
});

// ---------- 导出 ----------

el.exportBtn.addEventListener("click", async () => {
  el.exportBtn.disabled = true;
  el.exportResult.hidden = true;
  try {
    await postJson(`/api/projects/${state.projectId}/export`, planBody());
    setStatus("导出已开始（后台运行）…");
    pollExport();
  } catch (error) {
    setStatus(error.message, "error");
    el.exportBtn.disabled = false;
  }
});

async function pollExport() {
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    const job = project.export || {};
    if (job.status === "running") {
      state.exportTimer = setTimeout(pollExport, 3000);
      return;
    }
    el.exportBtn.disabled = false;
    if (job.status === "done") {
      setStatus("导出完成。");
      el.exportResult.hidden = false;
      el.exportResult.innerHTML = `
        ✅ 成片时长 ${fmtClock(job.duration_ms || 0)} · ${job.range_count} 个片段<br>
        <a href="/media/${state.projectId}/exports/${encodeURIComponent(job.video_name)}">下载视频</a> ·
        <a href="/media/${state.projectId}/exports/${encodeURIComponent(job.srt_name)}">下载字幕 SRT</a><br>
        <span style="color:var(--muted)">${escapeHtml(job.video || "")}</span>`;
    } else if (job.status === "error") {
      setStatus(`导出失败：${job.error}`, "error");
    }
  } catch (error) {
    el.exportBtn.disabled = false;
    setStatus(error.message, "error");
  }
}

// ---------- 金句混剪模式 ----------
function enterOrderedMode(clips) {
  state.orderedGroups = clips.map((clip) => ({ purpose: clip.purpose, segment_ids: clip.segment_ids, note: clip.note || "" }));
  const union = new Set(clips.flatMap((clip) => clip.segment_ids));
  for (const row of state.rows) row.checked = union.has(row.id);
  el.orderedBanner.hidden = false;
  renderRows();
  scheduleAutosave();
  setStatus("已进入金句混剪模式：预览与导出将按 HOOK→BODY→ECHO 顺序拼接。");
}

el.exitOrderedBtn.addEventListener("click", () => {
  state.orderedGroups = null;
  el.orderedBanner.hidden = true;
  refreshStats();
  scheduleAutosave();
  setStatus("已退出混剪模式，恢复按原文顺序剪辑。");
});

// ---------- AI 面板 ----------
el.aiPanelBtn.addEventListener("click", () => { el.aiPanel.hidden = !el.aiPanel.hidden; });
el.aiCloseBtn.addEventListener("click", () => { el.aiPanel.hidden = true; });
document.querySelectorAll(".ai-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".ai-tab").forEach((node) => node.classList.remove("active"));
    tab.classList.add("active");
    state.aiMode = tab.dataset.mode;
    renderAiPanel();
  });
});

el.aiRunBtn.addEventListener("click", async () => {
  el.aiRunBtn.disabled = true;
  try {
    await postJson(`/api/projects/${state.projectId}/ai/suggest`, { mode: state.aiMode, brief: el.aiBrief.value });
    state.aiOverview.modes[state.aiMode] = { status: "running" };
    renderAiPanel();
    pollAi(state.aiMode);
  } catch (error) {
    alert(error.message);
  } finally {
    el.aiRunBtn.disabled = false;
  }
});

function resumeAiPolling() {
  for (const mode of Object.keys(state.aiOverview.modes || {})) {
    const entry = state.aiOverview.modes[mode];
    if (entry && entry.status === "running") pollAi(mode);
    if (entry && entry.status === "done") fetchAiData(mode);
  }
}

async function pollAi(mode) {
  try {
    const payload = await api(`/api/projects/${state.projectId}/ai/${mode}`);
    if (payload.status === "running") {
      state.aiPollTimers[mode] = setTimeout(() => pollAi(mode), 3000);
      state.aiOverview.modes[mode] = { status: "running" };
    } else {
      state.aiOverview.modes[mode] = { status: payload.status, error: payload.error };
      if (payload.status === "done") {
        state.aiData[mode] = payload;
        if (mode === "koubo_tighten") await showEditor();
      }
    }
    if (mode === state.aiMode) renderAiPanel();
  } catch (error) {
    console.error(error);
  }
}

async function fetchAiData(mode) {
  if (state.aiData[mode]) return;
  try {
    const payload = await api(`/api/projects/${state.projectId}/ai/${mode}`);
    if (payload.status === "done") { state.aiData[mode] = payload; if (mode === state.aiMode) renderAiPanel(); }
  } catch (error) {
    console.error(error);
  }
}

function renderAiPanel() {
  const mode = state.aiMode;
  const entry = (state.aiOverview.modes || {})[mode] || { status: "idle" };
  const data = state.aiData[mode];
  if (entry.status === "running") {
    el.aiBody.innerHTML = `<div class="ai-hint">🤖 ${MODE_NAMES[mode]}分析中，请稍候…</div>`;
    return;
  }
  if (entry.status === "error") {
    el.aiBody.innerHTML = `<div class="ai-warning">失败：${escapeHtml(entry.error || "未知错误")}</div>`;
    return;
  }
  if (!data) {
    fetchAiData(mode);
    el.aiBody.innerHTML = `<div class="ai-hint">还没有${MODE_NAMES[mode]}结果。点击上方"运行 AI 分析"。</div>`;
    return;
  }
  if (mode === "koubo_tighten") renderKoubo(data);
  else if (mode === "topic_slicing") renderTopics(data);
  else renderRemix(data);
}

function warningsHtml(data) {
  const warnings = data.warnings || [];
  return warnings.length ? `<div class="ai-warning">⚠ ${warnings.map(escapeHtml).join("；")}</div>` : "";
}

function renderKoubo(data) {
  const keeps = (data.decisions || []).filter((item) => item.keep).length;
  const drops = (data.decisions || []).length - keeps;
  el.aiBody.innerHTML = `
    ${warningsHtml(data)}
    <div class="ai-summary">${escapeHtml(data.summary || "")}</div>
    <div class="ai-card">
      <h4>建议：保留 ${keeps} 句 · 删除 ${drops} 句</h4>
      <div class="meta">预计成片 ${fmtClock(data.keep_duration_ms || 0)}</div>
      <button class="btn primary" id="applyKoubo">应用到勾选</button>
    </div>
    <div class="ai-hint">应用后每句仍可手动改；AI 判断理由显示在字幕行右侧。</div>`;
  $("applyKoubo").addEventListener("click", () => {
    const keepSet = new Set(data.keep_segment_ids || []);
    for (const row of state.rows) row.checked = keepSet.has(row.id);
    state.orderedGroups = null;
    el.orderedBanner.hidden = true;
    renderRows();
    scheduleAutosave();
    setStatus("已应用口播精剪建议，可继续手动调整。");
  });
}

function renderTopics(data) {
  const cards = (data.topics || []).map((topic, index) => `
    <div class="ai-card">
      <h4>${escapeHtml(topic.title)}</h4>
      <div class="meta">主题 ${fmtClock(topic.duration_ms || 0)} · 最佳切片 ${fmtClock(topic.best_clip.duration_ms || 0)}</div>
      <div>${escapeHtml(topic.summary || "")}</div>
      <div class="meta" style="margin-top:4px">${escapeHtml(topic.best_clip.reason || "")}</div>
      <button class="btn primary" data-topic="${index}" data-act="only">仅保留此切片</button>
      <button class="btn" data-topic="${index}" data-act="add">追加此切片</button>
    </div>`).join("");
  el.aiBody.innerHTML = `${warningsHtml(data)}<div class="ai-summary">${escapeHtml(data.overview || "")}</div>${cards || '<div class="ai-hint">没有识别出主题。</div>'}`;
  el.aiBody.querySelectorAll("button[data-topic]").forEach((button) => {
    button.addEventListener("click", () => {
      const topic = (data.topics || [])[Number(button.dataset.topic)];
      const clipSet = new Set(topic.best_clip.segment_ids);
      for (const row of state.rows) {
        if (button.dataset.act === "only") row.checked = clipSet.has(row.id);
        else if (clipSet.has(row.id)) row.checked = true;
      }
      state.orderedGroups = null;
      el.orderedBanner.hidden = true;
      renderRows();
      scheduleAutosave();
      setStatus(`已${button.dataset.act === "only" ? "仅保留" : "追加"}主题切片「${topic.title}」。`);
    });
  });
}

function renderRemix(data) {
  const quotes = (data.golden_quotes || []).map((quote) => `
    <div class="ai-card">
      <div class="quote-strength">${"★".repeat(quote.strength || 3)}</div>
      <div>「${escapeHtml(quote.quote || "")}」</div>
      <div class="meta">${escapeHtml(quote.reason || "")}</div>
    </div>`).join("");
  const clips = (data.clips || []).map((clip) => `
    <div class="ai-card">
      <span class="clip-tag ${clip.purpose}">${clip.purpose.toUpperCase()}</span>
      ${fmtClock(clip.duration_ms || 0)} · ${clip.segment_ids.length} 句
      <div class="meta">${escapeHtml(clip.note || "")}</div>
    </div>`).join("");
  const titles = (data.title_suggestions || []).map((title) => `<li>${escapeHtml(title)}</li>`).join("");
  el.aiBody.innerHTML = `
    ${warningsHtml(data)}
    <div class="ai-card">
      <h4>混剪方案 · 预计 ${fmtClock(data.clips_duration_ms || 0)}</h4>
      <button class="btn primary" id="applyRemix">应用金句混剪（乱序成片）</button>
    </div>
    ${clips}
    <h4 style="margin:12px 0 6px">金句</h4>${quotes || '<div class="ai-hint">未识别出金句。</div>'}
    ${titles ? `<h4 style="margin:12px 0 6px">标题建议</h4><ul>${titles}</ul>` : ""}`;
  const applyBtn = $("applyRemix");
  if (applyBtn) applyBtn.addEventListener("click", () => enterOrderedMode(data.clips || []));
}

// ---------- 启动 ----------
refreshProjects();
setInterval(refreshProjects, 8000);
showView("empty");
