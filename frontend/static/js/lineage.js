/* ══════════════════════════════════════════════════════════
   lineage.js · 逻辑视图（语义中心 · 阶段四）
   ★ 对应 index.html 的 tab-lineage 页面。
   ★ 只读：全局语义关系图 + 实体上下游血缘 + 缺失/冲突提醒。
   ★ 依赖全局：api()、esc()。
   ══════════════════════════════════════════════════════════ */

let linGraph = null;       // 全局图 {nodes, edges, kinds}
let linConflicts = [];     // 冲突列表

/** 页面懒加载入口 */
async function lineageLoad() {
  try {
    const [ov, g, cf] = await Promise.all([
      api("/api/lineage/overview"),
      api("/api/lineage/graph"),
      api("/api/lineage/conflicts"),
    ]);
    linGraph = g;
    linConflicts = cf.items || [];
    lineageRenderOverview(ov);
    lineageRenderGraph(g);
    lineageRenderConflicts(linConflicts);
  } catch (e) {
    document.getElementById("linGraph").innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

/** 概览统计卡 */
function lineageRenderOverview(ov) {
  const items = [
    ["blue", ov.domains, "业务域"],
    ["blue", ov.subjects, "关系模型"],
    ["green", ov.entities, "实体"],
    ["green", ov.metrics, "指标"],
    ["orange", ov.dimensions, "维度"],
    ["orange", ov.relationships, "关系"],
    ["gray", ov.tables, "物理表"],
    ["gray", ov.conflicts, "冲突提醒"],
  ];
  document.getElementById("linOverview").innerHTML = items.map(([c, v, l]) =>
    `<div class="stat-card ${c}"><div class="stat-val">${v}</div><div class="stat-label">${l}</div></div>`).join("");
}

/** 全局语义关系图：分层（域 → 主题 → 实体 → 物理表） */
function lineageRenderGraph(g) {
  const box = document.getElementById("linGraph");
  if (!box) return;
  if (!g || !g.nodes.length) { box.innerHTML = `<div class="empty">暂无语义资产</div>`; return; }

  // 按 kind 分层，层内按 label 排序
  const layers = ["domain", "subject", "entity", "table"];
  const kindColor = { domain: "#7b61ff", subject: "var(--color-primary)", entity: "var(--color-success)", table: "var(--color-text-muted)" };
  const colX = { domain: 10, subject: 150, entity: 290, table: 470 };
  const colW = { domain: 120, subject: 120, entity: 150, table: 220 };

  const byKind = {};
  layers.forEach(k => byKind[k] = (g.nodes || []).filter(n => n.kind === k));
  const rowH = 34, nodeH = 24;
  const maxRows = Math.max(...layers.map(k => byKind[k].length), 1);
  const H = maxRows * rowH + 30;
  const W = 700;

  // 节点坐标
  const pos = {};
  layers.forEach(k => {
    byKind[k].forEach((n, i) => {
      pos[n.id] = { x: colX[k] + colW[k] / 2, y: 15 + i * rowH + rowH / 2 };
    });
  });

  let svg = `<svg viewBox="0 0 ${W} ${H}" width="100%" style="display:block;min-width:680px">`;
  // 边
  (g.edges || []).forEach(e => {
    const a = pos[e.from], b = pos[e.to];
    if (!a || !b) return;
    svg += `<line x1="${a.x}" y1="${a.y}" x2="${b.x}" y2="${b.y}" stroke="var(--color-border-strong)" stroke-width="1" opacity="0.6"/>`;
  });
  // 节点
  (g.nodes || []).forEach(n => {
    const p = pos[n.id];
    if (!p) return;
    const c = kindColor[n.kind] || "var(--color-text-muted)";
    const w = colW[n.kind];
    svg += `<g style="cursor:${n.kind === 'entity' ? 'pointer' : 'default'}" ${n.kind === 'entity' ? `onclick="lineageSearchEntity('${esc(n.name)}')"` : ""}>
      <rect x="${p.x - w / 2}" y="${p.y - nodeH / 2}" width="${w}" height="${nodeH}" rx="6"
        fill="#fff" stroke="${c}" stroke-width="1.2"/>
      <text x="${p.x}" y="${p.y + 4}" text-anchor="middle" font-size="11" fill="${c}">${esc(n.label)}</text>
    </g>`;
  });
  svg += `</svg>`;
  box.innerHTML = svg;
}

/** 缺失/冲突提醒 */
function lineageRenderConflicts(items) {
  document.getElementById("linConflictCount").textContent = items.length;
  const box = document.getElementById("linConflicts");
  if (!box) return;
  const levelStyle = {
    error: "background:#fff1f0;color:var(--color-danger);border-color:#fbb4ac",
    warn: "background:var(--color-warning-bg);color:var(--color-warning);border-color:#ffd591",
    info: "background:var(--color-primary-subtle);color:var(--color-primary);border-color:var(--color-info-border)",
  };
  box.innerHTML = items.length
    ? items.map(c => `
      <div style="padding:8px 10px;border-radius:8px;margin-bottom:8px;font-size:12px;border:1px solid;${levelStyle[c.level] || levelStyle.info}">
        <b>${esc(c.title)}</b>
        <div style="opacity:.85;margin-top:2px">${esc(c.detail)}</div>
      </div>`).join("")
    : `<div class="empty" style="color:var(--color-success)">✅ 未发现缺失关系或冲突</div>`;
}

/** 实体上下游查询 */
async function lineageSearchEntity(name) {
  const q = name || (document.getElementById("linEntitySearch")?.value || "").trim();
  if (!q) { alert("请输入实体名"); return; }
  const pane = document.getElementById("linEntityPane");
  pane.innerHTML = `<div class="empty">查询中…</div>`;
  try {
    const d = await api(`/api/lineage/entity/${encodeURIComponent(q)}`);
    lineageRenderEntityLineage(d);
  } catch (e) {
    pane.innerHTML = `<div class="empty">未找到实体「${esc(q)}」</div>`;
  }
}

/** 渲染实体上下游 */
function lineageRenderEntityLineage(d) {
  const pane = document.getElementById("linEntityPane");
  const e = d.entity || {};
  const up = d.upstream || [];
  const fields = d.fields || [];
  const rels = d.relations || [];
  const metrics = d.metrics || [];
  const dims = d.dimensions || [];
  const pk = e.primary_key || [];

  pane.innerHTML = `
    <div class="row" style="justify-content:space-between;align-items:flex-start">
      <div>
        <b style="font-size:16px">${esc(e.name)}</b>
        <span class="muted" style="font-size:11px;margin-left:8px">${esc(e.table_name || "（无主表）")}</span>
      </div>
      <span class="status-tag ${e.status === 'approved' ? 'success' : 'gray'}">${esc({ approved: "已审核", candidate: "候选", draft: "草稿", rejected: "已驳回" }[e.status] || e.status || "approved")}</span>
    </div>
    ${e.comment ? `<div class="muted" style="font-size:12px;margin-top:4px">${esc(e.comment)}</div>` : ""}
    ${pk.length ? `<div class="muted" style="font-size:11px;margin-top:2px">主键：${esc(pk.join(", "))}</div>` : ""}

    <h4 style="margin:14px 0 6px">上游（归属）</h4>
    ${up.length ? up.map(u => `<span class="tag" style="margin:2px;background:var(--color-primary-subtle);color:var(--color-primary)">${esc(u.domain || "?")} / ${esc(u.subject)}</span>`).join("") : `<span class="muted" style="font-size:12px">未归属任何业务主题</span>`}

    <h4 style="margin:14px 0 6px">字段属性（${fields.length}）</h4>
    <div style="max-height:160px;overflow:auto">
      ${fields.length ? fields.map(f => `
        <div class="row" style="justify-content:space-between;padding:4px 0;border-bottom:1px solid var(--color-border);font-size:12px">
          <span>${esc(f.column_name)}</span>
          <span class="muted" style="font-size:11px">${f.dimension_type ? esc(f.dimension_type) + " · " : ""}${esc((f.comment || "").slice(0, 16))}</span>
        </div>`).join("") : `<span class="muted" style="font-size:12px">无字段注释</span>`}
    </div>

    <h4 style="margin:14px 0 6px">实体关系（${rels.length}）</h4>
    <div style="max-height:100px;overflow:auto">
      ${rels.length ? rels.map(r => `<div style="font-size:12px;padding:3px 0">${esc(r.from_entity)} → ${esc(r.to_entity)} <span class="muted">${esc(r.cardinality || "?")}</span></div>`).join("") : `<span class="muted" style="font-size:12px">无</span>`}
    </div>

    <h4 style="margin:14px 0 6px">关联指标（${metrics.length}）</h4>
    <div style="max-height:100px;overflow:auto">
      ${metrics.length ? metrics.map(m => `<span class="tag" style="margin:2px;background:var(--color-bg-subtle);color:var(--color-text-secondary);cursor:pointer" onclick="lineageSearchMetric('${esc(m.name)}')">${esc(m.name)}</span>`).join("") : `<span class="muted" style="font-size:12px">无</span>`}
    </div>

    <h4 style="margin:14px 0 6px">关联维度（${dims.length}）</h4>
    <div style="max-height:80px;overflow:auto">
      ${dims.length ? dims.map(m => `<span class="tag" style="margin:2px;background:var(--color-primary-subtle);color:var(--color-primary)">${esc(m.name)}</span>`).join("") : `<span class="muted" style="font-size:12px">无</span>`}
    </div>`;
}

/** 指标引用关系（简版：跳转到指标详情弹窗） */
async function lineageSearchMetric(name) {
  try {
    const d = await api(`/api/lineage/metric/${encodeURIComponent(name)}`);
    const m = d.metric || {};
    const tbls = d.tables || [];
    alert(`指标「${name}」\n` +
      `类型：${m.metric_type || "—"}\n` +
      `口径：${(m.definition || "（无）").slice(0, 80)}\n` +
      `依赖表：${tbls.map(t => t.table).join("、") || "（无）"}`);
  } catch (e) { alert("查询失败：" + e.message); }
}
