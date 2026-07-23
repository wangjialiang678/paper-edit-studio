/* 时长预算条 + 导出前检查清单（V2）。

   数据契约（B10）：
   GET  /api/projects/{id}/budget?cut=  → {target_s, tolerance_s, estimated_ms, delta_ms, rows:[...]}
   POST /api/projects/{id}/budget/fit?cut= {strategy} → {suggestions:[{id,ms,role,reason}], projected_ms, infeasible?}
   PUT  /api/projects/{id}/brief?cut=   → 更新目标时长等
   GET  /api/projects/{id}/export-checklist?cut= → {items:[{key,ok,detail}], ok}

   预算是硬约束但绝不静默删：fit 只给建议列表，应用=取消勾选（走现有保存通道），锁定句永不进列表。 */
import { el, state, api, postJson, putJson, escapeHtml, setStatus, withCut } from "./shared.js";
import { renderRows } from "./rows.js";
import { scheduleAutosave } from "./plan.js";

const STRATEGY_LABELS = {
  strict: "严格时长（必须达标）",
  complete: "完整表达（只删冗余/支撑）",
  keep_quotes: "保金句（金句主张全保）",
};

let backendReady = true; // 首次 404/失败后停止打扰（旧后端兼容）

export async function refreshBudget() {
  if (!state.projectId || !backendReady) return;
  try {
    state.budget = await api(withCut(`/api/projects/${encodeURIComponent(state.projectId)}/budget`));
  } catch (error) {
    if (/not found|404|请求失败/i.test(error.message)) backendReady = false; // 旧后端：整套预算功能隐藏
    state.budget = null;
  }
  renderBudgetChip();
}

export function resetBudget() {
  state.budget = null;
  backendReady = true;
  renderBudgetChip();
}

function renderBudgetChip() {
  const chip = el.budgetChip;
  if (!chip) return;
  const budget = state.budget;
  if (!budget || budget.target_s == null) {
    // 无目标时长：显示设置入口（弱化）
    chip.hidden = !state.projectId || !backendReady;
    chip.className = "budget-chip idle";
    chip.innerHTML = "⏱ 设目标时长";
    return;
  }
  const est = Math.round((budget.estimated_ms || 0) / 1000);
  const delta = est - budget.target_s;
  const tol = budget.tolerance_s || 0;
  const ok = Math.abs(delta) <= tol || delta <= 0;
  chip.hidden = false;
  chip.className = `budget-chip ${ok ? "ok" : "over"}`;
  chip.innerHTML = `⏱ 目标 ${budget.target_s}s · 预计 ${est}s（${delta >= 0 ? "+" : ""}${delta}s ${ok ? "✅" : "⚠"}）`;
}

function openBudgetPopover() {
  document.querySelector(".budget-popover")?.remove();
  const budget = state.budget;
  const pop = document.createElement("div");
  pop.className = "q-popover budget-popover";
  const target = budget?.target_s ?? "";
  const tol = budget?.tolerance_s ?? 10;
  pop.innerHTML = `
    <div class="q-pop-text"><b>时长预算</b></div>
    <div class="budget-form">
      目标 <input type="number" id="budgetTargetInput" class="settings-input" style="width:70px" value="${escapeHtml(String(target))}" placeholder="秒"> s
      ± <input type="number" id="budgetTolInput" class="settings-input" style="width:50px" value="${escapeHtml(String(tol))}"> s
      <button class="btn small primary" id="budgetSaveBtn">保存</button>
    </div>
    ${budget?.target_s != null ? `
    <div class="meta" style="margin-top:6px">超预算时让 AI 给「可删清单」（只建议，锁定金句永不动）：</div>
    <div class="q-actions">
      ${Object.entries(STRATEGY_LABELS).map(([key, label]) =>
        `<button class="btn small" data-strategy="${key}">${label}</button>`).join("")}
    </div>` : ""}
    <div class="settings-note" id="budgetFitResult"></div>
    <div class="q-actions"><button class="btn small" data-act="close">关闭</button></div>`;
  document.body.appendChild(pop);
  const rect = el.budgetChip.getBoundingClientRect();
  pop.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 340)}px`;
  pop.style.top = `${rect.bottom + window.scrollY + 6}px`;
  const close = () => { pop.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!pop.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);
  pop.querySelector('[data-act="close"]').addEventListener("click", close);

  pop.querySelector("#budgetSaveBtn").addEventListener("click", async () => {
    const targetValue = pop.querySelector("#budgetTargetInput").value.trim();
    const tolValue = pop.querySelector("#budgetTolInput").value.trim();
    try {
      await putJson(withCut(`/api/projects/${encodeURIComponent(state.projectId)}/brief`), {
        target_duration_s: targetValue === "" ? null : Number(targetValue),
        tolerance_s: tolValue === "" ? null : Number(tolValue),
      });
      await refreshBudget();
      close();
      setStatus(targetValue === "" ? "已清除目标时长。" : `目标时长已设为 ${targetValue}s ± ${tolValue || 0}s。`);
    } catch (error) {
      pop.querySelector("#budgetFitResult").textContent = `❌ ${error.message}`;
    }
  });

  pop.querySelectorAll("[data-strategy]").forEach((button) => {
    button.addEventListener("click", async () => {
      const box = pop.querySelector("#budgetFitResult");
      box.textContent = "计算中…";
      try {
        const result = await postJson(withCut(`/api/projects/${encodeURIComponent(state.projectId)}/budget/fit`),
          { strategy: button.dataset.strategy });
        renderFitResult(pop, result);
      } catch (error) {
        box.textContent = `❌ ${error.message}`;
      }
    });
  });
}

function renderFitResult(pop, result) {
  const box = pop.querySelector("#budgetFitResult");
  const suggestions = result.suggestions || [];
  if (!suggestions.length) {
    box.textContent = result.infeasible
      ? `⚠ 就算删光可删句仍超 ${Math.round((result.projected_ms - (state.budget.target_s * 1000)) / 1000)}s——考虑放宽目标或手动删锁定内容。`
      : "✅ 已在预算内，无需删减。";
    return;
  }
  const naturalIndex = new Map(state.rows.map((row, i) => [row.id, i + 1]));
  box.innerHTML = `
    <div class="meta">建议删除 ${suggestions.length} 句（共 −${Math.round(suggestions.reduce((s, x) => s + (x.ms || 0), 0) / 1000)}s → 预计 ${Math.round((result.projected_ms || 0) / 1000)}s）${result.infeasible ? "，仍不达标 ⚠" : ""}：</div>
    ${suggestions.map((s) => `<div class="fit-item">#${naturalIndex.get(s.id) || "?"} ${escapeHtml(roleLabel(s.role))} −${Math.round((s.ms || 0) / 1000)}s ${escapeHtml(s.reason || "")}</div>`).join("")}
    <button class="btn small primary" id="budgetApplyBtn">一键应用（取消勾选这 ${suggestions.length} 句，可再手动捞回）</button>`;
  box.querySelector("#budgetApplyBtn").addEventListener("click", () => {
    const ids = new Set(suggestions.map((s) => s.id));
    for (const row of state.rows) if (ids.has(row.id)) row.checked = false;
    if (state.order.length) state.order = state.order.filter((id) => !ids.has(id));
    renderRows();
    scheduleAutosave();
    refreshBudgetSoon();
    setStatus(`已按建议取消勾选 ${suggestions.length} 句（列表里划线灰显，勾回即恢复）。`);
    pop.remove();
  });
}

export function roleLabel(role) {
  return { quote: "⭐金句", claim: "主张", background: "背景", support: "支撑", filler: "冗余" }[role] || role || "";
}

// ---------- 导出前检查清单 ----------

export async function renderChecklist(container) {
  if (!backendReady) { container.innerHTML = ""; return; }
  try {
    // 注意：此响应的 ok 字段语义是「检查是否全部通过」，不能走 api()（会把 ok:false 当请求失败）。
    const response = await fetch(withCut(`/api/projects/${encodeURIComponent(state.projectId)}/export-checklist`));
    if (!response.ok) { container.innerHTML = ""; return; }
    const checklist = await response.json();
    const items = checklist.items || [];
    if (!items.length) { container.innerHTML = ""; return; }
    container.innerHTML = `<div class="checklist">${items.map((item) =>
      `<span class="check-item ${item.ok === true ? "ok" : item.ok === false ? "bad" : "skip"}" title="${escapeHtml(item.detail || "")}">${item.ok === true ? "✅" : item.ok === false ? "⚠" : "－"} ${escapeHtml(checkLabel(item.key))}</span>`
    ).join("")}</div>`;
  } catch {
    container.innerHTML = "";
  }
}

function checkLabel(key) {
  return {
    topics_confirmed: "主题已确认",
    duration: "时长达标",
    quotes_locked: "金句在位",
    background_covered: "背景已露出",
  }[key] || key;
}

// ---------- 事件装配 ----------

let refreshTimer = null;
function refreshBudgetSoon() {
  if (refreshTimer) clearTimeout(refreshTimer);
  refreshTimer = setTimeout(refreshBudget, 1200);
}

el.budgetChip?.addEventListener("click", () => {
  if (!state.projectId) return;
  openBudgetPopover();
});

/* 计划保存成功后刷新预算（plan.js 派发）。 */
document.addEventListener("plan-synced", refreshBudgetSoon);
