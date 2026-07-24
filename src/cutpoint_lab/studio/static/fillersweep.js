/* AI 句内气口深扫：点「✂ 剪气口」时同步触发——规则建议秒剪，AI 建议异步返回后
   合入 row.suggested_cuts（kind:"ai"）并自动补剪，与规则剪除共用同一撤销批次。

   数据契约（server filler_sweep）：
   POST /api/projects/{id}/filler-sweep/analyze?cut=...  启动异步任务
   GET  /api/projects/{id}/filler-sweep/report?cut=...   → {status, suggestions:
        [{segment_id, start_token, end_token, kind:"ai", text}], dropped} */
import { state, api, postJson, setStatus } from "./shared.js";
import { applyAllSuggestedCuts } from "./rows.js";

const POLL_INTERVAL_MS = 3000;
const POLL_LIMIT = 40; // 最长约 2 分钟

let running = false;

export async function runAiFillerSweep() {
  if (running || !state.projectId) return;
  running = true;
  const projectId = state.projectId;
  const cutName = state.cutName;
  const base = `/api/projects/${encodeURIComponent(projectId)}/filler-sweep`;
  const cutParam = `cut=${encodeURIComponent(cutName)}`;
  try {
    setStatus("规则气口已剪；AI 正在深扫句内残留气口，完成后自动补剪（整批可撤销）…");
    try {
      await postJson(`${base}/analyze?${cutParam}`, {});
    } catch (err) {
      // 同 Cut 已有任务在跑：直接转入轮询等它的结果。
      if (!String(err?.message || err).includes("已在运行")) throw err;
    }
    const report = await pollReport(`${base}/report?${cutParam}`);
    if (!report) {
      setStatus("AI 气口深扫超时，本次跳过（不影响已剪的规则气口）。", "warn");
      return;
    }
    if (report.status === "error") {
      setStatus(`AI 气口深扫失败：${report.error || "未知错误"}（不影响已剪的规则气口）。`, "warn");
      return;
    }
    // 项目或方案已切走：结果不再适用，静默丢弃。
    if (state.projectId !== projectId || state.cutName !== cutName) return;
    const added = mergeSuggestions(report.suggestions || []);
    if (!added) {
      setStatus("AI 气口深扫完成：规则建议之外没有新的句内气口。");
      return;
    }
    const spans = applyAllSuggestedCuts({
      extend: true,
      statusPrefix: "AI 气口深扫补剪",
      quietWhenEmpty: true,
    });
    if (!spans) setStatus("AI 气口深扫完成：建议与已删除内容重叠，无新增剪除。");
  } catch (err) {
    setStatus(`AI 气口深扫未完成：${err?.message || err}（不影响已剪的规则气口）。`, "warn");
  } finally {
    running = false;
  }
}

async function pollReport(path) {
  for (let attempt = 0; attempt < POLL_LIMIT; attempt += 1) {
    await new Promise((resolve) => setTimeout(resolve, POLL_INTERVAL_MS));
    let report;
    try {
      report = await api(path);
    } catch {
      continue; // 单次网络抖动不终止轮询
    }
    if (report && report.status && report.status !== "running") return report;
  }
  return null;
}

/* 把 AI 建议合入对应行的 suggested_cuts；跳过与已有建议重叠的区间。返回新增条数。 */
function mergeSuggestions(suggestions) {
  let added = 0;
  for (const item of suggestions) {
    const row = state.rows.find((candidate) => candidate.id === item.segment_id);
    if (!row || !row.checked || !row.has_word_timestamps) continue;
    const start = Number(item.start_token);
    const end = Number(item.end_token);
    const count = (row.tokens || []).length;
    if (!Number.isInteger(start) || !Number.isInteger(end) || start > end) continue;
    if (start < 0 || end >= count || (start === 0 && end === count - 1)) continue;
    const overlaps = (row.suggested_cuts || []).some(
      (span) => span.start_token <= end && span.end_token >= start,
    );
    if (overlaps) continue;
    row.suggested_cuts = [
      ...(row.suggested_cuts || []),
      { start_token: start, end_token: end, kind: "ai", text: String(item.text || "") },
    ];
    added += 1;
  }
  return added;
}
