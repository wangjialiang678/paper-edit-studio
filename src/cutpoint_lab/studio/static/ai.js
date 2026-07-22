/* AI 选段面板：三模式运行/轮询/结果渲染、金句混剪模式、提示词查看与编辑。 */
import { $, el, state, api, postJson, putJson, setStatus, escapeHtml, fmtClock, MODE_NAMES } from "./shared.js";
import { renderRows, refreshStats } from "./rows.js";
import { scheduleAutosave } from "./plan.js";
import { showEditor } from "./editor.js";

// ---------- 金句混剪模式 ----------
export function enterOrderedMode(clips) {
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

// ---------- 面板开关与 tab ----------
el.aiPanelBtn.addEventListener("click", () => { el.aiPanel.hidden = !el.aiPanel.hidden; });
el.aiCloseBtn.addEventListener("click", () => { el.aiPanel.hidden = true; });
document.querySelectorAll(".ai-tab").forEach((tab) => {
  tab.addEventListener("click", () => {
    document.querySelectorAll(".ai-tab").forEach((node) => node.classList.remove("active"));
    tab.classList.add("active");
    state.aiMode = tab.dataset.mode;
    if (promptEdit.open) openPromptEditor();
    else renderAiPanel();
  });
});

el.aiRunBtn.addEventListener("click", async () => {
  el.aiRunBtn.disabled = true;
  try {
    await postJson(`/api/projects/${state.projectId}/ai/suggest`, { mode: state.aiMode, brief: el.aiBrief.value });
    state.aiOverview.modes[state.aiMode] = { status: "running" };
    promptEdit.open = false;
    renderAiPanel();
    pollAi(state.aiMode);
  } catch (error) {
    alert(error.message);
  } finally {
    el.aiRunBtn.disabled = false;
  }
});

// ---------- 运行状态轮询 ----------
export function resumeAiPolling() {
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

// ---------- 结果渲染 ----------
export function renderAiPanel() {
  if (promptEdit.open) { renderPromptEditor(); return; }
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

// ---------- 提示词查看与编辑 ----------
const promptEdit = { open: false, data: null, saving: false };

el.aiPromptBtn.addEventListener("click", () => {
  if (promptEdit.open) { promptEdit.open = false; renderAiPanel(); return; }
  openPromptEditor();
});

async function openPromptEditor() {
  promptEdit.open = true;
  el.aiBody.innerHTML = '<div class="ai-hint">加载提示词…</div>';
  try {
    promptEdit.data = await api(`/api/prompts/${state.aiMode}`);
    renderPromptEditor();
  } catch (error) {
    el.aiBody.innerHTML = `<div class="ai-warning">加载提示词失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderPromptEditor() {
  const data = promptEdit.data;
  if (!data || data.mode !== state.aiMode) { openPromptEditor(); return; }
  const sourceBadge = data.source === "override"
    ? '<span class="badge trimmed">已自定义</span>'
    : '<span class="badge">出厂默认</span>';
  const warnings = (data.warnings || []).length
    ? `<div class="ai-warning">⚠ ${data.warnings.map(escapeHtml).join("；")}</div>` : "";
  el.aiBody.innerHTML = `
    <div class="ai-card">
      <h4>${MODE_NAMES[state.aiMode]} · 提示词 ${sourceBadge}</h4>
      <div class="meta">修改后保存即生效（下次运行 AI 时使用），重启后保留。<br>请保留出厂模板中已有的 {{…}} 占位符（删掉会导致对应功能失效，保存时会提醒）。</div>
    </div>
    ${warnings}
    <textarea class="prompt-editor" id="promptEditorText" spellcheck="false"></textarea>
    <div class="prompt-actions">
      <button class="btn primary" id="promptSaveBtn">保存</button>
      ${data.source === "override" ? '<button class="btn" id="promptResetBtn">恢复默认</button>' : ""}
      <button class="btn" id="promptCloseBtn">返回结果</button>
    </div>
    <details class="prompt-extra">
      <summary>系统级硬约束（随提示词自动追加，不可编辑）</summary>
      <pre>${escapeHtml(data.hard_constraints || "")}</pre>
    </details>`;
  const textarea = $("promptEditorText");
  textarea.value = data.content || "";
  $("promptSaveBtn").addEventListener("click", async () => {
    if (promptEdit.saving) return;
    promptEdit.saving = true;
    try {
      const result = await putJson(`/api/prompts/${state.aiMode}`, { content: textarea.value });
      promptEdit.data = await api(`/api/prompts/${state.aiMode}`);
      const extra = (result.warnings || []).length ? `（提醒：${result.warnings.join("；")}）` : "";
      setStatus(`提示词已保存，立即生效${extra}`);
      renderPromptEditor();
    } catch (error) {
      setStatus(`提示词保存失败：${error.message}`, "error");
    } finally {
      promptEdit.saving = false;
    }
  });
  const resetBtn = $("promptResetBtn");
  if (resetBtn) resetBtn.addEventListener("click", async () => {
    if (!confirm("恢复出厂默认提示词？你的自定义内容将被删除。")) return;
    try {
      await api(`/api/prompts/${state.aiMode}`, { method: "DELETE" });
      promptEdit.data = await api(`/api/prompts/${state.aiMode}`);
      setStatus("已恢复默认提示词。");
      renderPromptEditor();
    } catch (error) {
      setStatus(`恢复默认失败：${error.message}`, "error");
    }
  });
  $("promptCloseBtn").addEventListener("click", () => { promptEdit.open = false; renderAiPanel(); });
}
