/* 共享基础：DOM 注册表、全局状态、HTTP 封装、通用工具。
   所有模块从这里取 el/state/pb，避免循环依赖扩散。 */
"use strict";

export const $ = (id) => document.getElementById(id);

export const el = {
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
  orderedBanner: $("orderedBanner"), exitOrderedBtn: $("exitOrderedBtn"), replaceBar: $("replaceBar"),
  aiPanelBtn: $("aiPanelBtn"), exportBtn: $("exportBtn"),
  aiPanel: $("aiPanel"), aiCloseBtn: $("aiCloseBtn"), aiBrief: $("aiBrief"), aiRunBtn: $("aiRunBtn"),
  aiBody: $("aiBody"), aiPromptBtn: $("aiPromptBtn"), cutFillersBtn: $("cutFillersBtn"),
  undoCutFillersBtn: $("undoCutFillersBtn"),
  settingsBtn: $("settingsBtn"), settingsPanel: $("settingsPanel"), settingsCloseBtn: $("settingsCloseBtn"),
  settingsBody: $("settingsBody"),
  qualityBtn: $("qualityBtn"), qualityPanel: $("qualityPanel"), qualityCloseBtn: $("qualityCloseBtn"),
  qualityBody: $("qualityBody"),
};

export const STAGE_LABELS = [
  ["imported", "已导入"],
  ["probing", "读取媒体信息"],
  ["extracting_audio", "提取音频"],
  ["transcribing", "语音识别（生成字幕）"],
  ["ai_suggesting", "AI 保留建议"],
  ["ready", "就绪"],
];
export const MODE_NAMES = { koubo_tighten: "口播精剪", topic_slicing: "主题切片", highlight_remix: "金句混剪" };

export const state = {
  projectId: null, project: null,
  rows: [], silences: [], sourceDurationMs: 0,
  aiOverview: { modes: {} }, aiMode: "koubo_tighten", aiData: {},
  orderedGroups: null,
  pollTimer: null, aiPollTimers: {}, exportTimer: null,
  quality: { report: null, aiRunning: false },
};

/* 播放引擎：mode 是持久状态——成片=按 plan.ranges 顺序跳播（默认），原片=线性播放。
   audition 是叠加在成片模式上的一次性试听（接缝/已删除句），播完自动暂停。
   audition.parts 支持多段顺序播放（接缝试听 = 前段末尾 + 跳切 + 后段开头）。 */
export const pb = {
  mode: "edited",
  ranges: [],           // 最新 plan.ranges，播放顺序即数组顺序（支持混剪乱序）
  editedTotal: 0,
  prefix: [],           // ranges 时长前缀和，用于成片时间轴映射
  rangeIndex: 0,
  audition: null,       // {parts: [{startMs, endMs}], index}
  raf: null,
};

export async function api(path, options = {}) {
  const response = await fetch(path, options);
  let payload = {};
  try { payload = await response.json(); } catch { /* 非 JSON 响应 */ }
  if (!response.ok || payload.ok === false) {
    throw new Error(payload.error || `请求失败（${response.status}）`);
  }
  return payload;
}

export function postJson(path, body) {
  return api(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

export function putJson(path, body) {
  return api(path, { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body || {}) });
}

export function fmtClock(ms) {
  const total = Math.max(0, Math.round(ms / 1000));
  return `${String(Math.floor(total / 60)).padStart(2, "0")}:${String(total % 60).padStart(2, "0")}`;
}

export function setStatus(message, kind = "") {
  el.statusBox.textContent = message;
  el.statusBox.className = `status ${kind}`.trim();
}

export function escapeHtml(text) {
  const div = document.createElement("div");
  div.textContent = String(text ?? "");
  return div.innerHTML;
}

export function autoGrow(textarea) {
  textarea.style.height = "auto";
  textarea.style.height = `${textarea.scrollHeight}px`;
}

export function markActive(id, scroll = false) {
  const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(id)}"]`);
  if (!node || node.classList.contains("active")) return;
  el.rows.querySelectorAll(".subtitle-row.active").forEach((item) => item.classList.remove("active"));
  node.classList.add("active");
  if (scroll) node.scrollIntoView({ block: "center", behavior: "smooth" });
}

/* 本地偏好（自动试听开关等），localStorage 持久。 */
export const prefs = {
  get autoAudition() { return localStorage.getItem("pes.autoAudition") !== "off"; },
  set autoAudition(value) { localStorage.setItem("pes.autoAudition", value ? "on" : "off"); },
};
