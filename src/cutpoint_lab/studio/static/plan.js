/* 剪辑计划：payload 组装、保存与自动保存。order（EDL 成片顺序）为唯一排序权威。 */
import { el, state, postJson, setStatus, withCut } from "./shared.js";
import { setRanges } from "./player.js";

export function planBody() {
  // 切点策略不再由前端指定，服务端使用工程默认 hybrid_valley。
  return {
    rows: state.rows.map((row) => {
      const item = { id: row.id, checked: row.checked, text: row.text };
      if (row.trim) item.trim = row.trim;
      if (row.nudge && (row.nudge.start_ms || row.nudge.end_ms)) item.nudge = row.nudge;
      if (row.cuts && row.cuts.length) item.cuts = row.cuts;
      if (row.role) item.role = row.role;       // 角色（⭐金句/主张/背景…），时长预算的输入
      if (row.locked) item.locked = true;       // 锁定=重跑 AI 不得无提示替换
      return item;
    }),
    order: state.order.length ? state.order : undefined,
  };
}

export function hasSelection() {
  if (state.order.length) return true;
  return state.rows.some((row) => row.checked);
}

/* 保存剪辑计划并刷新成片播放范围；无选中时清空。 */
export async function syncPlan() {
  if (!state.projectId || !hasSelection()) { setRanges([]); return null; }
  const payload = await postJson(withCut(`/api/projects/${state.projectId}/plan`), planBody());
  setRanges(payload.plan.ranges || []);
  document.dispatchEvent(new CustomEvent("plan-synced")); // budget.js 刷新预算条
  return payload;
}

let autosaveTimer = null;
export function scheduleAutosave() {
  if (autosaveTimer) clearTimeout(autosaveTimer);
  autosaveTimer = setTimeout(async () => {
    autosaveTimer = null;
    if (!state.projectId) return;
    try {
      await syncPlan();
    } catch (error) {
      setStatus(`自动保存失败：${error.message}`, "warn");
    }
  }, 800);
}

/* 立即保存（微调/拖拽后需要用最新 ranges 做接缝试听时使用）。 */
export async function flushPlanNow() {
  if (autosaveTimer) { clearTimeout(autosaveTimer); autosaveTimer = null; }
  return syncPlan();
}
