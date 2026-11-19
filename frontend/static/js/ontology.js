/* ══════════════════════════════════════════════════════════
   ontology.js · 本体建模（语义中心 · 阶段二）
   ★ 对应 index.html 的 tab-ontology 页面。
   ★ 三栏布局：左侧对象树（实体/属性/关系/规则）→ 中间关系画布（SVG）→ 右侧属性编辑器。
   ★ 依赖全局：api()、esc()、switchTab()。
   ══════════════════════════════════════════════════════════ */

let ontoEntities = [];       // 实体列表缓存
let ontoGraph = null;        // 关系图 {nodes, edges}
let ontoAttrs = [];          // 属性登记缓存
let ontoRules = [];          // 规则登记缓存
let ontoType = "object";     // 当前对象树类型
let ontoCurrent = null;      // 当前选中实体名
let ontoSearch = "";         // 搜索关键词

/** 页面懒加载入口 */
async function ontoLoad() {
  try {
    const [ents, graph, attrs, rules] = await Promise.all([
      api("/api/ontology/entities"),
      api("/api/ontology/graph"),
      api("/api/ontology/attributes"),
      api("/api/ontology/rules"),
    ]);
    ontoEntities = ents.items || [];
    ontoGraph = graph;
    ontoAttrs = attrs.items || [];
    ontoRules = rules.items || [];
  } catch (e) {
    document.getElementById("ontoTree").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    return;
  }
  ontoRenderGraph();
  ontoRenderTree();
}

/** 切换对象树类型 Tab */
function ontoSwitchType(type, btn) {
  ontoType = type;
  ["object", "attr", "rel", "rule"].forEach(k => {
    const el = document.getElementById("ontoTab" + k.charAt(0).toUpperCase() + k.slice(1));
    if (el) el.classList.toggle("active", k === type);
  });
  ontoRenderTree();
}

/** 搜索过滤（只对实体列表生效） */
function ontoFilterTree() {
  ontoSearch = (document.getElementById("ontoSearch")?.value || "").trim().toLowerCase();
  ontoRenderTree();
}

/** 渲染对象树（按 ontoType） */
function ontoRenderTree() {
  const box = document.getElementById("ontoTree");
  if (!box) return;

  if (ontoType === "object") {
    let list = ontoEntities;
    if (ontoSearch) {
      list = ontoEntities.filter(e =>
        (e.name || "").toLowerCase().includes(ontoSearch) ||
        (e.table_name || "").toLowerCase().includes(ontoSearch) ||
        (e.entity_code || "").toLowerCase().includes(ontoSearch));
    }
    if (!list.length) { box.innerHTML = `<div class="empty">无匹配实体</div>`; return; }
    box.innerHTML = "";
    list.forEach(e => {
      const div = document.createElement("div");
      div.className = "list-item" + (e.name === ontoCurrent ? " active" : "");
      div.innerHTML = `
        <div class="row" style="justify-content:space-between">
          <span>${esc(e.name)}</span>
          ${e.entity_code ? `<span class="tag" style="background:var(--color-primary-subtle);color:var(--color-primary);font-size:10px">${esc(e.entity_code)}</span>` : ""}
        </div>
        <div class="muted" style="font-size:11px">${esc(e.table_name || "（无主表）")}</div>`;
      div.onclick = () => ontoSelectEntity(e.name, div);
      box.appendChild(div);
    });
  } else if (ontoType === "attr") {
    if (!ontoAttrs.length) { box.innerHTML = `<div class="empty">暂无属性登记<br>选中实体后在右侧「+ 登记属性」</div>`; return; }
    box.innerHTML = "";
    ontoAttrs.forEach(a => {
      const div = document.createElement("div");
      div.className = "list-item";
      div.innerHTML = `
        <div class="row" style="justify-content:space-between">
          <span style="font-size:13px">${esc(a.attr_key)}</span>
          <span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary);font-size:10px">${esc(a.attr_type)}</span>
        </div>
        <div class="muted" style="font-size:11px">${esc(a.entity_key)}${a.label ? " · " + esc(a.label) : ""}${a.is_key ? " · 键" : ""}</div>`;
      div.onclick = () => ontoSelectEntity(a.entity_key);
      box.appendChild(div);
    });
  } else if (ontoType === "rel") {
    const edges = (ontoGraph && ontoGraph.edges) || [];
    if (!edges.length) { box.innerHTML = `<div class="empty">暂无关系</div>`; return; }
    box.innerHTML = "";
    edges.forEach(r => {
      const div = document.createElement("div");
      div.className = "list-item";
      div.innerHTML = `
        <div style="font-size:13px">${esc(r.from)} → ${esc(r.to)}</div>
        <div class="muted" style="font-size:11px">${esc(r.cardinality || "?")}${r.join_type ? " · " + esc(r.join_type) : ""}</div>`;
      div.onclick = () => ontoSelectEntity(r.from);
      box.appendChild(div);
    });
  } else if (ontoType === "rule") {
    if (!ontoRules.length) { box.innerHTML = `<div class="empty">暂无规则登记<br>选中实体后在右侧「+ 登记规则」</div>`; return; }
    box.innerHTML = "";
    ontoRules.forEach(r => {
      const div = document.createElement("div");
      div.className = "list-item";
      div.innerHTML = `
        <div style="font-size:13px">${esc(r.rule_ref)}</div>
        <div class="muted" style="font-size:11px">${esc(r.entity_key || "全局")} · ${esc(r.rule_type)}</div>`;
      div.onclick = () => { if (r.entity_key) ontoSelectEntity(r.entity_key); };
      box.appendChild(div);
    });
  }
}

/** 选中实体：渲染右侧编辑器 */
async function ontoSelectEntity(name, el) {
  ontoCurrent = name;
  if (el) {
    document.querySelectorAll("#ontoTree .list-item").forEach(e => e.classList.remove("active"));
    el.classList.add("active");
  }
  ontoRenderGraph();   // 高亮选中实体
  const editor = document.getElementById("ontoEditor");
  editor.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    const d = await api(`/api/ontology/entity/${encodeURIComponent(name)}`);
    ontoRenderEditor(d);
  } catch (e) {
    editor.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/** 渲染右侧属性编辑器 */
function ontoRenderEditor(d) {
  const ent = d.entity || {};
  const fields = d.attributes || [];
  const regAttrs = d.registered_attributes || [];
  const rels = d.relations || [];
  const rules = d.rules || [];
  const pk = ent.primary_key || [];
  const editor = document.getElementById("ontoEditor");

  const stTag = (ent.status === "approved")
    ? '<span class="status-tag success">已审核</span>'
    : `<span class="status-tag gray">${esc({ candidate: "候选", draft: "草稿", rejected: "已驳回" }[ent.status] || ent.status || "candidate")}</span>`;

  editor.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div>
        <b style="font-size:16px">${esc(ent.name)}</b>
        <div class="muted" style="font-size:11px;margin-top:2px">版本 v${ent.version || 1} · 来源 ${esc(ent.source || "manual")}</div>
      </div>
      ${stTag}
    </div>

    <label style="margin-top:12px">英文标识（entity_code）</label>
    <input id="ontoEdCode" value="${esc(ent.entity_code || "")}" placeholder="如 driver_order">

    <label>业务描述</label>
    <textarea id="ontoEdComment" style="min-height:56px">${esc(ent.comment || "")}</textarea>

    <label>数据粒度（grain）</label>
    <input id="ontoEdGrain" value="${esc(ent.grain || "")}" placeholder="如 一笔订单一行">

    <label>对应物理表</label>
    <input value="${esc(ent.table_name || "")}" disabled>

    <label>主键</label>
    <input id="ontoEdPk" value="${esc((pk || []).join(","))}" placeholder="逗号分隔，如 order_id">

    <div class="row" style="gap:8px;margin-top:14px">
      <button class="btn" onclick="ontoSaveEntity('${esc(ent.name)}')">保存</button>
    </div>

    <h4 style="margin:16px 0 8px">字段属性（${fields.length}）<span class="muted" style="font-size:11px;font-weight:400">由表结构派生</span></h4>
    <div style="max-height:160px;overflow:auto">
      ${fields.length ? fields.map(f => `
        <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--color-border);font-size:12px">
          <span>${esc(f.column_name)}</span>
          <span class="muted">${f.dimension_type ? esc(f.dimension_type) + " · " : ""}${esc((f.comment || "").slice(0, 14))}</span>
        </div>`).join("") : `<div class="muted" style="font-size:12px">（该实体无主表字段注释）</div>`}
    </div>

    <h4 style="margin:16px 0 8px">属性登记（${regAttrs.length}）</h4>
    <div style="max-height:120px;overflow:auto">
      ${regAttrs.length ? regAttrs.map(a => `
        <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--color-border);font-size:12px">
          <span>${esc(a.attr_key)}${a.label ? " · " + esc(a.label) : ""}</span>
          <span class="row" style="gap:6px">
            <span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary);font-size:10px">${esc(a.attr_type)}</span>
            <button class="ghost" style="padding:0 4px;font-size:11px" onclick="ontoDelAttr(${a.id})">删</button>
          </span>
        </div>`).join("") : `<div class="muted" style="font-size:12px">无</div>`}
    </div>
    <button class="ghost" style="width:100%;margin-top:6px" onclick="ontoAddAttr('${esc(ent.name)}')">+ 登记属性</button>

    <h4 style="margin:16px 0 8px">实体关系（${rels.length}）</h4>
    <div style="max-height:120px;overflow:auto">
      ${rels.length ? rels.map(r => `
        <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--color-border);font-size:12px">
          <span>${esc(r.from_entity)} → ${esc(r.to_entity)}</span>
          <span class="tag" style="background:var(--color-warning-bg);color:var(--color-warning);font-size:10px">${esc(r.cardinality || "?")}</span>
        </div>`).join("") : `<div class="muted" style="font-size:12px">无（关系在「知识资产→语义注册表」维护）</div>`}
    </div>

    <h4 style="margin:16px 0 8px">业务规则（${rules.length}）</h4>
    <div style="max-height:120px;overflow:auto">
      ${rules.length ? rules.map(r => `
        <div class="row" style="justify-content:space-between;padding:5px 0;border-bottom:1px solid var(--color-border);font-size:12px">
          <span>${esc(r.rule_ref)}</span>
          <span class="row" style="gap:6px">
            <span class="tag" style="background:var(--color-bg-subtle);color:var(--color-text-secondary);font-size:10px">${esc(r.rule_type)}</span>
            <button class="ghost" style="padding:0 4px;font-size:11px" onclick="ontoDelRule(${r.id})">删</button>
          </span>
        </div>`).join("") : `<div class="muted" style="font-size:12px">无</div>`}
    </div>
    <button class="ghost" style="width:100%;margin-top:6px" onclick="ontoAddRule('${esc(ent.name)}')">+ 登记规则</button>`;
}

/** 保存实体编辑 */
async function ontoSaveEntity(name) {
  const entity_code = document.getElementById("ontoEdCode").value.trim();
  const comment = document.getElementById("ontoEdComment").value.trim();
  const grain = document.getElementById("ontoEdGrain").value.trim();
  const pkRaw = document.getElementById("ontoEdPk").value.trim();
  const primary_key = pkRaw ? pkRaw.split(/[,，]/).map(s => s.trim()).filter(Boolean) : [];
  try {
    const r = await api(`/api/ontology/entity/${encodeURIComponent(name)}`, {
      method: "PUT",
      body: JSON.stringify({ entity_code, comment, grain, primary_key }),
    });
    alert(r.message || "已保存");
    await ontoLoad();
    if (ontoCurrent) ontoSelectEntity(ontoCurrent);
  } catch (e) { alert("保存失败：" + e.message); }
}

/** 登记属性（简单 prompt 表单） */
async function ontoAddAttr(entity) {
  const attrKey = prompt("属性键（column 用「表.字段」，dict 用字典名）：", "");
  if (!attrKey || !attrKey.trim()) return;
  const label = prompt("属性业务名（可空）：", "");
  try {
    await api("/api/ontology/attributes", {
      method: "POST",
      body: JSON.stringify({ entity_key: entity, attr_type: "column", attr_key: attrKey.trim(), label: label || "" }),
    });
    await ontoLoad();
    ontoSelectEntity(entity);
  } catch (e) { alert("登记失败：" + e.message); }
}

/** 删除属性登记 */
async function ontoDelAttr(id) {
  if (!confirm("删除该属性登记？（只删登记，不删属性本体）")) return;
  try {
    await api(`/api/ontology/attributes/${id}`, { method: "DELETE" });
    await ontoLoad();
    if (ontoCurrent) ontoSelectEntity(ontoCurrent);
  } catch (e) { alert("删除失败：" + e.message); }
}

/** 登记规则 */
async function ontoAddRule(entity) {
  const ruleRef = prompt("规则引用键（如 metric_constraint 的 constraint_id / 规则名）：", "");
  if (!ruleRef || !ruleRef.trim()) return;
  const ruleType = prompt("规则类型（metric_constraint / sql_rule / join_rule / biz_rule）：", "biz_rule");
  try {
    await api("/api/ontology/rules", {
      method: "POST",
      body: JSON.stringify({ entity_key: entity, rule_type: ruleType || "biz_rule", rule_ref: ruleRef.trim() }),
    });
    await ontoLoad();
    ontoSelectEntity(entity);
  } catch (e) { alert("登记失败：" + e.message); }
}

/** 删除规则登记 */
async function ontoDelRule(id) {
  if (!confirm("删除该规则登记？（只删登记，不删规则本体）")) return;
  try {
    await api(`/api/ontology/rules/${id}`, { method: "DELETE" });
    await ontoLoad();
    if (ontoCurrent) ontoSelectEntity(ontoCurrent);
  } catch (e) { alert("删除失败：" + e.message); }
}

/* ══════════════════════════════════════════════════════════
   关系画布（原生 SVG 圆形布局，选中实体高亮）
   ══════════════════════════════════════════════════════════ */
function ontoRenderGraph() {
  const canvas = document.getElementById("ontoCanvas");
  const info = document.getElementById("ontoGraphInfo");
  if (!canvas || !ontoGraph) return;

  const nodes = ontoGraph.nodes || [];
  const edges = ontoGraph.edges || [];
  // 只画「有关系」的实体，孤立实体不画（避免画布过密）
  const linked = new Set();
  edges.forEach(e => { linked.add(e.from); linked.add(e.to); });
  const linkedNodes = nodes.filter(n => linked.has(n.name));
  const isolated = nodes.length - linkedNodes.length;

  if (!linkedNodes.length) {
    canvas.innerHTML = `<div class="empty">暂无实体关系</div>`;
    info.textContent = "";
    return;
  }

  // 圆形布局（半径随节点数自适应）
  const n = linkedNodes.length;
  const R = Math.max(150, n * 9);
  const W = (R + 130) * 2, H = (R + 110) * 2;
  const cx = W / 2, cy = H / 2;
  const pos = {};
  linkedNodes.forEach((nd, i) => {
    const angle = (2 * Math.PI * i) / n - Math.PI / 2;
    pos[nd.name] = { x: cx + R * Math.cos(angle), y: cy + R * Math.sin(angle) };
  });

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block">`;
  // 边（先画边，再画节点，避免遮挡）
  edges.forEach(e => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    const sel = (e.from === ontoCurrent || e.to === ontoCurrent);
    svg += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}"
      stroke="${sel ? "var(--color-primary)" : "var(--color-border-strong)"}" stroke-width="${sel ? 2 : 1.2}" opacity="${ontoCurrent && !sel ? 0.3 : 0.9}"/>`;
    // 中点标注基数
    const mx = (a.x + b.x) / 2, my = (a.y + b.y) / 2;
    svg += `<text x="${mx}" y="${my - 4}" text-anchor="middle" font-size="10" fill="var(--color-text-muted)">${esc(e.cardinality || "?")}</text>`;
  });
  // 节点
  linkedNodes.forEach(nd => {
    const p = pos[nd.name];
    const sel = nd.name === ontoCurrent;
    const w = Math.min(120, Math.max(70, (nd.name.length + 2) * 11));
    svg += `<g style="cursor:pointer" onclick="ontoSelectEntity('${esc(nd.name)}')">
      <rect x="${p.x - w / 2}" y="${p.y - 16}" width="${w}" height="32" rx="8"
        fill="${sel ? "var(--color-primary-subtle)" : "#ffffff"}" stroke="${sel ? "var(--color-primary)" : "var(--color-border-strong)"}"
        stroke-width="${sel ? 2 : 1.2}"/>
      <text x="${p.x}" y="${p.y - 1}" text-anchor="middle" font-size="11"
        fill="${sel ? "var(--color-primary)" : "var(--color-text-primary)"}" font-weight="${sel ? 600 : 400}">${esc(nd.name)}</text>
      <text x="${p.x}" y="${p.y + 11}" text-anchor="middle" font-size="9" fill="var(--color-text-muted)">${esc(nd.table_name || "")}</text>
    </g>`;
  });
  svg += `</svg>`;

  canvas.innerHTML = svg;
  info.textContent = `${linkedNodes.length} 个实体有关系 · ${isolated} 个孤立实体 · ${edges.length} 条关系`;
}
