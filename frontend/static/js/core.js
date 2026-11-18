/* ══════════════════════════════════════════════════════════════
   core.js · 工具函数 · 登录态 · 登录/登出/启动
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// 工具函数
// ═══════════════════════════════════════════════════════════

// ═══════════════════════════════════════════════════════════
// ★ 登录态：前端只存"随机 token"，身份由后端每次校验
// ═══════════════════════════════════════════════════════════
//  【为什么不在前端存 role 来判断权限？】
//    前端存的东西都能被改。role 只用来"决定显示哪些入口"（体验层），
//    真正拦得住的是后端中间件 —— 普通用户即使把 role 改成 admin，
//    调管理接口也会被 403 挡回来。
const TOKEN_KEY = "zhishu_token";
let CURRENT_USER = null;              // 当前登录用户（由 /api/auth/me 或登录响应填充）

function getToken() { return localStorage.getItem(TOKEN_KEY) || ""; }
function setToken(t) { t ? localStorage.setItem(TOKEN_KEY, t) : localStorage.removeItem(TOKEN_KEY); }
function isAdmin() { return !!CURRENT_USER && CURRENT_USER.role === "admin"; }

/** 401 统一出口：清登录态并回到登录页（避免每个接口各写一遍） */
function handleUnauthorized(msg) {
  setToken("");
  CURRENT_USER = null;
  document.getElementById("appShell").classList.add("hidden");
  document.getElementById("pwdScreen").classList.add("hidden");
  document.getElementById("loginScreen").classList.remove("hidden");
  if (msg) setLoginMsg(msg);
}

/** 封装 fetch：自动带 token，统一处理错误与 401 */
async function api(url, options = {}) {
  const headers = { "Content-Type": "application/json", ...(options.headers || {}) };
  const token = getToken();
  if (token) headers["Authorization"] = "Bearer " + token;   // ★ 登录凭证

  const res = await fetch(url, { ...options, headers });
  if (res.status === 401) {                       // 未登录 / 会话过期
    handleUnauthorized("登录已过期，请重新登录");
    throw new Error("登录已过期，请重新登录");
  }
  if (!res.ok) {
    // 后端返回非 2xx 时，把错误信息读出来抛出去
    let d = {};
    try { d = await res.json(); } catch (e) {}
    // ★ 强制改密：后端对所有业务接口返回 403，这里直接把改密页顶上来
    if (res.status === 403 && d.type === "must_change_password") {
      showPwdScreen();
      throw new Error(d.detail || "请先修改密码");
    }
    throw new Error(d.detail || res.statusText);
  }
  return res.json();
}

// ═══════════════════════════════════════════════════════════
// 登录 / 登出 / 启动
// ═══════════════════════════════════════════════════════════
function setLoginMsg(t, kind) {
  const el = document.getElementById("lgMsg");
  el.textContent = t || "";
  el.style.color = kind === "ok" ? "var(--color-success)" : "var(--color-danger)";
}

async function doLogin() {
  const username = document.getElementById("lgUser").value.trim();
  const password = document.getElementById("lgPwd").value;
  if (!username || !password) { setLoginMsg("请输入账号和密码"); return; }

  const btn = document.getElementById("lgBtn");
  btn.disabled = true; btn.textContent = "登录中…";
  setLoginMsg("");
  try {
    // 登录用原生 fetch（此时还没有 token，且不能走 api() 的 401 跳转逻辑）
    const r = await fetch("/api/auth/login", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ username, password }),
    });
    const d = await r.json().catch(() => ({}));
    if (!r.ok) { setLoginMsg(d.detail || "登录失败"); return; }
    setToken(d.token);
    CURRENT_USER = d.user;
    document.getElementById("lgPwd").value = "";
    // ★ 首次登录 / 密码被重置 → 先改密，不进入主界面
    if (d.user && d.user.must_change_password) { showPwdScreen(); return; }
    enterApp();
  } catch (e) {
    setLoginMsg("网络错误：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "登 录";
  }
}

/** 显示强制改密页（隐藏登录页与主界面，避免绕过） */
function showPwdScreen() {
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("appShell").classList.add("hidden");
  document.getElementById("pwdScreen").classList.remove("hidden");
  ["pwOld", "pwNew", "pwNew2"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  const msg = document.getElementById("pwMsg"); if (msg) msg.textContent = "";
  setTimeout(() => { const el = document.getElementById("pwOld"); if (el) el.focus(); }, 60);
}

/** 提交自助改密 */
async function submitPwdChange() {
  const oldPwd = document.getElementById("pwOld").value;
  const newPwd = document.getElementById("pwNew").value;
  const newPwd2 = document.getElementById("pwNew2").value;
  const msg = document.getElementById("pwMsg");
  const btn = document.getElementById("pwBtn");
  const fail = t => { msg.textContent = t; msg.style.color = "var(--color-danger)"; };

  if (!oldPwd || !newPwd) return fail("请填写当前密码与新密码");
  if (newPwd.length < 6) return fail("新密码至少 6 位");
  if (newPwd !== newPwd2) return fail("两次输入的新密码不一致");
  if (newPwd === oldPwd) return fail("新密码不能与当前密码相同");

  btn.disabled = true; btn.textContent = "保存中…";
  msg.textContent = "";
  try {
    const res = await fetch("/api/me/password", {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        "Authorization": "Bearer " + getToken(),
      },
      body: JSON.stringify({ old_password: oldPwd, new_password: newPwd }),
    });
    const d = await res.json().catch(() => ({}));
    if (!res.ok) return fail(d.detail || "修改失败，请重试");
    CURRENT_USER = d.user || CURRENT_USER;
    document.getElementById("pwdScreen").classList.add("hidden");
    enterApp();
  } catch (e) {
    fail("网络错误：" + e.message);
  } finally {
    btn.disabled = false; btn.textContent = "保存新密码";
  }
}

async function doLogout() {
  if (!confirm("确认退出登录？")) return;
  try { await api("/api/auth/logout", { method: "POST" }); } catch (e) {}
  handleUnauthorized("");
}

/** 进入主界面：按角色决定"能看到什么" */
function enterApp() {
  document.getElementById("loginScreen").classList.add("hidden");
  document.getElementById("pwdScreen").classList.add("hidden");
  document.getElementById("appShell").classList.remove("hidden");

  // 侧栏底部的身份信息
  const u = CURRENT_USER || {};
  document.getElementById("meAvatar").textContent = (u.display_name || u.username || "?").slice(0, 1);
  document.getElementById("meName").textContent = u.display_name || u.username || "";
  document.getElementById("meRole").textContent = u.role_zh || "";

  // ★ 角色显隐：普通用户看不到任何管理入口
  document.querySelectorAll("[data-admin]").forEach(el => {
    el.classList.toggle("hidden", !isAdmin());
  });

  // 万一停在管理员页面但角色不是管理员 → 退回取数页
  const active = document.querySelector(".nav-item.active");
  if (active && active.hasAttribute("data-admin") && !isAdmin()) {
    switchTab("ask", document.querySelector('.nav-item[data-tab="ask"]'));
  }

  // 取数审核入口的角色化文案：管理员是"审别人"，普通用户是"看我自己的"
  //   ★ 连 data-title 一起改 —— 顶部工作栏的标题就是从这个属性读的，
  //     只改侧栏文字会出现"侧栏写我的审核、顶上写取数审核"的不一致。
  const rvNav = document.querySelector('.nav-item[data-tab="review"]');
  if (rvNav) {
    rvNav.querySelector("#navReviewLabel").textContent = isAdmin() ? "取数审核" : "我的审核";
    rvNav.dataset.title = isAdmin() ? "取数审核" : "我的审核";
    rvNav.dataset.sub = isAdmin()
      ? "复核每条待审取数的口径是否合理 —— 有问题顺手去知识库补上"
      : "查看自己提交的审核进度 —— 审核通过前请把结果当参考值";
    // 当前正停在这一页 → 标题也要立刻跟着变
    if (rvNav.classList.contains("active")) {
      document.getElementById("pageTitle").textContent = rvNav.dataset.title;
      document.getElementById("pageSub").textContent = rvNav.dataset.sub;
    }
  }

  loadStatus();
  if (isAdmin()) refreshFbBadge();      // 待处理反馈数（导航角标）
  if (isAdmin()) refreshGovBadge();     // 待复核知识数（导航角标）
  refreshReviewBadge();                 // 待审取数数（导航角标，两种角色都要）
  loadHistory();                        // 左侧「最近」历史
  loadFavorites();                      // 收藏标记（历史列表星标）
  startReviewPolling();                 // ★ 审核状态实时刷新
  if (typeof restoreLastQuery === "function") restoreLastQuery();  // ★ 刷新恢复上轮取数锚点
}

/** 启动：先看有没有旧 token，有就验一下是否还有效 */
async function bootstrapAuth() {
  if (!getToken()) {                       // 没登录过 → 直接显示登录页
    document.getElementById("loginScreen").classList.remove("hidden");
    return;
  }
  try {
    const r = await api("/api/auth/me");
    if (r && r.user) {
      CURRENT_USER = r.user;
      if (r.user.must_change_password) { showPwdScreen(); return; }
      enterApp();
    }
    else handleUnauthorized("");
  } catch (e) {
    if (getToken()) handleUnauthorized("登录状态校验失败，请重新登录");
  }
}

/** 切换标签页（左侧导航） */
function switchTab(name, btn) {
  // ① 所有 section 先隐藏
  document.querySelectorAll(".tab").forEach(el => el.classList.remove("active"));
  // ② 所有导航项取消高亮
  document.querySelectorAll(".nav-item").forEach(el => el.classList.remove("active"));
  // ③ 显示目标 section、高亮目标导航项
  const sec = document.getElementById("tab-" + name);
  if (!sec) return;
  sec.classList.add("active");
  if (btn) {
    btn.classList.add("active");
    // ④ 顶部工作栏的标题/说明取自导航项的 data-title / data-sub（只维护一份文案）
    document.getElementById("pageTitle").textContent = btn.dataset.title || "";
    document.getElementById("pageSub").textContent = btn.dataset.sub || "";
  }

  // ⑤ 按需加载数据（切到哪个页面才拉哪个数据）
  if (name === "prompt")   loadPrompts();
  if (name === "kb")       { kbLoadOverview(); kbLoadTables(); }
  if (name === "gov")      kbLoadGaps(false);      // 缺口扫描较慢，函数内部有缓存
  if (name === "feedback") kbLoadFeedback();
  if (name === "exp")      loadExpPage();
  if (name === "tables")   loadTables();
  if (name === "users")    loadUsers();
  if (name === "perm")     loadPermPage();
  if (name === "review")   loadReviewQueue();
  if (name === "fav")      loadFavoritePage();
  if (name === "ask")      { loadHistory(); refreshReviewBadge(); }
  if (name === "golden")   loadGoldenPage();
  if (name === "dash")     loadDashPage();
  if (name === "semantic") smLoadPage();
  if (name === "domain")   loadDomainPage();
  if (name === "ontology") ontoLoad();
  if (name === "lineage")  lineageLoad();
}

/** 折叠/展开导航分组（语义中心重构：5 组分层的折叠开关）
 *  ★ 只切换本组的 .collapsed，不碰 switchTab 的激活逻辑；
 *    折叠后仍可从别的入口（如语义工作台的跳转）切到组内任意 tab。 */
function toggleNavGroup(labelEl) {
  labelEl.parentElement.classList.toggle("collapsed");
}

