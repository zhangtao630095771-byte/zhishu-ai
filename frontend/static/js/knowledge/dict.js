/* ══════════════════════════════════════════════════════════════
   dict.js · 数据字典（编码 → 中文含义）
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════
// Tab 2 · 模块三：数据字典（编码 → 中文含义）
//   ★ 字典解决的是"编码猜不出来"的问题：
//     app_entity_source = 101 里的 101 是什么？模型不可能从字段名推出来。
//     不给字典，它会编一个看起来合理的条件，生成"能跑但全错"的 SQL。
// ═══════════════════════════════════════════════════════════
let dictCache = {};         // 字典条目缓存
let currentDict = null;     // 当前编辑的字典名
let dictSources = [];       // 可导入的字典类型缓存

/** 加载字典列表 */
async function kbLoadDicts() {
  const list = await api("/api/kb/dicts");
  dictCache = {};
  const box = document.getElementById("dictList");
  box.innerHTML = "";
  if (!list.length) {
    box.innerHTML = `<div class="empty">还没有字典<br>点上面的「从数据库导入」开始</div>`;
    return;
  }
  list.forEach(d => {
    dictCache[d.name] = d;
    const div = document.createElement("div");
    div.className = "list-item" + (d.name === currentDict ? " active" : "");
    // 停用的标红点；空字典标黄叹号 —— 空字典不会进 RAG，等于没配，必须一眼看出来
    const left = d.enabled ? "" : "🔴 ";
    const right = d.items.length
      ? `${d.items.length} 条`
      : `<span style="color:var(--color-warning)">⚠ 0 条（不会生效）</span>`;
    div.innerHTML = `<span>${left}${esc(d.name)}</span>
                     <span class="muted" style="white-space:nowrap">${right}</span>`;
    div.title = d.field_hint ? `适用字段：${d.field_hint}` : "⚠ 未填适用字段";
    div.onclick = () => kbSelectDict(d.name, div);
    box.appendChild(div);
  });
  if (!currentDict || !dictCache[currentDict]) kbSelectDict(list[0].name, box.firstChild);
}

/** 选中一个字典 → 填表单 */
function kbSelectDict(name, el) {
  currentDict = name;
  document.querySelectorAll("#dictList .list-item").forEach(e => e.classList.remove("active"));
  if (el) el.classList.add("active");
  const d = dictCache[name];
  document.getElementById("dictTitle").textContent = name;
  document.getElementById("dName").value = d.name;
  document.getElementById("dField").value = d.field_hint || "";
  document.getElementById("dKeywords").value = (d.keywords || []).join(",");
  document.getElementById("dItems").value = d.items.map(i => `${i.value}=${i.label}`).join("\n");
  kbRenderDictStatus(d);
  kbRenderDictCount();
}

/** 把开放状态渲染到卡片上 */
function kbRenderDictStatus(d) {
  document.getElementById("dStatus").textContent = d.enabled
    ? "🟢 已开放 —— 命中时注入给 AI"
    : "🔴 已停用 —— 不进知识库，AI 看不到编码含义";
  document.getElementById("dToggleBtn").textContent = d.enabled ? "停用" : "开放";
}

/** 值看起来像不像"编码"（纯数字/字母/下划线/点/横线，不含中文） */
function kbLooksLikeCode(s) {
  return /^[A-Za-z0-9_.\-]+$/.test(s);
}

/**
 * 解析取值文本框（每行一条）
 *
 * ★ 为什么要"兼容多种分隔符"？
 *   用户经常是从别处复制粘贴的，分隔符五花八门：
 *     "101=车拖车" / "101＝车拖车"（全角）/ "101:车拖车" / "101 车拖车"
 *   只认一种，用户就会白填一遍 —— 而且他还不知道为什么没生效。
 *
 * ★ 认不出来的行【不会】被丢掉，而是标记成 value="" 交给上层报错。
 *   静默丢弃是这个项目反复踩的坑：保存成功、实际是空的。
 */
function kbParseItems() {
  const SEP = /^(.+?)\s*[=＝:：,\uFF0C\t]\s*(.+)$/;      // 编码<分隔符>含义
  return document.getElementById("dItems").value.split("\n")
    .map(l => l.trim()).filter(Boolean)
    .map(l => {
      let m = l.match(SEP);
      if (m) return { value: m[1].trim(), label: m[2].trim() };
      // 再试"编码 含义"（空白分隔）—— 但前半段必须真的像编码，
      // 否则 "微信 小程序" 会被误当成 编码=微信，凭空造出一个错编码
      m = l.match(/^(\S+)\s+(.+)$/);
      if (m && kbLooksLikeCode(m[1])) return { value: m[1].trim(), label: m[2].trim() };
      return { value: "", label: l };                    // 认不出来 → 标记为无效行
    });
}

/** 实时提示：解析出几条 / 几行认不出来 / 适用字段填没填 */
function kbRenderDictCount() {
  const raw = document.getElementById("dItems").value.trim();
  const parsed = kbParseItems();
  const bad = parsed.filter(i => !i.value).length;
  const field = document.getElementById("dField").value.trim();

  let msg;
  if (!raw) {
    msg = "还没填取值";
  } else if (!parsed.length) {
    msg = "还没填取值";
  } else if (bad === parsed.length) {
    msg = `⚠ ${parsed.length} 行都没解析出「编码」—— 每行应为：编码=含义（如 101=车拖车）`;
  } else {
    msg = `解析出 ${parsed.length - bad} 条`;
    if (bad) msg += `，${bad} 行认不出编码（将被忽略）`;
  }
  if (!field) msg += "　⚠ 适用字段没填 —— 模型可能把编码用到别的字段上，静默算错";
  document.getElementById("dCount").textContent = msg;
}

/** 保存字典 */
async function kbSaveDict() {
  const name = document.getElementById("dName").value.trim();
  if (!name) { alert("字典名称不能为空"); return; }
  // "/" 会破坏接口地址（路径参数里它会被当成目录分隔符），提前拦住并给替代方案
  if (name.includes("/")) {
    alert("字典名称不能包含 「/」（会破坏接口地址）。\n可用「和」「-」「·」代替，例如：渠道和来源");
    return;
  }

  const parsed = kbParseItems();
  const ok = parsed.filter(i => i.value);
  const bad = parsed.filter(i => !i.value);

  // ★★★ 关键：绝不"静默丢弃"
  //   本项目今天已经三次栽在"保存成功、实际是空的"上（字段注释被丢弃、
  //   字典取值被丢弃）。所以这里必须把问题明确摆到用户面前。
  if (parsed.length && !ok.length) {
    alert(`取值列表里 ${parsed.length} 行都没解析出「编码」，无法保存。\n\n` +
          `每行格式应为：编码=含义\n例：101=车拖车\n\n` +
          `你填的第一行是：\n  ${parsed[0].label.slice(0, 60)}\n\n` +
          `没有编码的字典没法用来写 SQL 条件，所以不能存。\n\n` +
          `💡 如果这个字典在数据库里已经有了，点上面的「从数据库导入」\n` +
          `   更省事 —— 会自动带上全部编码，不用手抄。`);
    return;
  }
  if (bad.length) {
    if (!confirm(`有 ${bad.length} 行没解析出「编码」，将被忽略：\n\n` +
                 bad.slice(0, 5).map(b => "  " + b.label.slice(0, 40)).join("\n") +
                 (bad.length > 5 ? `\n  …还有 ${bad.length - 5} 行` : "") +
                 `\n\n继续保存其余 ${ok.length} 条吗？`)) return;
  }
  if (!ok.length) {
    if (!confirm("取值列表是空的 —— 空字典不会进入 RAG（等于没配）。\n\n仍然保存吗？")) return;
  }

  const split = s => s.split(",").map(x => x.trim()).filter(Boolean);
  const r = await api(`/api/kb/dict/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({
      dict_type: (dictCache[name] || {}).dict_type || "",
      field_hint: document.getElementById("dField").value.trim(),
      keywords: split(document.getElementById("dKeywords").value),
      items: ok,
    }),
  });
  alert(`${r.message}\n\n本次保存了 ${ok.length} 条取值。`);
  currentDict = name;
  kbLoadDicts();
  kbLoadOverview();
}

/** 切换开放 / 停用 */
async function kbToggleDictEnabled() {
  if (!currentDict) { alert("请先从左侧选择一个字典"); return; }
  const d = dictCache[currentDict];
  const next = !d.enabled;
  const r = await api(`/api/kb/dict/${encodeURIComponent(currentDict)}/enabled`, {
    method: "PUT", body: JSON.stringify({ enabled: next }),
  });
  d.enabled = next;
  kbRenderDictStatus(d);
  kbLoadDicts();
  kbLoadOverview();
  alert(r.message);
}

/** 删除字典 */
async function kbDeleteDict() {
  if (!currentDict) { alert("请先从左侧选择要删除的字典"); return; }
  if (!confirm(`确定删除字典「${currentDict}」？`)) return;
  await api(`/api/kb/dict/${encodeURIComponent(currentDict)}`, { method: "DELETE" });
  alert("已删除");
  currentDict = null;
  kbLoadDicts();
  kbLoadOverview();
}

/** 新建（清空表单） */
function kbNewDict() {
  currentDict = null;
  document.querySelectorAll("#dictList .list-item").forEach(e => e.classList.remove("active"));
  document.getElementById("dictTitle").textContent = "新建字典";
  ["dName", "dField", "dKeywords", "dItems"].forEach(id =>
    document.getElementById(id).value = "");
  kbRenderDictCount();
}

/** 展开 / 收起"从数据库导入"面板 */
async function kbToggleImport() {
  const p = document.getElementById("kbImportPanel");
  const show = p.style.display === "none";
  p.style.display = show ? "block" : "none";
  if (show && !dictSources.length) await kbLoadSources();
}

/** 读取数据库里可导入的字典类型 */
async function kbLoadSources() {
  const box = document.getElementById("kbSourceList");
  box.innerHTML = `<div class="empty">读取字典源表…</div>`;
  try {
    const r = await api("/api/kb/dict-sources");
    dictSources = r.sources;
    document.getElementById("kbSourceTable").textContent =
      `源表：${r.table} ｜ 共 ${r.sources.length} 个字典类型`;
    kbRenderSources();
  } catch (e) {
    box.innerHTML = `<div class="alert err">读取失败：${esc(e.message)}</div>`;
  }
}

/** 渲染可导入的字典类型列表 */
function kbRenderSources() {
  const kw = (document.getElementById("kbSourceSearch").value || "").trim().toLowerCase();
  const rows = dictSources.filter(s =>
    !kw || s.dict_type.toLowerCase().includes(kw) || (s.sample || "").toLowerCase().includes(kw));
  const box = document.getElementById("kbSourceList");
  box.innerHTML = "";
  if (!rows.length) { box.innerHTML = `<div class="empty">没有匹配的字典类型</div>`; return; }
  rows.forEach(s => {
    const div = document.createElement("div");
    div.className = "list-item";
    div.innerHTML = `
      <span>${esc(s.dict_type)} ${s.imported ? '<span class="status-tag success">已导入</span>' : ""}
        <div class="muted" style="font-size:12px">${esc(s.sample || "")}</div></span>
      <span class="muted" style="white-space:nowrap">${s.count} 条
        <button class="ghost" style="margin-left:8px"
          onclick="event.stopPropagation();kbImportDict('${esc(s.dict_type)}')">
          ${s.imported ? "更新" : "导入"}</button></span>`;
    box.appendChild(div);
  });
}

/** 导入某个字典类型 */
async function kbImportDict(dictType) {
  const name = prompt(`导入「${dictType}」\n\n给它起个中文名（也会作为检索关键词）：`, dictType);
  if (name === null) return;
  const field = prompt(
    "这个字典用在哪个字段上？（必填）\n例：app_entity_source\n\n" +
    "★ 不填的话，模型可能把编码用到别的字段上，得出错误结论。\n" +
    "不确定就先留空，之后再补 —— 但补完记得重建索引。", "");
  if (field === null) return;
  try {
    const r = await api("/api/kb/dict/import", {
      method: "POST",
      body: JSON.stringify({
        dict_type: dictType,
        name: (name || "").trim(),
        field_hint: (field || "").trim(),
      }),
    });
    alert(r.message);
    await kbLoadSources();          // 刷新"已导入"标记
    currentDict = r.name;
    kbLoadDicts();
    kbLoadOverview();
  } catch (e) {
    alert("导入失败：" + e.message);
  }
}

/** 切换导入面板的子标签：字典源表 / 数据表 */
async function kbSwitchImportTab(which, btn) {
  document.querySelectorAll("#kbImportPanel .subtabs button").forEach(b => b.classList.remove("active"));
  btn.classList.add("active");
  document.getElementById("kbImportSrcPane").style.display = which === "src" ? "block" : "none";
  document.getElementById("kbImportTablePane").style.display = which === "table" ? "block" : "none";
  if (which === "table") await kbLoadImportTables();
}

/** 加载可选的业务表（供"从数据表导入"） */
async function kbLoadImportTables() {
  const sel = document.getElementById("kbImpTable");
  if (sel.dataset.loaded) return;            // 只拉一次
  try {
    const rows = await api("/api/kb/tables?only_visible=true");
    const tables = Array.isArray(rows) ? rows : (rows.tables || []);
    sel.innerHTML = `<option value="">— 选择业务表 —</option>` +
      tables.map(t => `<option value="${esc(t.table)}">${esc(t.table)}${t.comment ? " · " + esc(t.comment) : ""}</option>`).join("");
    sel.dataset.loaded = "1";
  } catch (e) {
    sel.innerHTML = `<option value="">读取表列表失败：${esc(e.message)}</option>`;
  }
}

/** 选了表之后，加载它的字段到编码列 / 含义列下拉框 */
async function kbImpTableChanged() {
  const table = document.getElementById("kbImpTable").value;
  const vSel = document.getElementById("kbImpValue");
  const lSel = document.getElementById("kbImpLabel");
  vSel.innerHTML = `<option value="">— 编码列 —</option>`;
  lSel.innerHTML = `<option value="">— 含义列（可选）—</option>`;
  document.getElementById("kbImpPreview").textContent = "";
  if (!table) return;
  try {
    const r = await api("/api/kb/table-columns?table=" + encodeURIComponent(table));
    r.columns.forEach(c => {
      vSel.insertAdjacentHTML("beforeend", `<option value="${esc(c.name)}">${esc(c.name)} (${esc(c.type)})</option>`);
      lSel.insertAdjacentHTML("beforeend", `<option value="${esc(c.name)}">${esc(c.name)} (${esc(c.type)})</option>`);
    });
  } catch (e) {
    document.getElementById("kbImpPreview").textContent = "读取字段失败：" + e.message;
  }
}

/** 从数据表导入字典 */
async function kbImportFromTable() {
  const table = document.getElementById("kbImpTable").value;
  const valueCol = document.getElementById("kbImpValue").value;
  if (!table || !valueCol) { alert("请先选择业务表和编码列"); return; }
  const labelCol = document.getElementById("kbImpLabel").value;
  const name = prompt(`导入 ${table}.${valueCol}\n\n给这份字典起个名字（也会作为检索关键词）：`,
                      `${table}.${valueCol}`);
  if (name === null) return;
  const field = prompt(
    "这个字典用在哪个字段上？（必填）\n例：record.type\n\n" +
    "★ 不填的话，模型可能把编码用到别的字段上，得出错误结论。", "");
  if (field === null) return;
  try {
    const r = await api("/api/kb/dict/import-table", {
      method: "POST",
      body: JSON.stringify({
        table, value_col: valueCol, label_col: labelCol,
        name: (name || "").trim(), field_hint: (field || "").trim(),
      }),
    });
    alert(r.message);
    currentDict = r.name;
    kbLoadDicts();
    kbLoadOverview();
  } catch (e) {
    alert("导入失败：" + e.message);
  }
}

