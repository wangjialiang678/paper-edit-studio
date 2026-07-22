/* 设置面板：DashScope API Key（脱敏/修改/测试）+ 热词表（查看/编辑/新建）+ 纠错词典。 */
import { $, el, state, api, putJson, postJson, escapeHtml, setStatus } from "./shared.js";
import { showEditor } from "./editor.js";

const ui = { settings: null, vocab: null, loadingVocab: false, corrections: null, lastChangesetId: null };

const SOURCE_LABELS = {
  process_env: "进程环境变量（优先于 .env，改 .env 需先取消 export）",
  dotenv: "仓库根 .env 文件",
  api_vault: "~/.claude/api-vault.env（本机密钥保险库）",
  missing: "未配置",
};

el.settingsBtn.addEventListener("click", () => {
  const opening = el.settingsPanel.hidden;
  el.settingsPanel.hidden = !opening;
  if (opening) { el.aiPanel.hidden = true; loadSettings(); }
});
el.settingsCloseBtn.addEventListener("click", () => { el.settingsPanel.hidden = true; });

async function loadSettings() {
  el.settingsBody.innerHTML = '<div class="ai-hint">加载设置…</div>';
  try {
    ui.settings = await api("/api/settings");
    renderSettings();
    if (ui.settings.vocabulary_id) loadVocabulary();
  } catch (error) {
    el.settingsBody.innerHTML = `<div class="ai-warning">加载设置失败：${escapeHtml(error.message)}<br>（后端需要重启到最新版本）</div>`;
  }
}

function renderSettings() {
  const data = ui.settings || {};
  const key = data.dashscope_key || {};
  const llm = data.llm || {};
  el.settingsBody.innerHTML = `
    <div class="ai-card">
      <h4>阿里云 DashScope API Key</h4>
      <div class="meta">语音识别与 AI 选段共用。当前：<b>${escapeHtml(key.masked || "未配置")}</b><br>
      来源：${escapeHtml(SOURCE_LABELS[key.source] || key.source || "未知")}</div>
      <input type="password" class="settings-input" id="apiKeyInput" placeholder="粘贴新的 API Key（sk-…）">
      <div class="prompt-actions">
        <button class="btn primary" id="apiKeySaveBtn">保存到 .env</button>
        <button class="btn" id="apiKeyTestBtn">测试</button>
      </div>
      <div class="settings-note" id="apiKeyResult"></div>
    </div>
    <div class="ai-card">
      <h4>AI 选段模型</h4>
      <div class="meta">模型：${escapeHtml(llm.model || "-")}<br>
      接口：${escapeHtml(llm.base_url || "-")}<br>
      Key 来源：${escapeHtml(llm.key_name || "-")} · ${escapeHtml(SOURCE_LABELS[llm.key_source] || llm.key_source || "-")}</div>
    </div>
    <div class="ai-card">
      <h4>纠错词典（批量修正识别错误）</h4>
      <div class="meta">常见识别错误的"错词 → 正词"映射，跨项目全局生效。错词可填多个（逗号分隔），如：web coding, web courting → vibe coding。</div>
      <div id="correctionsArea"><div class="ai-hint">加载词典…</div></div>
    </div>
    <div class="ai-card">
      <h4>ASR 热词表</h4>
      <div class="meta">存于阿里云云端，本机只记录 ID。多人协作时各自新建词表（ID 不同）即互不影响。<br>
      当前 ID：<b id="vocabIdLabel">${escapeHtml(data.vocabulary_id || "未配置")}</b></div>
      <div id="vocabArea">${data.vocabulary_id ? '<div class="ai-hint">加载词表…</div>' : '<button class="btn" id="vocabCreateBtn">＋ 创建热词表</button>'}</div>
    </div>`;
  $("apiKeySaveBtn").addEventListener("click", saveApiKey);
  $("apiKeyTestBtn").addEventListener("click", testApiKey);
  const createBtn = $("vocabCreateBtn");
  if (createBtn) createBtn.addEventListener("click", () => { ui.vocab = { vocabulary_id: null, items: [] }; renderVocab(true); });
  loadCorrections();
}

// ---------- 纠错词典 ----------

async function loadCorrections() {
  try {
    ui.corrections = await api("/api/settings/corrections");
    renderCorrections();
  } catch (error) {
    const area = $("correctionsArea");
    if (area) area.innerHTML = `<div class="ai-warning">加载词典失败：${escapeHtml(error.message)}</div>`;
  }
}

function renderCorrections() {
  const area = $("correctionsArea");
  if (!area || !ui.corrections) return;
  const pairs = ui.corrections.pairs || [];
  const rows = pairs.map((pair, index) => `
    <div class="vocab-row" data-i="${index}">
      <input type="text" class="settings-input c-wrong" value="${escapeHtml(pair.wrong.join(", "))}" placeholder="错词（逗号分隔）">
      <span class="corr-arrow">→</span>
      <input type="text" class="settings-input c-right" value="${escapeHtml(pair.right)}" placeholder="正词">
      <label class="corr-term" title="是专有名词（应用后建议加热词）"><input type="checkbox" class="c-term" ${pair.is_term ? "checked" : ""}>术语</label>
      <button class="btn small c-del" title="删除">✕</button>
    </div>`).join("");
  const projectActions = state.projectId ? `
      <button class="btn small" id="corrPreviewBtn">预览当前项目命中</button>
      <button class="btn small primary" id="corrApplyBtn">应用到当前项目</button>
      ${ui.lastChangesetId ? '<button class="btn small" id="corrUndoBtn">撤销上次应用</button>' : ""}` : `
      <span class="settings-note">打开某个项目后可在此预览/应用。</span>`;
  area.innerHTML = `
    <div class="vocab-list">${rows || '<div class="ai-hint">词典为空。</div>'}</div>
    <div class="prompt-actions">
      <button class="btn small" id="corrAddBtn">＋ 添加纠错</button>
      <button class="btn small primary" id="corrSaveBtn">保存词典</button>
    </div>
    <div class="prompt-actions">${projectActions}</div>
    <div class="settings-note" id="corrResult"></div>`;
  const readBack = () => {
    ui.corrections.pairs = [...area.querySelectorAll(".vocab-row")].map((node) => ({
      wrong: node.querySelector(".c-wrong").value.split(/[,，]/).map((s) => s.trim()).filter(Boolean),
      right: node.querySelector(".c-right").value.trim(),
      is_term: node.querySelector(".c-term").checked,
    })).filter((pair) => pair.wrong.length && pair.right);
  };
  area.querySelectorAll(".c-del").forEach((button) => {
    button.addEventListener("click", () => {
      readBack();
      ui.corrections.pairs.splice(Number(button.parentElement.dataset.i), 1);
      renderCorrections();
    });
  });
  $("corrAddBtn").addEventListener("click", () => {
    readBack();
    ui.corrections.pairs.push({ wrong: [], right: "", is_term: true });
    renderCorrections();
    const inputs = area.querySelectorAll(".c-wrong");
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  $("corrSaveBtn").addEventListener("click", async () => {
    readBack();
    const box = $("corrResult");
    box.textContent = "保存中…";
    try {
      ui.corrections = await putJson("/api/settings/corrections", { pairs: ui.corrections.pairs });
      box.textContent = `✅ 词典已保存（${(ui.corrections.pairs || []).length} 组）。`;
    } catch (error) {
      box.textContent = `❌ ${error.message}`;
    }
  });
  const previewBtn = $("corrPreviewBtn");
  if (previewBtn) previewBtn.addEventListener("click", async () => {
    const box = $("corrResult");
    box.textContent = "统计中…";
    try {
      const preview = await api(`/api/projects/${encodeURIComponent(state.projectId)}/quality/corrections-preview`);
      if (!preview.total) { box.textContent = "当前项目没有词典命中。"; return; }
      const lines = (preview.items || []).map((item) =>
        `「${escapeHtml(item.wrong)}」→「${escapeHtml(item.right)}」 ×${item.count}`).join("<br>");
      box.innerHTML = `命中 ${preview.total} 处：<br>${lines}`;
    } catch (error) {
      box.textContent = `❌ ${error.message}`;
    }
  });
  const applyBtn = $("corrApplyBtn");
  if (applyBtn) applyBtn.addEventListener("click", async () => {
    const box = $("corrResult");
    box.textContent = "应用中…";
    try {
      const result = await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quality/apply-corrections`, {});
      ui.lastChangesetId = result.changeset_id;
      await showEditor();
      renderCorrections();
      $("corrResult").textContent = `✅ 已替换 ${result.applied} 处（changeset ${result.changeset_id}），可点「撤销上次应用」还原。`;
    } catch (error) {
      box.textContent = `❌ ${error.message}`;
    }
  });
  const undoBtn = $("corrUndoBtn");
  if (undoBtn) undoBtn.addEventListener("click", async () => {
    const box = $("corrResult");
    box.textContent = "撤销中…";
    try {
      const result = await postJson(`/api/projects/${encodeURIComponent(state.projectId)}/quality/undo/${encodeURIComponent(ui.lastChangesetId)}`, {});
      ui.lastChangesetId = null;
      await showEditor();
      renderCorrections();
      $("corrResult").textContent = `✅ 已撤销（还原 ${result.reverted} 处${(result.skipped || []).length ? `，跳过 ${result.skipped.length} 处已再次改动的行` : ""}）。`;
    } catch (error) {
      box.textContent = `❌ ${error.message}`;
    }
  });
}

async function saveApiKey() {
  const input = $("apiKeyInput");
  const resultBox = $("apiKeyResult");
  const value = input.value.trim();
  if (!value) { resultBox.textContent = "请输入 API Key。"; return; }
  resultBox.textContent = "保存中…";
  try {
    const result = await putJson("/api/settings/apikey", { key: value });
    resultBox.textContent = result.warning ? `已保存。⚠ ${result.warning}` : "已保存到 .env，立即生效。";
    input.value = "";
    ui.settings = await api("/api/settings");
    renderSettings();
    $("apiKeyResult").textContent = result.warning ? `已保存。⚠ ${result.warning}` : "已保存到 .env，立即生效。";
  } catch (error) {
    resultBox.textContent = `保存失败：${error.message}`;
  }
}

async function testApiKey() {
  const input = $("apiKeyInput");
  const resultBox = $("apiKeyResult");
  resultBox.textContent = "测试中…";
  try {
    const body = input.value.trim() ? { key: input.value.trim() } : {};
    const result = await postJson("/api/settings/apikey/test", body);
    const vocabNote = result.vocab_access === true ? "；热词表权限 ✓"
      : result.vocab_access === false ? "；热词表权限 ✗" : "";
    resultBox.textContent = result.ok ? `✅ ${result.detail || "Key 有效"}${vocabNote}` : `❌ ${result.detail || "Key 无效"}`;
  } catch (error) {
    resultBox.textContent = `测试失败：${error.message}`;
  }
}

// ---------- 热词表 ----------

async function loadVocabulary() {
  if (ui.loadingVocab) return;
  ui.loadingVocab = true;
  try {
    ui.vocab = await api("/api/settings/vocabulary");
    renderVocab(false);
  } catch (error) {
    const area = $("vocabArea");
    if (area) area.innerHTML = `<div class="ai-warning">拉取词表失败：${escapeHtml(error.message)}</div>`;
  } finally {
    ui.loadingVocab = false;
  }
}

function renderVocab(isNew) {
  const area = $("vocabArea");
  if (!area || !ui.vocab) return;
  const items = ui.vocab.items || [];
  const rows = items.map((item, index) => `
    <div class="vocab-row" data-i="${index}">
      <input type="text" class="settings-input v-text" value="${escapeHtml(item.text || "")}" placeholder="热词（如 人名/术语）">
      <input type="number" class="settings-input v-weight" value="${Number(item.weight) || 4}" min="1" max="5" title="权重 1-5">
      <button class="btn small v-del" title="删除">✕</button>
    </div>`).join("");
  area.innerHTML = `
    <div class="vocab-list">${rows || '<div class="ai-hint">词表为空。</div>'}</div>
    <div class="prompt-actions">
      <button class="btn small" id="vocabAddBtn">＋ 添加热词</button>
      <button class="btn small primary" id="vocabSaveBtn">${isNew ? "创建词表" : "保存词表"}</button>
      <button class="btn small" id="vocabReloadBtn" ${isNew ? "hidden" : ""}>重新拉取</button>
    </div>
    <div class="settings-note" id="vocabResult">共 ${items.length} 词（单表上限 500）。中文单词 ≤15 字，权重 1–5。</div>`;
  area.querySelectorAll(".v-text").forEach((input, index) => {
    input.addEventListener("input", () => { ui.vocab.items[index].text = input.value; });
  });
  area.querySelectorAll(".v-weight").forEach((input, index) => {
    input.addEventListener("input", () => { ui.vocab.items[index].weight = Number(input.value) || 4; });
  });
  area.querySelectorAll(".v-del").forEach((button) => {
    button.addEventListener("click", () => {
      ui.vocab.items.splice(Number(button.parentElement.dataset.i), 1);
      renderVocab(isNew);
    });
  });
  $("vocabAddBtn").addEventListener("click", () => {
    ui.vocab.items.push({ text: "", weight: 4, lang: "zh" });
    renderVocab(isNew);
    const inputs = area.querySelectorAll(".v-text");
    if (inputs.length) inputs[inputs.length - 1].focus();
  });
  $("vocabSaveBtn").addEventListener("click", async () => {
    const resultBox = $("vocabResult");
    const cleaned = ui.vocab.items.filter((item) => (item.text || "").trim());
    resultBox.textContent = isNew ? "创建中…" : "保存中…";
    try {
      const result = await putJson("/api/settings/vocabulary", { items: cleaned, create: isNew || undefined });
      ui.vocab.items = cleaned;
      if (result.vocabulary_id) {
        const label = $("vocabIdLabel");
        if (label) label.textContent = result.vocabulary_id;
      }
      resultBox.textContent = `✅ ${isNew ? "词表已创建并写入 .env" : "词表已更新"}，下次转写生效。`;
      if (isNew) { ui.settings.vocabulary_id = result.vocabulary_id; renderVocab(false); }
    } catch (error) {
      resultBox.textContent = `❌ ${error.message}`;
    }
  });
  const reloadBtn = $("vocabReloadBtn");
  if (reloadBtn) reloadBtn.addEventListener("click", loadVocabulary);
}
