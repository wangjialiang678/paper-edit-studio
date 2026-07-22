/* 编辑器视图装载：拉取字幕行 + 恢复 AI 概览 + 初始化播放。 */
import { el, state, pb, api, setStatus } from "./shared.js";
import { showView } from "./projects.js";
import { renderRows } from "./rows.js";
import { renderAiPanel, resumeAiPolling } from "./ai.js";
import { syncPlan } from "./plan.js";
import { updateTransport } from "./player.js";

export async function showEditor() {
  try {
    const payload = await api(`/api/projects/${state.projectId}/editor`);
    state.rows = payload.rows || [];
    state.silences = payload.silence_gaps || [];
    state.aiOverview = payload.ai || { modes: {} };
    state.sourceDurationMs = payload.duration_ms || payload.project.duration_ms || 0;
    showView("editor");
    const mediaUrl = `/media/${state.projectId}/source`;
    if (!el.video.src.endsWith(encodeURI(mediaUrl))) el.video.src = mediaUrl;
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
    const warning = payload.project.ai_warning;
    setStatus(warning ? warning : `已加载 ${state.rows.length} 句字幕。空格播放成片，点击句子从该处继续。`, warning ? "warn" : "");
    resumeAiPolling();
  } catch (error) {
    setStatus(error.message, "error");
    showView("editor");
  }
}
