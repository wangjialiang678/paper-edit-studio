/* 项目列表、导入（文件/路径）、流水线进度视图。 */
import { el, state, api, postJson, escapeHtml, fmtClock, STAGE_LABELS } from "./shared.js";
import { resetPlayback } from "./player.js";
import { showEditor } from "./editor.js";
import { clearFillerBatch } from "./rows.js";
import { resetBudget } from "./budget.js";

/* 项目抽屉（覆盖层）：选完项目自动收回，平时不占空间。 */
export function openDrawer() {
  el.projectDrawer.hidden = false;
  el.drawerMask.hidden = false;
}
export function closeDrawer() {
  el.projectDrawer.hidden = true;
  el.drawerMask.hidden = true;
}

/* 顶栏项目身份（全局层唯一信息） */
export function updateAppbar() {
  const project = state.project;
  el.appbarName.textContent = project?.name || project?.id || "Paper Edit Studio";
  el.appbarMeta.textContent = project
    ? `${fmtClock(project.duration_ms || state.sourceDurationMs || 0)} · ${state.rows.length || "…"} 句`
    : "";
}

export async function refreshProjects() {
  try {
    const { projects } = await api("/api/projects");
    el.railProjectCount.textContent = String(projects.length);
    el.railProjectCount.hidden = !projects.length;
    el.projectList.innerHTML = "";
    for (const project of projects) {
      const item = document.createElement("div");
      item.className = `project-item ${project.id === state.projectId ? "active" : ""}`;
      const meta = project.error
        ? `<div class="p-meta err">失败：${escapeHtml(String(project.error).slice(0, 60))}</div>`
        : `<div class="p-meta">${escapeHtml(project.stage_message || project.stage || "")}</div>`;
      item.innerHTML = `<div class="p-name">${escapeHtml(project.name || project.id)}</div>${meta}`;
      item.addEventListener("click", () => selectProject(project.id));
      el.projectList.appendChild(item);
    }
  } catch (error) {
    console.error(error);
  }
}

function uploadFile(file) {
  el.uploadProgress.hidden = false;
  const xhr = new XMLHttpRequest();
  xhr.open("POST", `/api/projects/upload?filename=${encodeURIComponent(file.name)}`);
  xhr.upload.onprogress = (event) => {
    if (event.lengthComputable) {
      const pct = Math.round((event.loaded / event.total) * 100);
      el.uploadBar.style.width = `${pct}%`;
      el.uploadPct.textContent = `${pct}%`;
    }
  };
  xhr.onload = () => {
    el.uploadProgress.hidden = true;
    el.uploadBar.style.width = "0";
    try {
      const payload = JSON.parse(xhr.responseText);
      if (xhr.status >= 400 || payload.ok === false) throw new Error(payload.error || "上传失败");
      refreshProjects();
      selectProject(payload.id);
    } catch (error) {
      alert(error.message);
    }
  };
  xhr.onerror = () => { el.uploadProgress.hidden = true; alert("上传失败"); };
  xhr.send(file);
}

el.pickFileBtn.addEventListener("click", () => el.fileInput.click());
el.fileInput.addEventListener("change", () => { if (el.fileInput.files[0]) uploadFile(el.fileInput.files[0]); el.fileInput.value = ""; });
el.importZone.addEventListener("dragover", (event) => { event.preventDefault(); el.importZone.classList.add("dragover"); });
el.importZone.addEventListener("dragleave", () => el.importZone.classList.remove("dragover"));
el.importZone.addEventListener("drop", (event) => {
  event.preventDefault();
  el.importZone.classList.remove("dragover");
  const file = event.dataTransfer.files && event.dataTransfer.files[0];
  if (file) uploadFile(file);
});
el.importPathBtn.addEventListener("click", async () => {
  const path = el.pathInput.value.trim();
  if (!path) return;
  try {
    const payload = await postJson("/api/projects/import-path", { path });
    el.pathInput.value = "";
    refreshProjects();
    selectProject(payload.id);
  } catch (error) {
    alert(error.message);
  }
});

export async function selectProject(projectId) {
  state.projectId = projectId;
  state.orderedGroups = null;
  state.order = [];
  state.viewOriginal = false;
  state.cutName = "default";
  state.contentMap = null;
  state.contentMapAiRunning = false;
  state.quotes = null;
  state.quotesAiRunning = false;
  state.budget = null;
  resetBudget(); // 重新探测预算后端（换项目/后端升级后恢复）
  resetPlayback();
  clearFillerBatch();
  clearTimers();
  el.exportResult.hidden = true;
  closeDrawer(); // 选完项目抽屉自动收回
  await refreshProjects();
  await pollProjectOnce();
}

export function clearTimers() {
  if (state.pollTimer) { clearTimeout(state.pollTimer); state.pollTimer = null; }
  if (state.exportTimer) { clearTimeout(state.exportTimer); state.exportTimer = null; }
  for (const key of Object.keys(state.aiPollTimers)) { clearTimeout(state.aiPollTimers[key]); delete state.aiPollTimers[key]; }
}

async function pollProjectOnce() {
  if (!state.projectId) return;
  try {
    const project = await api(`/api/projects/${state.projectId}`);
    state.project = project;
    updateAppbar();
    if (project.stage === "ready" && project.transcript_ready) {
      showEditor();
      return;
    }
    showPipeline(project);
    if (project.stage !== "error") {
      state.pollTimer = setTimeout(pollProjectOnce, 2000);
    }
  } catch (error) {
    showPipeline({ stage: "error", error: error.message, name: state.projectId });
  }
}

export function showView(name) {
  el.emptyView.hidden = name !== "empty";
  el.pipelineView.hidden = name !== "pipeline";
  el.editorView.hidden = name !== "editor";
  el.topicsView.hidden = name !== "topics";
}

function showPipeline(project) {
  showView("pipeline");
  el.pipelineTitle.textContent = `${project.name || ""} · ${project.stage_message || "处理中…"}`;
  el.stageList.innerHTML = "";
  const stageIndex = STAGE_LABELS.findIndex(([key]) => key === project.stage);
  STAGE_LABELS.forEach(([key, label], index) => {
    const li = document.createElement("li");
    li.textContent = label;
    if (project.stage === "error") {
      if (index < stageIndex) li.className = "done";
    } else if (index < stageIndex || project.stage === "ready") {
      li.className = "done";
      li.textContent = `✓ ${label}`;
    } else if (index === stageIndex) {
      li.className = "current";
    }
    el.stageList.appendChild(li);
  });
  const failed = project.stage === "error";
  el.pipelineError.hidden = !failed;
  if (failed) el.pipelineErrorText.textContent = project.error || "未知错误";
}

el.retryBtn.addEventListener("click", async () => {
  try {
    await postJson(`/api/projects/${state.projectId}/retry`);
    pollProjectOnce();
  } catch (error) {
    alert(error.message);
  }
});
