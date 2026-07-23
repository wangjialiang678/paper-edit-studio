/* 质检面板：质检报告展示、AI 复核触发、参考字幕导入、问题采纳/忽略。

   数据契约（与 quality/report.py 一致）：
   report = {generated_at, issues: [{id, segment_id, kind, span{text,...}, suggestion?,
             confidence, reason, source, status}], stats, meta}
   行内高亮由 rows.js 依据 state.quality.report 渲染（open 状态的 token 级 issue）。 */
import { $, el, state, api, postJson, escapeHtml, setStatus, markActive } from "./shared.js";
import { scheduleAutosave } from "./plan.js";
import { showEditor } from "./editor.js";

const KIND_LABELS = {
  low_confidence: "低置信片段",
  ai_suspect: "AI 存疑",
  term_candidate: "疑似专名",
  ref_mismatch: "与参考字幕不一致",
};

let aiPollTimer = null;

el.qualityBtn.addEventListener("click", () => {
  const opening = el.qualityPanel.hidden;
  el.qualityPanel.hidden = !opening;
  if (opening) {
    el.aiPanel.hidden = true;
    el.settingsPanel.hidden = true;
    renderQualityPanel();
  }
});
el.qualityCloseBtn.addEventListener("click", () => { el.qualityPanel.hidden = true; });

/* 项目载入时调用：拉取报告供面板与行内高亮使用；失败静默（旧后端兼容）。 */
export async function loadQualityReport() {
  if (!state.projectId) return;
  try {
    state.quality.report = await api(`/api/projects/${encodeURIComponent(state.projectId)}/quality/report`);
  } catch {
    state.quality.report = null;
  }
  if (!el.qualityPanel.hidden) renderQualityPanel();
}

export function openIssuesBySegment() {
  const map = {};
  for (const issue of state.quality.report?.issues || []) {
    if (issue.status !== "open") continue;
    (map[issue.segment_id] = map[issue.segment_id] || []).push(issue);
  }
  return map;
}

async function setIssueStatus(issue, status) {
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quality/issues/${encodeURIComponent(issue.id)}`, { status });
    issue.status = status;
  } catch (error) {
    setStatus(`更新质检项失败：${error.message}`, "warn");
  }
}

/* 采纳建议：改行文字（span 精确替换一次）→ 自动保存 → 标记 resolved。 */
export async function acceptIssue(issue) {
  const row = state.rows.find((item) => item.id === issue.segment_id);
  if (!row || !issue.suggestion) return;
  if (!row.text.includes(issue.span.text)) {
    setStatus(`该句文字已改动，找不到「${issue.span.text}」，请手工处理。`, "warn");
    await setIssueStatus(issue, "ignored");
    renderQualityPanel();
    return;
  }
  row.text = row.text.replace(issue.span.text, issue.suggestion);
  scheduleAutosave();
  document.dispatchEvent(new CustomEvent("row-struck-changed", { detail: { id: row.id } }));
  await setIssueStatus(issue, "resolved");
  setStatus(`已采纳：「${issue.span.text}」→「${issue.suggestion}」`);
  renderQualityPanel();
}

export async function ignoreIssue(issue) {
  await setIssueStatus(issue, "ignored");
  renderQualityPanel();
  document.dispatchEvent(new CustomEvent("row-struck-changed", { detail: { id: issue.segment_id } }));
}

function jumpToIssue(issue) {
  const row = state.rows.find((item) => item.id === issue.segment_id);
  if (!row) return;
  markActive(row.id, true);
  el.video.currentTime = row.start_ms / 1000;
}

async function runAnalyze(withAi) {
  const box = $("qualityResult");
  if (box) box.textContent = withAi ? "AI 复核已启动（后台运行，成本按 token 计）…" : "分析中…";
  try {
    state.quality.report = await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quality/analyze`, { ai: withAi });
    if (withAi) { state.quality.aiRunning = true; pollAiReview(); }
    renderQualityPanel();
    document.dispatchEvent(new CustomEvent("quality-report-updated"));
  } catch (error) {
    if (box) box.textContent = `❌ ${error.message}`;
  }
}

function pollAiReview() {
  if (aiPollTimer) clearTimeout(aiPollTimer);
  aiPollTimer = setTimeout(async () => {
    try {
      const project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
      const status = (project.quality_ai || {}).status;
      if (status === "running") { pollAiReview(); return; }
      state.quality.aiRunning = false;
      if (status === "done") {
        // AI 自动纠正可能已改写 selection，整体重载编辑器 + 报告。
        await showEditor();
        await loadQualityReport();
        renderQualityPanel();
        setStatus("AI 复核完成：自动纠正已应用（可在质检面板撤销），存疑项待人工裁决。");
      } else if (status === "error") {
        renderQualityPanel();
        setStatus(`AI 复核失败：${(project.quality_ai || {}).error || "未知错误"}`, "error");
      }
    } catch {
      pollAiReview();
    }
  }, 3000);
}

async function uploadReference(file) {
  const box = $("qualityResult");
  box.textContent = "上传参考字幕…";
  try {
    const response = await fetch(
      `/api/projects/${encodeURIComponent(state.projectId)}/reference?filename=${encodeURIComponent(file.name)}`,
      { method: "POST", body: file }
    );
    const payload = await response.json();
    if (!response.ok || payload.ok === false) throw new Error(payload.error || "上传失败");
    box.textContent = "✅ 参考字幕已登记，点「重新分析」生成对照。";
  } catch (error) {
    box.textContent = `❌ ${error.message}`;
  }
}

export function renderQualityPanel() {
  if (!state.projectId) {
    el.qualityBody.innerHTML = '<div class="ai-hint">打开项目后可查看质检报告。</div>';
    return;
  }
  const report = state.quality.report;
  const issues = (report?.issues || []).filter((issue) => issue.status === "open");
  const groups = {};
  for (const issue of issues) (groups[issue.kind] = groups[issue.kind] || []).push(issue);
  const meta = report?.meta || {};
  const undoAi = meta.ai_changeset_id
    ? `<button class="btn small" id="qualityUndoAiBtn">撤销 AI 自动纠正</button>` : "";
  const groupHtml = Object.entries(groups).map(([kind, list]) => `
    <div class="ai-card">
      <h4>${escapeHtml(KIND_LABELS[kind] || kind)} · ${list.length}</h4>
      ${list.map((issue) => `
        <div class="q-item" data-issue="${escapeHtml(issue.id)}">
          <a class="q-jump" data-act="jump">#${escapeHtml(String(issue.segment_id).replace("sentence_", ""))}</a>
          <span class="q-text">「${escapeHtml(issue.span?.text || "")}」${issue.suggestion ? ` → 「${escapeHtml(issue.suggestion)}」` : ""}</span>
          <div class="meta">${escapeHtml(issue.reason || "")}${issue.confidence != null ? `（置信 ${Number(issue.confidence).toFixed(2)}）` : ""}</div>
          <div class="q-actions">
            ${issue.suggestion ? '<button class="btn small primary" data-act="accept">采纳</button>' : ""}
            <button class="btn small" data-act="ignore">忽略</button>
          </div>
        </div>`).join("")}
    </div>`).join("");
  el.qualityBody.innerHTML = `
    <div class="prompt-actions">
      <button class="btn small" id="qualityAnalyzeBtn">重新分析</button>
      <button class="btn small primary" id="qualityAiBtn" ${state.quality.aiRunning ? "disabled" : ""}>${state.quality.aiRunning ? "AI 复核中…" : "AI 复核"}</button>
      <button class="btn small" id="qualityRefBtn">导入参考字幕</button>
      <input type="file" id="qualityRefFile" accept=".srt,.vtt" hidden>
      ${undoAi}
    </div>
    <div class="settings-note" id="qualityResult">${report ? `报告生成于 ${escapeHtml(report.generated_at || "-")}，待处理 ${issues.length} 项。` : "尚无报告，点「重新分析」生成（免费，不调用 AI）。"}</div>
    ${groupHtml || (report ? '<div class="ai-hint">🎉 没有待处理的质检问题。</div>' : "")}
    <div class="ai-hint" style="margin-top:8px">低置信扫描免费；「AI 复核」调用大模型综合上下文判断错词——确定的自动改（整批可撤销），拿不准的留在这里等你裁决。</div>`;
  $("qualityAnalyzeBtn").addEventListener("click", () => runAnalyze(false));
  $("qualityAiBtn").addEventListener("click", () => runAnalyze(true));
  $("qualityRefBtn").addEventListener("click", () => $("qualityRefFile").click());
  $("qualityRefFile").addEventListener("change", (event) => {
    if (event.target.files[0]) uploadReference(event.target.files[0]);
    event.target.value = "";
  });
  const undoBtn = $("qualityUndoAiBtn");
  if (undoBtn) undoBtn.addEventListener("click", async () => {
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quality/undo/${encodeURIComponent(meta.ai_changeset_id)}`, {});
      await showEditor();
      await loadQualityReport();
      setStatus("已撤销 AI 自动纠正。");
    } catch (error) {
      setStatus(`撤销失败：${error.message}`, "error");
    }
  });
  el.qualityBody.querySelectorAll(".q-item").forEach((node) => {
    const issue = issues.find((item) => item.id === node.dataset.issue);
    if (!issue) return;
    node.querySelector('[data-act="jump"]').addEventListener("click", () => jumpToIssue(issue));
    node.querySelector('[data-act="accept"]')?.addEventListener("click", () => acceptIssue(issue));
    node.querySelector('[data-act="ignore"]').addEventListener("click", () => ignoreIssue(issue));
  });
}
