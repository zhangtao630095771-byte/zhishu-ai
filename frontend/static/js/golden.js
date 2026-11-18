/* ══════════════════════════════════════════════════════════
   golden.js · 标准问题库（标准问数案例库） + AI 取数质量中心（评测看板）
   加载顺序：在 history.js 之后、boot.js 之前（依赖 core.js 的 api()、experience.js 的 esc()）

   ★ 改造要点（对齐任务书 P0）：
     ① 状态 / 审核态 / 验证状态全部中文映射，不再暴露 active/draft/ok 等内部值；
     ② 用抽屉替代 alert/prompt 弹窗：详情、新增/编辑、批量导入；
     ③ 列表支持关键词 / 业务主题 / 关联指标 / 状态 / 验证状态 / 审核态 / 创建日期筛选；
     ④ 评测看板拆六区块：核心运行概况 / AI 评测质量 / 知识资产质量 / 错误归因分析 / 反馈与优化；
     ⑤ 数据真实性：无数据展示「暂无数据 / N/A」，不填模拟数字。
   ══════════════════════════════════════════════════════════ */

// ── 状态中文映射（后端枚举 → 中文，任务书 §3.1）──
const GQ_STATUS_ZH = { active: "启用中", disabled: "已停用", archived: "已归档" };
// 审核态（既有能力，保留中文映射）
const GQ_REVIEW_ZH = { draft: "草稿", pending: "待审核", approved: "已通过", rejected: "已驳回" };
// 验证状态（后端当前仅 verified / unverified 两值；待验证/验证失败为预留筛选项）
const GQ_VERIFY_ZH = { verified: "已验证", unverified: "未验证", pending: "待验证", failed: "验证失败" };

// 状态 → 标签修饰符（统一 StatusTag：绿=正常 橙=待处理 红=异常 灰=归档/中性 蓝=信息）
const GQ_STATUS_CLS = { active: "success", disabled: "warning", archived: "gray" };
const GQ_REVIEW_CLS = { approved: "success", pending: "warning", draft: "gray", rejected: "danger" };
const GQ_VERIFY_CLS = { verified: "success", unverified: "gray", pending: "warning", failed: "danger" };

// ── 全局列表缓存（用于客户端筛选，避免每次筛选都重新请求）──
let GOLDEN_ITEMS = [];
let GOLDEN_METRIC_NAMES = [];   // 指标名列表（「关联指标」下拉数据源）

// ═══════════════════════════════════════════════════════════
// ① 标准问题库
// ═══════════════════════════════════════════════════════════

/** 加载标准问题库：全量列表 + 概览统计 + 指标下拉，然后渲染 */
async function loadGoldenPage() {
  // ── 并行拉三份数据：全量列表 / 概览统计 / 指标名（互不依赖）──
  let items = [], overview = null, metrics = [];
  try { items = (await api("/api/golden/cases?limit=1000")).items || []; }
  catch (e) { goldenShowError(e.message); return; }
  try { overview = await api("/api/golden/overview"); } catch (e) { /* 概览失败不阻塞列表 */ }
  try { metrics = await api("/api/metrics"); } catch (e) { /* 指标失败不阻塞 */ }

  GOLDEN_ITEMS = items;
  GOLDEN_METRIC_NAMES = (metrics || []).map(m => m.name).filter(Boolean);

  goldenRenderStats(overview);
  goldenFillMetricFilter();
  goldenRenderFiltered();
}

/** 列表页错误提示（顶部提示条） */
function goldenShowError(msg) {
  document.getElementById("goldenList").innerHTML =
    `<tr><td class="empty">加载失败：${esc(msg)}</td></tr>`;
}

/** 渲染统计卡片：总数 / 已验证 / 待验证 / 已停用
    （规范 §4：统计卡不用彩色顶边，同层级样式完全一致；无数据显示「—」） */
function goldenRenderStats(o) {
  const el = document.getElementById("goldenStats");
  if (!o) { el.innerHTML = `<div class="empty">统计加载中…</div>`; return; }
  const card = (label, val) =>
    `<div class="stat-card"><div class="stat-val">${val}</div><div class="stat-label">${label}</div></div>`;
  el.innerHTML =
    card("标准问题总数", o.total ?? "—") +
    card("已验证", o.verified ?? "—") +
    card("待验证", o.unverified ?? "—") +
    card("已停用", o.disabled ?? "—");
}

/** 填充「关联指标」下拉 */
function goldenFillMetricFilter() {
  const sel = document.getElementById("goldenFMetric");
  const cur = sel.value;
  sel.innerHTML = `<option value="">关联指标（全部）</option>` +
    GOLDEN_METRIC_NAMES.map(n => `<option value="${esc(n)}">${esc(n)}</option>`).join("");
  sel.value = cur;
}

/** 清空所有筛选条件并重新渲染 */
function goldenClearFilter() {
  ["goldenFQ", "goldenFMetric", "goldenFStatus", "goldenFVerify", "goldenFReview", "goldenFDate"].forEach(id => {
    document.getElementById(id).value = "";
  });
  goldenRenderFiltered();
}

/** 读取当前筛选条件，客户端过滤 GOLDEN_ITEMS 并渲染表格 */
function goldenRenderFiltered() {
  const q = (document.getElementById("goldenFQ").value || "").trim().toLowerCase();
  const metric = document.getElementById("goldenFMetric").value;
  const status = document.getElementById("goldenFStatus").value;
  const verify = document.getElementById("goldenFVerify").value;
  const review = document.getElementById("goldenFReview").value;
  const date = document.getElementById("goldenFDate").value;   // 创建日期（起）

  let list = GOLDEN_ITEMS.filter(it => {
    // 关键词：匹配 问题名称 / 标准问题 / 业务主题
    if (q) {
      const hay = `${it.id || ""} ${it.question || ""} ${it.business_line || ""}`.toLowerCase();
      if (!hay.includes(q)) return false;
    }
    // 关联指标：expected_metrics 数组里包含所选指标
    if (metric && !((it.expected_metrics || []).includes(metric))) return false;
    // 状态
    if (status && it.status !== status) return false;
    // 验证状态（后端仅 verified/unverified，其余筛选项匹配不到即为空态）
    if (verify && it.verify_status !== verify) return false;
    // 审核态
    if (review && it.review_status !== review) return false;
    // 创建日期：created_at >= 所选日期
    if (date && (!it.created_at || it.created_at < date)) return false;
    return true;
  });

  document.getElementById("goldenFilterHint").textContent =
    `共 ${list.length} 条（标准问题库当前共 ${GOLDEN_ITEMS.length} 条）`;
  goldenRenderList(list);
}

/** 渲染标准问题列表表格 */
function goldenRenderList(list) {
  const box = document.getElementById("goldenList");
  if (!list.length) {
    box.innerHTML = `<tr><td class="empty" colspan="9">
      暂无标准问题。点右上角「＋ 新增标准问题」创建第一条案例，
      或从「SQL 经验」中把已审核通过的黄金案例沉淀为标准问题。</td></tr>`;
    return;
  }
  const rows = list.map(it => {
    const st = it.status || "active";
    const rv = it.review_status || "draft";
    const vf = it.verify_status || "unverified";
    const metrics = (it.expected_metrics || []).slice(0, 3).join("、") || "—";
    const hasSql = it.expected_sql ? `<span class="status-tag success">已绑定</span>` : `<span class="status-tag gray">未绑定</span>`;
    return `<tr>
      <td><b>${esc(it.id)}</b><div class="sub muted">v${it.version || 1}</div></td>
      <td class="td-question">${esc(it.question || "")}</td>
      <td>${esc(it.business_line || "—")}</td>
      <td class="td-metric" title="${esc(metrics)}">${esc(metrics)}</td>
      <td>${hasSql}</td>
      <td><span class="status-tag ${GQ_VERIFY_CLS[vf] || "gray"}">${esc(GQ_VERIFY_ZH[vf] || vf)}</span></td>
      <td><span class="status-tag ${GQ_REVIEW_CLS[rv] || "gray"}">${esc(GQ_REVIEW_ZH[rv] || rv)}</span></td>
      <td><span class="status-tag ${GQ_STATUS_CLS[st] || "gray"}">${esc(GQ_STATUS_ZH[st] || st)}</span></td>
      <td class="u-ops center">
        <button class="ghost" onclick="goldenOpenDetail('${esc(it.id)}')">查看</button>
        <button class="ghost" onclick="goldenOpenEditor('${esc(it.id)}')">编辑</button>
        ${st === "archived"
          ? `<button class="ghost" onclick="goldenArchive('${esc(it.id)}','active')">恢复</button>`
          : `<button class="ghost" onclick="goldenArchive('${esc(it.id)}','archived')">归档</button>`}
      </td>
    </tr>`;
  }).join("");
  box.innerHTML =
    `<tr><th>问题名称</th><th>标准问题</th><th>业务主题</th><th>关联指标</th><th>标准SQL</th>
     <th>验证状态</th><th>审核态</th><th>状态</th><th>操作</th></tr>` + rows;
}

// ── 通用抽屉开关 ──
function goldenOpenMask(id) { document.getElementById(id).classList.remove("hidden"); }
function goldenCloseMask(id) { document.getElementById(id).classList.add("hidden"); }

// ═══════════════════════════════════════════════════════════
// ② 详情抽屉
// ═══════════════════════════════════════════════════════════
async function goldenOpenDetail(id) {
  goldenOpenMask("goldenDetailMask");
  document.getElementById("goldenDetailBody").innerHTML =
    `<div class="drawer-head"><b>标准问题详情</b>
       <button class="ghost" onclick="goldenCloseDetail()">× 关闭</button></div>
     <div class="muted" style="padding:12px 20px">加载中…</div>`;
  let c;
  try { c = await api(`/api/golden/cases/${encodeURIComponent(id)}`); }
  catch (e) { goldenCloseDetail(); alert("查看失败：" + e.message); return; }

  const st = c.status || "active", rv = c.review_status || "draft", vf = c.verify_status || "unverified";
  const rows = c.expected_result ? JSON.stringify(c.expected_result).slice(0, 200) : "（无）";
  const verifyLog = (c.verify_log || []).map(l =>
    `<div class="kv-row"><span class="kv-k">${esc(l.created_at || "")} · ${esc(l.checked_by || "—")}</span>
     <span class="status-tag ${l.result === "passed" ? "success" : "danger"}">${esc(l.result || "—")}</span></div>`
  ).join("") || `<div class="muted">暂无验证记录</div>`;
  const versions = (c.versions || []).map(v =>
    `<div class="kv-row"><span class="kv-k">v${v.version} · ${esc(v.changed_by || "—")} · ${esc(v.created_at || "")}</span>
     <span class="muted">${esc(v.change_note || "")}</span></div>`
  ).join("") || `<div class="muted">暂无版本历史</div>`;

  document.getElementById("goldenDetailBody").innerHTML = `
    <div class="drawer-head">
      <b>标准问题详情</b>
      <div class="spacer"></div>
      <button class="ghost" onclick="goldenCopySql('${esc(c.id)}')">复制 SQL</button>
      <button class="ghost" onclick="goldenOpenEditor('${esc(c.id)}')">编辑</button>
      <button class="ghost" onclick="goldenCloseDetail()">×</button>
    </div>
    <div class="drawer-body">
      <div class="detail-badges">
        <span class="status-tag ${GQ_STATUS_CLS[st] || "gray"}">${esc(GQ_STATUS_ZH[st] || st)}</span>
        <span class="status-tag ${GQ_REVIEW_CLS[rv] || "gray"}">审核：${esc(GQ_REVIEW_ZH[rv] || rv)}</span>
        <span class="status-tag ${GQ_VERIFY_CLS[vf] || "gray"}">验证：${esc(GQ_VERIFY_ZH[vf] || vf)}</span>
      </div>

      <h4>问题基础信息</h4>
      <div class="kv"><span class="kv-k">问题名称</span><span class="kv-v">${esc(c.id)}</span></div>
      <div class="kv"><span class="kv-k">标准问题</span><span class="kv-v">${esc(c.question || "—")}</span></div>
      <div class="kv"><span class="kv-k">业务主题</span><span class="kv-v">${esc(c.business_line || "—")}</span></div>
      <div class="kv"><span class="kv-k">创建人 / 时间</span><span class="kv-v">${esc(c.created_by || "—")} · ${esc(c.created_at || "—")}</span></div>

      <h4>业务语义与指标关联</h4>
      <div class="kv"><span class="kv-k">关联指标</span><span class="kv-v">${esc((c.expected_metrics || []).join("、") || "—")}</span></div>
      <div class="kv"><span class="kv-k">关联实体</span><span class="kv-v">${esc((c.expected_entities || []).join("、") || "—")}</span></div>
      <div class="kv"><span class="kv-k">时间口径</span><span class="kv-v">${esc(c.time_caliber || "—")}</span></div>
      <div class="kv"><span class="kv-k">统计维度</span><span class="kv-v">${esc((c.expected_dimensions || []).join("、") || "—")}</span></div>
      <div class="kv"><span class="kv-k">验收方式</span><span class="kv-v">${esc(c.acceptance || "—")}</span></div>

      <h4>标准 SQL</h4>
      <pre class="code sql-hl">${highlightSql(c.expected_sql)}</pre>

      <h4>预期结果</h4>
      <pre class="code">${esc(rows)}</pre>

      <h4>SQL 验证记录</h4>
      ${verifyLog}

      <h4>版本历史</h4>
      ${versions}
    </div>
    <div class="drawer-foot">
      ${vf !== "verified" ? `<button class="btn" onclick="goldenVerify('${esc(c.id)}','verified')">标记已验证</button>` : ""}
      ${rv !== "approved" ? `<button class="btn" onclick="goldenReview('${esc(c.id)}','approved')">审核通过</button>` : ""}
      ${rv !== "rejected" ? `<button class="ghost danger" onclick="goldenReview('${esc(c.id)}','rejected')">驳回</button>` : ""}
      ${st !== "disabled" ? `<button class="ghost" onclick="goldenArchive('${esc(c.id)}','disabled')">停用</button>` : ""}
      ${st === "disabled" ? `<button class="ghost" onclick="goldenArchive('${esc(c.id)}','active')">启用</button>` : ""}
    </div>`;
}

function goldenCloseDetail() { goldenCloseMask("goldenDetailMask"); }

/** 复制标准 SQL 到剪贴板 */
async function goldenCopySql(id) {
  let c;
  try { c = await api(`/api/golden/cases/${encodeURIComponent(id)}`); }
  catch (e) { alert("读取失败：" + e.message); return; }
  if (!c.expected_sql) { alert("该案例未绑定标准 SQL"); return; }
  try {
    await navigator.clipboard.writeText(c.expected_sql);
    alert("标准 SQL 已复制到剪贴板");
  } catch (e) {
    alert("复制失败（浏览器不支持剪贴板）：请手动选择复制");
  }
}

// ═══════════════════════════════════════════════════════════
// ③ 新增 / 编辑抽屉
// ═══════════════════════════════════════════════════════════
let GOLDEN_EDIT_ID = null;   // 当前编辑的 case_id；null = 新增

async function goldenOpenEditor(id) {
  GOLDEN_EDIT_ID = id || null;
  goldenOpenMask("goldenEditMask");
  document.getElementById("goldenEditBody").innerHTML =
    `<div class="drawer-head"><b>${id ? "编辑标准问题" : "新增标准问题"}</b>
       <button class="ghost" onclick="goldenCloseEditor()">×</button></div>
     <div class="muted" style="padding:12px 20px">加载中…</div>`;

  let c = null;
  if (id) {
    try { c = await api(`/api/golden/cases/${encodeURIComponent(id)}`); }
    catch (e) { goldenCloseEditor(); alert("读取失败：" + e.message); return; }
  }

  const v = c || {};   // 空对象 = 新增时的默认空值
  // ── JSON 数组字段转「逗号分隔」便于编辑 ──
  const arr = x => (x || []).join(", ");

  document.getElementById("goldenEditBody").innerHTML = `
    <div class="drawer-head">
      <b>${id ? "编辑标准问题" : "新增标准问题"}</b>
      <div class="spacer"></div>
      <button class="ghost" onclick="goldenCloseEditor()">取消</button>
      <button class="btn" onclick="goldenSaveEditor()">保存</button>
    </div>
    <div class="drawer-body">
      <div class="edit-note muted">带 ★ 为必填。标准 SQL 保存时会自动过安全闸门验证，未通过则拒绝保存。</div>

      <h4>基础信息</h4>
      <label>问题名称（唯一标识）★</label>
      <input id="geId" ${id ? "readonly" : ""} placeholder="如 g101（人可读 ID）" value="${esc(v.id || "")}">
      <label>标准问题（用户实际提问）★</label>
      <textarea id="geQuestion" style="min-height:64px" placeholder="如：统计2026年7月各城市的订单量和取消率">${esc(v.question || "")}</textarea>
      <label>业务主题</label>
      <input id="geBiz" placeholder="如：订单 / 司机 / 资金" value="${esc(v.business_line || "")}">
      <label>适用角色（逗号分隔）</label>
      <input id="geRoles" placeholder="如：管理员, 运营" value="${esc(arr(v.roles))}">

      <h4>业务语义</h4>
      <label>关联指标（逗号分隔，须为已登记指标名）</label>
      <input id="geMetrics" placeholder="如：订单量, 取消率" value="${esc(arr(v.expected_metrics))}">
      <label>关联实体（逗号分隔）</label>
      <input id="geEntities" placeholder="如：代驾订单" value="${esc(arr(v.expected_entities))}">
      <label>时间口径</label>
      <input id="geCaliber" placeholder="如：自然月 / 近 30 天" value="${esc(v.time_caliber || "")}">
      <label>统计维度（逗号分隔）</label>
      <input id="geDims" placeholder="如：城市, 月份" value="${esc(arr(v.expected_dimensions))}">

      <h4>标准答案</h4>
      <label>标准 SQL ★（过安全闸门验证）</label>
      <textarea id="geSql" class="mono" style="min-height:150px" placeholder="SELECT ... FROM ...">${esc(v.expected_sql || "")}</textarea>
      <label>验收方式</label>
      <select id="geAccept">
        <option value="manual" ${(v.acceptance || "manual") === "manual" ? "selected" : ""}>manual — 人工验收</option>
        <option value="rule" ${v.acceptance === "rule" ? "selected" : ""}>rule — 规则校验</option>
        <option value="auto" ${v.acceptance === "auto" ? "selected" : ""}>auto — 自动验收（须填预期结果）</option>
      </select>
      <label>预期结果（JSON，可选，auto 验收必填）</label>
      <textarea id="geResult" class="mono" style="min-height:80px" placeholder='{"columns":["城市"],"rows":[["北京"]],"precision":2}'>${v.expected_result ? esc(JSON.stringify(v.expected_result)) : ""}</textarea>
      <label>结果校验规则（JSON，可选）</label>
      <textarea id="geCheck" class="mono" style="min-height:60px" placeholder='{"min_rows":1}'>${v.result_check ? esc(JSON.stringify(v.result_check)) : ""}</textarea>
      <label>数据快照标识（可选）</label>
      <input id="geSnapshot" placeholder="如：2026-07 生产快照" value="${esc(v.data_snapshot || "")}">
    </div>
    <div class="drawer-foot">
      <span class="muted" id="geMsg"></span>
      <div class="spacer"></div>
      <button class="ghost" onclick="goldenCloseEditor()">取消</button>
      <button class="btn" onclick="goldenSaveEditor()">保存</button>
    </div>`;
}

function goldenCloseEditor() { goldenCloseMask("goldenEditMask"); }

/** 收集表单并提交新增/编辑 */
async function goldenSaveEditor() {
  const msg = el => { document.getElementById("geMsg").textContent = el; };
  const id = document.getElementById("geId").value.trim();
  const question = document.getElementById("geQuestion").value.trim();
  const sql = document.getElementById("geSql").value.trim();
  if (!id) return msg("请填写问题名称（唯一标识）");
  if (!question) return msg("请填写标准问题");
  if (!sql) return msg("请填写标准 SQL");

  // ── 逗号分隔 → 数组（JSON 字段）；预期结果/校验规则 → 尝试解析 JSON ──
  const splitArr = s => (s || "").split(/[,，]/).map(x => x.trim()).filter(Boolean);
  const parseJson = (s, name) => {
    if (!s || !s.trim()) return null;
    try { return JSON.parse(s); } catch (e) { msg(`${name} 不是合法 JSON`); throw e; }
  };

  const body = {
    question,
    business_line: document.getElementById("geBiz").value.trim(),
    roles: splitArr(document.getElementById("geRoles").value),
    expected_metrics: splitArr(document.getElementById("geMetrics").value),
    expected_entities: splitArr(document.getElementById("geEntities").value),
    time_caliber: document.getElementById("geCaliber").value.trim(),
    expected_dimensions: splitArr(document.getElementById("geDims").value),
    expected_sql: sql,
    acceptance: document.getElementById("geAccept").value,
    data_snapshot: document.getElementById("geSnapshot").value.trim(),
  };
  try {
    const result = parseJson(document.getElementById("geResult").value, "预期结果");
    if (result !== null) body.expected_result = result;
    const check = parseJson(document.getElementById("geCheck").value, "结果校验规则");
    if (check !== null) body.result_check = check;
  } catch (e) { return; }

  try {
    if (GOLDEN_EDIT_ID) {
      await api(`/api/golden/cases/${encodeURIComponent(GOLDEN_EDIT_ID)}`, { method: "PUT", body: JSON.stringify(body) });
      msg("已保存（标准 SQL 变更已重新验证并 +1 版本）");
    } else {
      body.id = id;
      const r = await api("/api/golden/cases", { method: "POST", body: JSON.stringify(body) });
      msg(`已创建 ${r.id}（校验状态：${r.validation || "—"}）`);
    }
    setTimeout(() => { goldenCloseEditor(); loadGoldenPage(); }, 600);
  } catch (e) {
    msg("保存失败：" + e.message);
  }
}

// ═══════════════════════════════════════════════════════════
// ④ 批量导入抽屉
// ═══════════════════════════════════════════════════════════
function goldenOpenImport() {
  goldenOpenMask("goldenImportMask");
  document.getElementById("goldenImportBody").innerHTML = `
    <div class="drawer-head"><b>批量导入标准问题</b>
      <button class="ghost" onclick="goldenCloseImport()">×</button></div>
    <div class="drawer-body">
      <div class="edit-note muted">
        每行一个案例的 JSON，或粘贴 JSON 数组。字段同「新增」：id / question / expected_sql 必填，
        business_line、expected_metrics、acceptance、expected_result 等可选。
        导入会逐条过安全验证，失败的会汇总列出、不影响成功的条目。
      </div>
      <label>案例 JSON（数组或逐行）</label>
      <textarea id="giJson" class="mono" style="min-height:200px"
        placeholder='[{"id":"g201","question":"统计7月各城市订单量","expected_sql":"SELECT ..."}]'></textarea>
    </div>
    <div class="drawer-foot">
      <span class="muted" id="giMsg"></span>
      <div class="spacer"></div>
      <button class="ghost" onclick="goldenCloseImport()">取消</button>
      <button class="btn" onclick="goldenRunImport()">导入</button>
    </div>`;
}

function goldenCloseImport() { goldenCloseMask("goldenImportMask"); }

async function goldenRunImport() {
  const msg = el => { document.getElementById("giMsg").textContent = el; };
  const text = document.getElementById("giJson").value.trim();
  if (!text) return msg("请粘贴要导入的 JSON");
  let items;
  try {
    items = text.startsWith("[") ? JSON.parse(text)
      : text.split(/\n+/).filter(l => l.trim()).map(l => JSON.parse(l));
  } catch (e) { return msg("JSON 解析失败：" + e.message); }
  if (!Array.isArray(items) || !items.length) return msg("未解析到有效案例");

  try {
    const r = await api("/api/golden/import", { method: "POST", body: JSON.stringify({ items }) });
    const fails = r.failed || [];
    msg(`导入完成：成功 ${r.ok} 条${fails.length ? `，失败 ${fails.length} 条（${fails.map(f => f.id + ":" + f.msg).join("；").slice(0, 200)}）` : ""}`);
    setTimeout(() => { goldenCloseImport(); loadGoldenPage(); }, 900);
  } catch (e) {
    msg("导入失败：" + e.message);
  }
}

// ── 审核 / 验证 / 归档（复用既有接口）──
async function goldenReview(id, status) {
  const note = prompt(`审核意见（${GQ_REVIEW_ZH[status] || status}，可留空）：`, "");
  if (note === null) return;
  try {
    await api(`/api/golden/cases/${encodeURIComponent(id)}/review`, {
      method: "POST", body: JSON.stringify({ status, note: note || "" }),
    });
    loadGoldenPage();
    goldenCloseDetail();
  } catch (e) { alert("审核失败：" + e.message); }
}

async function goldenVerify(id, status) {
  if (!confirm(`确认将该案例标记为「${GQ_VERIFY_ZH[status] || status}」？`)) return;
  try {
    await api(`/api/golden/cases/${encodeURIComponent(id)}/verify`, {
      method: "POST", body: JSON.stringify({ verify_status: status }),
    });
    loadGoldenPage();
    goldenCloseDetail();
  } catch (e) { alert("验证失败：" + e.message); }
}

async function goldenArchive(id, status) {
  const action = status === "archived" ? "归档（不再参与评测，可恢复）"
    : status === "disabled" ? "停用（暂停使用，可恢复）" : "恢复为启用";
  if (!confirm(`确认${action}？`)) return;
  try {
    await api(`/api/golden/cases/${encodeURIComponent(id)}/archive`, {
      method: "POST", body: JSON.stringify({ status }),
    });
    loadGoldenPage();
    goldenCloseDetail();
  } catch (e) { alert("操作失败：" + e.message); }
}

// ── SQL 轻量高亮（纯前端，无依赖）──
function highlightSql(sql) {
  if (!sql) return `<span class="muted">（无）</span>`;
  const s = esc(sql);
  const kw = /\b(SELECT|FROM|WHERE|GROUP\s+BY|ORDER\s+BY|HAVING|LEFT\s+JOIN|RIGHT\s+JOIN|INNER\s+JOIN|JOIN|ON|AS|AND|OR|NOT|IN|BETWEEN|LIKE|CASE|WHEN|THEN|ELSE|END|SUM|COUNT|AVG|MIN|MAX|COALESCE|DISTINCT|UNION|ALL|LIMIT|WITH|CURRENT|now)\b/gi;
  return s.replace(kw, '<span class="sql-kw">$1</span>');
}

// ═══════════════════════════════════════════════════════════
// ⑤ AI 取数质量中心（评测看板）
// ═══════════════════════════════════════════════════════════
async function loadDashPage() {
  const box = document.getElementById("dashBody");
  box.innerHTML = `<span class="muted">加载中…</span>`;
  let p0, ev;
  try {
    // ── 并行拉两份：核心运行/知识/反馈聚合 + 评测报告结构化 ──
    [p0, ev] = await Promise.all([api("/api/dashboard/p0"), api("/api/dashboard/eval")]);
  } catch (e) {
    box.innerHTML = `<div class="alert warn">加载失败：${esc(e.message)}</div>`;
    return;
  }
  const updated = p0.updated_at || "—";
  box.innerHTML =
    dashHero(p0) +
    `<div class="dash-cols">` +
      dashSectionCoreRun(p0) +
      dashSectionEval(ev) +
      dashSectionError(ev) +
      dashSectionKnowledge(p0) +
      dashSectionFeedback(p0) +
    `</div>` +
    `<div class="dash-updated muted">数据更新时间：${esc(updated)}（核心运行/知识/反馈为实时聚合，AI 评测为最近一次离线评测报告）</div>`;
}

/** 总评 Hero：以业务结果准确率为唯一主角，其余为关键分项 */
function dashHero(p0) {
  const sql = p0.sql || {};
  const rate = sql.business_verified_rate;   // 0~1 或 null
  const verdict = rate == null ? { cls: "gray", txt: "暂无数据" }
    : rate >= 0.9 ? { cls: "success", txt: "质量良好" }
    : rate >= 0.8 ? { cls: "warning", txt: "需改进" }
    : { cls: "danger", txt: "告警" };
  const note = rate == null
    ? "暂无可用于总评的审核数据，请先积累人工审核通过的业务案例。"
    : `累计取数 ${sql.total_records ?? "—"} 次，业务结果准确率 ${pct(rate)}。总评以人工审核通过数为准，与 SQL 执行成功率口径不同。`;
  const kpi = (name, val, sub) =>
    `<div class="dash-kpi"><div class="dk-name">${name}</div>` +
    `<div class="dk-val">${val}</div><div class="dk-sub">${sub}</div></div>`;
  return `
  <div class="dash-hero">
    <div class="dash-hero-main">
      <div class="dash-verdict"><span class="status-tag ${verdict.cls}"><i class="dot"></i>${verdict.txt}</span></div>
      <div class="dash-score"><span class="ds-num">${rate == null ? "—" : (rate * 100).toFixed(1)}</span><span class="ds-unit">%</span></div>
      <div class="dash-note">${note}</div>
    </div>
    <div class="dash-hero-side">
      ${kpi("SQL 生成成功率", pct(sql.generated_rate), "有 SQL / 全部记录")}
      ${kpi("SQL 执行成功率", pct(sql.exec_success_rate), "执行成功 / 有 SQL")}
      ${kpi("业务结果准确率", pct(sql.business_verified_rate), "审核通过 / 取数总数")}
    </div>
  </div>`;
}

/** 六区块①：核心运行概况（真实业务取数数据，来自 query_records） */
function dashSectionCoreRun(p0) {
  const sql = p0.sql || {}, fb = p0.feedback || {};
  const m = (label, val, caliber) => `
    <div class="dash-metric" title="${esc(caliber)}">
      <div class="dm-name">${label}</div>
      <div class="dm-val">${val}</div>
      <div class="dm-caliber">${esc(caliber)}</div>
    </div>`;
  return `
  <div class="dash-sec span2">
    <div class="dash-sec-head"><h4>核心运行概况</h4>
      <span class="muted">真实业务取数数据 · 与 AI 评测数据口径不同，独立展示</span></div>
    <div class="dash-metric-grid">
      ${m("实际取数总次数", sql.total_records ?? "N/A", "统计范围：query_records 全部记录")}
      ${m("已验证业务案例", sql.business_verified ?? "N/A", "口径：review_status='approved'（人工审核通过）的记录数")}
      ${m("业务结果准确率", pct(sql.business_verified_rate), "口径：人工审核通过数 / 取数总次数（≠SQL执行成功率）")}
      ${m("SQL 生成成功率", pct(sql.generated_rate), "口径：有 SQL 的记录 / 全部记录")}
      ${m("SQL 执行成功率", pct(sql.exec_success_rate), "口径：ok=1 / 有 SQL 的记录（执行成功 ≠ 业务正确）")}
      ${m("用户反馈解决率", pct(fb.resolution_rate), "口径：已解决反馈 / 反馈总数")}
    </div>
  </div>`;
}

/** 六区块②：AI 评测质量（离线评测集指标，中文名 + 悬停解释 + 分组） */
function dashSectionEval(ev) {
  if (!ev || ev.status !== "有报告" || !(ev.metrics || []).length) {
    return `<div class="dash-sec"><div class="dash-sec-head"><h4>AI 评测质量</h4></div>
      <div class="empty">暂无评测报告数据（评测由离线脚本执行，尚未运行或报告未生成）</div></div>`;
  }
  // ── 按 group 分组，避免长列表 ──
  const groups = {};
  (ev.metrics || []).forEach(x => { (groups[x.group] = groups[x.group] || []).push(x); });
  const groupOrder = ["核心准确率", "检索与知识依据", "执行与结果", "性能开销"];
  const groupHtml = groupOrder.map(g => {
    const items = groups[g] || [];
    if (!items.length) return "";
    const cards = items.map(x => `
      <div class="dash-metric" title="${esc(x.explain)}">
        <div class="dm-name">${esc(x.name)}</div>
        <div class="dm-val">${fmtMetric(x.value, x.kind)}</div>
      </div>`).join("");
    return `<div class="dm-group"><div class="dm-group-name">${g}</div>
      <div class="dash-metric-grid">${cards}</div></div>`;
  }).join("");

  return `
  <div class="dash-sec">
    <div class="dash-sec-head"><h4>AI 评测质量</h4>
      <span class="muted">评测版本：${esc(ev.tag || "—")} · 样本 ${ev.sample_count ?? "N/A"} 条（成功执行 ${ev.executed_count ?? "N/A"}） · ${esc(ev.generated_at || "—")}</span></div>
    <div class="dash-eval-meta muted">鼠标悬停各指标可查看口径解释；本区块为离线评测集结果，不代表线上取数实时表现。</div>
    ${groupHtml}
    ${ev.failures && ev.failures.length
      ? `<div class="dash-sec-foot"><button class="ghost" onclick="dashShowFailures()">查看 ${ev.failures.length} 条失败案例</button></div>` : ""}
  </div>`;
}

/** 六区块③：知识资产质量（区分正式 Golden SQL / 黄金候选 / 普通案例） */
function dashSectionKnowledge(p0) {
  const k = p0.knowledge || {}, b = p0.business || {};
  const m = (label, val, caliber, link) => `
    <div class="dash-metric clickable" onclick="${link || ""}" title="${esc(caliber)}">
      <div class="dm-name">${label}</div>
      <div class="dm-val">${val}</div>
      <div class="dm-caliber">${esc(caliber)}</div>
    </div>`;
  return `
  <div class="dash-sec">
    <div class="dash-sec-head"><h4>知识资产质量</h4>
      <span class="muted">正式 Golden SQL 与待审核候选严格区分，不混算</span></div>
    <div class="dash-metric-grid">
      ${m("正式 Golden SQL", k.golden_sql_count ?? "N/A", "sql_experience.trust_level='golden'", "switchTab('exp')")}
      ${m("黄金候选（待发布）", k.golden_candidate_count ?? "N/A", "sql_experience.trust_level='golden_candidate'（≠正式 Golden）", "switchTab('exp')")}
      ${m("普通 SQL 案例", k.candidate_count ?? "N/A", "sql_experience.trust_level='candidate'", "switchTab('exp')")}
      ${m("Golden SQL 复用率", pct(k.reuse_rate), "口径：reuse_count / recall 总数", "switchTab('exp')")}
      ${m("待审核候选规则", k.pending_rules ?? "N/A", "经验规则 candidate + draft 数量", "switchTab('exp')")}
      ${m("知识冲突", k.conflict_rules ?? "N/A", "经验规则 draft（存在冲突待处理）数量", "switchTab('exp')")}
      ${m("标准问题总数", b.golden_total ?? "N/A", "标准问题库（排除已归档）", "switchTab('golden')")}
      ${m("标准问题已验证", b.golden_verified ?? "N/A", "标准问题库 verify_status='verified'", "switchTab('golden')")}
    </div>
  </div>`;
}

/** 六区块④：错误归因分析（12 类统一分类 + 失败案例） */
function dashSectionError(ev) {
  if (!ev || ev.status !== "有报告" || !(ev.categories || []).length) {
    return `<div class="dash-sec"><div class="dash-sec-head"><h4>错误归因分析</h4></div>
      <div class="empty">暂无错误归因数据（无评测报告或报告无失败用例）</div></div>`;
  }
  const cats = ev.categories || [];
  const hasAny = cats.some(c => c.count > 0);
  const maxCount = Math.max(1, ...cats.map(c => c.count));
  const bars = cats.map(c => `
    <div class="err-row" onclick="dashShowFailureDetail('${esc(c.key)}')" title="点击查看「${esc(c.name)}」相关失败案例">
      <div class="err-name">${esc(c.name)}</div>
      <div class="err-bar"><div class="err-fill" style="width:${(c.count / maxCount * 100).toFixed(0)}%"></div></div>
      <div class="err-count">${c.count}（${(c.pct * 100).toFixed(1)}%）</div>
    </div>`).join("");

  return `
  <div class="dash-sec">
    <div class="dash-sec-head"><h4>错误归因分析</h4>
      <span class="muted">按统一 12 类错误分类统计（仅含评测报告中有真实记录的类别）</span></div>
    ${hasAny ? `<div class="err-list">${bars}</div>
      <div class="muted" style="margin-top:8px">字段选择/JOIN关系/权限问题三类当前评测逐题评分无直接数据来源，故计数为 0。</div>`
      : `<div class="empty">当前评测报告无失败用例</div>`}
    ${ev.failures && ev.failures.length ? `<div class="dash-sec-foot"><button class="ghost" onclick="dashShowFailures()">查看典型失败案例（${ev.failures.length} 条）</button></div>` : ""}
  </div>`;
}

/** 六区块⑤：反馈与持续优化 */
function dashSectionFeedback(p0) {
  const fb = p0.feedback || {}, ev = p0.evaluation || {};
  const recent = (fb.recent || []).map(r => `
    <div class="fb-item" onclick="switchTab('feedback')" title="前往反馈处理查看">
      <span class="status-tag ${r.status === "resolved" ? "success" : "warning"}">${r.status === "resolved" ? "已解决" : "待处理"}</span>
      <span class="fb-q">${esc(r.question || "")}</span>
      <span class="muted">${esc(r.created_at || "")}</span>
    </div>`).join("") || `<div class="muted">暂无反馈</div>`;
  return `
  <div class="dash-sec">
    <div class="dash-sec-head"><h4>反馈与持续优化</h4>
      <span class="muted">AI 取数 → 用户反馈 → 问题定位 → 知识修正 → 案例沉淀 → 回归评测</span></div>
    <div class="dash-metric-grid">
      ${dashMetricClick("反馈总数", fb.total ?? "N/A", "feedbacks 全部记录", "switchTab('feedback')")}
      ${dashMetricClick("待处理", fb.pending ?? "N/A", "status='pending'", "switchTab('feedback')")}
      ${dashMetricClick("已解决", fb.resolved ?? "N/A", "status='resolved'", "switchTab('feedback')")}
      ${dashMetricClick("反馈解决率", pct(fb.resolution_rate), "已解决 / 总数", "switchTab('feedback')")}
    </div>
    <div class="dash-sub">
      <div class="dm-group"><div class="dm-group-name">最近反馈（点击前往处理）</div>${recent}</div>
      <div class="dm-group"><div class="dm-group-name">评测报告</div>
        <div class="fb-item" onclick="dashViewLatestReport()" title="查看最新评测报告">
          <span class="status-tag info">报告</span>
          <span class="fb-q">${esc(ev.latest_report || "未运行")}</span>
          <span class="muted">共 ${ev.report_count ?? 0} 份</span>
        </div>
      </div>
    </div>
  </div>`;
}

function dashMetricClick(label, val, caliber, onclick) {
  return `<div class="dash-metric clickable" onclick="${onclick}" title="${esc(caliber)}">
    <div class="dm-name">${label}</div><div class="dm-val">${val}</div>
    <div class="dm-caliber">${esc(caliber)}</div></div>`;
}

// ── 评测看板：失败案例抽屉 ──
let DASH_FAILURES = [];
async function dashShowFailures() {
  try { const ev = await api("/api/dashboard/eval"); DASH_FAILURES = ev.failures || []; }
  catch (e) { alert("读取失败：" + e.message); return; }
  goldenOpenMask("goldenDetailMask");
  const list = DASH_FAILURES.map(f => {
    const catNames = (f.categories || []).map(c => CATEGORY_ZH[c] || c).join("、");
    return `<div class="fb-item" onclick="dashShowFailureItem('${esc(f.id)}')">
      <span class="status-tag danger">${esc(f.id)}</span>
      <span class="fb-q">${esc(f.question)}</span>
      <span class="muted">${esc(catNames)}</span></div>`;
  }).join("") || `<div class="muted">暂无失败案例</div>`;
  document.getElementById("goldenDetailBody").innerHTML = `
    <div class="drawer-head"><b>失败案例（${DASH_FAILURES.length} 条）</b>
      <button class="ghost" onclick="goldenCloseDetail()">×</button></div>
    <div class="drawer-body">${list}</div>`;
}

function dashShowFailureItem(id) {
  const f = DASH_FAILURES.find(x => x.id === id);
  if (!f) return;
  const catNames = (f.categories || []).map(c => CATEGORY_ZH[c] || c).join("、");
  document.getElementById("goldenDetailBody").innerHTML = `
    <div class="drawer-head"><b>失败案例详情</b>
      <div class="spacer"></div>
      <button class="ghost" onclick="dashShowFailures()">← 返回列表</button>
      <button class="ghost" onclick="goldenCloseDetail()">×</button></div>
    <div class="drawer-body">
      <h4>原始问题</h4><div class="kv-v">${esc(f.question)}</div>
      <h4>错误分类</h4><div class="kv-v">${esc(catNames)}</div>
      <h4>错误原因</h4><pre class="code">${esc(f.error || "（无）")}</pre>
      <h4>生成 SQL</h4><pre class="code sql-hl">${highlightSql(f.sql)}</pre>
    </div>`;
}

/** 查看最新评测报告（markdown 原文） */
async function dashViewLatestReport() {
  goldenOpenMask("goldenDetailMask");
  document.getElementById("goldenDetailBody").innerHTML =
    `<div class="drawer-head"><b>评测报告</b>
       <button class="ghost" onclick="goldenCloseDetail()">×</button></div>
     <div class="muted" style="padding:12px 20px">加载中…</div>`;
  let r;
  try { r = await api("/api/dashboard/eval/report"); }
  catch (e) { goldenCloseDetail(); alert("读取失败：" + e.message); return; }
  if (r.status !== "有报告") { goldenCloseDetail(); alert("暂无评测报告"); return; }
  document.getElementById("goldenDetailBody").innerHTML = `
    <div class="drawer-head"><b>评测报告</b>
      <span class="muted" style="margin-left:8px">${esc(r.filename || "")}</span>
      <div class="spacer"></div>
      <button class="ghost" onclick="goldenCloseDetail()">×</button></div>
    <div class="drawer-body"><pre class="report-md">${esc(r.markdown || "")}</pre></div>`;
}

// ── 评测错误分类 中文映射（与后端 EVAL_CATEGORY_ZH 对齐）──
const CATEGORY_ZH = {
  intent_error: "意图识别错误", metric_error: "指标识别错误",
  table_error: "数据表选择错误", field_error: "字段选择错误",
  join_error: "JOIN关系错误", sql_syntax_error: "SQL语法错误",
  sql_exec_error: "SQL执行失败", business_result_error: "业务结果错误",
  time_error: "时间范围错误", knowledge_recall_error: "知识召回错误",
  permission_error: "权限问题", other: "其他问题",
};

// ── 指标值格式化：ratio 乘 100 显示 %，sec 加 s，num 直接显示，None → N/A ──
function fmtMetric(v, kind) {
  if (v == null) return "N/A";
  if (kind === "ratio") return (v * 100).toFixed(1) + "%";
  if (kind === "sec") return v + "s";
  return String(v);
}

// ── 比率 → 百分比（0~1 → 0~100%），None → N/A ──
function pct(v) {
  return (v == null) ? "N/A" : (v * 100).toFixed(1) + "%";
}
