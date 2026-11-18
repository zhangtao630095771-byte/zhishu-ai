/* ══════════════════════════════════════════════════════════
   semantic-modeling.js · 语义建模（语义中心 · 阶段三）
   ★ 对应 index.html 的 tab-semantic 页面的三个建模 Tab：
     关系模型 / 指标管理 / 维度管理（OSSIE 导入导出仍由 semantic-model.js 承载）。
   ★ 依赖全局：api()、esc()、smExport()/smRefresh()/smPreviewImport()（semantic-model.js）。
   ══════════════════════════════════════════════════════════ */

let smmModels = [];          // 关系模型（业务主题）列表
let smmMetrics = [];         // 指标列表
let smmMetricFilter = "all"; // 指标分类筛选
let smmCurrent = null;       // 当前关系模型
let smmDims = [];            // 已登记维度
let smmDimCands = [];        // 维度候选

/** Tab 切换 */
function smModelTab(tab, btn) {
  ["model", "metric", "dim", "ossie"].forEach(k => {
    document.getElementById("smPane-" + k).classList.toggle("active", k === tab);
    const b = document.getElementById("smTab" + k.charAt(0).toUpperCase() + k.slice(1));
    if (b) b.classList.toggle("active", k === tab);
  });
  if (tab === "ossie") { if (typeof smLoadPage === "function") smLoadPage(); }
  if (tab === "metric") smModelRenderMetrics();
  if (tab === "dim") { smModelLoadDims(); }
  if (tab === "model") smModelRenderModels();
}

/** 初始化：加载关系模型 + 指标 + 维度 */
async function smModelLoad() {
  try {
    const [models, metrics] = await Promise.all([
      api("/api/modeling/models"),
      api("/api/modeling/metrics"),
    ]);
    smmModels = models.items || [];
    smmMetrics = metrics.items || [];
    smModelRenderModels();
    smModelRenderMetrics();
  } catch (e) { /* 页面未打开时忽略 */ }
}

/* ═══════════════ Tab 1：关系模型 ═══════════════ */
function smModelRenderModels() {
  const box = document.getElementById("smModelList");
  if (!box) return;
  if (!smmModels.length) { box.innerHTML = `<div class="empty">暂无关系模型（业务主题）</div>`; return; }
  box.innerHTML = "";
  smmModels.forEach(m => {
    const st = m.stats || {};
    const div = document.createElement("div");
    div.className = "list-item" + (m.name === smmCurrent ? " active" : "");
    div.innerHTML = `
      <div class="row" style="justify-content:space-between">
        <span>${esc(m.display_name || m.name)}</span>
        ${m.enabled ? '<span class="tag on" style="font-size:10px">启用</span>' : '<span class="tag off" style="font-size:10px">停用</span>'}
      </div>
      <div class="muted" style="font-size:11px">${esc(m.name)} · 实体 ${st.entity ?? 0} · 指标 ${st.metric ?? 0} · 关系 ${st.relationship ?? 0}</div>`;
    div.onclick = () => smModelSelectModel(m.name, div);
    box.appendChild(div);
  });
}

async function smModelSelectModel(key, el) {
  smmCurrent = key;
  if (el) {
    document.querySelectorAll("#smModelList .list-item").forEach(e => e.classList.remove("active"));
    el.classList.add("active");
  }
  const box = document.getElementById("smModelDetail");
  box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await api(`/api/modeling/models/${encodeURIComponent(key)}`);
    smModelRenderDetail(d);
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function smModelRenderDetail(d) {
  const box = document.getElementById("smModelDetail");
  const s = d.subject || {};
  const tables = d.tables || [];
  const joins = d.joins || [];
  const metrics = d.metrics || [];
  const dims = d.dimensions || [];

  const roleTag = r => r === "primary"
    ? '<span class="tag on" style="font-size:10px">主表</span>'
    : r === "satellite" ? '<span class="tag" style="background:var(--color-warning-bg);color:var(--color-warning);font-size:10px">卫星表</span>'
    : '<span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary);font-size:10px">关联表</span>';

  box.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div>
        <b style="font-size:16px">${esc(s.display_name || s.name)}</b>
        <span class="muted" style="font-size:12px;margin-left:8px">${esc(s.name)}</span>
        ${s.domain_name ? `<span class="muted" style="font-size:12px;margin-left:8px">域 ${esc(s.domain_name)}</span>` : ""}
      </div>
      <button class="ghost" onclick="smModelAddTable('${esc(s.name)}')">+ 登记物理表</button>
    </div>
    ${s.description ? `<div class="muted" style="font-size:12px;margin-top:6px">${esc(s.description)}</div>` : ""}

    <h4 style="margin:16px 0 8px">物理表（${tables.length}）</h4>
    ${tables.length ? `<div style="max-height:220px;overflow:auto">${tables.map(t => `
      <div class="row" style="justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--color-border);font-size:12px">
        <div>
          <b>${esc(t.table_name)}</b>
          ${t.dataset ? `<span class="muted" style="font-size:11px;margin-left:6px">${esc(t.dataset)}</span>` : ""}
        </div>
        <div class="row" style="gap:6px">
          <span class="muted" style="font-size:11px">字段 ${t.fields ?? 0}</span>
          ${roleTag(t.role)}
          ${t.source === "explicit" ? `<button class="ghost" style="padding:0 4px;font-size:11px" onclick="smModelDelTable(${t.id})">删</button>` : ""}
        </div>
      </div>`).join("")}</div>` : `<div class="muted" style="font-size:12px">无（关联实体后自动派生）</div>`}

    <h4 style="margin:16px 0 8px">表间 Join（${joins.length}）</h4>
    ${joins.length ? `<div style="max-height:180px;overflow:auto">${joins.map(j => `
      <div class="row" style="justify-content:space-between;padding:7px 0;border-bottom:1px solid var(--color-border);font-size:12px">
        <div>
          <b>${esc(j.from_table)}</b> → <b>${esc(j.to_table)}</b>
          ${j.join_sql ? `<div class="sql-hl" style="font-size:11px;margin-top:3px;padding:4px 6px">${esc(j.join_sql)}</div>` : ""}
        </div>
        <div class="row" style="gap:6px">
          ${j.join_type ? `<span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary);font-size:10px">${esc(j.join_type)}</span>` : ""}
          ${j.cardinality ? `<span class="tag" style="background:var(--color-warning-bg);color:var(--color-warning);font-size:10px">${esc(j.cardinality)}</span>` : ""}
          ${j.source === "explicit" ? `<button class="ghost" style="padding:0 4px;font-size:11px" onclick="smModelDelJoin(${j.id})">删</button>` : ""}
        </div>
      </div>`).join("")}</div>` : `<div class="muted" style="font-size:12px">无（关系在「本体建模/语义注册表」维护，按实体主表自动映射）</div>`}
    <button class="ghost" style="width:100%;margin-top:6px" onclick="smModelAddJoin('${esc(s.name)}')">+ 登记表级 Join</button>

    <h4 style="margin:16px 0 8px">关联指标（${metrics.length}）</h4>
    <div style="max-height:140px;overflow:auto">
      ${metrics.length ? metrics.map(m => `<span class="tag" style="margin:2px;background:var(--color-bg-subtle);color:var(--color-text-secondary)">${esc(m.name)}</span>`).join("") : `<span class="muted" style="font-size:12px">无</span>`}
    </div>

    <h4 style="margin:16px 0 8px">关联维度（${dims.length}）</h4>
    <div style="max-height:120px;overflow:auto">
      ${dims.length ? dims.map(m => `<span class="tag" style="margin:2px;background:var(--color-primary-subtle);color:var(--color-primary)">${esc(m.name)}</span>`).join("") : `<span class="muted" style="font-size:12px">无（在「维度管理」Tab 登记）</span>`}
    </div>`;
}

async function smModelAddTable(modelKey) {
  const tableName = prompt("物理表名：", "");
  if (!tableName || !tableName.trim()) return;
  const role = prompt("表角色（primary 主表 / related 关联表 / satellite 卫星表）：", "related");
  try {
    await api("/api/modeling/tables", {
      method: "POST",
      body: JSON.stringify({ model_key: modelKey, table_name: tableName.trim(), role: role || "related" }),
    });
    if (smmCurrent) smModelSelectModel(smmCurrent);
  } catch (e) { alert("登记失败：" + e.message); }
}

async function smModelDelTable(id) {
  if (!confirm("删除该表登记？")) return;
  try {
    await api(`/api/modeling/tables/${id}`, { method: "DELETE" });
    if (smmCurrent) smModelSelectModel(smmCurrent);
  } catch (e) { alert("删除失败：" + e.message); }
}

async function smModelAddJoin(modelKey) {
  const fromTable = prompt("From 表：", "");
  if (!fromTable) return;
  const toTable = prompt("To 表：", "");
  if (!toTable) return;
  const joinType = prompt("Join 类型（inner/left/right/full）：", "inner");
  const cardinality = prompt("基数（1:1 / 1:N / N:1 / N:M）：", "1:N");
  try {
    await api("/api/modeling/joins", {
      method: "POST",
      body: JSON.stringify({
        model_key: modelKey, from_table: fromTable.trim(), to_table: toTable.trim(),
        join_type: joinType || "inner", cardinality: cardinality || "1:N",
      }),
    });
    if (smmCurrent) smModelSelectModel(smmCurrent);
  } catch (e) { alert("登记失败：" + e.message); }
}

async function smModelDelJoin(id) {
  if (!confirm("删除该 Join 登记？")) return;
  try {
    await api(`/api/modeling/joins/${id}`, { method: "DELETE" });
    if (smmCurrent) smModelSelectModel(smmCurrent);
  } catch (e) { alert("删除失败：" + e.message); }
}

/* ═══════════════ Tab 2：指标管理 ═══════════════ */
const SMM_CAT_ZH = { atomic: "原子指标", derived: "衍生指标", composite: "复合指标" };

function smModelFilterMetrics(cat, btn) {
  if (cat) {
    smmMetricFilter = cat;
    ["all", "atomic", "derived", "composite"].forEach(k => {
      const b = document.getElementById("smMetric" + k.charAt(0).toUpperCase() + k.slice(1));
      if (b) b.style.cssText = k === cat ? "background:var(--color-primary-subtle);color:var(--color-primary)" : "";
    });
  }
  smModelRenderMetrics();
}

function smModelRenderMetrics() {
  const box = document.getElementById("smMetricTable");
  if (!box) return;
  const kw = (document.getElementById("smMetricSearch")?.value || "").trim().toLowerCase();
  let list = smmMetrics;
  if (smmMetricFilter !== "all") list = list.filter(m => m.metric_category === smmMetricFilter);
  if (kw) list = list.filter(m => (m.name || "").toLowerCase().includes(kw) || (m.metric_code || "").toLowerCase().includes(kw));

  if (!list.length) { box.innerHTML = `<tr><td class="empty">无匹配指标</td></tr>`; return; }
  const catColor = { atomic: "var(--color-bg-subtle),var(--color-text-secondary)", derived: "var(--color-primary-subtle),var(--color-primary)", composite: "var(--color-warning-bg),var(--color-warning)" };
  box.innerHTML = list.map(m => {
    const [bg, fg] = (catColor[m.metric_category] || "var(--color-bg-subtle),var(--color-text-secondary)").split(",");
    return `<tr>
      <td><b>${esc(m.name)}</b>${m.metric_code ? `<span class="muted" style="font-size:11px;margin-left:6px">${esc(m.metric_code)}</span>` : ""}</td>
      <td><span class="tag" style="background:${bg};color:${fg};font-size:10px">${esc(m.category_zh || SMM_CAT_ZH[m.metric_category] || "原子指标")}</span></td>
      <td><span class="muted" style="font-size:11px">${esc(m.metric_type || "")}</span></td>
      <td style="max-width:320px"><span class="muted" style="font-size:12px">${esc((m.definition || "").slice(0, 60))}${(m.definition || "").length > 60 ? "…" : ""}</span></td>
      <td>${m.expression ? `<span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary);font-size:10px">已提取表达式</span>` : `<span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary);font-size:10px">${esc(m.extract_status || "纯文本")}</span>`}</td>
      <td><button class="ghost" style="padding:0 6px;font-size:12px" onclick="smMetricEdit('${esc(m.name)}')">编辑</button></td>
    </tr>`;
  }).join("");
}

async function smMetricEdit(name) {
  const m = smmMetrics.find(x => x.name === name);
  if (!m) return;
  const code = prompt("英文标识（留空不修改）：", m.metric_code || "");
  const cat = prompt("三分类（atomic 原子 / derived 衍生 / composite 复合，留空不修改）：", m.metric_category || "atomic");
  try {
    await api(`/api/modeling/metrics/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ metric_code: code || "", metric_category: cat || "" }),
    });
    // 刷新指标列表
    const r = await api("/api/modeling/metrics");
    smmMetrics = r.items || [];
    smModelRenderMetrics();
  } catch (e) { alert("保存失败：" + e.message); }
}

/* ═══════════════ Tab 3：维度管理 ═══════════════ */
async function smModelLoadDims() {
  try {
    const [dims, cands] = await Promise.all([
      api("/api/modeling/dimensions"),
      api("/api/modeling/dimension-candidates"),
    ]);
    smmDims = dims.items || [];
    smmDimCands = cands.items || [];
    smModelRenderDims();
  } catch (e) { /* 忽略 */ }
}

function smModelRenderDims() {
  document.getElementById("smDimCount").textContent = smmDims.length;
  document.getElementById("smDimCandCount").textContent = smmDimCands.length;
  const dt = document.getElementById("smDimTable");
  dt.innerHTML = smmDims.length
    ? smmDims.map(m => `<tr>
        <td><b>${esc(m.name)}</b><span class="muted" style="font-size:11px;margin-left:6px">${esc(m.dimension_key)}</span></td>
        <td><span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary);font-size:10px">${esc(m.dimension_type || "other")}</span></td>
        <td><span class="muted" style="font-size:12px">${esc(m.source_table || "")}${m.source_column ? "." + esc(m.source_column) : ""}</span></td>
        <td><span class="muted" style="font-size:12px">${esc(m.entity_key || "")}</span></td>
        <td><button class="ghost" style="padding:0 6px;font-size:12px" onclick="smModelDelDim(${m.id})">删</button></td>
      </tr>`).join("")
    : `<tr><td class="empty">暂无已登记维度</td></tr>`;

  const ct = document.getElementById("smDimCandTable");
  ct.innerHTML = smmDimCands.length
    ? smmDimCands.map((c, i) => `<tr>
        <td><b>${esc(c.source_column)}</b><span class="muted" style="font-size:11px;margin-left:6px">${esc(c.source_table)}</span></td>
        <td><span class="tag" style="background:var(--color-warning-bg);color:var(--color-warning);font-size:10px">${esc(c.dimension_type)}</span></td>
        <td style="max-width:260px"><span class="muted" style="font-size:12px">${esc((c.comment || "").slice(0, 40))}</span></td>
        <td><button class="ghost" style="padding:0 6px;font-size:12px" onclick="smModelAdoptDim(${i})">采纳</button></td>
      </tr>`).join("")
    : `<tr><td class="empty">无派生候选</td></tr>`;
}

async function smModelAdoptDim(idx) {
  const c = smmDimCands[idx];
  if (!c) return;
  const dk = `${c.source_table}.${c.source_column}`.replace(/[^a-zA-Z0-9_.]/g, "_");
  try {
    await api("/api/modeling/dimensions", {
      method: "POST",
      body: JSON.stringify({
        dimension_key: dk, name: c.source_column,
        dimension_type: c.dimension_type, entity_key: c.entity_key,
        source_table: c.source_table, source_column: c.source_column,
      }),
    });
    await smModelLoadDims();
  } catch (e) { alert("采纳失败：" + e.message); }
}

async function smModelDelDim(id) {
  if (!confirm("删除该维度？")) return;
  try {
    await api(`/api/modeling/dimensions/${id}`, { method: "DELETE" });
    await smModelLoadDims();
  } catch (e) { alert("删除失败：" + e.message); }
}

/* ═══════════════ AI 智能建模（业务场景 → 完整语义模型）═══════════════ */
async function smAiModel() {
  const text = prompt("用自然语言描述业务场景（涉及哪些表 / 实体 / 指标 / 维度）…", "");
  if (!text || !text.trim()) return;
  try {
    const r = await api("/api/modeling/ai-model", { method: "POST", body: JSON.stringify({ text: text.trim() }) });
    const results = r.results || [];
    const lines = results.map(x =>
      `${x.type}「${x.name}」→ ${x.status === "ok" ? "已录入（待审核）" : "跳过：" + (x.message || "")}`).join("\n");
    alert(`AI 智能建模完成\n\n业务主题：${r.model_display || r.model_name || "（未识别）"}\n${r.model_description ? "说明：" + r.model_description + "\n" : ""}\n${lines || "未生成任何对象"}\n\n★ 生成内容均为「待审核」，请到「知识治理 → 知识审核」人工通过后才会被取数链路使用。`);
    smModelLoad();
  } catch (e) {
    alert("AI 建模失败：" + e.message);
  }
}
