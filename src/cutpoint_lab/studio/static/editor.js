/* 编辑器视图装载：拉取字幕行 + 恢复 AI 概览 + 初始化播放。 */
import { el, state, pb, api, setStatus, withCut } from "./shared.js";
import { showView, updateAppbar } from "./projects.js";
import { renderRows } from "./rows.js";
import { renderAiPanel, resumeAiPolling } from "./ai.js";
import { syncPlan } from "./plan.js";
import { updateTransport } from "./player.js";
import { loadQualityReport } from "./quality.js";
import { loadCuts } from "./cuts.js";
import { refreshBudget } from "./budget.js";

export async function showEditor() {
  try {
    const payload = await api(withCut(`/api/projects/${state.projectId}/editor`));
    state.rows = payload.rows || [];
    state.silences = payload.silence_gaps || [];
    state.order = payload.order || [];
    state.aiOverview = payload.ai || { modes: {} };
    state.sourceDurationMs = payload.duration_ms || payload.project.duration_ms || 0;
    await loadCuts();
    showView("editor");
    const mediaUrl = `/media/${state.projectId}/source`;
    if (!el.video.src.endsWith(encodeURI(mediaUrl))) el.video.src = mediaUrl;
    await loadQualityReport(); // 行内质检高亮依赖报告，先拉再渲染（本地文件，快）
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
    updateAppbar();
    refreshBudget(); // 预算 chip（旧后端 404 自动隐藏）
    // 回填看点：金句弹窗与「🗺 看点」入口需要
    api(`/api/projects/${encodeURIComponent(state.projectId)}/content-map`)
      .then((map) => { state.contentMap = map; })
      .catch(() => { /* 未梳理，保持 null */ });
    const warning = payload.project.ai_warning;
    setStatus(warning ? warning : `已加载 ${state.rows.length} 句字幕。空格播放成片，点击句子从该处继续。`, warning ? "warn" : "");
    resumeAiPolling();
  } catch (error) {
    setStatus(error.message, "error");
    showView("editor");
  }
}
