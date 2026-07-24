/* 屏 A：主题确认页（V2 内容地图）。

   数据契约（B10）：
   GET  /api/projects/{id}/content-map            → content_map.json（404=尚未生成）
   POST /api/projects/{id}/content-map/analyze    → 异步，轮询 project.content_map_ai.status
   PUT  /api/projects/{id}/content-map            → 保存人工编辑稿（改名/合并/状态/句子归属）
   POST /api/projects/{id}/content-map/topics/{tid}/create-cut → 从主题建 Cut（409=已存在）

   每个主题 = 一个独立成片方案（Cut），互不影响；「进入剪辑」= 建/切方案 → 屏 B 金句候选。 */
import { el, state, api, postJson, putJson, escapeHtml, fmtClock, setStatus } from "./shared.js";
import { showView } from "./projects.js";
import { showEditor } from "./editor.js";
import { openQuotesDialog } from "./quotes.js";

let aiPollTimer = null;

export async function openTopicsView() {
  if (!state.projectId) return;
  showView("topics");
  await loadContentMap();
  renderTopicsView();
}

async function loadContentMap() {
  try {
    state.contentMap = await api(`/api/projects/${encodeURIComponent(state.projectId)}/content-map`);
  } catch {
    state.contentMap = null; // 404 = 未生成；旧后端 = 功能未就绪
  }
}

function topicCutName(topic) {
  return `topic-${String(topic.id).toLowerCase().replace(/[^a-z0-9-]+/g, "-").slice(0, 26)}`;
}

function topicDurationLabel(topic) {
  const bits = [];
  if (topic.duration_ms) bits.push(`原始 ${fmtClock(topic.duration_ms)}`);
  if (topic.suggested_duration_s) bits.push(`建议成片 ${topic.suggested_duration_s}s`);
  return bits.join(" ｜ ");
}

function quoteCountFor(topicId) {
  const list = (state.quotes?.candidates || []).filter((c) => c.topic_id === topicId && c.status !== "rejected");
  return list.length;
}

export function renderTopicsView() {
  const project = state.project || {};
  const map = state.contentMap;
  const head = `
    <div class="topics-head">
      <div>
        <h2>${escapeHtml(project.name || state.projectId || "")}</h2>
        <div class="meta">${fmtClock(state.sourceDurationMs || project.duration_ms || 0)} · ${state.rows.length} 句已转写</div>
      </div>
      <button class="btn" id="topicsSkipBtn">跳过，整片直接剪 →</button>
    </div>`;

  if (!map) {
    el.topicsBody.innerHTML = `${head}
      <div class="ai-card">
        <h4>先让 AI 看看这视频讲了哪几件事</h4>
        <div class="meta">AI 通读整片字幕，把一段视频里的几个「看点」（可各剪一条短视频）跟「背景/案例/活动名称」分开，给出每个看点的范围、原始时长与建议成片时长——先确认要表达什么，再动剪刀。</div>
        <div class="prompt-actions" style="margin-top:8px">
          <button class="btn primary" id="topicsAnalyzeBtn" ${state.contentMapAiRunning ? "disabled" : ""}>${state.contentMapAiRunning ? "AI 梳理中…" : "🗺 AI 梳理看点"}</button>
        </div>
        <div class="settings-note" id="topicsHint">${state.contentMapAiRunning ? "梳理进行中（后台运行，完成自动刷新）…" : "短片约十几秒；长视频要分段分析再汇总，可能几分钟。"}</div>
      </div>`;
    bindCommon();
    return;
  }

  const topics = map.topics || [];
  const confirmed = topics.filter((t) => t.status === "confirmed");
  const cards = topics.map((topic) => {
    const isConfirmed = topic.status === "confirmed";
    const qn = quoteCountFor(topic.id);
    return `
    <div class="topic-card ${isConfirmed ? "confirmed" : ""}" data-topic="${escapeHtml(topic.id)}">
      <div class="topic-main">
        <div class="topic-name">${escapeHtml(topic.name || topic.id)}</div>
        <div class="meta">${escapeHtml(topic.summary || "")}</div>
        <div class="meta">${(topic.segment_ids || []).length} 句 ｜ ${topicDurationLabel(topic)}${qn ? ` ｜ 金句候选 ×${qn}` : ""}</div>
      </div>
      <div class="topic-actions">
        <button class="btn primary" data-act="enter">进入剪辑 →</button>
        <button class="btn small" data-act="rename">改名</button>
        <button class="btn small" data-act="merge">并入其他看点…</button>
        <button class="btn small ${isConfirmed ? "" : "primary"}" data-act="toggle-status">${isConfirmed ? "↩ 取消锁定" : "✅ 确认锁定"}</button>
      </div>
      <div class="topic-status">${isConfirmed ? "✅ 已锁定" : "⏳ 待处理"}</div>
    </div>`;
  }).join("");

  const backgrounds = (map.backgrounds || []).map((bg) =>
    `<span class="pill">${escapeHtml(bg.text || "")}${bg.segment_ids?.length ? `（${bg.segment_ids.length} 句）` : ""}</span>`
  ).join(" ");

  el.topicsBody.innerHTML = `${head}
    <div class="topics-tip">AI 梳理出 ${topics.length} 个看点${map.backgrounds?.length ? " + 背景段" : ""}，请确认边界（可改名/合并/锁定）。每个看点 = 一个独立剪辑方案，互不影响；随时回本页换看点。</div>
    ${cards || '<div class="ai-hint">还没梳理出看点，可点下方重新梳理。</div>'}
    ${map.backgrounds?.length ? `<div class="topic-bg"><b>背景/过渡段（不单独成片）：</b>${backgrounds}</div>` : ""}
    <div class="prompt-actions" style="margin-top:12px">
      <button class="btn small" id="topicsAnalyzeBtn" ${state.contentMapAiRunning ? "disabled" : ""}>${state.contentMapAiRunning ? "AI 梳理中…" : "🔄 重新梳理看点"}</button>
      <button class="btn accent" id="topicsExportAllBtn" ${confirmed.length ? "" : "disabled"}>⬇ 导出全部已锁定看点（${confirmed.length}/${topics.length}）</button>
    </div>
    <div class="settings-note" id="topicsHint"></div>`;
  bindCommon();
  bindTopicCards();
}

function bindCommon() {
  document.getElementById("topicsSkipBtn")?.addEventListener("click", async () => {
    state.cutName = "default";
    state.order = [];
    await showEditor();
  });
  document.getElementById("topicsAnalyzeBtn")?.addEventListener("click", runAnalyze);
}

let analyzeStartMs = 0;

async function runAnalyze() {
  try {
    await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/content-map/analyze`);
    state.contentMapAiRunning = true;
    analyzeStartMs = Date.now();
    renderTopicsView();
    tickProgress();
    pollAnalyze();
  } catch (error) {
    setStatus(`看点梳理启动失败：${error.message}`, "error");
    const hint = document.getElementById("topicsHint");
    if (hint) hint.textContent = `❌ ${error.message}`;
  }
}

/* 长视频（>150 句要分块+合并）分析可达几分钟，给个走动的进度反馈，别让人以为卡死。 */
function tickProgress() {
  if (!state.contentMapAiRunning) return;
  const elapsed = Math.round((Date.now() - analyzeStartMs) / 1000);
  const sentences = state.rows.length;
  const longNote = sentences > 150 ? `（本片 ${sentences} 句，要分段分析再汇总，约 2–8 分钟）` : "（约十几秒到一分钟）";
  const hint = document.getElementById("topicsHint");
  if (hint) hint.textContent = `AI 通读字幕中 · 已用 ${elapsed}s ${longNote}`;
  setTimeout(tickProgress, 1000);
}

function pollAnalyze() {
  if (aiPollTimer) clearTimeout(aiPollTimer);
  aiPollTimer = setTimeout(async () => {
    try {
      const project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
      const status = (project.content_map_ai || {}).status;
      if (status === "running") { pollAnalyze(); return; }
      state.contentMapAiRunning = false;
      if (status === "error") {
        setStatus(`看点梳理失败：${(project.content_map_ai || {}).error || "未知错误"}`, "error");
      } else {
        await loadContentMap();
        setStatus("看点已梳理好：请逐个确认边界（可改名/合并/锁定）。");
      }
      if (!el.topicsView.hidden) renderTopicsView();
    } catch {
      pollAnalyze();
    }
  }, 3000);
}

async function saveMap() {
  await putJson(`/api/projects/${encodeURIComponent(state.projectId)}/content-map`, state.contentMap);
}

function bindTopicCards() {
  el.topicsBody.querySelectorAll(".topic-card").forEach((card) => {
    const topicId = card.dataset.topic;
    const topic = (state.contentMap?.topics || []).find((t) => t.id === topicId);
    if (!topic) return;

    card.querySelector('[data-act="enter"]').addEventListener("click", async () => {
      const name = topicCutName(topic);
      try {
        await postJson(
          `/api/projects/${encodeURIComponent(state.projectId)}/content-map/topics/${encodeURIComponent(topicId)}/create-cut`,
          { name, label: topic.name || name }
        );
      } catch (error) {
        if (!/已存在|exists|409/.test(error.message)) {
          setStatus(`创建看点方案失败：${error.message}`, "error");
          return;
        }
      }
      state.cutName = name;
      state.order = [];
      state.viewOriginal = false;
      await showEditor();
      setStatus(`已进入看点「${topic.name || topicId}」的剪辑方案。第 1 步：挑金句（可跳过）。`);
      openQuotesDialog(topicId);
    });

    card.querySelector('[data-act="rename"]').addEventListener("click", async () => {
      const name = prompt("看点名称：", topic.name || "");
      if (name === null || !name.trim() || name.trim() === topic.name) return;
      topic.name = name.trim();
      try { await saveMap(); renderTopicsView(); }
      catch (error) { setStatus(`保存失败：${error.message}`, "error"); }
    });

    card.querySelector('[data-act="merge"]').addEventListener("click", async () => {
      const others = (state.contentMap.topics || []).filter((t) => t.id !== topicId);
      if (!others.length) { setStatus("没有其他看点可并入。", "warn"); return; }
      const choice = prompt(
        `把「${topic.name}」的句子并入哪个看点？输入编号：\n` +
        others.map((t, i) => `${i + 1}. ${t.name || t.id}`).join("\n")
      );
      const index = Number(choice) - 1;
      if (!(index >= 0 && index < others.length)) return;
      const target = others[index];
      const merged = new Set([...(target.segment_ids || []), ...(topic.segment_ids || [])]);
      target.segment_ids = [...merged];
      state.contentMap.topics = state.contentMap.topics.filter((t) => t.id !== topicId);
      try {
        await saveMap();
        renderTopicsView();
        setStatus(`已把「${topic.name}」并入「${target.name}」。`);
      } catch (error) {
        setStatus(`合并失败：${error.message}`, "error");
        await loadContentMap();
        renderTopicsView();
      }
    });

    card.querySelector('[data-act="toggle-status"]').addEventListener("click", async () => {
      topic.status = topic.status === "confirmed" ? "pending" : "confirmed";
      try { await saveMap(); renderTopicsView(); }
      catch (error) { setStatus(`保存失败：${error.message}`, "error"); }
    });
  });

  document.getElementById("topicsExportAllBtn")?.addEventListener("click", exportAllConfirmed);
}

/* 逐个导出已锁定看点的方案（顺序执行，避免并发编码互抢 CPU）。 */
async function exportAllConfirmed() {
  const confirmed = (state.contentMap?.topics || []).filter((t) => t.status === "confirmed");
  if (!confirmed.length) return;
  const hint = document.getElementById("topicsHint");
  const say = (msg) => { if (hint) hint.textContent = msg; };
  for (let i = 0; i < confirmed.length; i++) {
    const topic = confirmed[i];
    const cut = topicCutName(topic);
    say(`导出中 ${i + 1}/${confirmed.length}：「${topic.name}」…`);
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/export?cut=${encodeURIComponent(cut)}`, {});
      // 轮询该导出完成再下一个
      let done = false;
      while (!done) {
        await new Promise((resolve) => setTimeout(resolve, 3000));
        const project = await api(`/api/projects/${encodeURIComponent(state.projectId)}`);
        const job = project.export || {};
        if (job.status === "done") done = true;
        else if (job.status === "error") throw new Error(job.error || "导出失败");
      }
    } catch (error) {
      say(`❌「${topic.name}」导出失败：${error.message}（其余看点已停止）`);
      return;
    }
  }
  say(`✅ 已导出 ${confirmed.length} 个看点方案，文件在各方案的 exports/ 目录（也可逐个进入方案下载）。`);
}
