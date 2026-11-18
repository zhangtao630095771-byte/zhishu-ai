/* ══════════════════════════════════════════════════════════════
   admin.js · 表与权限 · 账号管理 · 字段级数据权限 · 角标
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// Tab 4：表与权限
// ═══════════════════════════════════════════════════════════
let ALL_TABLES = [];   // 缓存全部表，搜索时本地过滤（不用每次打后端）

async function loadTables() {
  const [tables, acl] = await Promise.all([api("/api/tables"), api("/api/acl")]);
  document.getElementById("aclMode").innerHTML = acl.default_visible
    ? "当前策略：<b>黑名单模式</b>（没登记的表默认允许访问，只有显式禁止的才不可见）"
    : "当前策略：<b>白名单模式</b>（没登记的表默认禁止访问，必须逐个授权）";
  ALL_TABLES = tables;
  renderTableList();
}

/** 表名前缀 → 数据分层标签（dim/dwd/dws/ads/mid/ods 之外算"其它"）*/
function layerOf(name) {
  const m = name.split("_")[0];
  return ["dim", "dwd", "dws", "ads", "mid", "ods"].includes(m) ? m : "其它";
}

/** 按搜索框 + 筛选下拉渲染表格（本地过滤，秒级响应 700+ 张表）*/
function renderTableList() {
  const kw = document.getElementById("tableSearch").value.trim().toLowerCase();
  const flt = document.getElementById("tableFilter").value;
  const rows = ALL_TABLES.filter(t => {
    if (kw && !(t.table.toLowerCase().includes(kw) || (t.comment || "").toLowerCase().includes(kw)))
      return false;
    if (flt === "allowed" && !t.visible) return false;
    if (flt === "denied"  &&  t.visible) return false;
    return true;
  });
  document.getElementById("tableCount").textContent = `共 ${rows.length} 张`;

  // ★ 列宽在这里定死（配合 CSS 的 table-layout: fixed）。
  //   为什么不用自适应布局？因为表名长度差异极大（dim_a vs ads_label_statistics_prd_all），
  //   自动布局会让"最长那个表名"决定整列宽度，其它列被挤扁、注释列被压到看不见字。
  //   这里只有「表名」一列留自适应，其余给定值 —— 宽度就稳了。
  const colgroup = `<colgroup>
      <col>
      <col style="width:176px"><col style="width:74px">
      <col style="width:128px"><col style="width:202px">
    </colgroup>`;
  //   ★ 「分层」不再单独占一列 —— 它从表名首段就能看出来，单独一列白占 60px。
  //     改成表名前的小标签，信息没丢，宽度省下来了。
  const head = `${colgroup}<thead><tr>
      <th>表名</th><th>业务注释</th><th>AI 可见</th><th>备注</th><th>操作</th>
    </tr></thead>`;
  const body = rows.map(t => `
    <tr>
      <td class="tname">
        <span class="tag layer" style="margin-right:6px">${layerOf(t.table)}</span>${esc(t.table)}</td>
      <td class="tcmt">
        <span class="tcmt-txt${t.comment ? "" : " none"}"
              title="${esc(t.comment || "（暂无业务注释）")}">${esc(t.comment) || "—"}</span>
        ${t.comment_source === "ai"
          ? '<span class="status-tag info" title="AI 生成的草稿注释：当前待复核、不进检索；核准或改过之后才生效">AI 生成</span>' : ""}
      </td>
      <td>${t.visible ? '<span class="status-tag success">可见</span>' : '<span class="status-tag gray">已屏蔽</span>'}</td>
      <td><input class="note-in" id="note_${esc(t.table)}" value="${esc(t.acl_note)}"
                 placeholder="备注"></td>
      <td class="ops">
        <div class="row">
          <button class="ghost" onclick="toggleAcl('${esc(t.table)}', ${!t.visible})">
            ${t.visible ? "禁止访问" : "开放访问"}</button>
          <button class="ghost" onclick="saveAclNote('${esc(t.table)}', ${t.visible})">存备注</button>
          <button class="ghost" onclick="delAcl('${esc(t.table)}')">取消登记</button>
        </div>
      </td>
    </tr>`).join("");
  document.getElementById("tableList").innerHTML = head + `<tbody>${body}</tbody>`;
}

/** 开放/禁止 —— 同时把备注一起带上保存 */
async function toggleAcl(table, visible) {
  const action = visible ? "开放" : "禁止";
  if (!confirm(`确定${action} AI 访问表「${table}」？`)) return;
  const note = (document.getElementById("note_" + table) || {}).value || "";
  const r = await api(`/api/acl/${encodeURIComponent(table)}`, {
    method: "PUT", body: JSON.stringify({ visible, note }),
  });
  loadTables();
  // ★ 刚开放权限 → 后端已经在后台生成表注释了，别让用户以为"啥也没发生"
  //   生成要调大模型（几秒），所以先给个反馈，再轮询等结果
  if (r.doc_generating) {
    aclNotice(`已开放「${esc(table)}」的访问权限。
      <div class="muted" style="font-size:12px;margin-top:4px">
        正在后台读它的字段清单，生成一份表业务注释 —— <b>生成后先标「待复核」，不进检索</b>，
        你可以再到「知识管理 → 表结构信息」里改。</div>`);
    aclPollDoc(table);
  } else {
    alert(r.message);
  }
}

/** 权限页的提示条（后台任务的反馈，不打断操作） */
function aclNotice(html, kind) {
  const box = document.getElementById("aclNotice");
  if (!box) return;
  if (!html) { box.style.display = "none"; box.innerHTML = ""; return; }
  box.className = "alert " + (kind || "info");
  box.style.display = "block";
  box.innerHTML = html;
}

/**
 * 轮询：等后台把这张表「开荒」完（表业务注释 + 业务实体）
 *
 * ★ 为什么改成轮询 /api/acl/prepare/{table} 而不是表详情？
 *   现在一次要生成【两样】：表注释和业务实体。
 *   只看表详情的话，"注释好了但实体还在生成"就会被误判成"完事了"，
 *   用户等不到实体结果 —— 而实体恰恰是新表最容易缺、也最影响可用性的一样。
 */
function aclPollDoc(table) {
  let tries = 0;
  const tick = async () => {
    tries++;
    let st;
    try {
      st = await api(`/api/acl/prepare/${encodeURIComponent(table)}`);
    } catch (e) {
      return;                         // 轮询失败就静默停，不打扰用户
    }
    const doc = st.doc, ent = st.entity;
    const running = s => s && (s.status === "pending");
    const done = !!doc && !!ent && !running(doc) && !running(ent);

    if (done || tries >= 40) {
      if (!doc) { aclNotice(""); return; }
      const line = (label, s, okText) => {
        if (!s) return `<div>· ${label}：<span class="muted">未处理</span></div>`;
        if (s.status === "done") return `<div>· ${label}：✅ ${okText}${s.detail ? " " + esc(s.detail) : ""}</div>`;
        if (s.status === "skipped") return `<div>· ${label}：<span class="muted">跳过（${esc(s.detail || "已有")}）</span></div>`;
        return `<div>· ${label}：<span style="color:var(--color-warning)">未生成（${esc(s.detail || "超时")}）</span></div>`;
      };
      aclNotice(`✅ 「${esc(table)}」已开放，并自动生成：
        <div style="margin:8px 0;line-height:1.9">
          ${line("表业务注释", doc, "已生成")}
          ${line("业务实体", ent, "已登记")}
        </div>
        <div class="muted" style="font-size:12px">
          <b>两样都已生成并标为「待复核」（暂不进检索）</b>，且标为「AI 生成」。
          去复核一遍（哪怕只改一个字）就转为人工版，AI 之后不再覆盖。
        </div>
        <div class="row" style="gap:8px;flex-wrap:wrap;margin-top:8px">
          <button class="ghost" data-t="${esc(table)}" onclick="gotoTableDoc(this.dataset.t)">去查看表注释</button>
          <button class="ghost" onclick="gotoKbRegistry()">去复核业务实体</button>
          <button class="ghost" onclick="aclNotice('')">知道了</button>
        </div>`);
      loadTables();                 // 列表上标出「AI 生成」
      return;
    }
    aclNotice(`已开放「${esc(table)}」，正在后台读它的字段清单生成
      <b>表业务注释 + 业务实体</b>…
      <div class="muted" style="font-size:12px;margin-top:4px">
        两样都会标为「待复核」，不进检索；去复核通过后才生效。</div>`);
    setTimeout(tick, 2500);
  };
  setTimeout(tick, 2500);
}

/** 跳到「知识管理 → 语义注册表」复核业务实体
    ★ 语义注册表已从「知识治理」移到「知识管理」（它编辑的是 P2/P4 内容），故此处先切到 kb 页。 */
function gotoKbRegistry() {
  aclNotice("");
  const navBtn = document.querySelector("#sideNav button[data-tab='kb']");
  if (navBtn) switchTab("kb", navBtn);
  const btn = document.getElementById("kbTabRegistry");
  if (btn) kbSwitchModule("registry", btn);
}

/** 从权限页跳到「知识管理 → 表结构信息」并打开这张表 */
async function gotoTableDoc(table) {
  aclNotice("");
  gotoKbSchema();
  await kbOpenTable(table);
}

/** 只保存备注（不改变可见性）*/
async function saveAclNote(table, visible) {
  const note = (document.getElementById("note_" + table) || {}).value || "";
  const r = await api(`/api/acl/${encodeURIComponent(table)}`, {
    method: "PUT", body: JSON.stringify({ visible, note }),
  });
  alert(r.message);
  loadTables();
}

/** 取消登记（回到默认策略；白名单模式下＝不可见）*/
async function delAcl(table) {
  if (!confirm(`取消登记「${table}」？白名单模式下将变为不可见。`)) return;
  const r = await api(`/api/acl/${encodeURIComponent(table)}`, { method: "DELETE" });
  alert(r.message);
  loadTables();
}

// ═══════════════════════════════════════════════════════════
// 账号管理（仅管理员）
// ═══════════════════════════════════════════════════════════
//  【两件事分开：前端"能不能点到" vs 后端"能不能做到"】
//    这里的按钮只是让界面干净；真正的权限在后端中间件。
let ALL_USERS = [];   // 缓存最近一次拉取的账号列表，搜索时前端过滤即可

async function loadUsers() {
  try {
    const d = await api("/api/users");
    ALL_USERS = d.items || [];
    renderUsers();
  } catch (e) {
    const box = document.getElementById("userList");
    box.innerHTML = `<tr><td class="empty">加载失败：${esc(e.message)}</td></tr>`;
  }
}

function renderUsers() {
  const box = document.getElementById("userList");
  const me = (CURRENT_USER || {}).username;
  const q = (document.getElementById("uSearch")?.value || "").trim().toLowerCase();
  const items = q
    ? ALL_USERS.filter(u =>
        (u.username || "").toLowerCase().includes(q) ||
        (u.display_name || "").toLowerCase().includes(q))
    : ALL_USERS;
  const rows = items.map(u => {
    const self = u.username === me;
    return `<tr>
        <td>
          <span class="u-name">${esc(u.username)}</span>
          ${self ? '<span class="u-self">（我）</span>' : ""}
        </td>
        <td>${esc(u.display_name || "")}</td>
        <td><span class="role-pill ${u.role === "admin" ? "" : "user"}">${esc(u.role_zh || u.role)}</span></td>
        <td>${u.enabled
              ? '<span class="status-tag success">启用</span>'
              : '<span class="status-tag gray">已停用</span>'}</td>
        <td class="muted" style="font-size:12px">${esc(u.created_at || "")}</td>
        <td><div class="u-ops">
          <button class="ghost" onclick="openUserEdit('${esc(u.username)}')">编辑</button>
          <button class="ghost" onclick="toggleUserRole('${esc(u.username)}','${u.role}')">
            ${u.role === "admin" ? "降为普通用户" : "升为管理员"}
          </button>
          <button class="ghost" onclick="toggleUserEnabled('${esc(u.username)}',${u.enabled})">
            ${u.enabled ? "停用" : "启用"}
          </button>
          <button class="ghost" onclick="resetUserPwd('${esc(u.username)}')">重置密码</button>
          <button class="ghost danger" onclick="deleteUser('${esc(u.username)}')">删除</button>
        </div></td>
      </tr>`;
  }).join("");
  box.innerHTML = `<tr>
      <th>登录名</th><th>显示名</th><th>角色</th><th>状态</th><th>创建时间</th><th>操作</th>
    </tr>` + (rows || `<tr><td class="empty" colspan="6">暂无账号</td></tr>`);
}

function userMsg(t, kind) {
  const el = document.getElementById("uMsg");
  el.textContent = t || "";
  el.style.color = kind === "err" ? "var(--color-danger)" : "var(--color-success)";
}

async function createUser() {
  const username = document.getElementById("uName").value.trim();
  const display_name = document.getElementById("uDisplay").value.trim();
  const password = document.getElementById("uPwd").value;
  const role = document.getElementById("uRole").value;
  if (!username) { userMsg("请填写登录名", "err"); return; }
  if (!password) { userMsg("请填写初始密码（至少 6 位）", "err"); return; }
  try {
    const r = await api("/api/users", {
      method: "POST",
      body: JSON.stringify({ username, password, role, display_name }),
    });
    userMsg(r.message);
    document.getElementById("uName").value = "";
    document.getElementById("uDisplay").value = "";
    document.getElementById("uPwd").value = "";
    loadUsers();
  } catch (e) {
    userMsg("创建失败：" + e.message, "err");
  }
}

async function toggleUserRole(username, role) {
  const next = role === "admin" ? "user" : "admin";
  const zh = next === "admin" ? "管理员" : "普通用户";
  if (!confirm(`把「${username}」改为${zh}？\\n（该账号已登录的会话会立即失效，需重新登录）`)) return;
  try {
    const r = await api("/api/users/" + encodeURIComponent(username), {
      method: "PUT", body: JSON.stringify({ role: next }),
    });
    alert(r.message); loadUsers();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function toggleUserEnabled(username, enabled) {
  const next = !enabled;
  if (!confirm(`确认${next ? "启用" : "停用"}「${username}」？`)) return;
  try {
    const r = await api("/api/users/" + encodeURIComponent(username), {
      method: "PUT", body: JSON.stringify({ enabled: next }),
    });
    alert(r.message); loadUsers();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function resetUserPwd(username) {
  const pwd = prompt(`给「${username}」设置新密码（至少 6 位）：`);
  if (pwd === null) return;
  if (pwd.length < 6) { alert("密码至少 6 位"); return; }
  try {
    const r = await api("/api/users/" + encodeURIComponent(username), {
      method: "PUT", body: JSON.stringify({ password: pwd }),
    });
    alert(r.message); loadUsers();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function deleteUser(username) {
  if (!confirm(`确认删除账号「${username}」？此操作不可恢复。`)) return;
  try {
    const r = await api("/api/users/" + encodeURIComponent(username), { method: "DELETE" });
    alert(r.message); loadUsers();
  } catch (e) { alert("删除失败：" + e.message); }
}

// ── 编辑账号弹窗：可改登录名 / 显示名 / 角色 / 状态 / 密码 ──
let U_EDIT_OLD = "";
function openUserEdit(username) {
  const u = (ALL_USERS || []).find(x => x.username === username);
  if (!u) return;
  U_EDIT_OLD = username;
  document.getElementById("uEditName").value = u.username;
  document.getElementById("uEditDisplay").value = u.display_name || "";
  document.getElementById("uEditRole").value = u.role;
  document.getElementById("uEditEnabled").value = u.enabled ? "1" : "0";
  document.getElementById("uEditPwd").value = "";
  const msg = document.getElementById("uEditMsg");
  msg.textContent = "";
  document.getElementById("userEditMask").classList.remove("hidden");
}

function closeUserEdit() {
  document.getElementById("userEditMask").classList.add("hidden");
}

async function saveUserEdit() {
  const name = document.getElementById("uEditName").value.trim();
  const display = document.getElementById("uEditDisplay").value.trim();
  const role = document.getElementById("uEditRole").value;
  const enabled = document.getElementById("uEditEnabled").value === "1";
  const pwd = document.getElementById("uEditPwd").value;
  const msg = document.getElementById("uEditMsg");
  msg.style.color = "var(--color-danger)";
  if (!name) { msg.textContent = "请填写登录名"; return; }
  if (pwd && pwd.length < 6) { msg.textContent = "密码至少 6 位"; return; }
  const body = {
    role, enabled, display_name: display,
    // 没改登录名就传 null（后端据此判断是"改名"还是"只改其它字段"）
    new_username: name === U_EDIT_OLD ? null : name,
  };
  if (pwd) body.password = pwd;
  try {
    const r = await api("/api/users/" + encodeURIComponent(U_EDIT_OLD), {
      method: "PUT", body: JSON.stringify(body),
    });
    closeUserEdit();
    alert(r.message);
    // 改了登录名或显示名，且改的是自己 → 后端已作废会话，引导重新登录以拿到新身份
    const me = (CURRENT_USER || {}).username;
    const meDisp = (CURRENT_USER || {}).display_name || "";
    if (U_EDIT_OLD === me && (name !== U_EDIT_OLD || display !== meDisp)) {
      handleUnauthorized("登录名 / 信息已变更，请重新登录");
      return;
    }
    loadUsers();
  } catch (e) {
    msg.textContent = "保存失败：" + e.message;
  }
}

// ═══════════════════════════════════════════════════════════
// 数据权限（字段级权限：角色 / 数据域 / 敏感字段 / 策略 / 审计 / AI 助手）
// ═══════════════════════════════════════════════════════════
function permSwitch(name, btn) {
  document.querySelectorAll(".perm-mod").forEach(el => el.classList.add("hidden"));
  const mod = document.getElementById("perm-" + name);
  if (mod) mod.classList.remove("hidden");
  document.querySelectorAll("[data-pm]").forEach(el => el.classList.remove("active"));
  if (btn) btn.classList.add("active");
}

async function loadPermPage() {
  permSwitch("ai", document.querySelector('[data-pm="ai"]'));
  await Promise.all([permLoadRoles(), permLoadDomains(), permLoadSensitive(),
                     permLoadPolicies(), permLoadDrafts(), permLoadAudit(),
                     permLoadBindUsers(), permLoadRow()]);
}

// ── AI 助手 ──
async function permAiDraft() {
  const text = document.getElementById("permNlText").value.trim();
  const msg = document.getElementById("permAiMsg");
  if (!text) { msg.textContent = "请先输入权限要求"; return; }
  msg.textContent = "解析中（约 2~5 秒）…";
  try {
    const r = await api("/api/permission/ai-draft", { method: "POST", body: { text } });
    msg.textContent = "";
    const preview = document.getElementById("permAiPreview");
    preview.classList.remove("hidden");
    let html = `<div class="alert ${r.ambiguities.length ? 'warn' : 'ok'}" style="font-size:12.5px">`;
    if (r.ambiguities.length) {
      html += `<b>⚠️ 存在需确认项：</b><ul>${r.ambiguities.map(a => `<li>${esc(a.issue)}</li>`).join("")}</ul>`;
    } else {
      html += `<b>✓ 已解析 ${r.rules.length} 条规则，可直接发布。</b>`;
    }
    html += `</div>`;
    html += `<table class="u-table"><tr><th>角色</th><th>表</th><th>字段</th><th>动作</th><th>级别</th><th>脱敏</th></tr>`;
    html += r.rules.map(x => `<tr><td>${esc(x.subject_name)}</td><td>${esc(x.table_name)}</td><td>${esc(x.column_name)}</td><td>${esc(x.action)}</td><td><b>${esc(x.access)}</b></td><td>${esc(x.mask_rule || "")}</td></tr>`).join("");
    html += `</table>`;
    html += `<div class="row" style="margin-top:10px">
      <button class="btn" onclick="permApplyDraft(${r.draft_id})">确认并发布</button>
      <button class="ghost" onclick="permRejectDraft(${r.draft_id})">驳回</button></div>`;
    preview.innerHTML = html;
    permLoadDrafts();
  } catch (e) {
    msg.textContent = "解析失败：" + e.message;
  }
}

async function permApplyDraft(id) {
  try {
    const r = await api(`/api/permission/ai-drafts/${id}/apply`, { method: "POST", body: { apply: 1 } });
    alert(`已发布 ${r.applied} 条规则`);
    permLoadDrafts(); permLoadPolicies();
  } catch (e) { alert("发布失败：" + e.message); }
}

async function permRejectDraft(id) {
  try {
    await api(`/api/permission/ai-drafts/${id}/apply`, { method: "POST", body: { apply: 0 } });
    alert("已驳回"); permLoadDrafts();
  } catch (e) { alert("操作失败：" + e.message); }
}

async function permLoadDrafts() {
  const box = document.getElementById("permDraftList");
  try {
    const d = await api("/api/permission/ai-drafts");
    const items = (d.items || []).slice(0, 20);
    box.innerHTML = items.length
      ? `<tr><th>ID</th><th>原文</th><th>规则数</th><th>状态</th><th>创建人</th><th>操作</th></tr>` +
        items.map(x => {
          // 草稿状态（后端枚举 pending/applied/rejected）：待发布=橙 / 已发布=绿 / 已驳回=红
          const stCls = x.status === "pending" ? "warning" : x.status === "applied" ? "success"
                      : x.status === "rejected" ? "danger" : "gray";
          const stTxt = ({ pending: "待发布", applied: "已发布", rejected: "已驳回" })[x.status] || x.status;
          return `<tr>
          <td>${x.id}</td>
          <td style="max-width:320px;white-space:normal">${esc(x.source_text)}</td>
          <td>${(x.parsed || []).length}</td>
          <td><span class="status-tag ${stCls}">${esc(stTxt)}</span></td><td>${esc(x.created_by || "")}</td>
          <td>${x.status === "pending" ? `<button class="ghost" onclick="permApplyDraft(${x.id})">发布</button>` : "—"}</td>
        </tr>`;
        }).join("")
      : `<tr><td class="empty">暂无草稿</td></tr>`;
  } catch (e) { box.innerHTML = `<tr><td class="empty">加载失败：${esc(e.message)}</td></tr>`; }
}

// ── 角色 ──
async function permLoadRoles() {
  const box = document.getElementById("permRoleList");
  try {
    const d = await api("/api/permission/roles");
    const items = d.items || [];
    box.innerHTML = `<tr><th>角色</th><th>显示名</th><th>内置</th><th>说明</th><th>操作</th></tr>` +
      items.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.display_name || "")}</td>
        <td>${x.builtin ? "是" : "否"}</td><td>${esc(x.description || "")}</td>
        <td>${!x.builtin ? `<button class="ghost" onclick="permDelRole('${esc(x.name)}')">删除</button>` : "—"}</td></tr>`).join("");
  } catch (e) { box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}

async function permCreateRole() {
  const name = document.getElementById("permRoleName").value.trim();
  const zh = document.getElementById("permRoleZh").value.trim();
  const desc = document.getElementById("permRoleDesc").value.trim();
  if (!name) { alert("角色名不能为空"); return; }
  try {
    await api("/api/permission/roles", { method: "POST", body: { display_name: zh || name, description: desc } });
    permLoadRoles(); permLoadBindUsers();
  } catch (e) { alert("创建失败：" + e.message); }
}

async function permDelRole(name) {
  if (!confirm(`删除角色「${name}」？`)) return;
  try { await api(`/api/permission/roles/${encodeURIComponent(name)}`, { method: "DELETE" }); permLoadRoles(); }
  catch (e) { alert("删除失败：" + e.message); }
}

// ── 用户绑定 ──
let PERM_ROLES = [], PERM_USERS = [], PERM_BINDING_USER = "";
async function permLoadBindUsers() {
  try {
    const d = await api("/api/permission/user-roles");
    PERM_USERS = d.users || [];
    PERM_ROLES = (await api("/api/permission/roles")).items || [];
    const sel = document.getElementById("permBindUser");
    sel.innerHTML = PERM_USERS.map(u => `<option value="${esc(u.username)}">${esc(u.display_name || u.username)}</option>`).join("");
    PERM_BINDING_USER = PERM_USERS[0]?.username || "";
    permRenderBindRoles();
    sel.onchange = () => { PERM_BINDING_USER = sel.value; permRenderBindRoles(); };
  } catch (e) {}
}
function permRenderBindRoles() {
  const box = document.getElementById("permBindRoles");
  box.innerHTML = PERM_ROLES.map(r => {
    const checked = r.name === "user" ? "checked disabled" : "";
    return `<label style="font-size:12.5px"><input type="checkbox" value="${esc(r.name)}" ${checked}> ${esc(r.display_name || r.name)}</label>`;
  }).join("");
}
async function permSaveBind() {
  const roles = Array.from(document.querySelectorAll("#permBindRoles input:checked")).map(x => x.value);
  try {
    await api(`/api/permission/user-roles/${encodeURIComponent(PERM_BINDING_USER)}`, { method: "PUT", body: { roles } });
    alert("已保存绑定");
  } catch (e) { alert("保存失败：" + e.message); }
}

// ── 数据域 / 敏感字段 ──
async function permLoadDomains() {
  const box = document.getElementById("permDomList");
  try {
    const d = await api("/api/permission/domains");
    const items = d.items || [];
    box.innerHTML = `<tr><th>标识</th><th>显示名</th><th>负责人</th><th>说明</th></tr>` +
      items.map(x => `<tr><td><b>${esc(x.name)}</b></td><td>${esc(x.display_name || "")}</td><td>${esc(x.owner || "")}</td><td>${esc(x.description || "")}</td></tr>`).join("")
      || `<tr><td class="empty">暂无数据域</td></tr>`;
  } catch (e) { box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}
async function permUpsertDomain() {
  const name = document.getElementById("permDomName").value.trim();
  if (!name) { alert("域标识不能为空"); return; }
  try {
    await api(`/api/permission/domains/${encodeURIComponent(name)}`, { method: "PUT", body: {
      display_name: document.getElementById("permDomZh").value.trim(),
      owner: document.getElementById("permDomOwner").value.trim(), enabled: 1 } });
    permLoadDomains();
  } catch (e) { alert("保存失败：" + e.message); }
}

async function permLoadSensitive() {
  const box = document.getElementById("permSrList");
  try {
    const d = await api("/api/permission/sensitive?confirmed_only=false");
    const items = d.items || [];
    box.innerHTML = `<tr><th>域</th><th>表</th><th>字段</th><th>等级</th><th>状态</th><th>操作</th></tr>` +
      items.map(x => `<tr><td>${esc(x.domain_name || "")}</td><td>${esc(x.table_name)}</td><td><b>${esc(x.column_name)}</b></td>
        <td>${esc(x.sensitivity)}</td><td>${x.mapping_status === "confirmed" ? "✅已确认" : "⏳待确认"}</td>
        <td><button class="ghost" onclick="permDelSensitive(${x.id})">删除</button></td></tr>`).join("")
      || `<tr><td class="empty">暂无敏感字段映射</td></tr>`;
  } catch (e) { box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}
async function permUpsertSensitive() {
  const t = document.getElementById("permSrTable").value.trim();
  const c = document.getElementById("permSrColumn").value.trim();
  if (!t || !c) { alert("表名与字段名不能为空"); return; }
  try {
    await api("/api/permission/sensitive", { method: "PUT", body: {
      domain_name: document.getElementById("permSrDomain").value.trim(),
      table_name: t, column_name: c,
      sensitivity: document.getElementById("permSrLevel").value,
      mapping_status: "confirmed" } });
    permLoadSensitive();
  } catch (e) { alert("登记失败：" + e.message); }
}
async function permDelSensitive(id) {
  if (!confirm("删除该敏感字段映射？")) return;
  try { await api(`/api/permission/sensitive/${id}`, { method: "DELETE" }); permLoadSensitive(); }
  catch (e) { alert("删除失败：" + e.message); }
}

// ── 字段策略 ──
async function permLoadPolicies() {
  const box = document.getElementById("permPolicyList");
  try {
    const d = await api("/api/permission/policies");
    const items = d.items || [];
    box.innerHTML = `<tr><th>主体</th><th>表</th><th>字段</th><th>动作</th><th>级别</th><th>脱敏</th><th>版本</th><th>状态</th><th>操作</th></tr>` +
      items.map(x => `<tr><td>${esc(x.subject_type)}:${esc(x.subject_name)}</td><td>${esc(x.table_name)}</td><td>${esc(x.column_name)}</td>
        <td>${esc(x.action)}</td><td><b>${esc(x.access)}</b></td><td>${esc(x.mask_rule || "")}</td>
        <td>v${x.version}</td><td>${x.enabled
          ? '<span class="status-tag success">启用</span>'
          : '<span class="status-tag gray">停用</span>'}</td>
        <td><button class="ghost" onclick="permTogglePolicy(${x.id}, ${x.enabled ? 0 : 1})">${x.enabled ? "停用" : "启用"}</button>
            <button class="ghost" onclick="permDelPolicy(${x.id})">删除</button></td></tr>`).join("")
      || `<tr><td class="empty">暂无策略</td></tr>`;
  } catch (e) { box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}
async function permUpsertPolicy() {
  const subject = document.getElementById("permPolSubject").value.trim();
  const table = document.getElementById("permPolTable").value.trim();
  const column = document.getElementById("permPolColumn").value.trim();
  if (!subject || !table || !column) { alert("主体/表/字段不能为空"); return; }
  try {
    await api("/api/permission/policies", { method: "PUT", body: {
      subject_type: document.getElementById("permPolType").value,
      subject_name: subject, table_name: table, column_name: column,
      access: document.getElementById("permPolAccess").value,
      action: document.getElementById("permPolAction").value,
      mask_rule: document.getElementById("permPolMask").value,
      change_note: "手动配置" } });
    permLoadPolicies();
  } catch (e) { alert("保存失败：" + e.message); }
}
async function permTogglePolicy(id, enabled) {
  try { await api(`/api/permission/policies/${id}/enabled`, { method: "POST", body: { enabled } }); permLoadPolicies(); }
  catch (e) { alert("操作失败：" + e.message); }
}
async function permDelPolicy(id) {
  if (!confirm("删除该策略？")) return;
  try { await api(`/api/permission/policies/${id}`, { method: "DELETE" }); permLoadPolicies(); }
  catch (e) { alert("删除失败：" + e.message); }
}

// ── 审计 ──
async function permLoadAudit() {
  const box = document.getElementById("permAuditList");
  try {
    const d = await api("/api/permission/audit?limit=50");
    const items = d.items || [];
    box.innerHTML = `<tr><th>时间</th><th>用户</th><th>动作</th><th>判定</th><th>表</th><th>拒绝原因</th></tr>` +
      items.map(x => {
        // 判定：拒绝=红 / 允许=绿 / 其它灰；文字保留原值，颜色不作为唯一识别方式
        const dv = String(x.decision || "").toLowerCase();
        const dCls = dv.includes("deny") || dv.includes("reject") ? "danger"
                   : dv.includes("allow") || dv.includes("pass") ? "success" : "gray";
        return `<tr><td>${esc(x.created_at)}</td><td>${esc(x.username)}</td><td>${esc(x.action)}</td>
        <td><span class="status-tag ${dCls}"><b>${esc(x.decision)}</b></span></td><td>${esc((x.tables || []).join(","))}</td>
        <td style="max-width:300px;white-space:normal">${esc(x.deny_reason || "")}</td></tr>`;
      }).join("")
      || `<tr><td class="empty">暂无审计记录</td></tr>`;
  } catch (e) { box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`; }
}

// ── 行级权限 ──
let PERM_ATTR_USER = "";
async function permLoadRow() {
  try {
    // 用户属性编辑下拉
    const users = (await api("/api/users")).items || [];
    const sel = document.getElementById("permAttrUser");
    sel.innerHTML = users.map(u => `<option value="${esc(u.username)}">${esc(u.display_name || u.username)}</option>`).join("");
    PERM_ATTR_USER = users[0]?.username || "";
    sel.onchange = () => { PERM_ATTR_USER = sel.value; };
    // 行级规则列表
    const d = await api("/api/permission/row-rules");
    const items = d.items || [];
    const box = document.getElementById("permRowList");
    box.innerHTML = `<tr><th>主体</th><th>表</th><th>过滤条件</th><th>状态</th></tr>` +
      items.map(x => `<tr><td>${esc(x.subject_type)}:${esc(x.subject_name)}</td><td>${esc(x.table_name)}</td>
        <td><code>${esc(x.filter_sql)}</code></td><td>${x.enabled
          ? '<span class="status-tag success">启用</span>'
          : '<span class="status-tag gray">停用</span>'}</td></tr>`).join("")
      || `<tr><td class="empty">暂无行级规则</td></tr>`;
  } catch (e) {
    const box = document.getElementById("permRowList");
    if (box) box.innerHTML = `<tr><td class="empty">${esc(e.message)}</td></tr>`;
  }
}
async function permSaveAttrs() {
  const raw = document.getElementById("permAttrJson").value.trim();
  let attrs = {};
  if (raw) {
    try { attrs = JSON.parse(raw); }
    catch (e) { alert("属性不是合法 JSON 对象：" + e.message); return; }
  }
  if (!PERM_ATTR_USER) { alert("请先选择用户"); return; }
  try {
    await api(`/api/users/${encodeURIComponent(PERM_ATTR_USER)}/attributes`, { method: "PUT", body: { attributes: attrs } });
    alert("已保存用户属性");
  } catch (e) { alert("保存失败：" + e.message); }
}
async function permUpsertRowRule() {
  const subject = document.getElementById("permRowSubject").value.trim();
  const table = document.getElementById("permRowTable").value.trim();
  const filter = document.getElementById("permRowFilter").value.trim();
  if (!subject || !table || !filter) { alert("主体/表/过滤条件不能为空"); return; }
  try {
    await api("/api/permission/row-rules", { method: "PUT", body: {
      subject_type: document.getElementById("permRowType").value,
      subject_name: subject, table_name: table, filter_sql: filter } });
    permLoadRow();
  } catch (e) { alert("新增失败：" + e.message); }
}

// ═══════════════════════════════════════════════════════════
// 反馈角标（待处理数）—— 让管理员一眼看到"还有几条没消化"
// ═══════════════════════════════════════════════════════════
async function refreshFbBadge() {
  if (!isAdmin()) return;
  try {
    const d = await api("/api/feedback");
    const n = (d.stats || {}).pending || 0;
    const el = document.getElementById("navFbCnt");
    el.textContent = n;
    el.classList.toggle("hidden", n === 0);
  } catch (e) { /* 角标失败不影响主流程 */ }
}

/**
 * 知识治理角标：待复核知识数
 * ★ 为什么单独做一个：严格模式下 AI 生成的知识不进检索，
 *   如果没人去点「通过」，知识库就只进不出地"卡"在门口。
 *   把这个数摆在导航上，积压一眼可见。
 */
async function refreshGovBadge() {
  if (!isAdmin()) return;
  try {
    const d = await api("/api/kb/governance/pending");
    const n = d.count || 0;
    const el = document.getElementById("navGovCnt");
    if (el) { el.textContent = n; el.classList.toggle("hidden", n === 0); }
    const seg = document.getElementById("govPendBadge");
    if (seg) {
      seg.textContent = n ? ` ${n}` : "";
      seg.style.cssText = n
        ? "display:inline-block;min-width:16px;padding:0 5px;margin-left:4px;border-radius:9px;background:var(--color-danger);color:#fff;font-size:11px;line-height:16px;text-align:center"
        : "";
    }
  } catch (e) { /* 角标失败不影响主流程 */ }
}

