/* 成片方案（Cut）切换条：一个项目多个方案（完整精剪/主题切片/混剪…）并存。
   数据契约（B8）：GET /api/projects/{id}/cuts → {cuts:[{name,label,...}]}
   POST /api/projects/{id}/cuts {name,label,from: blank|copy:<cut>|topic:<topic_id>}
   文稿建方案（B9）：POST /api/projects/{id}/cuts/from-script {name,label,script,ai} */
import { $, el, state, api, postJson, escapeHtml, setStatus } from "./shared.js";
import { showEditor } from "./editor.js";

export async function loadCuts() {
  if (!state.projectId) return;
  try {
    const payload = await api(`/api/projects/${encodeURIComponent(state.projectId)}/cuts`);
    state.cuts = payload.cuts || [];
  } catch {
    state.cuts = [{ name: "default", label: "默认方案" }]; // 旧后端兼容
  }
  if (!state.cuts.some((cut) => cut.name === state.cutName)) state.cutName = "default";
  renderCutBar();
}

export function renderCutBar() {
  if (!state.projectId) { el.cutBar.hidden = true; return; }
  el.cutBar.hidden = false;
  const pills = state.cuts.map((cut) => `
    <button class="cut-pill ${cut.name === state.cutName ? "active" : ""}" data-cut="${escapeHtml(cut.name)}">
      ${escapeHtml(cut.label || cut.name)}${cut.has_export ? " ⬇" : ""}
    </button>`).join("");
  el.cutBar.innerHTML = `
    <span class="cut-label">成片方案：</span>${pills}
    <button class="btn small" id="cutNewBtn">＋ 新建</button>
    <button class="btn small" id="cutScriptBtn" title="粘贴外部 AI 挑好/排好的成片文稿，自动对回原视频生成方案">📄 从文稿新建</button>
    <span class="cut-form" id="cutNewForm" hidden>
      <input type="text" class="settings-input cut-input" id="cutNameInput" placeholder="方案名（小写字母数字-）">
      <select id="cutFromSel" class="settings-input cut-input">
        <option value="blank">空白</option>
        <option value="copy">复制当前方案</option>
      </select>
      <button class="btn small primary" id="cutCreateBtn">创建</button>
      <button class="btn small" id="cutCancelBtn">取消</button>
    </span>`;
  el.cutBar.querySelectorAll(".cut-pill").forEach((pill) => {
    pill.addEventListener("click", async () => {
      if (pill.dataset.cut === state.cutName) return;
      state.cutName = pill.dataset.cut;
      state.order = [];
      state.viewOriginal = false;
      await showEditor();
      renderCutBar();
      setStatus(`已切换到方案「${pill.dataset.cut}」。`);
    });
  });
  $("cutNewBtn").addEventListener("click", () => { $("cutNewForm").hidden = !$("cutNewForm").hidden; });
  $("cutCancelBtn")?.addEventListener("click", () => { $("cutNewForm").hidden = true; });
  $("cutCreateBtn")?.addEventListener("click", async () => {
    const name = $("cutNameInput").value.trim();
    if (!name) return;
    const from = $("cutFromSel").value === "copy" ? `copy:${state.cutName}` : "blank";
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/cuts`, { name, label: name, from });
      state.cutName = name;
      state.order = [];
      await loadCuts();
      await showEditor();
      setStatus(`方案「${name}」已创建并切换。`);
    } catch (error) {
      setStatus(`创建方案失败：${error.message}`, "error");
    }
  });
  $("cutScriptBtn").addEventListener("click", openScriptDialog);
}

/* 从文稿新建方案：粘贴文稿 → 对齐反算 → 新 Cut + 对齐报告。 */
function openScriptDialog() {
  document.querySelector(".script-dialog")?.remove();
  const dlg = document.createElement("div");
  dlg.className = "script-dialog";
  dlg.innerHTML = `
    <div class="script-dialog-box">
      <h4>从文稿生成成片方案</h4>
      <div class="meta">粘贴外部 AI（或你自己）挑好/排好的成片文字。系统按原话对回视频：错字/标点/格式差异自动容错；改写过找不到原话的段落会列进报告让你裁决，不会硬凑。</div>
      <input type="text" class="settings-input" id="scriptCutName" placeholder="新方案名（如 waigao-v1）">
      <textarea class="prompt-editor" id="scriptText" placeholder="每个空行分段；段落顺序=成片顺序"></textarea>
      <label class="trim-auto"><input type="checkbox" id="scriptAi" checked>拿不准的段落交 AI 裁决（推荐）</label>
      <div class="prompt-actions">
        <button class="btn primary" id="scriptGoBtn">生成方案</button>
        <button class="btn" id="scriptCloseBtn">取消</button>
      </div>
      <div class="settings-note" id="scriptResult"></div>
    </div>`;
  document.body.appendChild(dlg);
  dlg.querySelector("#scriptCloseBtn").addEventListener("click", () => dlg.remove());
  dlg.querySelector("#scriptGoBtn").addEventListener("click", async () => {
    const name = dlg.querySelector("#scriptCutName").value.trim();
    const script = dlg.querySelector("#scriptText").value.trim();
    const box = dlg.querySelector("#scriptResult");
    if (!name || !script) { box.textContent = "请填方案名并粘贴文稿。"; return; }
    box.textContent = "对齐中（含 AI 裁决时约需十几秒）…";
    try {
      const result = await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/cuts/from-script`, {
        name, label: name, script, ai: dlg.querySelector("#scriptAi").checked,
      });
      const report = result.report || {};
      const stats = report.stats || {};
      const unmatched = (report.paragraphs || []).filter((p) => p.status === "unmatched");
      state.cutName = (result.cut && result.cut.name) || (typeof result.cut === "string" ? result.cut : name);
      state.order = [];
      await loadCuts();
      await showEditor();
      dlg.remove();
      const warn = unmatched.length ? `；⚠ ${unmatched.length} 段未匹配（原视频里没有这段话），详见状态提示` : "";
      setStatus(`文稿方案「${state.cutName}」已生成：自动匹配 ${stats.auto ?? "?"} 段、AI 裁决 ${stats.ai ?? 0} 段${warn}。`, unmatched.length ? "warn" : "");
    } catch (error) {
      box.textContent = `❌ ${error.message}`;
    }
  });
}
