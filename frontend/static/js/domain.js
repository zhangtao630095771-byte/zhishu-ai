/* ══════════════════════════════════════════════════════════════
   domain.js · 业务域管理 + 业务主题 + 语义模型发布
   ★ 对应 index.html 的 tab-domain 页面。
   ★ 业务域是企业业务组织的上层分类，组织业务主题与语义模型。
   ★ 依赖全局：api()、esc()、getToken()、handleUnauthorized()。
   ══════════════════════════════════════════════════════════════ */

let dmCurrent = null;        // 当前选中的业务域名
let dmDomains = [];          // 域列表缓存

/** 页面懒加载入口 */
async function loadDomainPage() {
  await dmRenderList();
}

/** 语义资产总量（Entity 与 Dataset 同源，不重复计） */
function dmAssetTotal(stats) {
  if (!stats) return 0;
  return (stats.dataset || 0) + (stats.metric || 0) + (stats.dimension || 0) + (stats.relationship || 0);
}

/** 渲染统计卡片网格（复用 app.css 的 .stat-grid/.stat-card） */
function dmStatGrid(stats, sqlCase) {
  const st = stats || {};
  const items = [
    ["blue", st.dataset || 0, "Dataset 数据集"],
    ["blue", st.metric || 0, "Metric 指标"],
    ["green", st.dimension || 0, "Dimension 维度"],
    ["orange", st.entity || 0, "Entity 实体"],
    ["orange", st.relationship || 0, "Relationship 关系"],
  ];
  let html = `<div class="stat-grid">`;
  html += items.map(([c, v, l]) =>
    `<div class="stat-card ${c}"><div class="stat-val">${v}</div><div class="stat-label">${l}</div></div>`
  ).join("");
  if (sqlCase !== undefined && sqlCase !== null) {
    html += `<div class="stat-card gray"><div class="stat-val">${sqlCase}</div><div class="stat-label">SQL 案例</div></div>`;
  }
  html += `</div>`;
  return html;
}

/** 渲染业务域列表项（可过滤，供列表加载与搜索复用） */
function dmRenderDomainItems(list) {
  const box = document.getElementById("dmList");
  if (!box) return;
  box.innerHTML = "";
  list.forEach(m => {
    const div = document.createElement("div");
    div.className = "list-item" + (m.name === dmCurrent ? " active" : "");
    const subjects = m.subjects || [];
    const st = m.stats || {};
    const subjTxt = subjects.length ? `${subjects.length} 主题` : "无主题";
    const assetTxt = `${dmAssetTotal(st)} 资产`;
    const onTag = m.enabled
      ? '<span class="status-tag success" style="font-size:10px;padding:0 6px">已启用</span>'
      : '<span class="status-tag gray" style="font-size:10px;padding:0 6px">停用</span>';
    div.innerHTML = `
      <div class="row" style="justify-content:space-between">
        <span>${esc(m.display_name || m.name)}</span>
        <span class="muted" style="font-size:12px">${esc(m.name)}</span>
      </div>
      <div class="muted" style="font-size:12px">${esc(subjTxt)} · ${assetTxt}${m.owner ? " · " + esc(m.owner) : ""}</div>
      <div class="row" style="justify-content:space-between;margin-top:4px">
        <span class="muted" style="font-size:11px">${m.department ? esc(m.department) : ""}</span>
        ${onTag}
      </div>`;
    div.onclick = () => dmSelectDomain(m.name, div);
    box.appendChild(div);
  });
}

/** 渲染业务域列表（懒加载入口） */
async function dmRenderList() {
  const box = document.getElementById("dmList");
  if (!box) return;
  box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await api("/api/domains");
    dmDomains = d.items || [];
    if (!dmDomains.length) {
      box.innerHTML = `<div class="empty">还没有业务域<br>点「新建业务域」创建（订单 / 司机 / 财务 …）</div>`;
      smLoadOverview(0);
      return;
    }
    dmRenderDomainItems(dmDomains);
    smLoadOverview();
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/** 数据概览：聚合所有业务域 stats，得到全局语义资产统计
 *  ★ 关系模型数 = 业务主题数（一个业务主题 = 一个关系模型，已拍板）。 */
function smLoadOverview(domainCount) {
  let domains, ent = 0, metric = 0, dim = 0, models = 0;
  if (domainCount !== undefined) {
    domains = domainCount;
  } else {
    domains = dmDomains.length;
    dmDomains.forEach(m => {
      const st = m.stats || {};
      ent += st.entity || 0;
      metric += st.metric || 0;
      dim += st.dimension || 0;
      models += (m.subjects || []).length;
    });
  }
  const set = (id, v) => { const el = document.getElementById(id); if (el) el.textContent = v; };
  set("smStatDomains", domains);
  set("smStatEntities", ent);
  set("smStatMetrics", metric);
  set("smStatDims", dim);
  set("smStatModels", models);
}

/** 搜索过滤业务域（不重新拉 API，直接过滤缓存 dmDomains） */
function dmFilterList() {
  const kw = (document.getElementById("dmSearch")?.value || "").trim().toLowerCase();
  const list = kw
    ? dmDomains.filter(m => (m.name || "").toLowerCase().includes(kw) || (m.display_name || "").toLowerCase().includes(kw))
    : dmDomains;
  dmRenderDomainItems(list);
}

/** 快捷入口：AI 智能建模 → 跳转知识资产「智能录入」（复用现有 AI 录入能力） */
function smGotoIngest() {
  const navBtn = document.querySelector('.nav-item[data-tab="kb"]');
  switchTab("kb", navBtn);
  const btn = document.getElementById("kbTabIngest");
  if (btn) kbSwitchModule("ingest", btn);
}

/** 快捷入口：导入历史资产 → 跳转语义建模「OSSIE 导入」 */
function smGotoImport() {
  const navBtn = document.querySelector('.nav-item[data-tab="semantic"]');
  switchTab("semantic", navBtn);
}

/** 渲染单个业务主题卡片（含分类资产数 + 操作按钮） */
function dmSubjectCard(s, domainName) {
  const st = s.stats || {};
  const chips = [
    `Dataset ${st.dataset ?? 0}`, `Metric ${st.metric ?? 0}`, `Dimension ${st.dimension ?? 0}`,
    `Entity ${st.entity ?? 0}`, `Relationship ${st.relationship ?? 0}`,
  ];
  const name = esc(s.name);
  return `
    <div class="card" style="margin-bottom:12px;padding:14px 16px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:8px">
        <div>
          <b>${esc(s.display_name || s.name)}</b>
          <span class="muted" style="font-size:12px;margin-left:8px">${name} · ${esc(domainName)}</span>
        </div>
        <div class="row" style="gap:6px;flex-wrap:wrap">
          <button class="ghost" onclick="dmEditSubject('${name}')">编辑主题</button>
          <button class="ghost" onclick="dmManageSemantic('${name}')">管理语义模型</button>
          <button class="ghost" onclick="dmViewRelations('${name}')">查看模型关系</button>
          <button class="ghost" onclick="dmPublishModel('${name}')">发布模型</button>
          <button class="ghost danger" onclick="dmDeleteSubject('${name}')">删除</button>
        </div>
      </div>
      ${s.description ? `<div class="muted" style="margin-top:6px">${esc(s.description)}</div>` : ""}
      <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:10px">
        ${chips.map(c => `<span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary)">${c}</span>`).join("")}
      </div>
    </div>`;
}

/** 选中域，渲染详情 */
async function dmSelectDomain(name, el) {
  dmCurrent = name;
  if (el) {
    document.querySelectorAll("#dmList .list-item").forEach(e => e.classList.remove("active"));
    el.classList.add("active");
  }
  document.getElementById("dmTitle").textContent = name;
  document.getElementById("dmEditBtn").style.display = "";
  document.getElementById("dmDeleteBtn").style.display = "";
  const box = document.getElementById("dmDetail");
  box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await api(`/api/domains/${encodeURIComponent(name)}`);
    const subjects = d.subjects || [];
    const st = d.stats || {};
    const subjectHtml = subjects.map(s => dmSubjectCard(s, name)).join("") || `<div class="muted">暂无主题</div>`;

    box.innerHTML = `
      <div class="row" style="gap:10px;flex-wrap:wrap;margin-bottom:8px">
        <b style="font-size:16px">${esc(d.display_name || d.name)}</b>
        <span class="muted" style="font-size:12px">${esc(name)}</span>
        ${d.enabled ? '<span class="status-tag success">启用</span>' : '<span class="status-tag gray">停用</span>'}
        ${d.owner ? `<span class="muted" style="font-size:12px">负责人：${esc(d.owner)}</span>` : ""}
        ${d.department ? `<span class="muted" style="font-size:12px">部门：${esc(d.department)}</span>` : ""}
      </div>
      ${d.description ? `<div class="muted" style="margin-bottom:14px">${esc(d.description)}</div>` : `<div style="height:6px"></div>`}
      <h4 style="margin:4px 0 10px">业务资产统计</h4>
      ${dmStatGrid(st, st.sql_case)}
      <div class="row" style="justify-content:space-between;margin:12px 0 8px">
        <h4 style="margin:0">业务主题（${subjects.length}）</h4>
        <button class="ghost" onclick="dmNewSubject()">+ 新建主题</button>
      </div>
      ${subjectHtml}`;
  } catch (e) {
    box.innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
  }
}

/* ══════════════════════════════════════════════════════════
   业务主题详情抽屉（Tab 式）—— P2 核心改造
   ★ 7 Tab：基本信息 / Dataset / Metric / Dimension / Entity / Relationship / 关联知识
   ★ 铁律：Dataset=实体派生、Dimension=字段维度派生、Relationship=端点派生，
     主题只做【组织 + 关联】，绝不复制语义资产本体。
   ══════════════════════════════════════════════════════════ */
const SM_TABS = [
  ["basic", "基本信息"], ["dataset", "Dataset"], ["metric", "Metric"],
  ["dimension", "Dimension"], ["entity", "Entity"],
  ["relationship", "Relationship"], ["knowledge", "关联知识"],
];

let smSubjName = "";        // 当前主题名
let smSubjDomain = "";      // 归属业务域
let smSubjInfo = null;      // 主题基本信息
let smSubjObjects = null;   // 语义对象 {datasets, metrics, relationships, un_mapped_metrics}
let smSubjEntities = [];    // 全部实体（关联候选）
let smSubjMetrics = [];     // 全部指标（关联候选 + 补充口径/来源表）
let smSubjRelations = [];   // 全部关系（补充 JOIN 类型/基数）
let smSubjDocs = [];        // 全部文档（关联候选）
let smSubjAssets = [];      // 主题已关联资产 [{asset_type, asset_key}]
let smSubjTab = "basic";    // 当前 Tab

/** 管理语义模型：打开主题详情抽屉 */
async function dmManageSemantic(subjectName) {
  smSubjName = subjectName;
  smSubjTab = "basic";
  document.getElementById("smSubjectTitle").textContent = `业务主题「${subjectName}」`;
  document.getElementById("smSubjectBody").innerHTML = `<div class="empty">加载中…</div>`;
  document.getElementById("smSubjectMask").classList.remove("hidden");
  dmRenderSubjectTabs();
  try {
    const [subjects, objects, entities, metrics, relations, docs] = await Promise.all([
      api("/api/subjects"),
      api(`/api/semantic/objects?subject=${encodeURIComponent(subjectName)}`),
      api("/api/kb/entities"),
      api("/api/metrics"),
      api("/api/kb/relationships"),
      api("/api/kb/docs"),
    ]);
    const subj = (subjects.items || []).find(s => s.name === subjectName);
    smSubjInfo = subj || null;
    smSubjDomain = (subj && subj.domain_name) || "";
    smSubjAssets = (subj && subj.assets) || [];
    smSubjObjects = objects || null;
    smSubjEntities = Array.isArray(entities) ? entities : [];
    smSubjMetrics = Array.isArray(metrics) ? metrics : [];
    smSubjRelations = Array.isArray(relations) ? relations : [];
    smSubjDocs = Array.isArray(docs) ? docs : [];
  } catch (e) {
    document.getElementById("smSubjectBody").innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
    return;
  }
  dmRenderSubjectTab();
}

/** 关闭主题详情抽屉 */
function dmCloseSubject() {
  document.getElementById("smSubjectMask").classList.add("hidden");
}

/** 渲染 Tab 导航 */
function dmRenderSubjectTabs() {
  const box = document.getElementById("smSubjectTabs");
  box.innerHTML = SM_TABS.map(([k, label]) => {
    const on = k === smSubjTab;
    return `<button class="ghost" style="${on ? "background:var(--color-primary-subtle);color:var(--color-primary);border-color:var(--color-info-border);" : ""}white-space:nowrap" onclick="dmSubjectTab('${k}')">${label}</button>`;
  }).join("");
}

/** 切换 Tab */
function dmSubjectTab(tab) {
  smSubjTab = tab;
  dmRenderSubjectTabs();
  dmRenderSubjectTab();
}

/** 渲染当前 Tab 内容 */
function dmRenderSubjectTab() {
  const body = document.getElementById("smSubjectBody");
  const fn = {
    basic: dmRenderBasic, dataset: dmRenderDataset, metric: dmRenderMetric,
    dimension: dmRenderDimension, entity: dmRenderEntity,
    relationship: dmRenderRelationship, knowledge: dmRenderKnowledge,
  }[smSubjTab];
  body.innerHTML = fn ? fn() : `<div class="empty">待实现</div>`;
}

/** 主题已关联某类型资产的 key 集合 */
function smLinkedKeys(type) {
  return new Set((smSubjAssets || []).filter(a => a.asset_type === type).map(a => a.asset_key));
}

/** 跳转到知识管理页对应二级模块（编辑资产本体，避免在主题里重复造编辑表单） */
function dmGotoKb(module) {
  dmCloseSubject();
  const navBtn = document.querySelector('.nav-item[data-tab="kb"]');
  switchTab("kb", navBtn);
  const idMap = { registry: "kbTabRegistry", metric: "kbTabMetric", schema: "kbTabSchema" };
  const btn = document.getElementById(idMap[module]);
  if (btn) kbSwitchModule(module, btn);
}

/* ── Tab 1：基本信息 ── */
function dmRenderBasic() {
  const s = smSubjInfo || {};
  return `
    <label>主题名称</label>
    <input id="smBasicDisplay" value="${esc(s.display_name || s.name || "")}" placeholder="如 订单经营">
    <label>主题编码（不可修改）</label>
    <input value="${esc(smSubjName)}" disabled>
    <label>所属业务域（不可修改）</label>
    <input value="${esc(smSubjDomain)}" disabled>
    <label>主题描述</label>
    <textarea id="smBasicDesc" placeholder="该主题覆盖的分析范围…">${esc(s.description || "")}</textarea>
    <label class="row" style="gap:6px;margin-top:12px">
      <input type="checkbox" id="smBasicEnabled" ${s.enabled ? "checked" : ""} style="width:auto">
      <span style="font-size:13px;color:var(--color-text-secondary)">启用该主题</span>
    </label>
    <div class="row" style="gap:8px;margin-top:14px">
      <button class="btn" onclick="dmSaveSubjectBasic()">保存</button>
    </div>`;
}

async function dmSaveSubjectBasic() {
  const display = document.getElementById("smBasicDisplay").value.trim();
  const desc = document.getElementById("smBasicDesc").value.trim();
  const enabled = document.getElementById("smBasicEnabled").checked;
  try {
    await api(`/api/subjects/${encodeURIComponent(smSubjName)}`, {
      method: "PUT",
      body: JSON.stringify({ name: smSubjName, domain_name: smSubjDomain, display_name: display, description: desc, enabled: enabled }),
    });
    smSubjInfo = { ...(smSubjInfo || {}), display_name: display, description: desc, enabled: enabled };
    alert("已保存");
    if (dmCurrent) dmSelectDomain(dmCurrent);
  } catch (e) { alert("保存失败：" + e.message); }
}

/* ── Tab 2：Dataset（实体派生：物理表/字段/粒度/主键）── */
function dmRenderDataset() {
  const datasets = (smSubjObjects && smSubjObjects.datasets) || [];
  const list = datasets.map(d => {
    const fields = d.fields || [];
    const dims = fields.filter(f => f.dimension || f.label).length;
    const pk = d.primary_key || [];
    return `<div class="card" style="margin-bottom:10px;padding:12px 14px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
        <b>${esc(d.name)}</b>
        <span class="muted" style="font-size:12px">${esc(d.source || "")}</span>
      </div>
      <div class="muted" style="font-size:12px;margin-top:4px">
        ${d.description ? esc(d.description) + " · " : ""}字段 ${fields.length} · 维度 ${dims}${pk.length ? " · 主键 " + esc(pk.join(",")) : ""}
      </div>
      <details style="margin-top:6px"><summary class="muted" style="cursor:pointer;font-size:12px">查看字段</summary>
        <div style="font-size:12px;margin-top:6px;max-height:200px;overflow:auto">
          ${fields.map(f => `<span class="tag" style="margin:2px;${(f.dimension || f.label) ? "background:var(--color-primary-subtle);color:var(--color-primary);" : ""}">${esc(f.name)}${f.dimension ? "·时间" : f.label ? "·" + esc(f.label) : ""}</span>`).join("")}
        </div>
      </details>
      <div class="row" style="gap:6px;margin-top:8px">
        <button class="ghost" onclick="dmUnlinkAsset('entity','${esc(d.name)}')">取消关联</button>
        <button class="ghost" onclick="dmGotoKb('registry')">编辑实体</button>
      </div>
    </div>`;
  }).join("") || `<div class="muted">暂无数据集（关联业务实体后自动派生）</div>`;

  return `
    <div class="alert info">★ Dataset 由<b>业务实体</b>派生（实体 = 数据集，含物理表/字段/粒度/主键），主题只做关联，不重复维护。</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">已关联数据集（${datasets.length}）</h4>
      <button class="ghost" onclick="dmLinkAsset('entity','数据集')">+ 关联数据集</button>
    </div>
    ${list}`;
}

/* ── Tab 3：Metric（指标口径/公式/来源数据集）── */
function dmRenderMetric() {
  const metrics = (smSubjObjects && smSubjObjects.metrics) || [];
  const unMapped = (smSubjObjects && smSubjObjects.un_mapped_metrics) || [];
  const fullMap = {};
  smSubjMetrics.forEach(m => fullMap[m.name] = m);
  const list = metrics.map(m => {
    const full = fullMap[m.name] || {};
    const tables = full.tables || [];
    return `<div class="card" style="margin-bottom:10px;padding:12px 14px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
        <b>${esc(m.name)}</b>
        <div class="row" style="gap:6px">
          <button class="ghost" onclick="dmUnlinkAsset('metric','${esc(m.name)}')">取消关联</button>
          <button class="ghost" onclick="dmGotoKb('metric')">编辑口径</button>
        </div>
      </div>
      ${m.expression && m.expression.sql ? `<div class="sql-hl" style="font-size:12px;padding:6px 8px;margin-top:6px;border-radius:6px">${esc(m.expression.sql)}</div>` : ""}
      ${m.description ? `<div class="muted" style="font-size:12px;margin-top:6px">${esc(m.description)}</div>` : ""}
      ${tables.length ? `<div class="muted" style="font-size:12px;margin-top:4px">来源数据集：${esc(tables.join("、"))}</div>` : ""}
    </div>`;
  }).join("") || `<div class="muted">暂无指标</div>`;
  const unList = unMapped.length ? `<div class="alert warn" style="margin-top:10px">★ ${unMapped.length} 个指标为纯文本口径（无标准 SQL 表达式）：${unMapped.map(esc).join("、")}</div>` : "";

  return `
    <div class="alert info">★ 指标口径与计算公式来自<b>指标知识库</b>（不在此复制维护），主题只做关联。</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">已关联指标（${metrics.length + unMapped.length}）</h4>
      <button class="ghost" onclick="dmLinkAsset('metric','指标')">+ 关联指标</button>
    </div>
    ${list}${unList}`;
}

/* ── Tab 4：Dimension（字段维度派生）── */
function dmRenderDimension() {
  const datasets = (smSubjObjects && smSubjObjects.datasets) || [];
  const rows = [];
  datasets.forEach(d => (d.fields || []).forEach(f => {
    const dim = f.dimension || f.label;
    if (dim) rows.push({ table: d.source, field: f.name, dim: f.dimension ? "时间" : dim, ds: d.name });
  }));
  const list = rows.map(r => `
    <div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
      <div><b>${esc(r.field)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc(r.table)}</span></div>
      <div class="row" style="gap:8px">
        <span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary)">${esc(r.dim)}</span>
        <span class="muted" style="font-size:12px">数据集 ${esc(r.ds)}</span>
      </div>
    </div>`).join("") || `<div class="muted">暂无维度字段</div>`;

  return `
    <div class="alert info">★ 维度由<b>字段维度类型</b>派生（时间/地域/状态/类型/渠道），在「表结构信息」里维护字段维度类型，此处实时反映。</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">维度字段（${rows.length}）</h4>
      <button class="ghost" onclick="dmGotoKb('schema')">维护维度类型</button>
    </div>
    ${list}`;
}

/* ── Tab 5：Entity（业务实体：物理表/主键/粒度）── */
function dmRenderEntity() {
  const linked = smLinkedKeys("entity");
  const entities = smSubjEntities.filter(e => linked.has(e.name));
  const list = entities.map(e => `
    <div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border);flex-wrap:wrap;gap:6px">
      <div>
        <b>${esc(e.name)}</b>
        <span class="muted" style="font-size:12px;margin-left:8px">${esc(e.table_name || "")}</span>
      </div>
      <div class="row" style="gap:6px">
        <span class="muted" style="font-size:12px">${e.grain ? esc(e.grain) : ""}${e.primary_key && e.primary_key.length ? " · 主键 " + esc(e.primary_key.join(",")) : ""}</span>
        <button class="ghost" onclick="dmUnlinkAsset('entity','${esc(e.name)}')">取消关联</button>
        <button class="ghost" onclick="dmGotoKb('registry')">编辑</button>
      </div>
    </div>`).join("") || `<div class="muted">暂无实体</div>`;

  return `
    <div class="alert info">★ Entity 与 Dataset 同源（业务实体），此处列出主题关联的实体及其物理表/主键/粒度。</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">业务实体（${entities.length}）</h4>
      <button class="ghost" onclick="dmLinkAsset('entity','实体')">+ 关联实体</button>
    </div>
    ${list}`;
}

/* ── Tab 6：Relationship（JOIN 类型/基数/关联字段）── */
function dmRenderRelationship() {
  const rels = (smSubjObjects && smSubjObjects.relationships) || [];
  const list = rels.map(r => {
    const full = smSubjRelations.find(x => x.from_entity === r.from && x.to_entity === r.to) || {};
    const fc = r.from_columns || [];
    const tc = r.to_columns || [];
    return `<div class="card" style="margin-bottom:10px;padding:12px 14px">
      <div class="row" style="justify-content:space-between;flex-wrap:wrap;gap:6px">
        <b>${esc(r.from)} → ${esc(r.to)}</b>
        <div class="row" style="gap:6px">
          ${full.join_type ? `<span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary)">${esc(full.join_type)}</span>` : ""}
          ${full.cardinality ? `<span class="tag" style="background:var(--color-warning-bg);color:var(--color-warning)">${esc(full.cardinality)}</span>` : ""}
          <button class="ghost" onclick="dmGotoKb('registry')">配置 JOIN</button>
        </div>
      </div>
      <div class="muted" style="font-size:12px;margin-top:6px">关联字段：${esc(fc.join(",") || "—")} = ${esc(tc.join(",") || "—")}</div>
      ${full.join_sql ? `<div class="sql-hl" style="font-size:12px;padding:6px 8px;margin-top:6px;border-radius:6px">${esc(full.join_sql)}</div>` : ""}
      ${full.rule_note ? `<div class="muted" style="font-size:12px;margin-top:4px">${esc(full.rule_note)}</div>` : ""}
    </div>`;
  }).join("") || `<div class="muted">暂无关系（关系按端点实体自动归属主题）</div>`;

  return `
    <div class="alert info">★ 关系按<b>端点实体</b>自动归属主题（实体在主题内即关系可见），JOIN 类型/基数/关联字段在「语义注册表」配置。</div>
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">实体关系（${rels.length}）</h4>
      <button class="ghost" onclick="dmViewRelations('${esc(smSubjName)}')">查看关系图</button>
    </div>
    ${list}`;
}

/* ── Tab 7：关联知识（业务文档 / SQL 案例 / 业务规则）── */
function dmRenderKnowledge() {
  const docs = smSubjAssets.filter(a => a.asset_type === "doc");
  const sqls = smSubjAssets.filter(a => a.asset_type === "golden_sql");
  const rules = smSubjAssets.filter(a => a.asset_type === "rule");
  const row = (key, type) => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
      <b>${esc(key)}</b><button class="ghost" onclick="dmUnlinkAsset('${type}','${esc(key)}')">取消关联</button></div>`;
  const docList = docs.map(a => row(a.asset_key, "doc")).join("") || `<div class="muted">未关联文档</div>`;
  const sqlList = sqls.map(a => row(a.asset_key, "golden_sql")).join("") || `<div class="muted">未关联 SQL 案例</div>`;
  const ruleList = rules.map(a => row(a.asset_key, "rule")).join("") || `<div class="muted">未关联业务规则</div>`;

  return `
    <div class="alert info">★ 关联<b>已有知识资产</b>（不复制、不重建），业务文档 / SQL 案例 / 业务规则仍在各自模块维护。</div>
    <div class="row" style="justify-content:space-between;margin:12px 0 6px">
      <h4 style="margin:0">业务文档</h4>
      <button class="ghost" onclick="dmLinkKnowledge('doc')">+ 关联文档</button>
    </div>
    ${docList}
    <div class="row" style="justify-content:space-between;margin:12px 0 6px">
      <h4 style="margin:0">SQL 案例</h4>
      <button class="ghost" onclick="dmLinkKnowledge('golden_sql')">+ 关联 SQL 案例</button>
    </div>
    ${sqlList}
    <div class="row" style="justify-content:space-between;margin:12px 0 6px">
      <h4 style="margin:0">业务规则</h4>
      <button class="ghost" onclick="dmLinkKnowledge('rule')">+ 关联规则</button>
    </div>
    ${ruleList}`;
}

async function dmLinkKnowledge(assetType) {
  const body = document.getElementById("smSubjectBody");
  body.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    let items = [], render = () => "", label = "";
    if (assetType === "doc") {
      label = "业务文档";
      const linked = smLinkedKeys("doc");
      items = smSubjDocs.filter(d => !linked.has(d.title));
      render = d => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
        <div><b>${esc(d.title)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc((d.content || "").slice(0, 40))}</span></div>
        <button class="ghost" onclick="dmDoLink('doc','${esc(d.title)}')">关联</button></div>`;
    } else if (assetType === "golden_sql") {
      label = "SQL 案例";
      const r = await api("/api/golden/cases?limit=200");
      const linked = smLinkedKeys("golden_sql");
      items = (r.items || []).filter(c => !linked.has(c.case_no || String(c.id)));
      render = c => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
        <div><b>${esc(c.case_no || c.id)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc((c.question || "").slice(0, 40))}</span></div>
        <button class="ghost" onclick="dmDoLink('golden_sql','${esc(c.case_no || c.id)}')">关联</button></div>`;
    } else if (assetType === "rule") {
      label = "业务规则";
      const r = await api("/api/exp/rules?limit=200");
      const linked = smLinkedKeys("rule");
      items = (r.items || []).filter(x => !linked.has(String(x.id)));
      render = x => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
        <div><b>#${esc(x.id)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc((x.title || x.note || x.template_sql || "").slice(0, 40))}</span></div>
        <button class="ghost" onclick="dmDoLink('rule','${esc(String(x.id))}')">关联</button></div>`;
    }
    body.innerHTML = `
      <div class="row" style="justify-content:space-between;margin-bottom:10px">
        <h4 style="margin:0">选择要关联的${label}（${items.length}）</h4>
        <button class="ghost" onclick="dmRenderSubjectTab()">返回</button>
      </div>
      ${items.length ? items.map(render).join("") : `<div class="muted">没有可关联的${label}</div>`}`;
  } catch (e) {
    body.innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
  }
}

/* ── 资产关联（通用：实体/指标）── */
function dmLinkAsset(assetType, label) {
  const linked = smLinkedKeys(assetType);
  let items = [], render = () => "";
  if (assetType === "entity") {
    items = smSubjEntities.filter(e => !linked.has(e.name));
    render = e => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
      <div><b>${esc(e.name)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc(e.table_name || "")}</span></div>
      <button class="ghost" onclick="dmDoLink('entity','${esc(e.name)}')">关联</button></div>`;
  } else if (assetType === "metric") {
    items = smSubjMetrics.filter(m => !linked.has(m.name));
    render = m => `<div class="row" style="justify-content:space-between;padding:8px 0;border-bottom:1px solid var(--color-border)">
      <div><b>${esc(m.name)}</b><span class="muted" style="font-size:12px;margin-left:8px">${esc((m.definition || "").slice(0, 40))}</span></div>
      <button class="ghost" onclick="dmDoLink('metric','${esc(m.name)}')">关联</button></div>`;
  }
  document.getElementById("smSubjectBody").innerHTML = `
    <div class="row" style="justify-content:space-between;margin-bottom:10px">
      <h4 style="margin:0">选择要关联的${label}（${items.length}）</h4>
      <button class="ghost" onclick="dmRenderSubjectTab()">返回</button>
    </div>
    ${items.length ? items.map(render).join("") : `<div class="muted">没有可关联的${label}</div>`}`;
}

async function dmDoLink(assetType, assetKey) {
  try {
    await api(`/api/subjects/${encodeURIComponent(smSubjName)}/assets`, {
      method: "POST",
      body: JSON.stringify({ asset_type: assetType, asset_key: assetKey }),
    });
    await dmReloadSubject();
    dmRenderSubjectTab();
  } catch (e) { alert("关联失败：" + e.message); }
}

async function dmUnlinkAsset(assetType, assetKey) {
  if (!confirm(`确定取消关联「${assetKey}」？`)) return;
  try {
    await api(`/api/subjects/${encodeURIComponent(smSubjName)}/assets/${assetType}/${encodeURIComponent(assetKey)}`, { method: "DELETE" });
    await dmReloadSubject();
    dmRenderSubjectTab();
  } catch (e) { alert("取消关联失败：" + e.message); }
}

/** 关联/取消关联后刷新主题资产与语义对象，并同步域详情统计 */
async function dmReloadSubject() {
  const [subjects, objects] = await Promise.all([
    api("/api/subjects"),
    api(`/api/semantic/objects?subject=${encodeURIComponent(smSubjName)}`),
  ]);
  const subj = (subjects.items || []).find(s => s.name === smSubjName);
  smSubjInfo = subj || null;
  smSubjAssets = (subj && subj.assets) || [];
  smSubjObjects = objects || null;
  if (dmCurrent) dmSelectDomain(dmCurrent);
  dmRenderList();   // ★ 同步刷新左侧列表资产数 + 顶部数据概览
}

/** 查看模型关系（P3：关系视图）—— 占位，后续阶段实现 */
function dmViewRelations(subjectName) {
  alert(`主题「${subjectName}」的模型关系视图将在下一阶段交付（主题→实体→数据集→物理表→字段，指标→数据集→维度，实体 JOIN 关系）。`);
}

/** 新建业务域 */
async function dmNewDomain() {
  const name = prompt("业务域编码（如 order / driver / finance）：", "");
  if (!name || !name.trim()) return;
  const display = prompt("业务域名称（如 订单 / 司机）：", "");
  try {
    await api("/api/domains", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), display_name: display || name.trim() }),
    });
    await dmRenderList();
    dmSelectDomain(name.trim());
  } catch (e) { alert("新建失败：" + e.message); }
}

/** 删除业务域 */
async function dmDeleteDomain() {
  if (!dmCurrent) { alert("请先选择业务域"); return; }
  if (!confirm(`确定删除业务域「${dmCurrent}」及其主题、资产关联？`)) return;
  try {
    await api(`/api/domains/${encodeURIComponent(dmCurrent)}`, { method: "DELETE" });
    dmCurrent = null;
    document.getElementById("dmTitle").textContent = "请从左侧选择";
    document.getElementById("dmDetail").innerHTML = `<div class="empty">← 选择或新建一个业务域</div>`;
    document.getElementById("dmDeleteBtn").style.display = "none";
    await dmRenderList();
  } catch (e) { alert("删除失败：" + e.message); }
}

/** 编辑业务域：在详情区渲染内联表单（锁定编码，可改展示名/描述/负责人/部门/启用） */
async function dmEditDomain() {
  if (!dmCurrent) return;
  let d;
  try { d = await api(`/api/domains/${encodeURIComponent(dmCurrent)}`); }
  catch (e) { alert("加载失败：" + e.message); return; }
  document.getElementById("dmTitle").textContent = `编辑业务域「${dmCurrent}」`;
  // 编辑态隐藏「编辑/删除」按钮，避免中途误删
  document.getElementById("dmEditBtn").style.display = "none";
  document.getElementById("dmDeleteBtn").style.display = "none";
  const box = document.getElementById("dmDetail");
  box.innerHTML = `
    <div class="muted" style="font-size:12px;margin-bottom:6px">编码 <b>${esc(dmCurrent)}</b> 不可修改（关联了主题与资产）</div>
    <label>展示名称</label>
    <input id="dmEfDisplay" value="${esc(d.display_name || "")}" placeholder="如 订单">
    <label>描述</label>
    <textarea id="dmEfDesc" placeholder="这个业务域负责什么业务范围…">${esc(d.description || "")}</textarea>
    <label>负责人</label>
    <input id="dmEfOwner" value="${esc(d.owner || "")}" placeholder="如 张三">
    <label>所属部门</label>
    <input id="dmEfDept" value="${esc(d.department || "")}" placeholder="如 运营部">
    <label class="row" style="gap:6px;margin-top:12px">
      <input type="checkbox" id="dmEfEnabled" ${d.enabled ? "checked" : ""} style="width:auto">
      <span style="font-size:13px;color:var(--color-text-secondary)">启用该业务域</span>
    </label>
    <div class="row" style="gap:8px;margin-top:14px">
      <button class="btn" onclick="dmSaveDomain()">保存</button>
      <button class="ghost" onclick="dmCancelEdit()">取消</button>
    </div>`;
}

/** 保存业务域编辑（PUT 复用 upsert，编码不变只更新其他字段） */
async function dmSaveDomain() {
  const display = document.getElementById("dmEfDisplay").value.trim();
  const desc = document.getElementById("dmEfDesc").value.trim();
  const owner = document.getElementById("dmEfOwner").value.trim();
  const dept = document.getElementById("dmEfDept").value.trim();
  const enabled = document.getElementById("dmEfEnabled").checked;
  try {
    await api(`/api/domains/${encodeURIComponent(dmCurrent)}`, {
      method: "PUT",
      body: JSON.stringify({
        name: dmCurrent, display_name: display, description: desc,
        owner: owner, department: dept, enabled: enabled,
      }),
    });
    await dmRenderList();
    dmSelectDomain(dmCurrent);
  } catch (e) { alert("保存失败：" + e.message); }
}

/** 取消业务域编辑：回到详情视图 */
function dmCancelEdit() {
  if (!dmCurrent) return;
  dmSelectDomain(dmCurrent);
}

/** 新建业务主题（挂在当前域下） */
async function dmNewSubject() {
  if (!dmCurrent) { alert("请先选择业务域"); return; }
  const name = prompt("主题标识（如 order_operation）：", "");
  if (!name || !name.trim()) return;
  const display = prompt("主题名称（如 订单经营）：", "");
  try {
    await api("/api/subjects", {
      method: "POST",
      body: JSON.stringify({ name: name.trim(), domain_name: dmCurrent, display_name: display || name.trim() }),
    });
    dmSelectDomain(dmCurrent);
  } catch (e) { alert("新建主题失败：" + e.message); }
}

/** 删除业务主题 */
async function dmDeleteSubject(name) {
  if (!confirm(`确定删除业务主题「${name}」及其资产关联？`)) return;
  try {
    await api(`/api/subjects/${encodeURIComponent(name)}`, { method: "DELETE" });
    dmSelectDomain(dmCurrent);
  } catch (e) { alert("删除主题失败：" + e.message); }
}

/** 编辑业务主题：渲染内联表单（锁定标识与归属域，可改展示名/描述/启用） */
async function dmEditSubject(subjectName) {
  if (!dmCurrent) return;
  let s = null;
  try {
    const d = await api(`/api/domains/${encodeURIComponent(dmCurrent)}`);
    s = (d.subjects || []).find(x => x.name === subjectName);
  } catch (e) {}
  if (!s) { alert("未找到该主题"); return; }
  const box = document.getElementById("dmDetail");
  box.innerHTML = `
    <div class="muted" style="font-size:12px;margin-bottom:6px">主题标识 <b>${esc(s.name)}</b>（归属业务域 <b>${esc(dmCurrent)}</b>，均不可修改）</div>
    <label>展示名称</label>
    <input id="dmSfDisplay" value="${esc(s.display_name || "")}" placeholder="如 订单经营">
    <label>描述</label>
    <textarea id="dmSfDesc" placeholder="该主题覆盖的分析范围…">${esc(s.description || "")}</textarea>
    <label class="row" style="gap:6px;margin-top:12px">
      <input type="checkbox" id="dmSfEnabled" ${s.enabled ? "checked" : ""} style="width:auto">
      <span style="font-size:13px;color:var(--color-text-secondary)">启用该主题</span>
    </label>
    <div class="row" style="gap:8px;margin-top:14px">
      <button class="btn" onclick="dmSaveSubject('${esc(subjectName)}')">保存</button>
      <button class="ghost" onclick="dmCancelEdit()">取消</button>
    </div>`;
}

/** 保存主题编辑（PUT /api/subjects/{name}，标识与归属域不变） */
async function dmSaveSubject(subjectName) {
  if (!dmCurrent) return;
  const display = document.getElementById("dmSfDisplay").value.trim();
  const desc = document.getElementById("dmSfDesc").value.trim();
  const enabled = document.getElementById("dmSfEnabled").checked;
  try {
    await api(`/api/subjects/${encodeURIComponent(subjectName)}`, {
      method: "PUT",
      body: JSON.stringify({
        name: subjectName, domain_name: dmCurrent,
        display_name: display, description: desc, enabled: enabled,
      }),
    });
    dmSelectDomain(dmCurrent);
  } catch (e) { alert("保存失败：" + e.message); }
}

/** 发布语义模型（动态组装 → 固化快照） */
async function dmPublishModel(subject) {
  try {
    const r = await api(`/api/semantic/models/${encodeURIComponent(subject)}/publish`, { method: "POST" });
    alert(r.message || "已发布");
  } catch (e) { alert("发布失败：" + e.message); }
}
