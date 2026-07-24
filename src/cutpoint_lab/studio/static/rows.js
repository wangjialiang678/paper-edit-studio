/* 字幕行列表：渲染、勾选、文本编辑、静默段标记、统计。

   行文本有两种显示模式（.row-text-slot 内切换）：
   - 无句内删除：可编辑 textarea（原行为）；
   - 有 trim/cuts：富渲染视图——整句词块就地显示，被剪词划删除线，
     点删除线词=恢复、点正常词=剪掉，双击切回 textarea 改文字。
   trim.js 的 applyStruck 通过 row-struck-changed 事件通知本模块局部刷新，
   避免 rows↔trim 模块环，也不破坏打开中的微调面板。 */
import { el, state, pb, fmtClock, escapeHtml, autoGrow, markActive, setStatus, api, putJson } from "./shared.js";
import { rangeIndexForRow, auditionRange, segmentBaseId } from "./player.js";
import { scheduleAutosave } from "./plan.js";
import { toggleTrimPanel, applySuggestedCuts, struckSet, applyStruck } from "./trim.js";
import { openIssuesBySegment, acceptIssue, ignoreIssue } from "./quality.js";

let issuesCache = {}; // segment_id → open issues（renderRows 时刷新）

/* 成片顺序辅助：order 非空时它同时决定"保留哪些句"与"输出顺序"（EDL 语义）。 */
export function orderActive() {
  return state.order.length > 0;
}

function outputPositions() {
  // id → 首次输出位（1 起）；重复引用只记首位，徽章另示 ×N
  const pos = {};
  state.order.forEach((id, index) => { if (!(id in pos)) pos[id] = index + 1; });
  return pos;
}

export function renderRows() {
  issuesCache = openIssuesBySegment();
  el.rows.innerHTML = "";
  if (renderPlanEmptyState()) return; // 空态即引导：没有剪辑方案时区域本身解释做什么
  el.rows.hidden = false;
  const orderedView = orderActive() && !state.viewOriginal;
  el.origOrderBtn.hidden = !orderActive();
  el.origOrderBtn.textContent = state.viewOriginal ? "▶ 回到输出顺序" : "↩ 回看原始顺序";
  el.orderedBanner.hidden = !orderActive();

  if (orderedView) {
    // 输出顺序视图：按 order 排列（重复引用合并显示），未进成片的句子后置
    const seen = new Set();
    const naturalIndex = new Map(state.rows.map((row, i) => [row.id, i + 1]));
    let outPos = 0;
    for (const id of state.order) {
      outPos += 1;
      if (seen.has(id)) continue;
      seen.add(id);
      const row = state.rows.find((item) => item.id === id);
      if (!row) continue;
      const dupCount = state.order.filter((x) => x === id).length;
      el.rows.appendChild(rowNode(row, { outPos, naturalPos: naturalIndex.get(id), dupCount }));
    }
    const rest = state.rows.filter((row) => !seen.has(row.id));
    if (rest.length) {
      const divider = document.createElement("div");
      divider.className = "silence-row order-divider";
      divider.innerHTML = `<span class="pill">未进成片 ${rest.length} 句</span><span>勾选即追加到成片末尾</span>`;
      el.rows.appendChild(divider);
      for (const row of rest) el.rows.appendChild(rowNode(row, { unordered: true }));
    }
  } else {
    const silenceAfter = new Map();
    let headSilence = null;
    for (const gap of state.silences) {
      if (gap.after_segment_id) silenceAfter.set(gap.after_segment_id, gap);
      else headSilence = gap;
    }
    if (headSilence) el.rows.appendChild(silenceNode(headSilence));
    const positions = orderActive() ? outputPositions() : {};
    for (const row of state.rows) {
      el.rows.appendChild(rowNode(row, orderActive() ? { outPosHint: positions[row.id] } : {}));
      const gap = silenceAfter.get(row.id);
      if (gap) el.rows.appendChild(silenceNode(gap));
    }
  }
  refreshStats();
}

/* 空态判定：只有 default 一个方案、AI 初剪还没落、无自定义顺序 = 全新项目。
   引导写在空态里，布局不增删元素（2026-07-24 拍板：反对时有时无的引导 chrome）。 */
function renderPlanEmptyState() {
  const pristine = state.cuts.length <= 1
    && !state.rows.some((row) => row.ai_keep === true || row.ai_keep === false)
    && !orderActive()
    && !state.planEmptyDismissed;
  el.planEmpty.hidden = !pristine;
  el.rows.hidden = pristine;
  if (!pristine) return false;
  el.planEmpty.innerHTML = `
    <h3>这条视频还没有剪辑方案</h3>
    <p>让 AI 通读字幕出方案（分主题 → 挑金句放开头 → 按目标时长筛句子），<br>或把你在别处（Codex / GPT…）排好的剪辑稿粘进来。<br>出了方案后在这张表里逐句精修。</p>
    <div class="cta">
      <button class="cta-main" id="emptyAiBtn">🤖 AI 智能剪辑</button>
      <button class="cta-alt" id="emptyScriptBtn">📄 粘贴我的剪辑稿</button>
    </div>
    <span class="cta-skip" id="emptySkipBtn">不用 AI，直接手工剪 →</span>`;
  el.planEmpty.querySelector("#emptyAiBtn").addEventListener("click", () => {
    el.aiPanel.hidden = true;
    el.aiPanelBtn.click();
  });
  el.planEmpty.querySelector("#emptyScriptBtn").addEventListener("click", async () => {
    const cutsModule = await import("./cuts.js");
    cutsModule.openScriptDialog();
  });
  el.planEmpty.querySelector("#emptySkipBtn").addEventListener("click", () => {
    state.planEmptyDismissed = true;
    renderRows();
  });
  refreshStats();
  return true;
}

function silenceNode(gap) {
  const div = document.createElement("div");
  div.className = "silence-row";
  div.innerHTML = `<span class="pill">无声 ${(gap.gap_ms / 1000).toFixed(2)}s</span><span>剪辑时自动移除</span>`;
  return div;
}

const ROLE_LABELS = { claim: "主张", background: "背景", support: "支撑", filler: "冗余" };

function badgesHtml(row) {
  const badges = [];
  // ⭐ 金句角色：点击切换（role=quote + locked，重跑 AI 不得动）；其余角色只展示
  if (row.role === "quote") {
    badges.push('<button class="btn tiny role-star active" title="已锁定为金句（点击取消）">⭐ 金句·锁定</button>');
  } else {
    if (row.role && ROLE_LABELS[row.role]) badges.push(`<span class="badge role">${ROLE_LABELS[row.role]}</span>`);
    if (row.checked) badges.push('<button class="btn tiny role-star" title="设为金句：锁定保留，时长预算优先，重跑 AI 不得替换">☆</button>');
  }
  // AI 保留/删除不再出徽标（勾选状态+划线已表达，纯增视觉噪音）；删除理由仍显示在行下
  if ((row.ai_labels || []).includes("golden_quote") && row.role !== "quote") badges.push('<span class="badge quote">金句</span>');
  if (row.trim || row.nudge || (row.cuts || []).length) badges.push('<span class="badge trimmed">✂ 已微调</span>');
  if ((row.suggested_cuts || []).length) badges.push(`<span class="badge suggest">气口 ×${row.suggested_cuts.length}</span>`);
  if ((issuesCache[row.id] || []).length) badges.push(`<span class="badge qissue" title="本句有 ${issuesCache[row.id].length} 处疑似识别错字，打开 🔎 字幕校对处理">疑似错字 ×${issuesCache[row.id].length}</span>`);
  if (row.has_word_timestamps) badges.push('<button class="btn tiny trim-toggle" title="句内微调：删词/剪气口/拖切点">✂ 微调</button>');
  const reason = row.ai_reason ? `<div class="row-reason">${escapeHtml(row.ai_reason)}</div>` : "";
  return badges.join("") + reason;
}

/* 行内只标「有修改建议」的（2026-07-24 拍板 suggest-only）：
   高亮的黄金法则——能点、点了有用才配高亮；纯低置信（尤其英文词，fun-asr 置信度天然低）
   不再画波浪线，只进 🔎 字幕校对面板统计。 */
function tokenIssues(row) {
  return (issuesCache[row.id] || []).filter((issue) =>
    issue.span && issue.span.token_start != null
    && issue.suggestion
    && !/^[\x00-\x7F]+$/.test(issue.span.text || "")); // 纯 ASCII（英文/数字）豁免
}

function hasInlineStruck(row) {
  const struck = row.trim || (row.cuts || []).length;
  return Boolean(row.has_word_timestamps && (struck || tokenIssues(row).length) && !row._editText);
}

/* 行文本槽位渲染：textarea 或 划线视图。 */
function renderRowText(row, slot) {
  slot.innerHTML = "";
  if (hasInlineStruck(row)) {
    const view = document.createElement("div");
    view.className = "row-text render";
    view.title = "点删除线词=恢复；点正常词=剪掉；双击改文字";
    const struck = struckSet(row);
    const suggested = new Set();
    for (const span of row.suggested_cuts || []) {
      for (let i = span.start_token; i <= span.end_token; i++) suggested.add(i);
    }
    const issueByToken = new Map();
    for (const issue of tokenIssues(row)) {
      for (let i = issue.span.token_start; i <= (issue.span.token_end ?? issue.span.token_start); i++) {
        if (!issueByToken.has(i)) issueByToken.set(i, issue);
      }
    }
    row.tokens.forEach((token, index) => {
      const tok = document.createElement("span");
      const issue = issueByToken.get(index);
      const issueClass = issue ? (issue.kind === "ref_mismatch" ? " ref" : " suspect") : "";
      const extra = struck.has(index) ? " cut" : suggested.has(index) ? " suggest" : issueClass;
      tok.className = `tok${extra}`;
      tok.textContent = token.text;
      if (issue && !struck.has(index)) tok.title = `质检：${issue.reason || issue.kind}（点击查看）`;
      tok.addEventListener("click", (event) => {
        event.stopPropagation();
        if (issue && !struck.has(index)) { showIssuePopover(tok, issue); return; }
        const next = struckSet(row);
        if (next.has(index)) next.delete(index);
        else next.add(index);
        applyStruck(row, next); // 成功后经 row-struck-changed 事件刷新本行
      });
      view.appendChild(tok);
    });
    view.addEventListener("dblclick", (event) => {
      event.stopPropagation();
      row._editText = true;
      renderRowText(row, slot);
      slot.querySelector("textarea")?.focus();
    });
    slot.appendChild(view);
    return;
  }
  const textarea = document.createElement("textarea");
  textarea.className = "row-text";
  textarea.spellcheck = false;
  textarea.value = row.text;
  requestAnimationFrame(() => autoGrow(textarea));
  textarea.addEventListener("focus", () => { row._preEdit = row.text; });
  textarea.addEventListener("input", () => { row.text = textarea.value; autoGrow(textarea); scheduleAutosave(); });
  textarea.addEventListener("blur", () => {
    const before = row._preEdit;
    delete row._preEdit;
    if (row._editText) { delete row._editText; renderRowText(row, slot); }
    if (before !== undefined && before !== row.text) offerReplaceEverywhere(row, before, row.text);
  });
  slot.appendChild(textarea);
}

// ---------- 改一处，提示改全部 ----------

/* 从一次行内编辑提取"单点替换对"候选：
   前后缀对齐得最小改动段（如 我们→咱们 缩成 我→咱、超导→超脑 缩成 导→脑），
   直接用最小对批量替换会误伤别处（每个"我"都换掉），
   所以再用左右各一个公共词字符组装扩展候选（我们→咱们、超导→超脑、张三丰→张三峰），
   由调用方选"在其他句真有命中且最长"的候选。 */
const WORD_CHAR = /[\p{Script=Han}A-Za-z0-9]/u;

function extractReplacementCandidates(before, after) {
  if (!before || !after || before === after) return [];
  let prefix = 0;
  while (prefix < before.length && prefix < after.length && before[prefix] === after[prefix]) prefix++;
  let suffix = 0;
  while (
    suffix < before.length - prefix && suffix < after.length - prefix &&
    before[before.length - 1 - suffix] === after[after.length - 1 - suffix]
  ) suffix++;
  const oldCore = before.slice(prefix, before.length - suffix);
  const newCore = after.slice(prefix, after.length - suffix);
  if (!oldCore.trim() || !newCore.trim()) return []; // 纯插入/纯删除不算替换
  const left = prefix > 0 && WORD_CHAR.test(before[prefix - 1]) ? before[prefix - 1] : "";
  const right = suffix > 0 && WORD_CHAR.test(before[before.length - suffix]) ? before[before.length - suffix] : "";
  const candidates = [];
  const push = (o, n) => {
    if (!o.trim() || !n.trim() || o === n) return;
    if (o.length > 20 || n.length > 20) return;
    if (/\n/.test(o) || /\n/.test(n)) return;
    candidates.push({ oldWord: o, newWord: n });
  };
  push(left + oldCore + right, left + newCore + right);
  push(left + oldCore, left + newCore);
  push(oldCore + right, newCore + right);
  push(oldCore, newCore);
  return candidates; // 已按从长到短排列
}

function offerReplaceEverywhere(editedRow, before, after) {
  let pair = null;
  let others = [];
  let count = 0;
  for (const candidate of extractReplacementCandidates(before, after)) {
    const hits = state.rows.filter((row) => row.id !== editedRow.id && row.text.includes(candidate.oldWord));
    if (!hits.length) continue;
    pair = candidate;
    others = hits;
    count = hits.reduce((sum, row) => sum + row.text.split(candidate.oldWord).length - 1, 0);
    break; // 候选从长到短，取最长且有命中的
  }
  if (!pair || !count) return;
  const bar = el.replaceBar;
  bar.hidden = false;
  bar.innerHTML = `
    <span>「<b>${escapeHtml(pair.oldWord)}</b>」在其他句还出现 ${count} 处，全部改为「<b>${escapeHtml(pair.newWord)}</b>」？</span>
    <button class="btn small primary" data-act="all">全部替换</button>
    <button class="btn small" data-act="dict">替换并加入纠错词典</button>
    <button class="btn small" data-act="dismiss">忽略</button>`;
  const close = () => { bar.hidden = true; bar.innerHTML = ""; };
  const replaceAll = () => {
    for (const row of others) row.text = row.text.split(pair.oldWord).join(pair.newWord);
    renderRows();
    scheduleAutosave();
    setStatus(`已把其余 ${count} 处「${pair.oldWord}」替换为「${pair.newWord}」。`);
  };
  bar.querySelector('[data-act="all"]').addEventListener("click", () => { replaceAll(); close(); });
  bar.querySelector('[data-act="dismiss"]').addEventListener("click", close);
  bar.querySelector('[data-act="dict"]').addEventListener("click", async () => {
    replaceAll();
    try {
      const dict = await api("/api/settings/corrections");
      const pairs = dict.pairs || [];
      const hit = pairs.find((item) => item.right === pair.newWord);
      if (hit) { if (!hit.wrong.includes(pair.oldWord)) hit.wrong.push(pair.oldWord); }
      else pairs.push({ wrong: [pair.oldWord], right: pair.newWord, is_term: true });
      await putJson("/api/settings/corrections", { pairs });
      bar.innerHTML = `
        <span>已加入纠错词典（${escapeHtml(pair.oldWord)} → ${escapeHtml(pair.newWord)}），下次可一键批量纠错。把「<b>${escapeHtml(pair.newWord)}</b>」加入热词表，从源头减少识别错误？</span>
        <button class="btn small primary" data-act="hotword">加入热词表</button>
        <button class="btn small" data-act="dismiss2">不用了</button>`;
      bar.querySelector('[data-act="dismiss2"]').addEventListener("click", close);
      bar.querySelector('[data-act="hotword"]').addEventListener("click", async () => {
        try {
          const vocab = await api("/api/settings/vocabulary");
          const items = vocab.items || [];
          if (!items.some((item) => item.text === pair.newWord)) items.push({ text: pair.newWord, weight: 4 });
          await putJson("/api/settings/vocabulary", { items });
          setStatus(`「${pair.newWord}」已加入热词表，下次转写生效。`);
        } catch (error) {
          setStatus(`加入热词表失败：${error.message}`, "warn");
        }
        close();
      });
    } catch (error) {
      setStatus(`加入纠错词典失败：${error.message}`, "warn");
      close();
    }
  });
}

function bindBadgeActions(row, div) {
  const trimBtn = div.querySelector(".trim-toggle");
  if (trimBtn) trimBtn.addEventListener("click", (event) => { event.stopPropagation(); toggleTrimPanel(row, div); });
  const starBtn = div.querySelector(".role-star");
  if (starBtn) starBtn.addEventListener("click", (event) => {
    event.stopPropagation();
    if (row.role === "quote") {
      delete row.role;
      delete row.locked;
      unpromoteQuote(row);
      setStatus(`「#${row.index}」已取消金句：开头的置顶引用已移除，本句回到原位。`);
      renderRows(); // 顺序变了，整表刷新
    } else {
      row.role = "quote";
      row.locked = true;
      row.checked = true;
      promoteQuote(row);
      setStatus(`「#${row.index}」已设为金句：已复制到成片开头（原位保留，🔒 重跑 AI 不会动它）。`);
      renderRows();
    }
    scheduleAutosave();
  });
}

/* 金句置顶 = 复制到 order 头部（原位保留，EDL 允许重复 id）；取消金句 = 移除头部那份引用。 */
export function promoteQuote(row) {
  if (!orderActive()) {
    state.order = state.rows.filter((item) => item.checked).map((item) => item.id);
  }
  if (state.order[0] !== row.id) state.order = [row.id, ...state.order];
}

function unpromoteQuote(row) {
  if (!orderActive()) return;
  // 只当头部是它、且后面还有一份（说明头部是置顶复制）时才移除头部
  if (state.order[0] === row.id && state.order.indexOf(row.id, 1) !== -1) {
    state.order = state.order.slice(1);
  }
}

function rowNode(row, meta = {}) {
  const div = document.createElement("div");
  div.className = `subtitle-row ${row.checked ? "" : "dropped"}`;
  div.dataset.id = row.id;
  const posBits = [];
  if (meta.outPos) {
    posBits.push(`<span class="pos-out">▶${meta.outPos}</span>`);
    if (meta.naturalPos && meta.naturalPos !== meta.outPos) posBits.push(`<span class="pos-orig" title="原始位置">原#${meta.naturalPos}</span>`);
    if (meta.dupCount > 1) posBits.push(`<span class="pos-orig" title="在成片中重复引用">×${meta.dupCount}</span>`);
  } else if (meta.outPosHint) {
    posBits.push(`<span class="pos-out" title="成片输出位置">→${meta.outPosHint}</span>`);
  }
  const draggable = row.checked && !meta.unordered;
  div.innerHTML = `
    <label class="row-check">${draggable ? '<span class="drag-handle" title="拖动调整成片顺序">⠿</span>' : '<span class="drag-handle drag-off">·</span>'}<input type="checkbox" ${row.checked ? "checked" : ""}><span>#${row.index}</span>${posBits.join("")}</label>
    <div class="row-time">${row.start}<br>${row.end}</div>
    <div class="row-text-slot"></div>
    <div class="row-badges">${badgesHtml(row)}</div>`;
  renderRowText(row, div.querySelector(".row-text-slot"));
  const handle = div.querySelector(".drag-handle:not(.drag-off)");
  if (handle) bindRowDrag(handle, div, row);
  const checkbox = div.querySelector("input");
  checkbox.addEventListener("change", () => {
    row.checked = checkbox.checked;
    div.classList.toggle("dropped", !row.checked);
    if (orderActive()) {
      if (row.checked && !state.order.includes(row.id)) {
        state.order = [...state.order, row.id]; // 追加到成片末尾
        setStatus(`「#${row.index}」已追加到成片末尾（可拖 ⠿ 调整位置）。`);
      } else if (!row.checked) {
        state.order = state.order.filter((id) => id !== row.id);
      }
      renderRows();
    }
    refreshStats();
    scheduleAutosave();
  });
  bindBadgeActions(row, div);
  div.addEventListener("click", (event) => {
    if (event.target.closest(".trim-panel")) return;
    const tag = event.target.tagName;
    if (tag === "TEXTAREA" || tag === "INPUT" || tag === "LABEL" || tag === "SPAN" || tag === "BUTTON" || tag === "CANVAS") return;
    markActive(row.id);
    if (pb.mode === "edited") {
      if (row.checked) {
        pb.audition = null;
        const index = rangeIndexForRow(row);
        if (index >= 0) {
          pb.rangeIndex = index;
          const target = pb.ranges[index];
          // 本句就是该 range 的开头时，从真实切点（含策略吸附与手动微移）起播，
          // 让句首微调在点击试听时立即可感知；否则从句子首词起播。
          const firstId = (target.source_segment_ids || [])[0];
          const fromMs = segmentBaseId(firstId) === row.id
            ? target.start_ms
            : Math.max(target.start_ms, Math.min(row.start_ms, target.end_ms));
          el.video.currentTime = fromMs / 1000;
        } else {
          el.video.currentTime = row.start_ms / 1000;
          scheduleAutosave();
        }
        el.video.play();
      } else {
        auditionRange(row.start_ms, row.end_ms, "试听已删除句：播完自动停，按空格回到成片。");
      }
    } else {
      el.video.currentTime = row.start_ms / 1000;
      el.video.play();
    }
  });
  return div;
}

// ---------- 拖拽调序（⠿ 手柄）：写 EDL.order，预览/导出/SRT 全部跟随 ----------

function bindRowDrag(handle, div, row) {
  handle.addEventListener("pointerdown", (event) => {
    event.preventDefault();
    event.stopPropagation();
    // 首次拖动且尚无自定义顺序：以"当前勾选句的原始顺序"为初值
    if (!orderActive()) {
      state.order = state.rows.filter((item) => item.checked).map((item) => item.id);
    }
    const hadDuplicates = new Set(state.order).size !== state.order.length;
    div.classList.add("dragging");
    const move = (e) => {
      const siblings = [...el.rows.querySelectorAll(".subtitle-row")].filter((n) => n !== div);
      let target = null;
      for (const node of siblings) {
        const rect = node.getBoundingClientRect();
        if (e.clientY < rect.top + rect.height / 2) { target = node; break; }
      }
      if (target) el.rows.insertBefore(div, target);
      else el.rows.appendChild(div);
    };
    const up = () => {
      window.removeEventListener("pointermove", move);
      window.removeEventListener("pointerup", up);
      div.classList.remove("dragging");
      // 从 DOM 顺序重建 order（仅勾选句）
      const checkedSet = new Set(state.rows.filter((item) => item.checked).map((item) => item.id));
      state.order = [...el.rows.querySelectorAll(".subtitle-row")]
        .map((node) => node.dataset.id)
        .filter((id) => checkedSet.has(id));
      if (hadDuplicates) setStatus("提示：拖动调序后，重复引用（×N）已合并为单次出现。", "warn");
      state.viewOriginal = false;
      renderRows();
      scheduleAutosave();
      setStatus(`成片顺序已更新：「#${row.index}」移到第 ${state.order.indexOf(row.id) + 1} 位。预览与导出将按新顺序。`);
    };
    window.addEventListener("pointermove", move);
    window.addEventListener("pointerup", up);
  });
}

/* 质检高亮词的处置浮层：采纳/忽略/跳过。 */
function showIssuePopover(anchor, issue) {
  document.querySelector(".q-popover")?.remove();
  const pop = document.createElement("div");
  pop.className = "q-popover";
  pop.innerHTML = `
    <div class="q-pop-text">「${escapeHtml(issue.span?.text || "")}」${issue.suggestion ? ` → 「${escapeHtml(issue.suggestion)}」` : ""}</div>
    <div class="meta">${escapeHtml(issue.reason || "")}</div>
    <div class="q-actions">
      ${issue.suggestion ? '<button class="btn small primary" data-act="accept">采纳</button>' : ""}
      <button class="btn small" data-act="ignore">忽略</button>
    </div>`;
  document.body.appendChild(pop);
  const rect = anchor.getBoundingClientRect();
  pop.style.left = `${Math.min(rect.left + window.scrollX, window.innerWidth - 280)}px`;
  pop.style.top = `${rect.bottom + window.scrollY + 6}px`;
  const close = () => { pop.remove(); document.removeEventListener("pointerdown", outside, true); };
  const outside = (event) => { if (!pop.contains(event.target)) close(); };
  document.addEventListener("pointerdown", outside, true);
  pop.querySelector('[data-act="ignore"]').addEventListener("click", () => { ignoreIssue(issue); close(); });
  pop.querySelector('[data-act="accept"]')?.addEventListener("click", () => { acceptIssue(issue); close(); });
}

/* 质检报告更新 → 整表重渲染（徽章与高亮跟着变）。 */
document.addEventListener("quality-report-updated", () => {
  if (state.rows.length) renderRows();
});

/* trim.js 通知：某行的删除集合变了 → 局部刷新文本槽与徽章（不动微调面板）。 */
document.addEventListener("row-struck-changed", (event) => {
  issuesCache = openIssuesBySegment(); // 采纳/忽略质检项后徽章计数同步
  const row = state.rows.find((item) => item.id === event.detail.id);
  const node = el.rows.querySelector(`.subtitle-row[data-id="${CSS.escape(event.detail.id)}"]`);
  if (!row || !node) return;
  renderRowText(row, node.querySelector(".row-text-slot"));
  const badges = node.querySelector(".row-badges");
  if (badges) { badges.innerHTML = badgesHtml(row); bindBadgeActions(row, node); }
});

export function refreshStats() {
  let keptMs = 0;
  let keptCount = 0;
  if (orderActive()) {
    const durations = new Map(state.rows.map((row) => [row.id, Math.max(0, row.end_ms - row.start_ms)]));
    for (const id of state.order) { keptCount += 1; keptMs += durations.get(id) || 0; }
  } else {
    for (const row of state.rows) {
      if (row.checked) { keptCount += 1; keptMs += Math.max(0, row.end_ms - row.start_ms); }
    }
  }
  el.statDuration.textContent = fmtClock(keptMs);
  el.statKept.textContent = String(keptCount);
}

// ---------- 一键剪气口 + 整批撤销 ----------
let lastFillerBatch = null; // { snapshots: Map(id → {trim, cuts, text}) }

export function applyAllSuggestedCuts() {
  const snapshots = new Map();
  let rowsTouched = 0;
  let spans = 0;
  for (const row of state.rows) {
    if (!row.checked || !(row.suggested_cuts || []).length) continue;
    const before = {
      trim: row.trim ? { ...row.trim } : undefined,
      cuts: (row.cuts || []).map((c) => ({ ...c })),
      text: row.text,
    };
    const applied = applySuggestedCuts(row);
    if (applied) { rowsTouched += 1; spans += applied; snapshots.set(row.id, before); }
  }
  if (rowsTouched) {
    lastFillerBatch = { snapshots };
    renderRows();
    el.undoCutFillersBtn.hidden = false;
    setStatus(`一键剪气口：${rowsTouched} 句共剪除 ${spans} 处——被剪词在句中以删除线显示，点它可单独恢复，或点「撤销剪气口」整批还原。`);
  } else {
    setStatus("没有可应用的气口建议。");
  }
}

export function undoFillerBatch() {
  if (!lastFillerBatch) return;
  let restored = 0;
  for (const [id, before] of lastFillerBatch.snapshots) {
    const row = state.rows.find((item) => item.id === id);
    if (!row) continue;
    if (before.trim) row.trim = before.trim; else delete row.trim;
    if (before.cuts.length) row.cuts = before.cuts; else delete row.cuts;
    row.text = before.text;
    restored += 1;
  }
  lastFillerBatch = null;
  el.undoCutFillersBtn.hidden = true;
  renderRows();
  scheduleAutosave();
  setStatus(`已撤销剪气口（还原 ${restored} 句）。`);
}

export function clearFillerBatch() {
  lastFillerBatch = null;
  el.undoCutFillersBtn.hidden = true;
}
