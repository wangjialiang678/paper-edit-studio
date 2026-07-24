/* AI 出剪辑方案面板：一条管线（分主题 → 挑金句 → 筛句子）替代旧三模式并列。
   设计依据 docs/specs/unified-pipeline-design.md（2026-07-24 拍板）：
   - 意图 = 选择题预设（多选）+ 可选自由补充；
   - 目标时长 = 区间（默认 3–5 分钟），记住上次填写（prefs）；
   - 长视频自动勾「分主题」；跑批期间显示阶段进度；
   - 外部 AI 稿导入与内置 AI 平级露出（复用「套用已有剪辑稿」对话框）；
   - 每次出方案 = 新建剪辑方案（Cut），绝不覆盖手工成果。
   转写完成后的自动初剪（koubo_tighten）仍在后台运行，结果以行内徽标/理由呈现。 */
import { $, el, state, pb, api, postJson, putJson, setStatus, escapeHtml, prefs } from "./shared.js";
import { renderRows, refreshStats } from "./rows.js";
import { scheduleAutosave } from "./plan.js";
import { showEditor } from "./editor.js";
import { loadCuts, openScriptDialog } from "./cuts.js";

const INTENTS = [
  { key: "cut_fillers", label: "删口癖 / 废话 / 重复", def: true },
  { key: "hook_first", label: "开头放钩子金句", def: true },
  { key: "keep_insights", label: "保留干货观点", def: false },
  { key: "keep_stories", label: "保留案例 / 故事", def: false },
  { key: "cut_smalltalk", label: "删寒暄 / 闲聊", def: false },
  { key: "keep_data", label: "保留数据 / 结论", def: false },
];

const PROMPT_STAGES = [
  ["koubo_tighten", "筛句子（口播精简）"],
  ["content_map", "分主题（看点梳理）"],
  ["quote_candidates", "挑金句"],
];

let planPollTimer = null;

// ---------- 顺序横幅 ----------
el.exitOrderedBtn.addEventListener("click", () => {
  state.order = [];
  state.viewOriginal = false;
  renderRows();
  refreshStats();
  scheduleAutosave();
  setStatus("已恢复按原文顺序成片。");
});

// ---------- 面板开关 ----------
el.aiPanelBtn.addEventListener("click", () => {
  el.aiPanel.hidden = !el.aiPanel.hidden;
  if (!el.aiPanel.hidden) renderAiPanel();
});
el.aiCloseBtn.addEventListener("click", () => { el.aiPanel.hidden = true; });

// ---------- 主渲染 ----------
export function renderAiPanel() {
  if (promptEdit.open) { renderPromptEditor(); return; }
  const planAi = state.project?.plan_ai || {};
  if (state.planAiRunning || planAi.status === "running") { renderProgress(planAi); return; }
  renderForm(planAi);
}

function savedIntents() {
  try { return JSON.parse(localStorage.getItem("pes.planIntents") || "null"); } catch { return null; }
}

function isLongVideo() {
  const durationMs = state.sourceDurationMs || state.project?.duration_ms || 0;
  return durationMs >= 10 * 60 * 1000 || state.rows.length >= 140;
}

function renderForm(planAi) {
  const picked = savedIntents() || INTENTS.filter((i) => i.def).map((i) => i.key);
  const chips = INTENTS.map((intent) => `
    <label class="intent-chip"><input type="checkbox" value="${intent.key}" ${picked.includes(intent.key) ? "checked" : ""}>${escapeHtml(intent.label)}</label>`).join("");
  const long = isLongVideo();
  const lastDone = planAi.status === "done" && (planAi.cuts || []).length
    ? `<div class="ai-card"><h4>上次出的方案</h4><div class="meta">${(planAi.cuts || []).map(escapeHtml).join(" · ")}（在上方方案条切换查看）</div></div>` : "";
  const lastError = planAi.status === "error"
    ? `<div class="ai-warning">上次运行失败：${escapeHtml(planAi.error || "未知错误")}</div>` : "";
  el.aiBody.innerHTML = `
    <div class="ai-hint" style="margin-bottom:8px">AI 通读字幕，一次完成：${long ? "分大主题 → " : ""}挑金句（复制到开头）→ 按目标时长筛句子，产出可切换的剪辑方案。</div>
    <div class="ai-card">
      <h4>想怎么剪？（多选）</h4>
      <div class="intent-chips">${chips}</div>
      <textarea class="intent-extra" id="planIntentExtra" placeholder="补充要求（可选）：如「只保留讲 AI 教育的部分」"></textarea>
    </div>
    <div class="ai-card">
      <h4>每条成片目标时长</h4>
      <div class="budget-form">
        <input type="number" id="planDurMin" class="settings-input" style="width:58px" min="0.5" step="0.5" value="${prefs.planDurMin}"> –
        <input type="number" id="planDurMax" class="settings-input" style="width:58px" min="0.5" step="0.5" value="${prefs.planDurMax}"> 分钟
      </div>
      <label class="trim-auto" style="margin-top:8px"><input type="checkbox" id="planSplit" ${long ? "checked" : ""}>视频较长时先分大主题，每个主题各出一条${long ? `（本片 ${state.rows.length} 句，已自动勾选）` : ""}</label>
    </div>
    ${lastError}${lastDone}
    <button class="btn primary" id="planRunBtn" style="width:100%">▶ 开始出方案${long ? "（长视频约 2–8 分钟）" : "（约 1–2 分钟）"}</button>
    <div class="settings-note" id="planHint"></div>
    <div class="alt-divider">或者</div>
    <div class="alt-path">
      <div class="alt-path-text">已经用自己的 AI（Codex / GPT…）挑好排好了？</div>
      <button class="btn" id="planImportBtn" style="width:100%">📄 粘贴你的剪辑稿，直接对回视频</button>
    </div>`;
  $("planRunBtn").addEventListener("click", runPlan);
  $("planImportBtn").addEventListener("click", () => {
    el.aiPanel.hidden = true;
    openScriptDialog();
  });
}

async function runPlan() {
  const intent = [...el.aiBody.querySelectorAll(".intent-chip input:checked")].map((node) => node.value);
  const intentExtra = $("planIntentExtra").value.trim();
  const durMin = Number($("planDurMin").value) || 3;
  const durMax = Math.max(Number($("planDurMax").value) || 5, durMin);
  const split = $("planSplit").checked;
  localStorage.setItem("pes.planIntents", JSON.stringify(intent));
  prefs.planDurMin = durMin;
  prefs.planDurMax = durMax;
  const hint = $("planHint");
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/plans/generate`, {
      intent, intent_extra: intentExtra || undefined,
      duration_min_s: Math.round(durMin * 60), duration_max_s: Math.round(durMax * 60),
      split_topics: split,
    });
    state.planAiRunning = true;
    state.planAiStartMs = Date.now();
    renderProgress({ status: "running", detail: "正在启动…" });
    pollPlan();
  } catch (error) {
    if (hint) hint.textContent = /not found|404/i.test(error.message)
      ? "❌ 出方案后端还没就绪（正在升级中），稍后再试。"
      : `❌ ${error.message}`;
  }
}

function renderProgress(planAi) {
  const elapsed = state.planAiStartMs ? Math.round((Date.now() - state.planAiStartMs) / 1000) : 0;
  const stageLabel = { topics: "① 分大主题", quotes: "② 挑金句", select: "③ 筛句子" }[planAi.stage] || "准备中";
  const topicsNote = planAi.topics_total ? ` · 主题 ${planAi.topics_done ?? 0}/${planAi.topics_total}` : "";
  el.aiBody.innerHTML = `
    <div class="ai-card">
      <h4>🤖 出方案中 · 已用 ${elapsed}s</h4>
      <div class="meta">${escapeHtml(stageLabel)}${topicsNote}</div>
      <div class="meta">${escapeHtml(planAi.detail || "AI 通读字幕中…")}</div>
    </div>
    <div class="ai-hint">后台运行，可以先关掉面板做别的；完成后方案条会出现新方案并自动试听。</div>`;
}

function pollPlan() {
  if (planPollTimer) clearTimeout(planPollTimer);
  planPollTimer = setTimeout(async () => {
    try {
      const project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
      state.project = project;
      const planAi = project.plan_ai || {};
      if (planAi.status === "running") {
        if (!el.aiPanel.hidden && !promptEdit.open) renderProgress(planAi);
        pollPlan();
        return;
      }
      state.planAiRunning = false;
      if (planAi.status === "done") {
        await finishPlan(planAi);
      } else if (planAi.status === "error") {
        setStatus(`AI 出方案失败：${planAi.error || "未知错误"}`, "error");
        if (!el.aiPanel.hidden) renderAiPanel();
      }
    } catch {
      pollPlan();
    }
  }, 3000);
}

/* 完成：切到第一个新方案，自动从头试听。 */
async function finishPlan(planAi) {
  const cuts = planAi.cuts || [];
  await loadCuts();
  if (cuts.length) {
    state.cutName = cuts[0];
    state.order = [];
    state.viewOriginal = false;
    await showEditor();
    if (pb.ranges.length) {
      pb.audition = null;
      pb.rangeIndex = 0;
      el.video.currentTime = pb.ranges[0].start_ms / 1000;
      el.video.play();
    }
    setStatus(cuts.length > 1
      ? `AI 出了 ${cuts.length} 个剪辑方案（方案条切换查看），已切到「${cuts[0]}」并从头试听。`
      : `剪辑方案「${cuts[0]}」已生成，正在从头试听；不满意可在表里逐句调整。`);
  } else {
    setStatus("AI 出方案完成，但没有生成新方案（详见面板）。", "warn");
  }
  if (!el.aiPanel.hidden) renderAiPanel();
}

/* 编辑器载入时恢复：管线运行中则续接轮询；自动初剪 running 时轮询让徽标就位。 */
export function resumeAiPolling() {
  const planAi = state.project?.plan_ai;
  if (planAi?.status === "running") {
    state.planAiRunning = true;
    if (!state.planAiStartMs) state.planAiStartMs = Date.now();
    pollPlan();
  }
  const koubo = (state.aiOverview.modes || {}).koubo_tighten;
  if (koubo && koubo.status === "running") pollKoubo();
}

function pollKoubo() {
  setTimeout(async () => {
    try {
      const payload = await api(`/api/projects/${state.projectId}/ai/koubo_tighten`);
      if (payload.status === "running") { pollKoubo(); return; }
      state.aiOverview.modes.koubo_tighten = { status: payload.status, error: payload.error };
      if (payload.status === "done") await showEditor(); // 自动初剪结果落行内徽标/勾选
    } catch { /* 静默，编辑器重载会再取 */ }
  }, 3000);
}

// ---------- 提示词查看与编辑（按管线阶段选择） ----------
const promptEdit = { open: false, mode: "koubo_tighten", data: null, saving: false };

el.aiPromptBtn.addEventListener("click", () => {
  if (el.aiPanel.hidden) el.aiPanel.hidden = false;
  if (promptEdit.open) { promptEdit.open = false; renderAiPanel(); return; }
  openPromptEditor();
});

async function openPromptEditor() {
  promptEdit.open = true;
  el.aiBody.innerHTML = '<div class="ai-hint">加载提示词…</div>';
  try {
    promptEdit.data = await api(`/api/prompts/${promptEdit.mode}`);
    renderPromptEditor();
  } catch (error) {
    el.aiBody.innerHTML = `<div class="ai-warning">加载提示词失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderPromptEditor() {
  const data = promptEdit.data;
  if (!data || data.mode !== promptEdit.mode) { openPromptEditor(); return; }
  const sourceBadge = data.source === "override"
    ? '<span class="badge trimmed">已自定义</span>'
    : '<span class="badge">出厂默认</span>';
  const warnings = (data.warnings || []).length
    ? `<div class="ai-warning">⚠ ${data.warnings.map(escapeHtml).join("；")}</div>` : "";
  const stageOptions = PROMPT_STAGES.map(([key, label]) =>
    `<option value="${key}" ${key === promptEdit.mode ? "selected" : ""}>${label}</option>`).join("");
  el.aiBody.innerHTML = `
    <div class="ai-card">
      <h4>剪辑理念 <select id="promptStageSel" class="settings-input" style="width:auto">${stageOptions}</select> ${sourceBadge}</h4>
      <div class="meta">用自然语言描述这个阶段的判断标准和取舍偏好即可，怎么改都不会弄坏功能。<br>输出格式等技术协议由系统自动附加（见下方高级选项）。保存即生效，重启后保留。</div>
    </div>
    ${warnings}
    <textarea class="prompt-editor" id="promptEditorText" spellcheck="false"></textarea>
    <div class="prompt-actions">
      <button class="btn primary" id="promptSaveBtn">保存</button>
      ${data.source === "override" ? '<button class="btn" id="promptResetBtn">恢复默认</button>' : ""}
      <button class="btn" id="promptCloseBtn">返回</button>
    </div>
    <details class="prompt-extra">
      <summary>高级选项：查看发送给 AI 的完整提示词（协议与硬约束由系统维护，只读）</summary>
      <pre>${escapeHtml((data.assembled_template || data.content || "") + "\n" + (data.hard_constraints || ""))}</pre>
    </details>`;
  $("promptStageSel").addEventListener("change", (event) => {
    promptEdit.mode = event.target.value;
    openPromptEditor();
  });
  const textarea = $("promptEditorText");
  textarea.value = data.content || "";
  $("promptSaveBtn").addEventListener("click", async () => {
    if (promptEdit.saving) return;
    promptEdit.saving = true;
    try {
      const result = await putJson(`/api/prompts/${promptEdit.mode}`, { content: textarea.value });
      promptEdit.data = await api(`/api/prompts/${promptEdit.mode}`);
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
      await api(`/api/prompts/${promptEdit.mode}`, { method: "DELETE" });
      promptEdit.data = await api(`/api/prompts/${promptEdit.mode}`);
      setStatus("已恢复默认提示词。");
      renderPromptEditor();
    } catch (error) {
      setStatus(`恢复默认失败：${error.message}`, "error");
    }
  });
  $("promptCloseBtn").addEventListener("click", () => { promptEdit.open = false; renderAiPanel(); });
}
