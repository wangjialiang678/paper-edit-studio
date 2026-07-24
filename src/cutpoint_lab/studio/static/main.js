/* 启动入口：装配各模块（导入顺序即事件绑定顺序）、全局键盘、工具栏、启动流程。 */
import { el, pb, state } from "./shared.js";
import { togglePlay, setMode } from "./player.js";
import { refreshProjects, showView } from "./projects.js";
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
import { applyAdvTray } from "./cuts.js";

el.origOrderBtn.addEventListener("click", () => {
  state.viewOriginal = !state.viewOriginal;
  renderRows();
});

el.topicsBtn.addEventListener("click", openTopicsView);
el.quotesBtn.addEventListener("click", () => openQuotesDialog(null));
el.moreBtn.addEventListener("click", () => {
  state.moreOpen = !state.moreOpen;
  applyAdvTray();
  el.moreBtn.classList.toggle("active", state.moreOpen);
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
