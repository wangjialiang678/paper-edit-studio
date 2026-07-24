/* 成片方案（Cut）切换条：一个项目多个方案（完整精剪/主题切片/混剪…）并存。
   数据契约（B8）：GET /api/projects/{id}/cuts → {cuts:[{name,label,...}]}
   POST /api/projects/{id}/cuts {name,label,from: blank|copy:<cut>|topic:<topic_id>}
   文稿建方案（B9）：POST /api/projects/{id}/cuts/from-script {name,label,script,ai} */
import { $, el, state, pb, api, postJson, escapeHtml, setStatus } from "./shared.js";
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

/* 方案显示名：default 内部名不变，界面显示「默认方案」。 */
function cutLabel(cut) {
  if (cut.name === "default") return cut.label && cut.label !== "default" ? cut.label : "默认方案";
  return cut.label || cut.name;
}

export function renderCutBar() {
  if (!state.projectId) { el.cutBar.innerHTML = ""; return; }
  const pills = state.cuts.map((cut) => `
    <button class="cut-pill ${cut.name === state.cutName ? "active" : ""}" data-cut="${escapeHtml(cut.name)}" title="${escapeHtml(cutLabel(cut))}（右键：改名/删除）">
      ${escapeHtml(cutLabel(cut))}${cut.has_export ? " ⬇" : ""}
    </button>`).join("");
  el.cutBar.innerHTML = `${pills}
    <button class="cut-pill cut-new" id="cutNewMenuBtn" title="新方案：AI 智能剪辑 / 粘贴剪辑稿 / 复制当前方案">＋ 新方案 ▾</button>`;
  el.cutBar.querySelectorAll(".cut-pill[data-cut]").forEach((pill) => {
    pill.addEventListener("click", async () => {
      if (pill.dataset.cut === state.cutName) return;
      state.cutName = pill.dataset.cut;
      state.order = [];
      state.viewOriginal = false;
      await showEditor();
      // 切方案后自动从这个方案的开头试听，马上感受剪出来的效果
      const label = cutLabel(state.cuts.find((c) => c.name === pill.dataset.cut) || { name: pill.dataset.cut });
      if (pb.ranges.length) {
        pb.audition = null;
        pb.rangeIndex = 0;
        el.video.currentTime = pb.ranges[0].start_ms / 1000;
        el.video.play();
        setStatus(`已切换到方案「${label}」，正在从头试听成片。`);
      } else {
        setStatus(`已切换到方案「${label}」（还没有保留句，勾选或跑 AI 智能剪辑后可试听）。`);
      }
    });
    // 右键 = 该方案的上下文菜单（改名/删除），比行尾 ⋯ 更顺手（2026-07-24 用户反馈）
    pill.addEventListener("contextmenu", (event) => {
      event.preventDefault();
      openCutContextMenu(pill.dataset.cut, event.clientX, event.clientY);
    });
  });
  $("cutNewMenuBtn").addEventListener("click", openNewPlanMenu);
}

/* ＋ 新方案：三种来源归一处（AI 智能剪辑 / 粘贴剪辑稿 / 复制当前方案）。 */
function openNewPlanMenu() {
  document.querySelector(".newplan-pop")?.remove();
  const menu = document.createElement("div");
  menu.className = "q-popover newplan-pop newplan-menu";
  menu.innerHTML = `
    <button class="btn small" data-act="ai">🤖 AI 智能剪辑（分主题 · 挑金句 · 筛句子）</button>
    <button class="btn small" data-act="script">📄 粘贴我的剪辑稿（对回视频）</button>
    <button class="btn small" data-act="copy" title="带着当前方案的全部手工调整派生一个变体">⧉ 复制当前方案</button>`;
  document.body.appendChild(menu);
  const rect = $("cutNewMenuBtn").getBoundingClientRect();
  menu.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 320)}px`;
  menu.style.top = `${rect.bottom + window.scrollY + 6}px`;
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!menu.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);
  menu.querySelector('[data-act="ai"]').addEventListener("click", () => {
    close();
    el.aiPanel.hidden = true;
    el.aiPanelBtn.click();
  });
  menu.querySelector('[data-act="script"]').addEventListener("click", () => { close(); openScriptDialog(); });
  menu.querySelector('[data-act="copy"]').addEventListener("click", async () => {
    close();
    const name = prompt("新方案名（小写字母数字-）：", `${state.cutName}-v2`.slice(0, 32));
    if (!name) return;
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/cuts`,
        { name: name.trim(), label: name.trim(), from: `copy:${state.cutName}` });
      state.cutName = name.trim();
      state.order = [];
      await loadCuts();
      await showEditor();
      setStatus(`已复制出方案「${name.trim()}」并切换（原方案的手工调整都带过来了）。`);
    } catch (error) {
      setStatus(`复制方案失败：${error.message}`, "error");
    }
  });
}

/* 方案右键菜单：作用于被右键的那个方案（不必先切换过去）。默认方案禁改名/删除。 */
function openCutContextMenu(cutName, x, y) {
  document.querySelector(".cut-menu")?.remove();
  const menu = document.createElement("div");
  menu.className = "q-popover cut-menu";
  const cut = state.cuts.find((c) => c.name === cutName) || { name: cutName };
  const isDefault = cutName === "default";
  menu.innerHTML = `
    <div class="q-pop-text">方案「${escapeHtml(cutLabel(cut))}」</div>
    <div class="q-actions" style="flex-direction:column;align-items:stretch">
      <button class="btn small" data-act="rename" ${isDefault ? "disabled title=\"默认方案不改名\"" : ""}>✏ 更名</button>
      <button class="btn small" data-act="copy" title="带着该方案的全部手工调整派生一个变体">⧉ 复制一份</button>
      <button class="btn small" data-act="delete" ${isDefault ? "disabled title=\"默认方案不可删除\"" : ""}>🗑 删除</button>
    </div>`;
  document.body.appendChild(menu);
  menu.style.left = `${Math.min(x + window.scrollX, window.innerWidth - 220)}px`;
  menu.style.top = `${y + window.scrollY + 4}px`;
  const close = () => { menu.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!menu.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);

  menu.querySelector('[data-act="rename"]').addEventListener("click", async () => {
    if (isDefault) return;
    close();
    const label = prompt("方案显示名：", cut.label || cutName);
    if (label === null || !label.trim()) return;
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/plan?cut=${encodeURIComponent(cutName)}`, { rows: [], label: label.trim() });
      await loadCuts();
      setStatus(`方案已更名为「${label.trim()}」。`);
    } catch (error) {
      setStatus(`更名失败：${error.message}`, "error");
    }
  });
  menu.querySelector('[data-act="copy"]').addEventListener("click", async () => {
    close();
    const name = prompt("新方案名（小写字母数字-）：", `${cutName}-v2`.slice(0, 32));
    if (!name) return;
    try {
      await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/cuts`,
        { name: name.trim(), label: name.trim(), from: `copy:${cutName}` });
      state.cutName = name.trim();
      state.order = [];
      await loadCuts();
      await showEditor();
      setStatus(`已复制出方案「${name.trim()}」并切换（原方案的手工调整都带过来了）。`);
    } catch (error) {
      setStatus(`复制方案失败：${error.message}`, "error");
    }
  });
  menu.querySelector('[data-act="delete"]').addEventListener("click", async () => {
    if (isDefault) return;
    close();
    if (!confirm(`删除方案「${cutLabel(cut)}」？其勾选/微调/导出都会删除（其他方案不受影响）。`)) return;
    try {
      await api(`/api/projects/${encodeURIComponent(state.projectId)}/cuts/${encodeURIComponent(cutName)}`, { method: "DELETE" });
      if (state.cutName === cutName) {
        state.cutName = "default";
        state.order = [];
        await showEditor();
      }
      await loadCuts();
      setStatus(`方案「${cutLabel(cut)}」已删除。`);
    } catch (error) {
      setStatus(`删除失败：${error.message}`, "error");
    }
  });
}

/* 套用已有剪辑稿：粘贴文稿 → 对齐反算 → 新 Cut + 对齐报告。（AI 面板的外部稿入口也用它） */
export function openScriptDialog() {
  document.querySelector(".script-dialog")?.remove();
  const dlg = document.createElement("div");
  dlg.className = "script-dialog";
  dlg.innerHTML = `
    <div class="script-dialog-box">
      <h4>套用已有剪辑稿</h4>
      <div class="meta">你已经有一份排好的成片文字（自己写的，或别的 AI 挑好/排好的）？粘进来，系统按原话对回视频：错字/标点/格式差异自动容错；改写过、找不到原话的段落会列进报告让你裁决，不会硬凑。</div>
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
