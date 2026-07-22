/* 导出：提交导出任务并轮询结果。 */
import { el, state, api, postJson, setStatus, fmtClock, escapeHtml } from "./shared.js";
import { planBody } from "./plan.js";

el.exportBtn.addEventListener("click", async () => {
  el.exportBtn.disabled = true;
  el.exportResult.hidden = true;
  try {
    await postJson(`/api/projects/${state.projectId}/export`, planBody());
    setStatus("导出已开始（后台运行）…");
    pollExport();
  } catch (error) {
    setStatus(error.message, "error");
    el.exportBtn.disabled = false;
  }
});

async function pollExport() {
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    const job = project.export || {};
    if (job.status === "running") {
      state.exportTimer = setTimeout(pollExport, 3000);
      return;
    }
    el.exportBtn.disabled = false;
    if (job.status === "done") {
      setStatus("导出完成。");
      el.exportResult.hidden = false;
      el.exportResult.innerHTML = `
        ✅ 成片时长 ${fmtClock(job.duration_ms || 0)} · ${job.range_count} 个片段<br>
        <a href="/media/${state.projectId}/exports/${encodeURIComponent(job.video_name)}">下载视频</a> ·
        <a href="/media/${state.projectId}/exports/${encodeURIComponent(job.srt_name)}">下载字幕 SRT</a><br>
        <span style="color:var(--muted)">${escapeHtml(job.video || "")}</span>`;
    } else if (job.status === "error") {
      setStatus(`导出失败：${job.error}`, "error");
    }
  } catch (error) {
    el.exportBtn.disabled = false;
    setStatus(error.message, "error");
  }
}
