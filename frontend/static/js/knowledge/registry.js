/* ══════════════════════════════════════════════════════════════
   registry.js · 语义注册表（实体/关系/文档）· 模糊推荐
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// Tab 2 · 语义注册表管理（实体 / 关系 / 文档，P2/P4）
// ═══════════════════════════════════════════════════════════
async function kbLoadRegistry() {
  initRegistrySuggest();      // ★ 挂上「表名/实体」模糊推荐（幂等，重复调用无副作用）
  await regRenderEntities();
  await regRenderRels();
  await regRenderDocs();
}

// ═══════════════════════════════════════════════════════════
// 模糊推荐：表名 / 实体（录实体主表、配 JOIN 时用）
//   ★ 输入关键词 → 调 /api/tables?search=（按【表名 + 业务注释】模糊）→ 弹候选 → 点击带入
//   ★ 幂等：同一个 input 只挂一次（kbLoadRegistry 会被反复调用）
// ═══════════════════════════════════════════════════════════
function attachSuggest(input, opts) {
  if (!input || input.dataset.sugAttached === "1") return;
  input.dataset.sugAttached = "1";
  input.setAttribute("autocomplete", "off");
  opts = opts || {};
  const wrap = input.parentNode;
  if (!wrap || !wrap.classList.contains("sug-wrap")) return;   // 约定：input 外层必须是 .sug-wrap
  const box = document.createElement("div");
  box.className = "sug-box";
  box.style.display = "none";
  wrap.appendChild(box);
  let timer = null, items = [], active = -1;

  const hide = () => { box.style.display = "none"; box.innerHTML = ""; active = -1; };
  function render() {
    if (!items.length) {
      box.innerHTML = '<div class="sug-item"><span class="sub">无匹配（换个关键词，或直接填表名）</span></div>';
      box.style.display = "block"; return;
    }
    box.innerHTML = items.map((it, i) =>
      `<div class="sug-item${i === active ? " active" : ""}" data-i="${i}">
         <b>${esc(it.label)}</b>${it.sub ? `<span class="sub">${esc(it.sub)}</span>` : ""}
       </div>`).join("");
    box.style.display = "block";
  }
  function pick(i) {
    const it = items[i];
    if (!it) return;
    input.value = it.pick;
    hide();
    input.dispatchEvent(new Event("change"));
  }
  async function search() {
    const q = input.value.trim();
    if (!q) { hide(); return; }
    try {
      const [tables, ents] = await Promise.all([
        api(`/api/tables?search=${encodeURIComponent(q)}`),
        opts.withEntities ? api("/api/kb/entities").catch(() => []) : Promise.resolve([]),
      ]);
      const arr = [];
      (ents || []).filter(e => (e.name || "").includes(q) ||
            (e.keywords || []).some(k => String(k).includes(q)))
        .slice(0, 5)
        .forEach(e => arr.push({ label: e.name, sub: `实体 → ${e.table_name}`, pick: e.table_name }));
      (tables || []).slice(0, 10)
        .forEach(t => arr.push({ label: t.table, sub: t.comment || "", pick: t.table }));
      items = arr; active = -1; render();
    } catch (e) { hide(); }
  }

  input.addEventListener("input", () => { clearTimeout(timer); timer = setTimeout(search, 200); });
  input.addEventListener("focus", () => { if (input.value.trim()) search(); });
  input.addEventListener("keydown", ev => {
    if (box.style.display === "none") return;
    if (ev.key === "ArrowDown") { active = Math.min(active + 1, items.length - 1); render(); ev.preventDefault(); }
    else if (ev.key === "ArrowUp") { active = Math.max(active - 1, 0); render(); ev.preventDefault(); }
    else if (ev.key === "Enter" && active >= 0) { pick(active); ev.preventDefault(); }
    else if (ev.key === "Escape") { hide(); }
  });
  box.addEventListener("mousedown", ev => {
    const el = ev.target.closest(".sug-item");
    if (el && el.dataset.i !== undefined) { ev.preventDefault(); pick(+el.dataset.i); }
  });
  input.addEventListener("blur", () => setTimeout(hide, 150));
}

function initRegistrySuggest() {
  attachSuggest(document.getElementById("reTable"));
  attachSuggest(document.getElementById("rrFrom"), { withEntities: true });
  attachSuggest(document.getElementById("rrTo"),   { withEntities: true });
}

// ── JOIN 关联字段推荐：选好左右表 → 点推荐 → 点候选自动拼出 join_sql ──
let rrSug = null;                                  // 最近一次推荐结果
const RR_SRC_TAG = { existing: "已有规则", same_name: "同名字段", fk_guess: "外键推断", ai: "AI 推断" };

async function regSuggestJoin() {
  const box = document.getElementById("rrCands");
  const picked = document.getElementById("rrPicked");
  const left = document.getElementById("rrFrom").value.trim();
  const right = document.getElementById("rrTo").value.trim();
  if (!left || !right) { alert("请先填写左表/实体与右表/实体"); return; }
  picked.innerHTML = "";
  box.innerHTML = '<div class="muted" style="margin-top:8px">推荐中…（读取两表字段，可能需几秒）</div>';
  try {
    const d = await api("/api/kb/suggest-join", { method: "POST", body: JSON.stringify({
      left, right, hint: document.getElementById("rrHint").value.trim() }) });
    rrSug = d;
    const L = d.left || {}, R = d.right || {};
    let html = `<div class="muted" style="font-size:12px;margin-top:8px">
      左：<b>${esc(L.table || "?")}</b>${L.note ? `（${esc(L.note)}）` : ""}
      　右：<b>${esc(R.table || "?")}</b>${R.note ? `（${esc(R.note)}）` : ""}</div>`;
    if ((L.ambiguous || []).length > 1)
      html += `<div class="muted" style="font-size:12px">左表候选：${esc(L.ambiguous.join("、"))}</div>`;
    if ((R.ambiguous || []).length > 1)
      html += `<div class="muted" style="font-size:12px">右表候选：${esc(R.ambiguous.join("、"))}</div>`;
    const cs = d.candidates || [];
    if (!cs.length) {
      html += `<div class="alert warn" style="margin-top:8px">${esc(d.message || "没有推荐出关联字段，请手工填写 join_sql")}</div>`;
    } else {
      html += `<div style="margin-top:8px;font-size:13px">点一条候选 → 自动填入 join_sql（表名自动带入）：</div>`;
      html += cs.map((c, i) => {
        const txt = c.on_clause ? c.on_clause : `a.${c.left_col} = b.${c.right_col}`;
        return `<div class="sug-item" style="border:1px solid var(--color-border);border-radius:8px;margin-top:6px" onclick="regPickJoin(${i})">
          <code>${esc(txt)}</code> <span class="badge">${esc(RR_SRC_TAG[c.source] || c.source)}</span>
          <div class="sub">${esc(c.reason || "")}</div></div>`;
      }).join("");
    }
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div class="alert err" style="margin-top:8px">推荐失败：${esc(e.message || e)}</div>`;
  }
}

function regPickJoin(i) {
  const c = rrSug && (rrSug.candidates || [])[i];
  if (!c) return;
  const L = (rrSug.left || {}).table, R = (rrSug.right || {}).table;
  const on = c.on_clause ? c.on_clause : `a.${c.left_col} = b.${c.right_col}`;
  document.getElementById("rrJoin").value = `${L} a LEFT JOIN ${R} b ON ${on}`;
  if (c.cardinality) document.getElementById("rrCard").value = c.cardinality;
  if (c.reason && !document.getElementById("rrNote").value.trim())
    document.getElementById("rrNote").value = c.reason;
  document.getElementById("rrPicked").innerHTML =
    `<div class="alert info" style="margin-top:8px">已填入 join_sql（来源：${esc(RR_SRC_TAG[c.source] || c.source)}）—— 请确认关联字段正确后再保存。</div>`;
}

// ── 语义注册表：编辑状态（null = 新增）与列表缓存 ──
//   ★ 编辑/删除按钮一律传【下标】，不把名称拼进 onclick —— 名称里带引号会直接崩。
let regEdit = { entity: null, rel: null, doc: null };
let regCache = { entities: [], rels: [], docs: [] };
const regBadge = en => (en === false ? '<span class="status-tag gray">已停用</span> ' : "");

async function regRenderEntities() {
  const box = document.getElementById("regEntities");
  if (!box) return;
  try {
    const list = await api("/api/kb/entities");
    regCache.entities = list || [];
    if (!list.length) { box.innerHTML = '<div class="empty">暂无实体</div>'; return; }
    box.innerHTML = list.map((e, i) => `
      <div class="reg-row">
        <div class="reg-info">
          ${regBadge(e.enabled)}<b>${esc(e.name)}</b>
          <span class="muted"> → ${esc(e.table_name)}${e.grain ? "（" + esc(e.grain) + "）" : ""}</span>
          ${e.comment ? `<div class="muted" style="font-size:12px">${esc(e.comment)}</div>` : ""}
        </div>
        <div class="reg-acts">
          <button class="ghost" onclick="regEditEntity(${i})">编辑</button>
          <button class="ghost" onclick="regDelEntity(${i})">删除</button>
        </div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<div class="empty">加载失败：${esc(e.message || e)}</div>`; }
}

async function regRenderRels() {
  const box = document.getElementById("regRels");
  if (!box) return;
  try {
    const list = await api("/api/kb/relationships");
    regCache.rels = list || [];
    if (!list.length) { box.innerHTML = '<div class="empty">暂无关系</div>'; return; }
    box.innerHTML = list.map((r, i) => `
      <div class="reg-row">
        <div class="reg-info">
          ${regBadge(r.enabled)}<b>${esc(r.from_entity)} → ${esc(r.to_entity)}</b>
          <span class="muted"> ${esc(r.cardinality)}${r.pre_agg_required ? " · 需预聚合" : ""}</span>
          ${r.join_sql ? `<div class="muted" style="font-size:12px">${esc(r.join_sql)}</div>` : ""}
        </div>
        <div class="reg-acts">
          <button class="ghost" onclick="regEditRel(${i})">编辑</button>
          <button class="ghost" onclick="regDelRel(${i})">删除</button>
        </div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<div class="empty">加载失败：${esc(e.message || e)}</div>`; }
}

async function regRenderDocs() {
  const box = document.getElementById("regDocs");
  if (!box) return;
  try {
    const list = await api("/api/kb/docs");
    regCache.docs = list || [];
    if (!list.length) { box.innerHTML = '<div class="empty">暂无文档</div>'; return; }
    box.innerHTML = list.map((d, i) => `
      <div class="reg-row">
        <div class="reg-info">
          ${regBadge(d.enabled)}<b>${esc(d.title)}</b>
          ${d.content ? `<div class="muted" style="font-size:12px">${esc(String(d.content).slice(0, 80))}</div>` : ""}
        </div>
        <div class="reg-acts">
          <button class="ghost" onclick="regEditDoc(${i})">编辑</button>
          <button class="ghost" onclick="regDelDoc(${i})">删除</button>
        </div>
      </div>`).join("");
  } catch (e) { box.innerHTML = `<div class="empty">加载失败：${esc(e.message || e)}</div>`; }
}

// ── 业务实体：新增 / 编辑 / 取消 ──
function regOpenEntityForm() {
  regEdit.entity = null;
  ["reName", "reTable", "reGrain", "reKw", "reComment", "rePrimaryKey"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  document.getElementById("reEnabled").checked = true;
  document.getElementById("reMode").textContent = "";
  document.getElementById("regEntityForm").style.display = "block";
  document.getElementById("reName").focus();
}
function regEditEntity(i) {
  const e = regCache.entities[i];
  if (!e) return;
  regEdit.entity = e.name;                    // ★ 记住原实体名：改名时后端据此删旧行
  document.getElementById("reName").value = e.name || "";
  document.getElementById("reTable").value = e.table_name || "";
  document.getElementById("reGrain").value = e.grain || "";
  document.getElementById("reKw").value = (e.keywords || []).join("，");
  document.getElementById("reComment").value = e.comment || "";
  document.getElementById("rePrimaryKey").value = (e.primary_key || []).join("，");
  document.getElementById("reEnabled").checked = e.enabled !== false;
  document.getElementById("reMode").textContent = `编辑中：${e.name}（改名会自动迁移旧记录）`;
  document.getElementById("regEntityForm").style.display = "block";
  document.getElementById("regEntityForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function regCloseEntityForm() {
  regOpenEntityForm();
  document.getElementById("regEntityForm").style.display = "none";
}

// ── 关联 / Join 规则：新增 / 编辑 / 取消 ──
function regOpenRelForm() {
  regEdit.rel = null;
  ["rrFrom", "rrTo", "rrCard", "rrJoin", "rrNote", "rrHint", "rrFromCols", "rrToCols"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  document.getElementById("rrPreAgg").checked = false;
  document.getElementById("rrEnabled").checked = true;
  document.getElementById("rrMode").textContent = "";
  document.getElementById("rrCands").innerHTML = "";
  document.getElementById("rrPicked").innerHTML = "";
  rrSug = null;
  document.getElementById("regRelForm").style.display = "block";
  document.getElementById("rrFrom").focus();
}
function regEditRel(i) {
  const r = regCache.rels[i];
  if (!r) return;
  regEdit.rel = r.id;                         // ★ 记住原 id：改了左右实体时后端清理旧行
  document.getElementById("rrFrom").value = r.from_entity || "";
  document.getElementById("rrTo").value = r.to_entity || "";
  document.getElementById("rrCard").value = r.cardinality || "";
  document.getElementById("rrJoin").value = r.join_sql || "";
  document.getElementById("rrNote").value = r.rule_note || "";
  document.getElementById("rrFromCols").value = (r.from_columns || []).join("，");
  document.getElementById("rrToCols").value = (r.to_columns || []).join("，");
  document.getElementById("rrPreAgg").checked = !!r.pre_agg_required;
  document.getElementById("rrEnabled").checked = r.enabled !== false;
  document.getElementById("rrMode").textContent =
    `编辑中：#${r.id} ${r.from_entity} → ${r.to_entity}（改左右实体会替换掉旧规则）`;
  document.getElementById("rrCands").innerHTML = "";
  document.getElementById("rrPicked").innerHTML = "";
  rrSug = null;
  document.getElementById("regRelForm").style.display = "block";
  document.getElementById("regRelForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function regCloseRelForm() {
  regOpenRelForm();
  document.getElementById("regRelForm").style.display = "none";
}

// ── 业务文档：新增 / 编辑 / 取消 ──
function regOpenDocForm() {
  regEdit.doc = null;
  ["rdTitle", "rdContent", "rdKw"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  document.getElementById("rdEnabled").checked = true;
  document.getElementById("rdMode").textContent = "";
  document.getElementById("regDocForm").style.display = "block";
  document.getElementById("rdTitle").focus();
}
function regEditDoc(i) {
  const d = regCache.docs[i];
  if (!d) return;
  regEdit.doc = { id: d.id, priority: d.priority || 4 };   // ★ 传 id → 就地更新（支持改标题）
  document.getElementById("rdTitle").value = d.title || "";
  document.getElementById("rdContent").value = d.content || "";
  document.getElementById("rdKw").value = (d.keywords || []).join("，");
  document.getElementById("rdEnabled").checked = d.enabled !== false;
  document.getElementById("rdMode").textContent = `编辑中：${d.title}`;
  document.getElementById("regDocForm").style.display = "block";
  document.getElementById("regDocForm").scrollIntoView({ behavior: "smooth", block: "nearest" });
}
function regCloseDocForm() {
  regOpenDocForm();
  document.getElementById("regDocForm").style.display = "none";
}

async function regSaveEntity() {
  const name = document.getElementById("reName").value.trim();
  if (!name) { alert("实体名必填"); return; }
  const d = await api(`/api/kb/entities/${encodeURIComponent(name)}`, {
    method: "PUT",
    body: JSON.stringify({
      table_name: document.getElementById("reTable").value.trim(),
      grain: document.getElementById("reGrain").value.trim(),
      comment: document.getElementById("reComment").value.trim(),
      keywords: (document.getElementById("reKw").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
      primary_key: (document.getElementById("rePrimaryKey").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
      enabled: document.getElementById("reEnabled").checked,
      old_name: regEdit.entity || "",          // ★ 编辑改名时通知后端删旧行
    }),
  });
  alert("已保存。" + (d && d.renamed_from ? `（原「${d.renamed_from}」已迁移）` : "")
        + "★ 记得点『重建索引』生效");
  regCloseEntityForm();
  kbLoadRegistry();
}

async function regSaveRel() {
  const from = document.getElementById("rrFrom").value.trim();
  const to = document.getElementById("rrTo").value.trim();
  if (!from || !to) { alert("左右表/实体必填"); return; }
  await api("/api/kb/relationships", {
    method: "POST",
    body: JSON.stringify({
      from_entity: from, to_entity: to,
      join_sql: document.getElementById("rrJoin").value.trim(),
      join_type: "LEFT",
      cardinality: document.getElementById("rrCard").value.trim() || "1:1",
      pre_agg_required: document.getElementById("rrPreAgg").checked,
      rule_note: document.getElementById("rrNote").value.trim(),
      from_columns: (document.getElementById("rrFromCols").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
      to_columns: (document.getElementById("rrToCols").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
      enabled: document.getElementById("rrEnabled").checked,
      old_id: regEdit.rel || null,             // ★ 编辑时传原 id（改了左右实体则后端清旧行）
    }),
  });
  alert("已保存。★ 记得点『重建索引』生效");
  regCloseRelForm();
  kbLoadRegistry();
}

async function regSaveDoc() {
  const title = document.getElementById("rdTitle").value.trim();
  if (!title) { alert("标题必填"); return; }
  const editing = regEdit.doc;                 // null = 新增
  await api("/api/kb/docs", {
    method: "POST",
    body: JSON.stringify({
      id: editing ? editing.id : null,         // ★ 带 id → 就地更新同一条，不产生重复文档
      title: title,
      content: document.getElementById("rdContent").value.trim(),
      keywords: (document.getElementById("rdKw").value || "").split(/[,，]/).map(s => s.trim()).filter(Boolean),
      priority: editing ? (editing.priority || 4) : 4,   // 编辑时保留原优先级（不暴露给用户乱改）
      enabled: document.getElementById("rdEnabled").checked,
    }),
  });
  alert((editing ? "已更新。" : "已保存。") + "★ 记得点『重建索引』生效");
  regCloseDocForm();
  kbLoadRegistry();
}

async function regDelEntity(i) {
  const e = regCache.entities[i];
  if (!e) return;
  if (!confirm(`删除实体「${e.name}」？`)) return;
  await api(`/api/kb/entities/${encodeURIComponent(e.name)}`, { method: "DELETE" });
  kbLoadRegistry();
}
async function regDelRel(i) {
  const r = regCache.rels[i];
  if (!r) return;
  if (!confirm(`删除关联规则「${r.from_entity} → ${r.to_entity}」？`)) return;
  await api(`/api/kb/relationships/${r.id}`, { method: "DELETE" });
  kbLoadRegistry();
}
async function regDelDoc(i) {
  const d = regCache.docs[i];
  if (!d) return;
  if (!confirm(`删除业务文档「${d.title}」？`)) return;
  await api(`/api/kb/docs/${d.id}`, { method: "DELETE" });
  kbLoadRegistry();
}

/**
 * 知识库导出下载
 *
 * ★ 为什么不能用 <a href="/api/export...">？
 *   登录凭证是 localStorage 里的 Bearer token，浏览器直接点链接不会带 Authorization 头，
 *   后端中间件只会返回 401。必须用 fetch 带上 token，拿到 blob 后再触发下载。
 *
 * ★ 文件名从 Content-Disposition 的 filename* 取；
 *   取不到就用本地兜底名，避免浏览器把文件存成 "export"。
 */
async function downloadKnowledgeExport(format, scope, btn) {
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "导出中…"; }
  try {
    const token = getToken();
    const res = await fetch(
      `/api/export?format=${encodeURIComponent(format)}&scope=${encodeURIComponent(scope)}`,
      { headers: token ? { Authorization: "Bearer " + token } : {} });

    if (res.status === 401) {
      handleUnauthorized("登录已过期，请重新登录");
      throw new Error("登录已过期，请重新登录");
    }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }

    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    let filename = `知数AI知识库_${scope === "manual" ? "人工维护" : "完整"}_${format}.${format === "json" ? "json" : "md"}`;
    const m = /filename\*=UTF-8''([^;]+)/i.exec(cd);
    if (m) { try { filename = decodeURIComponent(m[1]); } catch (e) {} }

    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url;
    a.download = filename;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  } catch (e) {
    alert("导出失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

let kbTables = [];          // 表列表缓存（本地过滤用）
let kbCurrent = null;       // 当前打开的表名
let kbColumns = [];         // 当前表的字段缓存（draft 存未保存的草稿）

/** 覆盖率概览 —— 按模块分别渲染（表结构模块看注释，指标模块看口径） */
async function kbLoadOverview() {
  const schemaBox = document.getElementById("kbStatsSchema");
  const metricBox = document.getElementById("kbStatsMetric");
  const dictBox = document.getElementById("kbStatsDict");
  const exportBox = document.getElementById("kbExportStats");
  try {
    const o = await api("/api/kb/overview");
    // 模块一「表结构信息」：表注释 + 字段注释
    schemaBox.innerHTML =
      kbStat("表注释", o.tables_commented, o.tables_visible,
             "有业务注释 / 会进 RAG 的表") +
      kbStat("字段注释", o.columns_commented, o.columns_total,
             `其中人工维护 ${o.columns_manual} 条 ← 消除幻觉的主力`);
    // 模块二「指标信息」：指标口径
    metricBox.innerHTML =
      kbStat("指标口径", o.metrics_enabled, o.metrics_total, "已开放 / 全部指标");
    // 模块三「数据字典」：编码字典
    dictBox.innerHTML =
      kbStat("数据字典", o.dicts_enabled, o.dicts_total, "已开放 / 全部字典");
    // 模块四「导出备份」：同一份覆盖数据，另外把"导不出去的东西"说清楚
    exportBox.innerHTML =
      kbStat("表", o.tables_commented, o.tables_visible, "有注释 / 全部") +
      kbStat("字段", o.columns_commented, o.columns_total,
             `其中人工维护 ${o.columns_manual} 条`) +
      kbStat("指标口径", o.metrics_enabled, o.metrics_total, "已开放 / 全部") +
      kbStat("数据字典", o.dicts_enabled, o.dicts_total, "已开放 / 全部");

    // ★ 有东西导不出去就必须说出来
    //   人工注释指向的表如果不可见（没授权、或表已经不存在），它既进不了 RAG，
    //   也不会出现在导出文件里。不提示的话，用户会以为"我写的都在里面"，
    //   等哪天真要迁移，才发现少了几条。
    const warnBox = document.getElementById("kbExportWarn");
    warnBox.innerHTML = o.orphan_notes
      ? `<div class="alert warn" style="margin-top:14px">
           ★ 有 <b>${o.orphan_notes}</b> 条人工注释指向的表当前不可见（未授权或不存在），
           <b>不会包含在导出文件里</b>：
           <span class="mono">${esc((o.orphan_tables || []).join("、"))}</span><br>
           如果这些表本来就该被访问 → 去「表与权限」授权；如果是早期试验留下的，可以清理掉。
         </div>`
      : "";
  } catch (e) {
    const msg = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
    // 用循环统一处理，免得以后加了新模块又忘了在 catch 里补一个
    ["kbStatsSchema", "kbStatsMetric", "kbStatsDict", "kbExportStats"].forEach(id => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = msg;
    });
  }
}

/** 渲染一个覆盖率卡片（带进度条） */
function kbStat(label, done, total, hint) {
  const p = total ? Math.round(done / total * 100) : 0;
  const color = p >= 80 ? "var(--color-success)" : p >= 30 ? "var(--color-primary)" : "var(--color-warning)";
  return `<div class="kb-stat">
    <div class="num">${done}<small> / ${total}</small></div>
    <div class="lbl">${label} ｜ ${hint}</div>
    <div class="kb-bar"><i style="width:${p}%;background:${color}"></i></div>
  </div>`;
}

/** 加载表列表 */
async function kbLoadTables() {
  const only = document.getElementById("kbOnlyVisible").checked;
  kbTables = await api(`/api/kb/tables?only_visible=${only}`);
  if (kbCurrent && !kbTables.some(t => t.table === kbCurrent)) {
    kbCurrent = null;   // 当前表被筛掉了，清掉选中态
  }
  kbRenderTables();
}

/** 标记左侧表列表的选中项（按名字匹配，任何调用方都能正确恢复高亮） */
function kbMarkActiveTable(name) {
  document.querySelectorAll("#kbTableList .list-item").forEach(e =>
    e.classList.toggle("active", e.dataset.table === name));
}

/** 渲染表列表（本地过滤，秒响应） */
function kbRenderTables() {
  const kw = document.getElementById("kbSearch").value.trim().toLowerCase();
  const rows = kbTables.filter(t =>
    !kw || t.table.toLowerCase().includes(kw) || (t.comment || "").toLowerCase().includes(kw));
  const box = document.getElementById("kbTableList");
  box.innerHTML = "";
  if (!rows.length) {
    box.innerHTML = `<div class="empty">没有匹配的表</div>`;
    return;
  }
  rows.forEach(t => {
    const div = document.createElement("div");
    div.className = "list-item" + (t.table === kbCurrent ? " active" : "");
    div.dataset.table = t.table;          // 记下名字，便于按名字恢复选中态
    // 没表注释的标个灰点：一眼看出哪些还待补
    div.innerHTML = `<span>${t.comment ? "" : "⚪ "}${esc(t.table)}</span>`;
    div.title = t.comment || "（暂无表注释）";
    div.onclick = () => kbOpenTable(t.table, div);
    box.appendChild(div);
  });
}

/** 打开一张表：拉表注释 + 全部字段 */
async function kbOpenTable(name, el) {
  kbCurrent = name;
  // ★ 选中态必须"先清后加" —— 和指标列表 selectMetric 保持一致。
  //   之前的实现只清不加，所以点完表高亮就没了（用户看不到自己在看哪张表）。
  if (el) {
    document.querySelectorAll("#kbTableList .list-item").forEach(e => e.classList.remove("active"));
    el.classList.add("active");
  } else {
    kbMarkActiveTable(name);          // 没传元素时按名字找（如清注释后刷新详情）
  }
  const box = document.getElementById("kbDetail");
  box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await api(`/api/kb/table/${encodeURIComponent(name)}`);
    kbColumns = d.columns;
    //   ★ 表注释的来源只有三种（字段层另有 db，但表级没有"库自带"这一层了）：
    //     manual = 人写的（或人改过 AI 版）→ 定稿，重新授权不会被覆盖
    //     ai     = AI 生成的临时版      → 已生效并注入 RAG，但下次重新授权会被覆盖
    //     ""     = 还没有
    const srcTag = d.comment_source === "manual" ? '<span class="status-tag success">人工</span>'
      : d.comment_source === "ai" ? '<span class="status-tag info">AI 生成</span>'
      : '<span class="status-tag gray">待补</span>';
    box.innerHTML = `
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
        <h3 style="margin:0">${esc(d.table)}
          ${d.visible ? '<span class="status-tag success">已授权</span>' : '<span class="status-tag gray">未授权</span>'}
          ${srcTag}
        </h3>
        <div class="row">
          <button class="ghost" onclick="kbSaveColumns()">保存全部改动</button>
          <button class="btn" onclick="kbSaveTableComment()">保存表注释</button>
        </div>
      </div>

      <label>表业务注释（注入给大模型）</label>
      <textarea id="kbTableComment" style="min-height:54px"
        placeholder="例：车辆运输订单全流程明细，记录客户发单、司机接单、装车送达…">${esc(d.comment)}</textarea>
      <!-- ★ 来源提示行：
             AI 生成的注释不是"参考"，它<b>已经生效</b>并注入了 RAG ——
             摆这一行的作用是告诉用户"你现在看到的这份是机器写的，可以直接在上面改；
             你不改的话，这张表下次重新授权时会被新生成的一版覆盖掉"。 -->
      <div class="kb-src" id="kbSrcNote">${kbSrcNoteHtml(d.comment_source, d.doc_generating)}</div>
      <div class="row" style="margin-top:8px;gap:8px;flex-wrap:wrap;align-items:center">
        <button class="ghost" id="kbDocBtn" onclick="kbSuggestTableComment()">
          ${d.comment_source === "manual" ? "用 AI 重新起草" : "AI 生成表注释"}</button>
        <span class="muted" style="font-size:12px">
          读这张表的字段名和注释生成一句；新开放访问权限的表会自动生成
        </span>
      </div>

      <div class="row" style="justify-content:space-between;margin:18px 0 6px;gap:8px;flex-wrap:wrap">
        <label style="margin:0">字段信息（共 ${d.column_count} 个，已注释 ${d.commented_count} 个）</label>
        <input id="kbColSearch" placeholder="搜字段名 / 注释…" style="width:200px" oninput="kbRenderColumns()">
      </div>
      <div class="kb-tip" style="margin-bottom:8px">
        「基本注释」是数据库自带的，只作参考；<b>「业务注释」才是注入给大模型的</b> ——
        你填了就盖过库注释，留空则沿用库注释。
      </div>
      <div class="kb-cols" id="kbCols"></div>`;
    kbRenderColumns();
    // ★ 刚开放权限的表可能正在后台生成注释 —— 前端不傻等（生成要调模型，约 2~5 秒），
    //   开轮询，生成完把新注释直接填进上面的输入框（它此刻已经生效进 RAG 了）
    if (d.doc_generating) kbPollTableComment(name);
  } catch (e) {
    box.innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
  }
}

/** 把输入框的即时输入记成草稿（这样切过滤条件也不会丢未保存的编辑） */
function kbDraft(col, val) {
  const c = kbColumns.find(x => x.column === col);
  if (c) c.draft = val;
}

/** 渲染字段编辑表格 */
function kbRenderColumns() {
  const kw = (document.getElementById("kbColSearch").value || "").trim().toLowerCase();
  const rows = kbColumns.filter(c =>
    !kw || c.column.toLowerCase().includes(kw)
        || (c.comment || "").toLowerCase().includes(kw)
        || (c.db_comment || "").toLowerCase().includes(kw));  // ★ 库注释也能搜
  //  ★ 为什么把「基本注释」放在「业务注释」前面？
  //    编辑时要先看到"库里原来怎么写的"，才知道该不该覆盖、怎么补。
  //    顺序反过来的话，人容易照着左边的框改，却不知道原来有内容。
  const head = `<thead><tr>
      <th style="width:148px">字段</th><th style="width:118px">类型</th>
      <th style="width:96px">维度</th>
      <th style="width:180px">基本注释（库自带 · 仅参考）</th>
      <th>业务注释（注入给大模型）</th><th style="width:62px">来源</th><th style="width:110px">操作</th>
    </tr></thead>`;
  const body = rows.map(c => {
    const val = c.draft !== undefined ? c.draft : c.comment;   // 草稿优先
    //   ★ 四态：人工（定稿）/ AI（机器补的，可改）/ 库（自带，仅参考）/ 待补
    //     AI 这一态必须显示出来：它表示"这份注释能用了，但没人核实过"。
    //     不标的话，用户会把 AI 的推断当成同事确认过的口径去用。
    const src = c.comment_source === "manual" ? '<span class="status-tag success">人工</span>'
      : c.comment_source === "ai" ? '<span class="status-tag info" title="AI 根据真实取值生成，未经人工核实">AI</span>'
      : c.comment_source === "db" ? '<span class="status-tag info">库</span>'
      : '<span class="status-tag gray">待补</span>';
    //  AI 生成的那份，把原注释顶掉之前给个提示：改动会锁定成人工版
    const aiTip = c.comment_source === "ai"
      ? `<span class="muted" style="font-size:12px">改过即锁定</span>` : "";
    //  库自带注释：只读。为空时给个短横线，别留白让人以为"没加载出来"
    const dbc = c.db_comment ? esc(c.db_comment) : '<span class="muted">—</span>';
    //  ★ 维度下拉：人工指定维度类型（空=自动识别）。onchange 单独保存，不依赖注释保存。
    //    生效维度 c.dimension（后端已自动识别）用 title 提示，便于用户判断要不要手动覆盖。
    const dimSel = kbDimensionSelect(c.column, c.dimension_type || "", c.dimension);
    return `<tr>
      <td style="font-weight:600">${esc(c.column)}</td>
      <td style="color:var(--color-text-muted)">${esc(c.type)}</td>
      <td>${dimSel}</td>
      <td class="dbcmt">${dbc}</td>
      <td class="cmt">
        <input id="kbCol_${esc(c.column)}" value="${esc(val)}"
               oninput="kbDraft('${esc(c.column)}', this.value)"
               placeholder="${c.db_comment ? "留空则沿用左边的库注释" : "例：订单状态：6已完成 7已取消"}">
      </td>
      <td>${src}</td>
      <td class="ops">
        ${aiTip}
        <button class="ghost" onclick="kbSaveColumn('${esc(c.column)}')">保存</button>
        <button class="ghost" onclick="kbClearColumn('${esc(c.column)}')">清除</button>
      </td>
    </tr>`;
  }).join("");
  document.getElementById("kbCols").innerHTML =
    `<table class="data">${head}<tbody>${body}</tbody></table>`;
}

/** 维度下拉（空=自动识别；人工指定 time/geo/status/type/channel） */
function kbDimensionSelect(col, current, detected) {
  const opts = [["", "自动"], ["time", "时间"], ["geo", "地域"],
                ["status", "状态"], ["type", "类型"], ["channel", "渠道"]];
  const title = detected ? `自动识别为「${detected}」` : "";
  return `<select onchange="kbSaveDimension('${esc(col)}', this.value)" title="${esc(title)}"
            style="width:100%;padding:2px 4px;font-size:12px">
    ${opts.map(([v, l]) => `<option value="${v}" ${current === v ? "selected" : ""}>${l}</option>`).join("")}
  </select>`;
}

/** 单独保存字段维度类型（onchange 触发，空=回到自动识别） */
async function kbSaveDimension(col, dimType) {
  const c = kbColumns.find(x => x.column === col);
  // ★ 保存维度时带上【当前已保存的注释】，避免把注释误清空
  const curComment = c ? (c.comment || "") : "";
  try {
    await api(`/api/kb/column/${encodeURIComponent(kbCurrent)}/${encodeURIComponent(col)}`, {
      method: "PUT", body: JSON.stringify({ comment: curComment, dimension_type: dimType }),
    });
    if (c) { c.dimension_type = dimType; }
  } catch (e) { alert("维度保存失败：" + e.message); }
}

/** 保存单个字段注释 */
async function kbSaveColumn(col) {
  const el = document.getElementById("kbCol_" + col);
  await api(`/api/kb/column/${encodeURIComponent(kbCurrent)}/${encodeURIComponent(col)}`, {
    method: "PUT", body: JSON.stringify({ comment: el.value }),
  });
  const c = kbColumns.find(x => x.column === col);
  if (c) {
    c.comment = el.value;
    c.comment_source = el.value.trim() ? "manual" : (c.db_comment ? "db" : "");
    delete c.draft;
  }
  kbRenderColumns();
  kbLoadOverview();                    // 覆盖率变了，刷新概览
}

/** 保存全部改动（只提交真正改过的字段，避免几百次无谓请求） */
async function kbSaveColumns() {
  const changed = kbColumns.filter(c => c.draft !== undefined && c.draft !== c.comment);
  if (!changed.length) { alert("没有检测到改动"); return; }
  let n = 0;
  for (const c of changed) {
    await api(`/api/kb/column/${encodeURIComponent(kbCurrent)}/${encodeURIComponent(c.column)}`, {
      method: "PUT", body: JSON.stringify({ comment: c.draft }),
    });
    c.comment = c.draft;
    c.comment_source = String(c.draft).trim() ? "manual" : (c.db_comment ? "db" : "");
    delete c.draft;
    n++;
  }
  alert(`已保存 ${n} 个字段的注释。★ 点右上角「重建索引」后生效`);
  kbRenderColumns();
  kbLoadOverview();
}

/** 清除单个字段的人工注释 */
async function kbClearColumn(col) {
  if (!confirm(`清除字段「${col}」的人工注释？`)) return;
  await api(`/api/kb/column/${encodeURIComponent(kbCurrent)}/${encodeURIComponent(col)}`, {
    method: "DELETE",
  });
  const c = kbColumns.find(x => x.column === col);
  if (c) {
    c.comment = "";
    c.comment_source = c.db_comment ? "db" : "";
    delete c.draft;
  }
  kbRenderColumns();
  kbLoadOverview();
}

/** 保存表注释 */
async function kbSaveTableComment() {
  const v = document.getElementById("kbTableComment").value;
  const r = await api(`/api/kb/table/${encodeURIComponent(kbCurrent)}`, {
    method: "PUT", body: JSON.stringify({ comment: v }),
  });
  const t = kbTables.find(x => x.table === kbCurrent);
  if (t) { t.comment = v; t.comment_source = v.trim() ? "manual" : ""; }
  kbRenderTables();
  kbLoadOverview();
  //   ★ 人一保存就锁定成 manual —— 提示行要跟着变，否则用户以为改完还会被覆盖
  kbRefreshSrcNote(v.trim() ? "manual" : "", false);
  alert(r.message);
}

/**
 * AI 生成表业务注释 —— 生成完标为 candidate（待复核、不进检索），用户改一遍保存即核准
 *
 * ★ 为什么值得做？
 *   表注释决定"模型能不能找对表"，但要通读几百个字段才写得出来 ——
 *   人做这件事性价比极低（一张表 210 个字段，看完小半小时），
 *   而 AI 扫一遍字段清单就能抓住"这张表到底存的是什么"。
 *
 * ★ 为什么生成后落「待复核」，而不是先摆一份"参考"等人点采用？
 *   参考层看着稳妥，实际是个死结：
 *     ① 授权 30 张表 = 30 份参考等人逐个点，多数永远不会点 —— 知识库照样是空的
 *     ② 没写进 table_comments 就谈不上治理（没有对象、没有版本、没有审计）
 *   所以改成：AI 生成 = 直接落库，但状态是 candidate（待复核），
 *   在「知识治理 → 待复核」一键通过后才进 RAG。
 *   ★ 为什么不是"生成即 approved"？因为严格模式下只有 approved 能进检索，
 *     AI 草稿未经人看就参与取数，等于把幻觉直接喂给业务数字。
 *
 * ★ 那 AI 写错了怎么办？—— 靠 source 列区分"谁写的"来兜底：
 *     source = 'ai'     → 临时版，可被下一次生成覆盖（前提是还没人核准）
 *     source = 'manual' → 人改过的定稿版，永远不被覆盖
 *   用户在输入框里改完点保存，source 立刻变 manual，这份注释就被锁定了。
 *   （字段层同理，只是字段层还多保留一份 db 库注释作参考）
 *
 * ★ 两个触发时机：
 *   ① 「表与权限」里开放访问权限 → 后台自动生成
 *      （授权是用户唯一会主动碰"这张表"的时刻，此刻生成最顺手；
 *        等用户想起来去补，往往已经是答错几次之后了）
 *   ② 这里点「AI 生成表注释 / 用 AI 重新起草」 → 手动生成
 */
let kbDocTimer = null;         // 轮询定时器（后台生成时刷新用）

/** 来源提示行的文案：把"这份注释现在是草稿态还是定稿态"说清楚 */
function kbSrcNoteHtml(source, generating) {
  if (generating) {
    return `<span class="status-tag info">生成中</span>
      <span>正在通读这张表的字段清单生成注释…（几秒，可以先做别的）</span>`;
  }
  if (source === "ai") {
    return `<span class="status-tag info">AI 生成</span>
      <span>这是 AI 根据字段信息生成的<b>草稿注释，当前是「待复核」，还没进检索</b>。
        可以直接在上面改；你不改的话，这张表下次重新开放权限时会被新一版覆盖。</span>`;
  }
  if (source === "manual") {
    return `<span class="status-tag success">人工</span>
      <span>已定稿 —— 这张表重新开放权限时<b>不会被</b> AI 覆盖。</span>`;
  }
  return `<span class="status-tag gray">待补</span>
    <span>还没有表注释 —— 点下面按钮让 AI 生成，或先在「表与权限」里开放这张表。</span>`;
}

/** 只刷新来源提示行（生成完 / 保存完单独调它，比整块重绘轻） */
function kbRefreshSrcNote(source, generating) {
  const box = document.getElementById("kbSrcNote");
  if (box) box.innerHTML = kbSrcNoteHtml(source, generating);
  const btn = document.getElementById("kbDocBtn");
  if (btn) btn.textContent = source === "manual" ? "用 AI 重新起草" : "AI 生成表注释";
}

/** 轮询：等后台生成完，把新注释直接填进输入框（它此刻已经生效进 RAG 了） */
function kbPollTableComment(table) {
  clearTimeout(kbDocTimer);
  let tries = 0;
  const tick = async () => {
    tries++;
    try {
      const d = await api(`/api/kb/table/${encodeURIComponent(table)}`);
      if (kbCurrent !== table) return;              // 用户已切到别的表 → 别乱改界面
      if (!d.doc_generating || tries >= 20) {
        const ta = document.getElementById("kbTableComment");
        if (ta && d.comment && !ta.value.trim()) ta.value = d.comment;
        kbRefreshSrcNote(d.comment_source, d.doc_generating);
        kbLoadTables();                             // 刷新列表上的「AI 生成」标记
        return;
      }
      kbRefreshSrcNote(d.comment_source, true);
    } catch (e) {
      return;                                       // 轮询失败就静默停，不打扰用户
    }
    kbDocTimer = setTimeout(tick, 2500);
  };
  kbDocTimer = setTimeout(tick, 2500);
}

/** 手动生成：结果直接写进 table_comments(source='ai')，所以生成完要重开这张表 */
async function kbSuggestTableComment() {
  const table = kbCurrent;
  const btn = document.getElementById("kbDocBtn");
  //   ★ 人工定稿过的要问一句 —— 重新生成会覆盖掉人精修的内容
  if (btn && btn.textContent.includes("重新起草")
      && !confirm("这份注释你改过。用 AI 重新起草会覆盖它，确定？")) return;
  btn.disabled = true; btn.textContent = "读字段中…";
  kbRefreshSrcNote("ai", true);
  try {
    const r = await api(`/api/kb/table/${encodeURIComponent(table)}/suggest-comment`,
                        { method: "POST" });
    if (kbCurrent !== table) return;                // 期间用户切走了
    if (!r.suggested) throw new Error("模型没返回内容");
    const ta = document.getElementById("kbTableComment");
    if (ta) ta.value = r.suggested;                 // 已生效的注释 → 直接显示出来给人改
    kbRefreshSrcNote("ai", false);
    kbLoadTables();
    alert("已生成草稿（待复核，暂不进检索）：可在上面直接修改，改完点「保存表注释」即核准生效。"
          + "\n★ 点『重建索引』后检索效果最佳");
  } catch (e) {
    kbRefreshSrcNote("", false);
    alert("生成失败：" + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 生成表注释";
  }
}

