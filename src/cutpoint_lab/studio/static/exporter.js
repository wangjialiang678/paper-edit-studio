/* 导出：确认弹层（含检查清单，只提醒不阻断）→ 提交导出任务并轮询结果。 */
import { el, state, api, postJson, setStatus, fmtClock, escapeHtml, withCut } from "./shared.js";
import { planBody } from "./plan.js";
import { renderChecklist } from "./budget.js";

el.exportBtn.addEventListener("click", () => {
  document.querySelector(".export-pop")?.remove();
  const pop = document.createElement("div");
  pop.className = "q-popover export-pop";
  pop.innerHTML = `
    <div class="q-pop-text"><b>导出方案「${escapeHtml(state.cutName)}」</b></div>
    <div class="meta">成片 mp4 + 重排字幕 SRT（后台运行，完成后此处出下载链接）。</div>
    <div id="exportPopChecklist" class="meta" style="margin-top:6px">检查中…</div>
    <div class="q-actions">
      <button class="btn small primary" data-act="go">开始导出</button>
      <button class="btn small" data-act="cancel">取消</button>
    </div>`;
  document.body.appendChild(pop);
  const rect = el.exportBtn.getBoundingClientRect();
  pop.style.left = `${Math.min(rect.left + window.scrollX - 160, window.innerWidth - 340)}px`;
  pop.style.top = `${rect.bottom + window.scrollY + 6}px`;
  const close = () => { pop.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!pop.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);
  renderChecklist(pop.querySelector("#exportPopChecklist")); // 异步展示，不阻断
  pop.querySelector('[data-act="cancel"]').addEventListener("click", close);
  pop.querySelector('[data-act="go"]').addEventListener("click", async () => {
    close();
    el.exportBtn.disabled = true;
    el.exportResult.hidden = true;
    try {
      await postJson(withCut(`/api/projects/${state.projectId}/export`), planBody());
      setStatus("导出已开始（后台运行）…");
      pollExport();
    } catch (error) {
      setStatus(error.message, "error");
      el.exportBtn.disabled = false;
    }
  });
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
        <a href="${withCut(`/media/${state.projectId}/exports/${encodeURIComponent(job.video_name)}`)}">下载视频</a> ·
        <a href="${withCut(`/media/${state.projectId}/exports/${encodeURIComponent(job.srt_name)}`)}">下载字幕 SRT</a><br>
        <span style="color:var(--muted)">${escapeHtml(job.video || "")}</span>`;
    } else if (job.status === "error") {
      setStatus(`导出失败：${job.error}`, "error");
    }
  } catch (error) {
    el.exportBtn.disabled = false;
    setStatus(error.message, "error");
  }
}
