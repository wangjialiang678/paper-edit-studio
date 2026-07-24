/* 屏 B：金句候选（进入主题后第 1 步，可跳过）。

   数据契约（B10）：
   GET  /api/projects/{id}/quotes                    → quote_candidates.json（404=尚未生成）
   POST /api/projects/{id}/quotes/analyze {topic_id?} → 异步，轮询 project.quotes_ai.status
   POST /api/projects/{id}/quotes/{qid}/accept {cut, promote} → 该句 role=quote, locked=true；promote=插到 order 头部
   POST /api/projects/{id}/quotes/{qid}/reject

   你的判断永远优先：字幕表格里任意句子点 ⭐ 也能设为金句（rows.js），确认后 AI 不得再动。 */
import { state, api, postJson, escapeHtml, fmtClock, setStatus } from "./shared.js";
import { showEditor } from "./editor.js";
import { refreshBudget, fmtMinutes } from "./budget.js";
import { promoteQuote, renderRows } from "./rows.js";
import { flushPlanNow } from "./plan.js";

const TYPE_LABELS = { claim: "主张", hook: "钩子", background: "背景", question: "提问", action: "行动" };

let aiPollTimer = null;
let currentTopicId = null;

export async function loadQuotes() {
  try {
    state.quotes = await api(`/api/projects/${encodeURIComponent(state.projectId)}/quotes`);
  } catch {
    state.quotes = null;
  }
}

export function openQuotesDialog(topicId = null) {
  currentTopicId = topicId;
  document.querySelector(".quotes-dialog")?.remove();
  const dlg = document.createElement("div");
  dlg.className = "script-dialog quotes-dialog";
  dlg.innerHTML = `<div class="script-dialog-box"><div id="quotesDialogBody">加载中…</div></div>`;
  document.body.appendChild(dlg);
  renderQuotesDialog();
  Promise.all([ensureContentMap(), loadQuotes()]).then(renderQuotesDialog);
}

/* 金句挑选依赖「看点」（content_map 里有已确认看点）；未加载时先取一次，好决定显示分析入口还是引导。 */
async function ensureContentMap() {
  if (state.contentMap) return;
  try {
    state.contentMap = await api(`/api/projects/${encodeURIComponent(state.projectId)}/content-map`);
  } catch { state.contentMap = null; }
}

function closeDialog() {
  if (aiPollTimer) { clearTimeout(aiPollTimer); aiPollTimer = null; }
  document.querySelector(".quotes-dialog")?.remove();
}

function rowFor(segmentId) {
  return state.rows.find((row) => row.id === segmentId);
}

function candidatesForCurrent() {
  const all = state.quotes?.candidates || [];
  return currentTopicId ? all.filter((c) => c.topic_id === currentTopicId) : all;
}

function topicName() {
  if (!currentTopicId) return "整片";
  const topic = (state.contentMap?.topics || []).find((t) => t.id === currentTopicId);
  return topic?.name || currentTopicId;
}

function renderQuotesDialog() {
  const body = document.getElementById("quotesDialogBody");
  if (!body) return;
  const topics = state.contentMap?.topics || [];
  const canAnalyze = topics.some((t) => t.status === "confirmed"); // 金句分析要求已有确认看点
  const candidates = candidatesForCurrent().filter((c) => c.status !== "rejected");
  const accepted = candidatesForCurrent().filter((c) => c.status === "accepted");
  const acceptedMs = accepted.reduce((sum, c) => {
    const row = rowFor(c.segment_id);
    return sum + (row ? Math.max(0, row.end_ms - row.start_ms) : 0);
  }, 0);
  const budget = state.budget;
  const targetLabel = budget?.target_s != null
    ? `${fmtMinutes(Math.max(0, budget.target_s - (budget.tolerance_s || 0)))}–${fmtMinutes(budget.target_s + (budget.tolerance_s || 0))} 分钟`
    : null;

  const cards = candidates.map((c) => {
    const row = rowFor(c.segment_id);
    const dur = row ? `${((row.end_ms - row.start_ms) / 1000).toFixed(0)}s` : "";
    const at = row ? fmtClock(row.start_ms) : "";
    const isAccepted = c.status === "accepted";
    return `
    <div class="quote-card ${isAccepted ? "accepted" : ""}" data-qid="${escapeHtml(c.id)}">
      <div class="quote-text">
        <span class="badge quote">${escapeHtml(TYPE_LABELS[c.type] || c.type || "候选")}</span>
        「${escapeHtml(row?.text || c.text || c.segment_id)}」
        <span class="meta">｜${at}｜${dur}</span>
      </div>
      <div class="meta">理由：${escapeHtml(c.reason || "—")}${c.context ? `｜上下文：${escapeHtml(c.context)}` : ""}</div>
      <div class="q-actions">
        ${isAccepted
          ? '<span class="pill">✅ 已采纳</span>'
          : `<button class="btn small primary" data-act="top">⬆ 置顶开头</button>
             <button class="btn small" data-act="keep">保留原位</button>
             <button class="btn small" data-act="drop">弃用</button>`}
      </div>
    </div>`;
  }).join("");

  body.innerHTML = `
    <div class="quotes-head">
      <h4>${escapeHtml(topicName())} · 挑金句（可跳过）</h4>
      <div class="quotes-head-actions">
        <button class="btn small" id="quotesSkipBtn">${currentTopicId ? "跳过，直接选段 →" : "关闭"}</button>
        <button class="btn small icon" id="quotesCloseBtn" title="关闭">✕</button>
      </div>
    </div>
    ${state.quotesAiRunning
      ? '<div class="ai-hint">AI 正在找金句（后台运行，完成自动刷新）…</div>'
      : cards || (canAnalyze ? `
        <div class="ai-card">
          <div class="meta">还没有金句候选。AI 会在${currentTopicId ? "本看点" : "全片"}里挑 3–5 个能单句立住的句子（主张/钩子/行动），给理由供你裁决。</div>
          <div class="prompt-actions" style="margin-top:8px"><button class="btn primary" id="quotesAnalyzeBtn">⭐ AI 找金句</button></div>
        </div>` : `
        <div class="ai-card">
          <div class="meta">金句是在「看点」的基础上挑的——先花十几秒到几分钟让 AI 理解这段视频<b>讲了哪几件事</b>、并确认一个看点，就能在它里面挑金句了。</div>
          <div class="prompt-actions" style="margin-top:8px"><button class="btn primary" id="quotesGotoTopicsBtn">① 先做「看点梳理」 →</button></div>
          <div class="ai-hint" style="margin-top:6px">或者直接关掉这里，在下面的字幕表格里给任意句子点 ⭐ 手动设金句。</div>
        </div>`)}
    <div class="quotes-manual">——— 不满意 AI 挑的？———<br>字幕表格里任意句子点 ⭐ 即设为金句（你的判断永远优先，确认后 AI 不得再动）。</div>
    <div class="quotes-budget">${accepted.length ? `已选金句 ${accepted.length} 句 · 合计 ${fmtClock(acceptedMs)}` : "尚未采纳金句"}${targetLabel ? ` / 目标成片 ${targetLabel}（金句先占预算，其余给主张和支撑句）` : ""}</div>
    <div class="prompt-actions">
      <button class="btn primary" id="quotesDoneBtn">${currentTopicId ? "完成，进入选段 →" : "完成"}</button>
    </div>
    <div class="settings-note" id="quotesHint"></div>`;

  body.querySelector("#quotesSkipBtn").addEventListener("click", closeDialog);
  body.querySelector("#quotesCloseBtn").addEventListener("click", closeDialog);
  body.querySelector("#quotesDoneBtn").addEventListener("click", closeDialog);
  body.querySelector("#quotesAnalyzeBtn")?.addEventListener("click", runAnalyze);
  body.querySelector("#quotesGotoTopicsBtn")?.addEventListener("click", () => {
    closeDialog();
    import("./topics.js").then((m) => m.openTopicsView());
  });
  body.querySelectorAll(".quote-card").forEach((card) => {
    const qid = card.dataset.qid;
    card.querySelector('[data-act="top"]')?.addEventListener("click", () => acceptQuote(qid, true));
    card.querySelector('[data-act="keep"]')?.addEventListener("click", () => acceptQuote(qid, false));
    card.querySelector('[data-act="drop"]')?.addEventListener("click", () => rejectQuote(qid));
  });
}

async function runAnalyze() {
  const hint = document.getElementById("quotesHint");
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quotes/analyze`,
      currentTopicId ? { topic_id: currentTopicId } : {});
    state.quotesAiRunning = true;
    renderQuotesDialog();
    pollAnalyze();
  } catch (error) {
    if (hint) hint.textContent = `❌ ${error.message}`;
  }
}

function pollAnalyze() {
  if (aiPollTimer) clearTimeout(aiPollTimer);
  aiPollTimer = setTimeout(async () => {
    try {
      const project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
      const status = (project.quotes_ai || {}).status;
      if (status === "running") { pollAnalyze(); return; }
      state.quotesAiRunning = false;
      if (status === "error") {
        setStatus(`金句分析失败：${(project.quotes_ai || {}).error || "未知错误"}`, "error");
      } else {
        await loadQuotes();
      }
      renderQuotesDialog();
    } catch {
      pollAnalyze();
    }
  }, 3000);
}

/* 采纳：后端写 EDL（role=quote, locked=true）→ 置顶时由前端「复制」到 order 头部（原位保留）。 */
async function acceptQuote(qid, promote) {
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quotes/${encodeURIComponent(qid)}/accept`,
      { cut: state.cutName, promote: false });
    await loadQuotes();
    await showEditor();
    if (promote) {
      const candidate = (state.quotes?.candidates || []).find((c) => c.id === qid);
      const row = candidate && state.rows.find((item) => item.id === candidate.segment_id);
      if (row) {
        promoteQuote(row);
        renderRows();
        await flushPlanNow();
      }
    }
    refreshBudget();
    renderQuotesDialog();
    setStatus(promote ? "金句已复制到成片开头（原位保留，🔒 重跑 AI 不会动它）。" : "金句已锁定在原位。");
  } catch (error) {
    const hint = document.getElementById("quotesHint");
    if (hint) hint.textContent = `❌ ${error.message}`;
  }
}

async function rejectQuote(qid) {
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quotes/${encodeURIComponent(qid)}/reject`, {});
    await loadQuotes();
    renderQuotesDialog();
  } catch (error) {
    const hint = document.getElementById("quotesHint");
    if (hint) hint.textContent = `❌ ${error.message}`;
  }
}
