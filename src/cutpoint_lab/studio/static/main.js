/* 启动入口：装配各模块（导入顺序即事件绑定顺序）、全局键盘、窄栏/抽屉/帮助、启动流程。 */
import { el, pb, state } from "./shared.js";
import { togglePlay, setMode } from "./player.js";
import { refreshProjects, showView, openDrawer, closeDrawer } from "./projects.js";
import { applyAllSuggestedCuts, undoFillerBatch } from "./rows.js";
import "./editor.js";
import "./exporter.js";
import "./ai.js";
import "./settings.js";
import "./quality.js";
import "./cuts.js";
import "./budget.js";
import { renderRows } from "./rows.js";
import { openTopicsView } from "./topics.js";
import { openQuotesDialog } from "./quotes.js";

el.origOrderBtn.addEventListener("click", () => {
  state.viewOriginal = !state.viewOriginal;
  renderRows();
});

el.topicsBtn.addEventListener("click", openTopicsView);
el.quotesBtn.addEventListener("click", () => openQuotesDialog(null));

// 窄栏：项目抽屉 / 导入
el.railProjectsBtn.addEventListener("click", () => {
  if (el.projectDrawer.hidden) openDrawer();
  else closeDrawer();
});
el.railImportBtn.addEventListener("click", () => { openDrawer(); el.fileInput.click(); });
el.drawerCloseBtn.addEventListener("click", closeDrawer);
el.drawerMask.addEventListener("click", closeDrawer);

// 固定帮助入口：三步用法（位置永远不变——引导的常驻兜底）
el.helpBtn.addEventListener("click", () => {
  document.querySelector(".help-pop")?.remove();
  const pop = document.createElement("div");
  pop.className = "q-popover help-pop";
  pop.innerHTML = `
    <div class="q-pop-text"><b>三步用法</b></div>
    <div>① <b>🤖 AI 智能剪辑</b>（或 📄 粘贴你的剪辑稿）——AI 通读字幕：分主题 → 挑金句放开头 → 按目标时长筛句子，产出可切换的剪辑方案；</div>
    <div>② <b>在字幕表里精修</b>——☑ 勾选保留、点词删词、拖 ⠿ 调顺序、点 ⭐ 设金句；✂ 剪气口、🔎 字幕校对、⏱ 时长都在表上方；</div>
    <div>③ <b>⬇ 导出本方案</b>——成片 mp4 + 字幕 SRT。</div>
    <div class="meta" style="margin-top:6px">每个方案独立保存，随时切换、复制、回头改。</div>
    <div class="q-actions"><button class="btn small" data-act="close">关闭</button></div>`;
  document.body.appendChild(pop);
  const rect = el.helpBtn.getBoundingClientRect();
  pop.style.left = `${Math.min(rect.left + window.scrollX - 300, window.innerWidth - 380)}px`;
  pop.style.top = `${rect.bottom + window.scrollY + 6}px`;
  const close = () => { pop.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!pop.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);
  pop.querySelector('[data-act="close"]').addEventListener("click", close);
});

document.addEventListener("keydown", (event) => {
  if (el.editorView.hidden) return;
  const target = event.target;
  if (target && (target.tagName === "TEXTAREA" || target.tagName === "INPUT")) return;
  if (event.code === "Space") { event.preventDefault(); togglePlay(); }
  else if (event.key === "m" || event.key === "M") { setMode(pb.mode === "edited" ? "source" : "edited"); }
});

el.cutFillersBtn.addEventListener("click", () => {
  if (!state.rows.length) return;
  applyAllSuggestedCuts();
});
el.undoCutFillersBtn.addEventListener("click", undoFillerBatch);


refreshProjects();
setInterval(refreshProjects, 8000);
showView("empty");
openDrawer(); // 首屏未选项目：抽屉打开等选择
