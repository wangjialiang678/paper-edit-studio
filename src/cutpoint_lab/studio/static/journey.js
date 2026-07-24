/* 轻引导：把「一排平级功能」重构成用户旅程进度条（① 看AI初剪 → ⑤ 导出）。
   只做「你现在在哪、下一步做什么」的定位与一键直达；所有功能仍可随时点、跳、回头。
   工作台评审拍板（2026-07-23）：引导强度=轻引导；分主题=长视频才主动提示。 */
import { el, state, fmtClock } from "./shared.js";
import { openTopicsView } from "./topics.js";

/* 三步旅程（2026-07-24 拍板）：与两步核心模型对齐（出方案 → 精修），导出单列一步。 */
const STEPS = [
  { key: "plan", label: "① AI 出剪辑方案",
    hint: "告诉 AI 想怎么剪（多选意图 + 目标时长），它一次完成分主题、挑金句、筛句子，产出可切换的剪辑方案。已有自己的 AI 剪辑稿也可以直接粘贴导入。",
    action: () => { el.aiPanel.hidden = true; el.aiPanelBtn.click(); } },
  { key: "refine", label: "② 精修",
    hint: "下面这张表就是成片：☑ 勾选保留、点词删词、拖 ⠿ 调顺序、点 ⭐ 设金句（自动复制到开头）；✂ 剪气口、🔎 字幕校对、⏱ 时长都在工具栏。列表·预览·导出永远与表一致。",
    action: () => el.rows?.scrollIntoView({ behavior: "smooth", block: "start" }) },
  { key: "export", label: "③ 导出",
    hint: "满意了就导出成片，字幕 SRT 一起出。",
    action: () => el.exportBtn.click() },
];

/* 长视频（多话题可能各剪一条）才主动提示看点梳理；短片零打扰直接进初剪。 */
const LONG_MS = 10 * 60 * 1000;
const LONG_SENTENCES = 140;

export function renderJourney() {
  if (!el.journeyStrip) return;
  // 出过方案（管线跑过 / 有非 default 方案 / 自动初剪已落）即视为第 1 步完成
  const planDone = (state.project?.plan_ai || {}).status === "done"
    || state.cuts.some((cut) => cut.name !== "default")
    || state.rows.some((r) => r.ai_keep === true || r.ai_keep === false);
  const done = { plan: planDone };
  const suggested = planDone ? "refine" : "plan";

  el.journeyStrip.hidden = false;
  el.journeyStrip.innerHTML = `
    <div class="journey-steps">
      ${STEPS.map((s) => `<button class="journey-step ${done[s.key] ? "done" : ""} ${s.key === suggested ? "suggested" : ""}" data-step="${s.key}">${done[s.key] ? "✓ " : ""}${s.label}</button>`).join("")}
    </div>
    <div class="journey-hint">${(STEPS.find((s) => s.key === suggested) || STEPS[0]).hint}<span class="journey-note"> · 每步都能跳过 / 回头改，也可直接在表里操作</span></div>`;
  el.journeyStrip.querySelectorAll(".journey-step").forEach((btn) => {
    const step = STEPS.find((s) => s.key === btn.dataset.step);
    if (step) btn.addEventListener("click", step.action);
  });

  renderTopicHint();
}

function renderTopicHint() {
  const banner = el.topicHintBanner;
  if (!banner) return;
  const durationMs = state.sourceDurationMs || state.project?.duration_ms || 0;
  const isLong = durationMs >= LONG_MS || state.rows.length >= LONG_SENTENCES;
  const show = isLong
    && !state.topicHintDismissed
    && !state.contentMap                 // 已梳理过就不再劝
    && state.cutName === "default";      // 已进入某看点方案则无需
  if (!show) { banner.hidden = true; return; }
  banner.hidden = false;
  banner.innerHTML = `
    <span>这段有点长（${state.rows.length} 句 · ${fmtClock(durationMs)}），可能讲了好几件事——要不要先让 AI <b>梳理看点</b>、分别各剪一条短视频？</span>
    <button class="btn small primary" id="topicHintGoBtn">🗺 梳理看点</button>
    <button class="btn small" id="topicHintDismissBtn">不用，整片剪</button>`;
  banner.querySelector("#topicHintGoBtn").addEventListener("click", () => openTopicsView());
  banner.querySelector("#topicHintDismissBtn").addEventListener("click", () => {
    state.topicHintDismissed = true;
    banner.hidden = true;
  });
}
