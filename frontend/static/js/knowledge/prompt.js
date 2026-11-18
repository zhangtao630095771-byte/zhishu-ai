/* ══════════════════════════════════════════════════════════════
   prompt.js · Prompt 配置（系统规则 / 角色提示词的在线编辑）
   由 kb.js 按职责重新划分而来（P1 知识域重排：Prompt 配置独立成文件）。
   依赖：core.js 的 api()、experience.js 的 esc()（均为全局函数）。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// Tab 2：Prompt 配置
// ═══════════════════════════════════════════════════════════
let promptCache = {};
let currentPromptKey = null;

async function loadPrompts() {
  const list = await api("/api/prompts");
  promptCache = {};
  const box = document.getElementById("promptList");
  box.innerHTML = "";
  list.forEach(p => {
    promptCache[p.key] = p.content;
    const div = document.createElement("div");
    div.className = "list-item";
    div.innerHTML = `<span>${esc(p.key)}</span><span class="muted">${p.content.length} 字</span>`;
    div.onclick = () => selectPrompt(p.key, div);
    box.appendChild(div);
  });
  // 默认选中第一项
  if (list.length) selectPrompt(list[0].key, box.firstChild);
}

function selectPrompt(key, el) {
  currentPromptKey = key;
  document.querySelectorAll("#promptList .list-item").forEach(e => e.classList.remove("active"));
  if (el) el.classList.add("active");
  document.getElementById("promptTitle").textContent = key;
  document.getElementById("promptEditor").value = promptCache[key] || "";
}

async function savePrompt() {
  if (!currentPromptKey) { alert("请先选择一项配置"); return; }
  const content = document.getElementById("promptEditor").value;
  const r = await api(`/api/prompts/${encodeURIComponent(currentPromptKey)}`, {
    method: "PUT",
    body: JSON.stringify({ content }),
  });
  alert(r.message);
  promptCache[currentPromptKey] = content;
  loadPrompts();
}

async function resetPrompt() {
  if (!currentPromptKey) return;
  if (!confirm(`确定把 ${currentPromptKey} 恢复成默认值？`)) return;
  await api(`/api/prompts/${encodeURIComponent(currentPromptKey)}/reset`, { method: "POST" });
  alert("已恢复默认值");
  loadPrompts();
}
