/* ══════════════════════════════════════════════════════════════
   experience.js · SQL 经验中心（成功/失败/修正/规则/使用日志）
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// ★ SQL 经验中心
//   页面分五块：成功案例 / 失败经验 / 修正对照 / 经验规则 / 使用日志
//   ★ 刻意把「审核」与「验证」做成两个按钮、两个状态：
//     合成一个按钮，运营就会以为"点了通过 = 数对了"，这正是要避免的误解。
// ═══════════════════════════════════════════════════════════
let xpTab = "success";
let xpOverview = null;
let xpCases = [];          // 当前列表（详情弹窗从这里取，避免二次请求）
let xpRules = [];
let xpUsage = [];
let xpDetailId = null;

async function loadExpPage() {
  try {
    xpOverview = await api("/api/exp/overview");
    renderXpStats();
    await xpLoadList();
  } catch (e) {
    document.getElementById("xpList").innerHTML =
      `<div class="alert err">加载失败：${esc(e.message)}</div>`;
  }
}

function renderXpStats() {
  const o = xpOverview;
  if (!o) return;
  const box = document.getElementById("xpStats");
  box.innerHTML = [
    ["案例总数", o.cases_total, "", "成功 " + (o.by_kind.success || 0) +
      " / 失败 " + (o.by_kind.failure || 0) + " / 修正 " + (o.by_kind.correction || 0)],
    ["正式黄金 SQL", o.by_trust.golden || 0, "ok", "进入正向召回"],
    ["黄金候选", o.by_trust.golden_candidate || 0, "cand", "已验证，待人工发布"],
    ["已通过审核", o.by_review.approved || 0, "", "★ 不等于结果正确"],
    ["结果已验证", (o.by_verify.manual_confirmed || 0) + (o.by_verify.result_verified || 0),
      "ok", "人工确认 / 机器+人工"],
    ["审核通过但执行失败", o.approved_but_failed || 0, "warn", "永远不会成为黄金 SQL"],
    ["经验规则", o.rules_total || 0, "", "已发布 " + (o.by_rule_status.approved || 0)],
    ["经验被采用次数", o.reuse_total || 0, "", "日志 " + o.usage_total + " 条"],
  ].map(([label, val, cls, hint]) => `
    <div class="xp-stat ${cls}">
      <b>${val}</b><span>${label}</span>
      <div class="muted" style="font-size:11px;margin-top:2px">${esc(hint)}</div>
    </div>`).join("");

  const f = o.flags || {};
  document.getElementById("xpFlagTip").innerHTML =
    `当前生效开关：经验总开关 <b>${f.enabled ? "开" : "关"}</b> ｜ ` +
    `成功案例参考 <b>${f.success_recall ? "开" : "关"}</b> ｜ ` +
    `失败经验预防 <b>${f.fail_prevent ? "开" : "关"}</b> ｜ ` +
    `命中后自动修复 <b>${f.fail_repair ? "开" : "关（默认关，评测时开）"}</b> ｜ ` +
    `归纳规则 <b>${f.rules ? "开" : "关"}</b> ｜ ` +
    `精确复用 <b>${f.exact_reuse ? "开" : "关（默认关）"}</b>`;
}

function xpSwitchTab(kind, btn) {
  xpTab = kind;
  document.querySelectorAll("#tab-exp .subtabs button").forEach(b => b.classList.remove("active"));
  if (btn) btn.classList.add("active");
  xpLoadList();
}

/* 三类状态标签统一走 .status-tag（规范 §4）：
   绿=已定稿/通过，橙=待人工/候选，红=失败/驳回，灰=未提交/中性 */
function xpTrustTag(c) {
  const t = c.trust_level;
  const cls = t === "golden" ? "success" : t === "golden_candidate" ? "warning"
            : t === "revoked" ? "danger" : "gray";
  return `<span class="status-tag ${cls}">${esc(c.trust_zh || t)}</span>`;
}

function xpVerifyTag(c) {
  const v = c.verify_status;
  const cls = (v === "manual_confirmed" || v === "result_verified") ? "success"
            : v === "reexec_ok" ? "warning" : v === "verify_failed" ? "danger" : "gray";
  return `<span class="status-tag ${cls}">验证：${esc(c.verify_zh || v)}</span>`;
}

function xpReviewTag(c) {
  const m = { approved: ["success", "审核通过"], rejected: ["danger", "审核未通过"],
              pending: ["warning", "待审核"], none: ["gray", "未提交审核"] };
  const [cls, label] = m[c.review_status] || ["gray", c.review_status];
  return `<span class="status-tag ${cls}">${label}</span>`;
}

async function xpLoadList() {
  const box = document.getElementById("xpList");
  const bar = document.getElementById("xpToolbar");
  box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    if (xpTab === "rule") {
      bar.innerHTML = `<button class="ghost" onclick="xpMine()">AI 归纳候选规则</button>
        <span class="muted" style="font-size:12px">只有「已发布」的规则会进入取数链路</span>`;
      const d = await api("/api/exp/rules");
      xpRules = d.items || [];
      box.innerHTML = xpRules.length ? xpRules.map(xpRuleCard).join("")
        : `<div class="empty">还没有经验规则。<br>先积累足够的「已审核 + 已验证」案例，再点「AI 归纳候选规则」。</div>`;
      return;
    }
    if (xpTab === "usage") {
      bar.innerHTML = `<span class="muted" style="font-size:12px">记录每次召回/采用/放弃 —— 排查误召回、统计命中率都靠它</span>`;
      const d = await api("/api/exp/usage?limit=120");
      xpUsage = d.items || [];
      box.innerHTML = xpUsage.length ? xpUsage.map(xpUsageRow).join("")
        : `<div class="empty">还没有使用记录。做过取数之后（且库里有高可信案例）才会有。</div>`;
      return;
    }

    // 案例三视图共用一套列表接口，用 kind 区分
    const kind = xpTab === "success" ? "success" : xpTab === "failure" ? "failure" : "correction";
    const filters = xpTab === "success"
      ? [["", "全部"], ["candidate", "普通案例"], ["golden_candidate", "黄金候选"], ["golden", "正式黄金"]]
      : xpTab === "failure"
        ? [["", "全部"], ["wrong_table", "选表错"], ["wrong_field", "选字段错"],
           ["wrong_metric", "口径错"], ["wrong_time", "时间错"], ["wrong_status", "状态条件错"],
           ["join_relation", "JOIN 错"], ["sql_error", "执行失败"], ["missing_filter", "条件遗漏"]]
        : [];
    const cur = xpTab === "success" ? (xpState.trust || "") : (xpState.ftype || "");
    bar.innerHTML =
      `<input class="u-search" id="xpSearch" placeholder="搜索问题 / SQL / 编号…"
              value="${esc(xpState.q || "")}" oninput="xpState.q=this.value;xpDebounce()">
       <button class="ghost" onclick="xpState.q='';xpState.trust='';xpState.ftype='';xpLoadList()">清空</button>` +
      filters.map(([k, label]) => {
        const isCur = (k === cur);
        return `<button class="${isCur ? "btn" : "ghost"}" onclick="xpSetFilter('${k}')">${label}</button>`;
      }).join("");

    const params = new URLSearchParams({ kind, limit: 100 });
    if (xpState.q) params.set("q", xpState.q);
    if (kind === "success" && xpState.trust) params.set("trust", xpState.trust);
    if (kind === "failure" && xpState.ftype) params.set("failure_type", xpState.ftype);
    const d = await api("/api/exp/cases?" + params.toString());
    xpCases = d.items || [];
    box.innerHTML = xpCases.length ? xpCases.map(c => xpCaseCard(c, kind)).join("")
      : `<div class="empty">这一类还没有记录${kind === "correction" ? "（在失败案例上挂「修正 SQL」就会生成）" : ""}</div>`;
  } catch (e) {
    box.innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
  }
}

let xpState = { q: "", trust: "", ftype: "" };
let xpTimer = null;
function xpDebounce() {
  clearTimeout(xpTimer);
  xpTimer = setTimeout(xpLoadList, 350);   // 输入时防抖，避免每敲一个字打一次接口
}
function xpSetFilter(k) {
  if (xpTab === "success") xpState.trust = k; else xpState.ftype = k;
  xpLoadList();
}

function xpCaseCard(c, kind) {
  const cls = c.trust_level === "golden" ? "golden"
            : c.trust_level === "golden_candidate" ? "cand"
            : kind === "failure" ? "fail" : "";
  const ftype = c.failure_type
    ? `<span class="status-tag danger">${esc(c.failure_type_zh || c.failure_type)}</span>` : "";
  const conf = c.root_cause_confirmed ? `<span class="status-tag success">根因已确认</span>`
    : (c.failure_type ? `<span class="status-tag warning">根因待分析</span>` : "");
  return `<div class="card xp-card ${cls}">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        <span class="tag layer">${esc(c.case_no)}</span>
        <span class="xp-q">${esc(c.question || "(无问题)")}</span>
      </div>
      <span class="muted" style="font-size:12px">#${c.id} · ${esc(c.created_at || "")}</span>
    </div>
    <div class="xp-meta">
      ${xpTrustTag(c)} ${xpReviewTag(c)} ${xpVerifyTag(c)} ${ftype} ${conf}
      ${(c.tables || []).slice(0, 4).map(t => `<span class="tag">${esc(t)}</span>`).join("")}
      ${c.reuse_count ? `<span class="muted" style="font-size:12px">被采用 ${c.reuse_count} 次</span>` : ""}
    </div>
    ${c.sql_raw ? `<pre class="xp-sql">${esc((c.sql_formatted || c.sql_raw).slice(0, 700))}</pre>` : ""}
    <div class="xp-acts">
      <button class="ghost" onclick="xpOpen(${c.id})">查看详情 / 审核</button>
      ${kind === "success" && c.trust_level === "candidate"
        ? `<button class="ghost" onclick="xpAct(${c.id}, 'verify-reexec')">重跑验证</button>` : ""}
      ${kind === "success" && c.trust_level === "golden_candidate"
        ? `<button class="btn" onclick="xpAct(${c.id}, 'promote', {target:'golden'})">发布为正式黄金 SQL</button>` : ""}
      ${kind === "success" && c.trust_level === "golden"
        ? `<button class="ghost danger" onclick="xpRevoke(${c.id})">撤销</button>` : ""}
      ${kind === "failure" && !c.corrected_sql
        ? `<button class="ghost" onclick="xpOpen(${c.id})">补修正 SQL</button>` : ""}
    </div>
  </div>`;
}

function xpRuleCard(r) {
  // 规则状态：已发布=绿 / 草稿=灰 / 其余（撤销、驳回）=红；文案优先用后端中文
  const stCls = r.status === "approved" ? "success" : r.status === "draft" ? "gray" : "danger";
  const stTxt = r.status_zh || (r.status === "approved" ? "已发布" : r.status === "draft" ? "草稿" : r.status);
  return `<div class="card xp-card ${r.status === "approved" ? "golden" : ""}">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        <span class="tag layer">${esc(r.rule_no)}</span>
        <span class="xp-q">${esc(r.title)}</span>
      </div>
      <span class="muted" style="font-size:12px">${esc(r.topic || "")}</span>
    </div>
    <div class="xp-meta">
      <span class="status-tag ${stCls}">${esc(stTxt)}</span>
      <span class="tag layer">${esc(r.layer_zh || r.layer)}</span>
      <span class="tag">置信度 ${esc(r.confidence)}</span>
      <span class="tag">来源 ${r.source_count} 条案例</span>
      ${r.conflict_note ? `<span class="status-tag danger">存在冲突</span>` : ""}
    </div>
    <div style="font-size:13px;color:var(--color-text-secondary);margin:6px 0;white-space:pre-wrap">${esc(r.content || "")}</div>
    ${r.template_formatted ? `<pre class="xp-sql">${esc(r.template_formatted.slice(0, 600))}</pre>` : ""}
    ${r.conflict_note ? `<div class="alert warn" style="margin:6px 0">冲突：${esc(r.conflict_note)}</div>` : ""}
    <div class="kb-tip">来源案例：${(r.sources || []).map(s =>
      `<a onclick="xpOpen(${s.id})" style="cursor:pointer;color:var(--color-primary)">${esc(s.case_no)}</a>`).join("、") || "（无）"}</div>
    <div class="xp-acts">
      ${r.status !== "approved" ? `<button class="btn" onclick="xpRuleAct(${r.id}, 'publish')">发布</button>` : ""}
      ${r.status === "approved" ? `<button class="ghost danger" onclick="xpRuleAct(${r.id}, 'revoke')">撤销</button>` : ""}
    </div>
  </div>`;
}

function xpUsageRow(u) {
  const ev = { recall: "召回", skip: "未采用", verify: "验证", reuse: "复用", conflict: "冲突" };
  return `<div class="card" style="padding:10px 14px">
    <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
      <span class="tag layer">${esc(ev[u.event] || u.event)}</span>
      <span class="tag">${u.case_id ? "案例 #" + u.case_id : (u.rule_id ? "规则 #" + u.rule_id : "")}</span>
      <span class="muted" style="font-size:12px">${esc(u.ts || "")} ${u.record_id ? "· 取数 #" + u.record_id : ""}</span>
      <span style="font-size:13px">${esc((u.question || "").slice(0, 40))}</span>
      ${u.score ? `<span class="tag">分数 ${Number(u.score).toFixed(3)}</span>` : ""}
    </div>
    ${u.reason ? `<div class="muted" style="font-size:12px;margin-top:4px">${esc(u.reason)}</div>` : ""}
  </div>`;
}

// ── 详情弹窗 ──
async function xpOpen(id) {
  try {
    const c = await api("/api/exp/cases/" + id);
    xpDetailId = id;
    const isSuccess = c.kind === "success";
    const canVerify = isSuccess;
    document.getElementById("xpModal").innerHTML = `
      <div class="row" style="justify-content:space-between;align-items:flex-start">
        <div>
          <h3>${esc(c.question || "(无问题)")}</h3>
          <div class="xp-meta">
            <span class="tag layer">${esc(c.case_no)}</span>${xpTrustTag(c)}${xpReviewTag(c)}${xpVerifyTag(c)}
            ${c.failure_type ? `<span class="status-tag danger">${esc(c.failure_type_zh)}</span>` : ""}
          </div>
        </div>
        <button class="ghost" onclick="xpClose()">关闭</button>
      </div>

      <div class="xp-kv">
        <div class="k">来源</div><div>${esc(c.source_type)} ${c.source_id ? " #" + esc(c.source_id) : ""}
          ${c.source_record ? `· 原始记录行数 ${c.source_record.row_count} · 审核 ${esc(c.source_record.review_status)}` : ""}</div>
        <div class="k">SQL 方言</div><div>${esc(c.dialect || "-")}</div>
        <div class="k">涉及表</div><div>${(c.tables || []).map(esc).join("、") || "-"}</div>
        <div class="k">涉及指标</div><div>${(c.metrics || []).map(esc).join("、") || "-"}</div>
        <div class="k">统计粒度</div><div>${esc(c.grain || "-")}</div>
        <div class="k">审核意见</div><div>${esc(c.review_note || "-")} ${c.reviewer ? "（" + esc(c.reviewer) + "）" : ""}</div>
        <div class="k">验证证据</div><div>${esc((c.verify_evidence && (c.verify_evidence.summary || "")) || "-")}</div>
        ${c.root_cause ? `<div class="k">已知根因</div><div>${esc(c.root_cause)}
          ${c.root_cause_confirmed ? "（已确认）" : "（待分析）"}</div>` : ""}
        ${c.correction_note ? `<div class="k">修正说明</div><div>${esc(c.correction_note)}</div>` : ""}
      </div>

      <div class="xp-sec">
        <h4>SQL 原文</h4>
        <pre class="xp-sql" style="max-height:280px">${esc(c.sql_formatted || c.sql_raw || "（无）")}</pre>
        ${c.corrected_formatted ? `<h4 style="margin-top:10px">修正后的 SQL</h4>
          <pre class="xp-sql" style="max-height:240px">${esc(c.corrected_formatted)}</pre>` : ""}
      </div>

      ${(c.related || []).length ? `<div class="xp-sec"><h4>关联案例</h4>
        ${c.related.map(r => `<a onclick="xpOpen(${r.id})" style="cursor:pointer;color:var(--color-primary);margin-right:12px">
          ${esc(r.case_no)}（${esc(r.kind)}）</a>`).join("")}</div>` : ""}

      <div class="xp-sec">
        <h4>① 分类审核（看口径对不对）</h4>
        <div class="xp-form">
          <input id="xpReviewNote" placeholder="审核意见（驳回必填）" value="${esc(c.review_note || "")}">
          <div class="row" style="gap:8px">
            <button class="btn" onclick="xpReview('approved')">审核通过</button>
            <button class="ghost danger" onclick="xpReview('rejected')">驳回</button>
            <span class="muted" style="font-size:12px">★ 通过只代表口径认可，不代表结果正确</span>
          </div>
        </div>
      </div>

      ${canVerify ? `<div class="xp-sec">
        <h4>② 结果验证（看数对不对 —— 与审核独立）</h4>
        <div class="xp-form">
          <input id="xpVerifyNote" placeholder="验证依据（人工确认必填：跟谁核对、核对了什么）">
          <div class="row" style="gap:8px;flex-wrap:wrap">
            <button class="ghost" onclick="xpAct(${c.id}, 'verify-reexec')">机器重跑验证（只读+白名单）</button>
            <button class="ghost" onclick="xpVerify('manual_confirmed')">人工确认业务含义</button>
            <button class="ghost" onclick="xpVerify('result_verified')">人工确认 + 机器证据</button>
            <button class="ghost danger" onclick="xpVerify('verify_failed')">验证不通过</button>
          </div>
        </div>
      </div>

      <div class="xp-sec">
        <h4>③ 黄金 SQL 资格</h4>
        <div class="row" style="gap:8px;flex-wrap:wrap">
          <button class="ghost" onclick="xpAct(${c.id}, 'promote', {target:'golden_candidate'})">提交黄金候选</button>
          <button class="btn" onclick="xpAct(${c.id}, 'promote', {target:'golden'})">发布为正式黄金 SQL</button>
          <span class="muted" style="font-size:12px">
            条件：审核通过 + 执行成功 + 已人工确认（或结果已验证）+ 显式发布。缺一条后端直接拒绝。
          </span>
        </div>
      </div>` : ""}

      ${c.kind === "failure" ? `<div class="xp-sec">
        <h4>④ 失败归因与修正</h4>
        <div class="xp-form">
          <select id="xpFtype">
            ${Object.entries((xpOverview.status_zh || {}).failure || {}).map(([k, v]) =>
              `<option value="${k}" ${c.failure_type === k ? "selected" : ""}>${esc(v)}</option>`).join("")}
          </select>
          <input id="xpRoot" placeholder="已知根因（写了即视为已确认，请谨慎）" value="${esc(c.root_cause || "")}">
          <input id="xpScope" placeholder="适用条件 / 已知限制" value="${esc(c.scope || "")}">
          <textarea id="xpCorrSql" placeholder="修正后的 SQL（会同时生成一条「修正对照」案例）">${esc(c.corrected_sql || "")}</textarea>
          <input id="xpCorrNote" placeholder="修正说明（哪里错了、为什么这么改）" value="${esc(c.correction_note || "")}">
          <div class="row" style="gap:8px">
            <button class="ghost" onclick="xpSaveFields()">保存失败类型 / 根因</button>
            <button class="btn" onclick="xpCorrection()">保存修正 SQL</button>
          </div>
        </div>
      </div>` : ""}

      <div class="xp-sec">
        <h4>⑤ 撤销</h4>
        <div class="row" style="gap:8px">
          <input id="xpRevokeReason" placeholder="撤销原因（必填）" style="flex:1">
          <button class="ghost danger" onclick="xpRevoke(${c.id})">撤销该案例</button>
        </div>
        <div class="muted" style="font-size:12px;margin-top:6px">
          撤销后立即退出正向召回，但历史记录、审核结论与来源全部保留（审计链不断）。
        </div>
      </div>`;
    document.getElementById("xpMask").classList.remove("hidden");
  } catch (e) {
    alert("加载失败：" + e.message);
  }
}

function xpClose() {
  document.getElementById("xpMask").classList.add("hidden");
  xpDetailId = null;
}

async function xpAct(id, action, payload) {
  try {
    if (action === "verify-reexec") {
      const r = await api(`/api/exp/cases/${id}/reexec`, { method: "POST", body: "{}" });
      alert("重跑结果：" + (r.message || "完成"));
    } else if (action === "promote") {
      const r = await api(`/api/exp/cases/${id}/promote`,
        { method: "POST", body: JSON.stringify(payload || {}) });
      alert(r.message || "完成");
    }
    await loadExpPage();
    if (xpDetailId === id) await xpOpen(id);
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

async function xpReview(status) {
  const note = (document.getElementById("xpReviewNote") || {}).value || "";
  try {
    const r = await api(`/api/exp/cases/${xpDetailId}/review`,
      { method: "POST", body: JSON.stringify({ status, note }) });
    alert(r.message || "已记录");
    await loadExpPage();
    await xpOpen(xpDetailId);
  } catch (e) { alert("操作失败：" + e.message); }
}

async function xpVerify(status) {
  const note = (document.getElementById("xpVerifyNote") || {}).value || "";
  try {
    const r = await api(`/api/exp/cases/${xpDetailId}/verify`,
      { method: "POST", body: JSON.stringify({ status, note }) });
    alert(r.message || "已记录");
    await loadExpPage();
    await xpOpen(xpDetailId);
  } catch (e) { alert("操作失败：" + e.message); }
}

async function xpSaveFields() {
  const fields = {
    failure_type: (document.getElementById("xpFtype") || {}).value,
    root_cause: (document.getElementById("xpRoot") || {}).value,
    scope: (document.getElementById("xpScope") || {}).value,
  };
  if (fields.root_cause) fields.root_cause_confirmed = 1;
  try {
    await api(`/api/exp/cases/${xpDetailId}`, { method: "PUT", body: JSON.stringify({ fields }) });
    await loadExpPage();
    await xpOpen(xpDetailId);
  } catch (e) { alert("保存失败：" + e.message); }
}

async function xpCorrection() {
  const body = {
    sql: (document.getElementById("xpCorrSql") || {}).value || "",
    note: (document.getElementById("xpCorrNote") || {}).value || "",
    root_cause: (document.getElementById("xpRoot") || {}).value || "",
    failure_type: (document.getElementById("xpFtype") || {}).value || "",
  };
  if (!body.sql.trim()) { alert("请先填写修正后的 SQL"); return; }
  try {
    const r = await api(`/api/exp/cases/${xpDetailId}/correction`,
      { method: "POST", body: JSON.stringify(body) });
    alert(r.message || "已保存");
    await loadExpPage();
    await xpOpen(xpDetailId);
  } catch (e) { alert("保存失败：" + e.message); }
}

async function xpRevoke(id) {
  const el = document.getElementById("xpRevokeReason");
  const note = (el && el.value || "").trim() || prompt("撤销原因（必填）：") || "";
  if (!note.trim()) { alert("撤销必须写清原因"); return; }
  try {
    const r = await api(`/api/exp/cases/${id}/promote`,
      { method: "POST", body: JSON.stringify({ target: "revoked", note }) });
    alert(r.message || "已撤销");
    await loadExpPage();
    if (xpDetailId === id) await xpOpen(id);
  } catch (e) { alert("操作失败：" + e.message); }
}

async function xpRuleAct(id, action) {
  try {
    let r;
    if (action === "publish") {
      r = await api(`/api/exp/rules/${id}/publish`, { method: "POST", body: JSON.stringify({}) });
    } else {
      const reason = prompt("撤销原因（必填）：") || "";
      if (!reason.trim()) return;
      r = await api(`/api/exp/rules/${id}/revoke`,
        { method: "POST", body: JSON.stringify({ reason }) });
    }
    alert(r.message || "完成");
    await loadExpPage();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function xpImport() {
  if (!confirm("把现有的取数记录导入经验库？\n\n导入会保留原始审核状态，未验证的记录一律标为「未验证」，不会自动升级为黄金 SQL。")) return;
  try {
    const r = await api("/api/exp/import", { method: "POST", body: JSON.stringify({ feedbacks: true }) });
    alert(r.message || "导入完成");
    await loadExpPage();
  } catch (e) { alert("导入失败：" + e.message); }
}

async function xpMine() {
  if (!confirm("让 AI 归纳候选规则？\n\n只会生成「候选规则」，不会自动生效；每条规则都必须能追溯到具体案例，发布需要你手动确认。")) return;
  const btn = (typeof event !== "undefined") ? event.target : null;
  if (btn) { btn.disabled = true; btn.textContent = "归纳中（可能要 1~2 分钟）…"; }
  try {
    const r = await api("/api/exp/mine", { method: "POST", body: JSON.stringify({}) });
    const okN = (r.results || []).filter(x => x.rule_id).length;
    const errN = (r.results || []).filter(x => x.error).length;
    alert(`归纳完成：生成候选规则 ${okN} 条${errN ? `，跳过 ${errN} 组（${(r.results.find(x => x.error) || {}).error}）` : ""}`);
    xpSwitchTab("rule", document.querySelector("#tab-exp .subtabs button:nth-child(4)"));
  } catch (e) { alert("归纳失败：" + e.message); }
  finally { if (btn) { btn.disabled = false; btn.textContent = "AI 归纳候选规则"; } }
}

/** 跳转到「知识管理 → 表结构信息」（多处复用：缺口/反馈/权限页的"去看这张表"） */
function gotoKbSchema() {
  if (!isAdmin()) return;
  switchTab("kb", document.querySelector('.nav-item[data-tab="kb"]'));
  const schemaBtn = document.getElementById("kbTabSchema");
  if (schemaBtn) kbSwitchModule("schema", schemaBtn);
}

/** 简单的 HTML 转义，防止表格里的内容破坏页面结构 */
function esc(s) {
  if (s === null || s === undefined) return "";
  return String(s).replace(/[&<>"]/g, c => ({ "&":"&amp;", "<":"&lt;", ">":"&gt;", '"':"&quot;" }[c]));
}

