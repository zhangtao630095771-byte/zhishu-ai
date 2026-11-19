/* ══════════════════════════════════════════════════════════════
   ask.js · 取数页 · 结果渲染 · 结果反馈
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// 系统状态
// ═══════════════════════════════════════════════════════════
async function loadStatus() {
  try {
    const s = await api("/api/status");
    // 普通用户只关心"能不能用"，技术细节（方言/模型）对他是噪音 → 少显示
    document.getElementById("statusBar").innerHTML = isAdmin()
      ? `数据源 <b>${s.db_impl}</b> ｜ 方言 <b>${s.sql_dialect}</b> ｜ ` +
        `可见表 <b>${s.tables_in_index}</b> ｜ 指标 <b>${s.metrics_in_index}</b> ｜ ` +
        `模型 <b>${s.llm_model}</b>`
      : `可用表 <b>${s.tables_in_index}</b> ｜ 指标 <b>${s.metrics_in_index}</b>`;
    return s;
  } catch (e) {
    document.getElementById("statusBar").textContent = "状态获取失败：" + e.message;
  }
}

// ═══════════════════════════════════════════════════════════
// Tab 1：取数
// ═══════════════════════════════════════════════════════════
async function askQuestion() {
  const question = document.getElementById("question").value.trim();
  if (!question) { alert("请先输入问题"); return; }

  // 从"历史记录"视图回到"新取数"视图：恢复反馈入口与提示文案
  const fbOpen = document.getElementById("fbOpenBtn");
  if (fbOpen) fbOpen.classList.remove("hidden");
  // ★ 新开一个全新查询 → 上一轮的"增量修改"上下文作废（避免改到旧查询上）
  lastQueryContextId = null;
  if (window.AppState) AppState.clear();   // ★ 同步清掉刷新恢复的锚点
  const mb = document.getElementById("modifyBox");
  if (mb) mb.style.display = "none";
  const _hint = document.getElementById("askHint");
  if (_hint) _hint.textContent = "支持自然语言，AI 会自动检索表结构、生成 SQL 并执行";

  const btn = document.getElementById("askBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>正在理解问题…';
  hideClarify();                                 // 收起上一次的澄清卡片
  document.getElementById("askResult").style.display = "none";
  showProgress();                                // ★ 先亮出进度条（后端事件驱动逐段推进）

  try {
    // ★ 统一走 SSE 流式澄清入口：后端先判断「直接执行」还是「需澄清」，
    //   边执行边推阶段事件（理解/检索/生成/校验/执行），不再等全部跑完一次性返回。
    await streamAsk("/api/ask/stream", { question }, {
      onEvent: ev => handleStreamEvent(ev, question),
    });
  } catch (e) {
    if (e && e.name === "AbortError") {
      // 用户主动取消：不提示（取消按钮已处理）
    } else {
      alert("请求失败：" + (e && e.message ? e.message : e));
    }
  } finally {
    hideProgress();
    btn.disabled = false;
    btn.textContent = "提 交";
  }
}

// ═══════════════════════════════════════════════════════════
// ★ 多轮澄清交互 —— 口径歧义先问清楚，不擅自替用户选口径
// ═══════════════════════════════════════════════════════════
//  为什么值得做：
//    同样一句"上个月订单量"，业务里可能有发单量/完单量等多个可信口径。
//    让模型自己选一个，答案是"快"，但可能不是你要的那个数。
//    所以这里在拿到结构化意图后，把「可信候选」摆出来让用户点一下确认，
//    选择只代表"你要哪个口径"，不改变该指标的业务定义（定义仍取自权威指标）。
let currentClarify = null;   // { query_id, question, clarification, task_id, selected } 当前待确认的澄清任务
let currentTask = null;      // { task_id, ctrl } 当前 SSE 任务（用于取消 / 断线重连）

// ═══════════════════════════════════════════════════════════
// ★ 当前窗口增量修改 —— 在上一轮查询结果基础上做自然语言修改
// ═══════════════════════════════════════════════════════════
//  为什么值得做（本次专项修复的核心）：
//    加完多轮澄清后，原本"在同一窗口里接着上一轮改查询"的能力被削弱了 ——
//    用户再说"去掉应用主体分组"，要么丢失原指标/时间/过滤，要么又被重新丢进澄清流程。
//    这里直接走 /api/ask + base_query_id：后端在上一轮上下文上只改用户要求的部分，
//    并重跑完整安全闸门（只读 / 表权限 / 字段级权限 / 业务校验），不绕过任何校验。
//    成功失败都能继续改：失败时用户指出错在哪，也能重新执行。
async function modifyQuestion() {
  const el = document.getElementById("modifyInput");
  const question = el ? el.value.trim() : "";
  if (!question) { alert("请先输入你想怎么改（例如：去掉应用主体分组，只按月份）"); return; }
  if (!lastQueryContextId) { alert("没有可供修改的上轮查询，请先执行一次查询。"); return; }

  const btn = document.getElementById("modifyBtn");
  const mainBtn = document.getElementById("askBtn");
  if (btn) { btn.disabled = true; btn.innerHTML = '<span class="spinner"></span>正在按修改生成 SQL…'; }
  if (mainBtn) mainBtn.disabled = true;     // 主按钮也锁住，避免重复提交；下方 finally 解锁
  hideClarify();
  document.getElementById("askResult").style.display = "none";

  const ctrl = new AbortController();
  const timer = setTimeout(() => ctrl.abort(), 180000);
  try {
    // ★ 关键点：携带 base_query_id → 后端走"增量修改"专用路径，不再触发澄清决策
    const r = await api("/api/ask", {
      method: "POST",
      body: JSON.stringify({ question, base_query_id: lastQueryContextId }),
      signal: ctrl.signal,
    });
    renderAnswer(r);                        // 与直接查询共用渲染；renderAnswer 会刷新 lastQueryContextId
  } catch (e) {
    if (e && e.name === "AbortError") {
      alert("修改后查询超时（已超过 3 分钟）。复杂修改建议拆小后再试。");
    } else {
      alert("修改请求失败：" + (e && e.message ? e.message : e));
    }
  } finally {
    clearTimeout(timer);
    if (btn) { btn.disabled = false; btn.textContent = "按此修改重新查询"; }
    if (mainBtn) { mainBtn.disabled = false; mainBtn.textContent = "提 交"; }
  }
}

function hideClarify() {
  currentClarify = null;
  const box = document.getElementById("clarifyBox");
  if (box) { box.style.display = "none"; box.innerHTML = ""; }
}

function showClarify() {
  const box = document.getElementById("clarifyBox");
  if (box) box.style.display = "block";
  document.getElementById("askResult").style.display = "none";
}

/** 渲染一轮澄清问题（候选 + 自由输入 + 取消） */
function renderClarify(r, question) {
  currentClarify = { query_id: r.query_id, question: question,
                     clarification: r.clarification, reason: r.reason || "" };
  showClarify();
  paintClarify();
}

/** 知识缺失：找不到可信口径时明确提示，不编造表/字段/口径 */
function renderUnknown(r, question) {
  currentClarify = { query_id: r.query_id, question: question, clarification: null };
  showClarify();
  const box = document.getElementById("clarifyBox");
  box.innerHTML = `
    <div class="clarify-card">
      <div class="clarify-head">⚠️ 暂时无法回答这个问题</div>
      <div class="clarify-q">${esc(question || "")}</div>
      <div class="alert warn" style="margin-top:10px">${esc(r.reason || "未检索到可信指标定义，缺少业务口径。")}</div>
      <div class="muted" style="margin-top:8px">为避免编造口径，系统已停止生成 SQL。请补充指标定义后再查询，或联系管理员维护业务口径。</div>
      <div class="row" style="margin-top:14px">
        <button class="ghost" onclick="hideClarify()">返回修改问题</button>
      </div>
    </div>`;
}

// ═══════════════════════════════════════════════════════════
// ★ SSE 流式响应 —— fetch + ReadableStream 读流，兼容 POST + Token 鉴权
// ═══════════════════════════════════════════════════════════
/** 发起一个 SSE 流式请求，逐帧解析 `data: {...}\n\n` 并回调 onEvent。 */
async function streamAsk(url, body, handlers) {
  const ctrl = new AbortController();
  currentTask = { task_id: (currentTask && currentTask.task_id) || null, ctrl };
  const token = getToken();
  const res = await fetch(url, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      ...(token ? { "Authorization": "Bearer " + token } : {}),
    },
    body: JSON.stringify(body),
    signal: ctrl.signal,
  });
  if (res.status === 401) { handleUnauthorized("登录已过期，请重新登录"); throw new Error("登录已过期，请重新登录"); }
  if (!res.ok) {
    let d = {};
    try { d = await res.json(); } catch (e) {}
    if (res.status === 403 && d.type === "must_change_password") {
      showPwdScreen();
      throw new Error(d.detail || "请先修改密码");
    }
    throw new Error(d.detail || res.statusText);
  }
  const reader = res.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buf = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += decoder.decode(value, { stream: true });
    let idx;
    while ((idx = buf.indexOf("\n\n")) >= 0) {
      const chunk = buf.slice(0, idx);
      buf = buf.slice(idx + 2);
      for (const line of chunk.split("\n")) {
        const t = line.trim();
        if (!t.startsWith("data:")) continue;
        const payload = t.slice(5).trim();
        if (!payload) continue;
        try {
          const ev = JSON.parse(payload);
          if (handlers.onEvent) handlers.onEvent(ev);
        } catch (e) { /* 单帧解析失败不影响整体 */ }
      }
    }
  }
}

// ── 进度条（后端事件驱动，禁止定时器模拟）──
const PROGRESS_STEPS = [
  { key: "understanding",  label: "正在理解问题" },
  { key: "retrieval",      label: "正在检索企业知识" },
  { key: "sql_generating", label: "正在生成 SQL" },
  { key: "sql_validating", label: "正在校验 SQL" },
  { key: "sql_executing",  label: "正在执行查询" },
  { key: "analyzing",      label: "正在分析结果" },
];

function showProgress() {
  const box = document.getElementById("progressBox");
  if (box) box.style.display = "block";
  renderProgress(0);      // 第 0 步 active，其余 pending
}

function hideProgress() {
  const box = document.getElementById("progressBox");
  if (box) { box.style.display = "none"; box.innerHTML = ""; }
}

/** doneCount：已完成的步数；第 doneCount 步（若存在）显示 spinner 为 active。 */
function renderProgress(doneCount) {
  const box = document.getElementById("progressBox");
  if (!box || box.style.display === "none") return;
  const steps = PROGRESS_STEPS.map((s, i) => {
    let cls = "pending", ic = "";
    if (i < doneCount) { cls = "done"; ic = "✓"; }
    else if (i === doneCount) { cls = "active"; }
    return `<div class="progress-step ${cls}">
      ${i === doneCount ? '<span class="ps-spin"></span>' : `<span class="ps-ic">${ic}</span>`}
      <span>${esc(s.label)}</span>
    </div>`;
  }).join("");
  box.innerHTML = `
    <div class="progress-head">
      <span class="p-title">正在执行查询</span>
      <button class="ghost" onclick="cancelTask()">取消</button>
    </div>
    <div class="progress-list">${steps}</div>`;
}

/** 事件名 → 已完成步数（任务开始=0，理解=1 … 执行完成=5 进入分析结果）。 */
function markProgress(event) {
  const map = {
    task_started: 0, understanding: 1, retrieval: 2,
    sql_generating: 3, sql_validating: 4, sql_executing: 5,
    result: 6, done: 6,
  };
  if (!(event in map)) return;          // heartbeat / clarification / error 不更新进度
  renderProgress(map[event]);
}

/** 用户主动取消：置取消信号 + 中止当前流。 */
async function cancelTask() {
  if (currentTask && currentTask.task_id) {
    try { await api(`/api/task/${currentTask.task_id}/cancel`, { method: "POST" }); } catch (e) {}
  }
  if (currentTask && currentTask.ctrl) currentTask.ctrl.abort();
}

/** 统一处理一条 SSE 事件：驱动进度 / 澄清 / 结果 / 错误。 */
function handleStreamEvent(ev, question) {
  const e = ev.event, d = ev.data || {};
  if (ev.task_id) {
    currentTask = currentTask || {};
    currentTask.task_id = ev.task_id;
  }
  switch (e) {
    case "task_started":
      showProgress();
      markProgress("task_started");
      break;
    case "understanding":
      markProgress("understanding");
      break;
    case "clarification_required":
      // 进入澄清：收起进度，渲染交互式澄清卡片（选中 → 确认 → 提交）
      currentClarify = {
        query_id: d.query_id, task_id: ev.task_id, question: question,
        clarification: d.clarification, reason: d.reason || "", selected: null,
      };
      hideProgress();
      showClarify();
      paintClarify();
      break;
    case "clarification_updated":
      // 多轮澄清：更新问题，保留 query_id / task_id
      if (currentClarify) {
        currentClarify.clarification = d.clarification;
        currentClarify.reason = d.reason || "";
        currentClarify.selected = null;
        if (d.query_id) currentClarify.query_id = d.query_id;
        hideProgress();
        showClarify();
        paintClarify();
      }
      break;
    case "retrieval":
    case "sql_generating":
    case "sql_validating":
    case "sql_executing":
      markProgress(e);
      break;
    case "result":
      // 完整结果：复用现有 renderAnswer（不改结果展示），并刷新增量修改锚点
      hideProgress();
      hideClarify();
      renderAnswer(d);
      break;
    case "error":
      hideProgress();
      if (d.cancelled) {
        hideClarify();                       // 用户主动取消，静默
      } else if (d.kind === "unknown") {
        renderUnknown({ query_id: d.query_id, reason: d.reason }, question);
      } else {
        hideClarify();
        alert("查询失败：" + (d.message || "未知错误"));
      }
      break;
    case "done":
      hideProgress();
      break;
    case "heartbeat":
      break;
  }
}

// ═══════════════════════════════════════════════════════════
// ★ 统一澄清组件：选项点击 → 选中态 → 确认提交（支持取消重选 / 多选 / 已选汇总）
// ═══════════════════════════════════════════════════════════
/** 把当前澄清任务画到页面上（候选选项 / 选中态 / 确认按钮 / 自由输入 / 取消） */
function paintClarify() {
  const box = document.getElementById("clarifyBox");
  const c = currentClarify && currentClarify.clarification;
  if (!box || !c) return;

  const multi = !!(c.multiple);
  const sel = currentClarify.selected;
  const isSel = v => multi ? (Array.isArray(sel) && sel.includes(v)) : (sel === v);

  // 候选选项：点一下只切换选中态（高亮），点「确认」才提交 —— 支持取消重选
  const opts = (c.options || []).map((o, i) => `
    <button class="clarify-opt ${isSel(o.value) ? "selected" : ""}" onclick="clarifyToggle(${i})">
      <span class="co-check">${isSel(o.value) ? "✓" : ""}</span>
      <span>
        <div class="co-name">${esc(o.label)}
          ${o.status === "approved" ? '<span class="status-tag success">已审核</span>' : ''}</div>
        <div class="co-def">${esc(o.definition || "（无定义）")}</div>
        <div class="co-meta">${esc(o.diff || o.source || "")}${o.impact && o.impact !== "—" ? " · " + esc(o.impact) : ""}</div>
      </span>
    </button>`).join("");

  // 自由输入：让用户能直接写口径 / 时间 / 过滤条件，不必死磕选项
  const free = c.allow_free_text ? `
    <div class="clarify-free">
      <div class="muted" style="margin:10px 0 6px">也可以直接输入你的口径或补充条件（例如「发单量」「上一个自然月」「只看临沂」）：</div>
      <div class="row" style="gap:8px">
        <input id="clarifyFreeInput" placeholder="直接输入口径 / 时间 / 条件…"
               onkeydown="if(event.key==='Enter'){event.preventDefault();clarifyFreeSubmit();}" />
        <button class="btn" onclick="clarifyFreeSubmit()">按输入查询</button>
      </div>
    </div>` : "";

  const cancel = c.allow_cancel
    ? `<button class="ghost" onclick="clarifyCancel()">取消查询</button>` : "";

  const roundHint = c.max_rounds > 1
    ? `<div class="muted" style="font-size:12px;margin-top:10px">第 ${c.round} / ${c.max_rounds} 轮确认</div>` : "";

  // 已选条件实时汇总（方案：显示澄清问题及原因 + 已选择条件）
  const hasSel = multi ? (Array.isArray(sel) && sel.length) : !!sel;
  const summary = hasSel
    ? `<div class="clarify-summary">已选择：${multi
        ? sel.map(v => _optLabel(c, v)).join("、")
        : esc(_optLabel(c, sel))}</div>` : "";

  box.innerHTML = `
    <div class="clarify-card">
      <div class="clarify-head">🔎 需要确认一下口径</div>
      <div class="clarify-q">${esc(currentClarify.question || "")}</div>
      ${currentClarify.reason ? `<div class="clarify-reason">${esc(currentClarify.reason)}</div>` : ""}
      <div class="clarify-question">${esc(c.question || "")}</div>
      <div class="clarify-opts">${opts}</div>
      ${free}
      ${summary}
      <div class="clarify-confirm">
        <button class="btn" ${hasSel ? "" : "disabled"} onclick="clarifyConfirm()">确认并查询</button>
        ${cancel}
      </div>
      ${c.hint ? `<div class="muted" style="font-size:12px;margin-top:8px">${esc(c.hint)}</div>` : ""}
      ${roundHint}
    </div>`;
}

/** 由 value 取选项 label（用于已选汇总） */
function _optLabel(c, value) {
  const o = (c.options || []).find(x => x.value === value);
  return (o && o.label) || value;
}

/** 点击选项：切换选中态（单选可取消重选；多选增删） */
function clarifyToggle(i) {
  const c = currentClarify && currentClarify.clarification;
  const o = c && c.options && c.options[i];
  if (!o) return;
  const multi = !!(c.multiple);
  let sel = currentClarify.selected;
  if (multi) {
    sel = Array.isArray(sel) ? sel.slice() : [];
    const k = sel.indexOf(o.value);
    if (k >= 0) sel.splice(k, 1); else sel.push(o.value);
  } else {
    sel = (sel === o.value) ? null : o.value;   // 再点一次取消选择
  }
  currentClarify.selected = sel;
  paintClarify();
}

/** 确认提交：把已选条件交给后端，未选则忽略（按钮已 disabled 兜底） */
function clarifyConfirm() {
  const c = currentClarify && currentClarify.clarification;
  const sel = currentClarify && currentClarify.selected;
  const multi = !!(c && c.multiple);
  if (multi) {
    if (!Array.isArray(sel) || !sel.length) return;
    submitClarifyStream(sel.join(","));
  } else {
    if (!sel) return;
    submitClarifyStream(sel);
  }
}

/** 自由文本提交 */
function clarifyFreeSubmit() {
  const el = document.getElementById("clarifyFreeInput");
  const v = el ? el.value.trim() : "";
  if (!v) { alert("请先输入口径或条件"); return; }
  submitClarifyStream(v);
}

/** 取消查询：结束本次澄清，不执行 SQL */
async function clarifyCancel() {
  if (!currentClarify || !currentClarify.query_id) { hideClarify(); return; }
  try { await api(`/api/clarify/${currentClarify.query_id}/cancel`, { method: "POST" }); } catch (e) {}
  hideClarify();
}

/** 提交澄清回答：走 SSE 流式，恢复同一任务继续执行（携带 task_id 避免重建无上下文任务） */
async function submitClarifyStream(answer) {
  if (!currentClarify || !currentClarify.query_id) return;
  const btn = document.getElementById("askBtn");
  btn.disabled = true;
  btn.innerHTML = '<span class="spinner"></span>正在按确认口径生成 SQL 并执行…';
  showProgress();
  try {
    await streamAsk("/api/clarify/answer/stream", {
      query_id: currentClarify.query_id,
      answer: answer,
      task_id: currentClarify.task_id || "",
    }, {
      onEvent: ev => handleStreamEvent(ev, currentClarify.question),
    });
  } catch (e) {
    if (e && e.name === "AbortError") {
      // 用户主动取消，静默
    } else {
      alert("请求失败：" + (e && e.message ? e.message : e));
    }
  } finally {
    hideProgress();
    btn.disabled = false;
    btn.textContent = "提 交";
  }
}

/** 把后端返回的结果渲染成界面 */
// ═══════════════════════════════════════════════════════════
// ★ 结果反馈 —— 把"踩坑"记下来，变成知识库的输入
// ═══════════════════════════════════════════════════════════
//  为什么值得做：
//    现在发现幻觉全靠人主动来说，而且说完就散了 —— 没沉淀成"下次不再错"的知识。
//    加了这个入口，每次答错都会变成一条带完整上下文的记录：
//      问了什么、生成了什么 SQL、命中了哪些表/指标/字典、错在哪一类。
//    复盘时不用回忆，也能直接看出"该补哪条知识"。
let lastAnswer = null;          // 最近一次取数的完整结果
let lastQueryContextId = null;  // 最近一次取数的查询上下文 id（供"当前窗口增量修改"锚定）
let fbReasonsCache = null;      // 错误类型（从后端拿，前端不抄一份 —— 抄两份必然不一致）

/** 刷新后恢复"上轮查询"的增量修改锚点（由 core.js enterApp 在登录后调用） */
function restoreLastQuery() {
  if (!window.AppState) return;
  const s = AppState.restore();
  if (!s || !s.queryContextId) return;
  lastQueryContextId = s.queryContextId;
  // 回填上轮问题（仅当输入框为空），让用户知道"基于什么继续"
  const q = document.getElementById("question");
  if (q && !q.value.trim() && s.question) q.value = s.question;
  const mb = document.getElementById("modifyBox");
  if (mb) mb.style.display = "block";
}

function fbReset() {
  const form = document.getElementById("fbForm");
  if (form) form.style.display = "none";
  const d = document.getElementById("fbDetail");
  if (d) d.value = "";
  const m = document.getElementById("fbMsg");
  if (m) m.innerHTML = "";
  document.querySelectorAll("#fbReasons .fb-reason").forEach(b => b.classList.remove("on"));
}

async function fbToggle() {
  const form = document.getElementById("fbForm");
  const opening = form.style.display === "none";
  form.style.display = opening ? "block" : "none";
  if (opening) {
    await fbLoadReasons();
    document.getElementById("fbDetail").focus();
  }
}

async function fbLoadReasons() {
  const box = document.getElementById("fbReasons");
  if (fbReasonsCache) return;                     // 拿过一次就不再请求
  try {
    const d = await api("/api/feedback");
    fbReasonsCache = d.reasons || {};
  } catch (e) {
    fbReasonsCache = { other: "其他（请补充说明）" };   // 拿不到也要能用
  }
  box.innerHTML = Object.entries(fbReasonsCache).map(([k, v]) =>
    `<button class="fb-reason" data-k="${esc(k)}" onclick="fbPick(this)">${esc(v)}</button>`
  ).join("");
}

function fbPick(el) {
  document.querySelectorAll("#fbReasons .fb-reason").forEach(b => b.classList.remove("on"));
  el.classList.add("on");
}

async function fbSubmit() {
  if (!lastAnswer) return;
  const picked = document.querySelector("#fbReasons .fb-reason.on");
  const detail = document.getElementById("fbDetail").value.trim();
  const msg = document.getElementById("fbMsg");

  // ★ 两个必填都要卡住，理由：
  //   不选类型 → 统计不出"我们最常错在哪"，这份数据就白攒了
  //   不写说明 → 只记"不对"，下次复盘还是不知道该改哪条知识
  if (!picked) {
    msg.innerHTML = `<div class="alert warn" style="margin-top:10px">请先选一类「哪里不对」—— 不选就没法统计我们最常错在哪，这份记录的价值就少一半。</div>`;
    return;
  }
  if (!detail) {
    msg.innerHTML = `<div class="alert warn" style="margin-top:10px">请补充说明「应该是什么」—— 只记「不对」，下次复盘还是不知道该改哪条知识。</div>`;
    return;
  }

  const r = lastAnswer;
  const result = (r.rows || []).slice(0, 3)
    .map(row => JSON.stringify(row)).join(" | ").slice(0, 500);   // 结果可能很长，只留个摘要
  try {
    const res = await api("/api/feedback", {
      method: "POST",
      body: JSON.stringify({
        question: r.question || "",
        sql: r.sql || "",
        result: result,
        exec_ok: !!r.ok,
        reason: picked.dataset.k,
        detail: detail,
        tables: r.tables || [],
        metrics: r.metrics || [],
        dicts: r.dicts || [],
      }),
    });
    msg.innerHTML = `<div class="alert info" style="margin-top:10px">
      已记录（编号 ${res.id}）。去「反馈处理 → 问题反馈」能看到它；
      处理完（补了注释/字典/口径）记得在那里标一下<b>已处理</b>并写一句改了什么。
    </div>`;
    document.getElementById("fbDetail").value = "";
    document.querySelectorAll("#fbReasons .fb-reason").forEach(b => b.classList.remove("on"));
  } catch (e) {
    msg.innerHTML = `<div class="alert err" style="margin-top:10px">提交失败：${esc(e.message)}</div>`;
  }
}

// ═══════════════════════════════════════════════════════════
// ★ 状态链四徽章 —— 已生成 · 已校验 · 需求已核 · 已执行
// ═══════════════════════════════════════════════════════════
//  【为什么必须分开画，而不是一个"成功"绿勾】
//    这条流水线上有四件事，任何一件成立都不蕴含下一件：
//      模型生成了 SQL ⇏ 校验通过 ⇏ 需求被满足 ⇏ 执行成功 ⇏ 业务正确
//    最危险的一种是「执行成功但需求没满足」：数字是真的、SQL 也不报错，
//    但它回答的不是用户问的问题（漏了过滤、把替换写成了 OR 追加、丢了分组）。
//    这种结果以前在界面上和"完全正确"长得一模一样 —— 四徽章就是把它显式暴露出来。
//
//  【状态语义】（与后端 agent.py 的 status_chain 字段一一对应）
//    ok      = 这一步确实成立
//    bad     = 这一步确实没成立（有硬证据）
//    warn    = 有疑问 / 待人工确认
//    skip    = 本次没跑这一步（不等于失败，也不等于通过）
//    unknown = 无从判断（历史记录没留存该字段）
//  ★ 最后两种必须与 bad 画得不一样：「没审」被画成「不通过」，用户就会去查一个根本不存在的问题。
const REQ_VERDICT_VIEW = {
  pass:    ["ok",    "需求已核",   "本轮要求已逐条核对，未发现偏差"],
  fail:    ["bad",   "需求未满足", "生成的 SQL 没有满足你这次的要求"],
  unsure:  ["warn",  "需求待确认", "审核没把握，需要你确认口径"],
  skipped: ["skip",  "需求未审",   "首轮查询或审核未启用，本次未做需求核对"],
};

const CHAIN_ICON = { ok: "✓", bad: "✕", warn: "!", skip: "·", unknown: "—" };

function chainStep(state, label, detail) {
  return `<span class="chain-step ${state}" title="${esc(detail || "")}">`
       + `<span class="cs-ic">${CHAIN_ICON[state] || "·"}</span>${esc(label)}</span>`;
}

/** 业务是否已核：唯一事实源是「人工审核状态」，不从链里缓存（否则撤销审核后徽章还亮着） */
function _bizChip(c) {
  const rs = (typeof CURRENT_RECORD !== "undefined" && CURRENT_RECORD)
    ? CURRENT_RECORD.review_status : null;
  if (rs === "approved") return `<span class="status-tag success">业务已核</span>`;
  if (rs === "rejected") return `<span class="status-tag danger">业务未通过</span>`;
  if (rs === "pending")  return `<span class="status-tag warning">待业务审核</span>`;
  if (c && c.business_verified === true)  return `<span class="status-tag success">业务已核</span>`;
  if (c && c.business_verified === false) return `<span class="status-tag danger">业务未通过</span>`;
  return `<span class="status-tag gray">业务未核</span>`;
}

/** 只重画「业务已核」这个副标签（审核状态变化时调用，不整页重画） */
function refreshBizChip() {
  const el = document.getElementById("chainBiz");
  if (el) el.innerHTML = _bizChip(null);
}

/** 渲染状态链（取数页 / 历史回看共用；没有状态链时明说，而不是画一排灰点） */
function renderStatusChain(r) {
  const box = document.getElementById("chainBar");
  if (!box) return;
  const c = (r && r.status_chain) || null;

  if (!c) {
    // 旧流水线兜底 / 升级前的老记录 —— 不编造状态
    box.style.display = "block";
    box.innerHTML = `<div class="chain-head">执行状态</div>
      <div class="muted" style="font-size:12px;line-height:1.7">
        本次没有状态链（走了旧流水线兜底，或这是一条升级前的旧记录），
        无法区分「已生成 / 已校验 / 需求已核 / 已执行」四步。
        下面的「执行成功」只代表 SQL 跑通了，<b>不代表需求已被核对</b>。
      </div>`;
    return;
  }

  box.style.display = "block";
  const steps = [];

  // ① 已生成
  steps.push(chainStep(c.generated ? "ok" : "bad",
    c.generated ? "已生成 SQL" : "未生成 SQL",
    c.generated ? "模型产出了可执行语句" : "没有产出 SQL（拒答 / 知识缺失 / 被拦截）"));

  // ② 已校验（只读 + 表权限 + 五维校验 + 字段权限）
  const g = c.gate_passed;
  steps.push(chainStep(g === true ? "ok" : g === false ? "bad" : "unknown",
    g === true ? "已校验" : g === false ? "校验未过" : "校验未知",
    g === true ? "只读 / 表权限 / 五维校验 / 字段权限 全部通过"
      : g === false ? "安全闸门或五维校验拦截了这条 SQL"
        : "本次没有留存校验结论（旧流水线或旧记录）"));

  // ③ 需求已核（第六维）
  const v = (c.requirement_verdict || "skipped");
  const vv = REQ_VERDICT_VIEW[v] || ["unknown", "需求未审", v];
  steps.push(chainStep(vv[0], vv[1], vv[2]));

  // ④ 已执行
  //   ★ 三态必须分清：executed=false（确实没走到执行）≠ executed=null（无从判断）。
  //     前者是"这步没发生"（被闸门拦下 / 需求未满足就不执行），画成虚线"未知"会让人以为系统出问题；
  //     后者是老记录没留档，只能承认"不知道"。
  const ex = c.exec_ok === true ? "ok"
    : (c.executed === true ? "bad" : (c.executed === false ? "skip" : "unknown"));
  steps.push(chainStep(ex,
    ex === "ok" ? "已执行" : ex === "bad" ? "执行失败" : ex === "skip" ? "未执行" : "执行未知",
    ex === "ok" ? "SQL 已执行并拿到结果集"
      : ex === "bad" ? "SQL 执行了但报错，没有拿到结果"
        : ex === "skip" ? "没有走到执行这一步（未生成 / 被拦截 / 需求未满足）"
          : "本次没有留存执行结论（旧流水线或旧记录）"));

  // 副标签：只放"四步之外但影响可信度"的信息
  const meta = [];
  if (c.result_sane === false) {
    meta.push(`<span class="status-tag warning">结果有待核疑点</span>`);
  } else if (c.result_sane === true) {
    meta.push(`<span class="status-tag success">结果体检无疑点</span>`);
  }
  if (c.requirement_attempts > 0) {
    meta.push(`<span class="status-tag warning" title="需求审核判不通过后，模型被要求定向修正了几轮">需求修正 ${c.requirement_attempts} 次</span>`);
  }
  meta.push(`<span id="chainBiz">${_bizChip(c)}</span>`);

  // 需求未满足 / 待确认 → 把原因列出来（只有徽章没有原因，用户不知道要改什么）
  const issues = ((r.req_review && r.req_review.issues) || r.req_findings || [])
    .filter(i => i && (i.severity === "error" || i.severity === "warning"));
  let find = "";
  if (issues.length && (v === "fail" || v === "unsure")) {
    const errs = issues.filter(i => i.severity === "error");
    const li = issues.slice(0, 4).map(i =>
      `<li>${esc(i.message || i.rule || "")}</li>`).join("");
    find = `<div class="chain-find ${errs.length ? "err" : ""}">
      <b>${v === "unsure" ? "审核没把握的地方" : "需求和 SQL 对不上的地方"}：</b>
      <ul>${li}</ul></div>`;
  }

  const partial = c.partial
    ? `<div class="chain-partial">※ 历史记录：本次只留档了执行结论与需求审核结论，
        校验过程与结果体检没有留档，因此显示为「未知 / 未审」。</div>` : "";

  box.innerHTML = `
    <div class="chain-head">执行状态
      <span style="color:var(--color-border-strong)">·</span>
      <span>四步互不等价：执行成功 ≠ 需求已满足 ≠ 业务正确</span>
    </div>
    <div class="chain-row">${steps.join('<span class="chain-arrow">→</span>')}</div>
    <div class="chain-meta">${meta.join("")}</div>
    ${find}${partial}`;
}

/** 需求审核没能收敛时，把"需要你确认什么"直接摆出来（而不是只回一句已拒绝） */
function renderReqClarify(r) {
  const box = document.getElementById("reqClarifyBox");
  if (!box) return;
  const nc = (r && r.needs_clarification) || null;
  if (!nc) { box.style.display = "none"; box.innerHTML = ""; return; }
  const qs = (nc.questions || []).map(q => `<li>${esc(q)}</li>`).join("");
  box.style.display = "block";
  box.innerHTML = `<div class="req-clarify">
    <div class="rc-head">⚠️ 这次的结果我不敢直接给你</div>
    <div class="rc-reason">${esc(nc.reason || "无法确认本次查询是否符合你的要求")}</div>
    ${qs ? `<ul class="rc-qs">${qs}</ul>` : ""}
    <div class="rc-hint">为了不给你一个"看着对、其实口径不对"的数，系统没有执行这条 SQL。
      按上面的问题补充一句（例如指定口径 / 时间范围 / 过滤条件）再问一次即可。</div>
  </div>`;
}

function renderAnswer(r) {
  lastAnswer = r;                                  // ★ 记下来：反馈时要把当时的上下文一起存
  // ★ 锚定"当前窗口增量修改"上下文：成功/失败都有，失败也可继续纠错重新执行
  lastQueryContextId = r.query_context_id || null;
  // ★ 刷新恢复：把"增量修改锚点 + 上轮问题"持久化，刷新后仍能继续修改
  if (window.AppState) AppState.save(lastQueryContextId, r.question || "");
  const mb = document.getElementById("modifyBox");
  if (mb) mb.style.display = lastQueryContextId ? "block" : "none";
  fbReset();                                       // 新结果 → 收起并清空上一次的反馈表单
  document.getElementById("fbBar").style.display = "block";
  document.getElementById("askResult").style.display = "block";

  // ★ 新取数 → 它刚落库时必然是"未提交审核"，把当前记录切到它
  //   （后端在 /api/ask 里已经自动落了一条记录，这里只是把 id 接上）
  CURRENT_RECORD = r.record_id
    ? { id: r.record_id, question: r.question, review_status: "none",
        review_status_zh: "未提交审核", remind_count: 0 }
    : null;
  renderReviewState();
  renderTrustBar(r);
  // ★ 状态链要接在 renderReviewState 之后：业务是否已核 是从 CURRENT_RECORD.review_status 读的
  renderStatusChain(r);
  renderReqClarify(r);
  updateFavButton();
  if (r.record_id) {
    refreshReviewBadge();
    loadHistory();
  }

  // ① 处理过程
  const steps = document.getElementById("steps");
  steps.innerHTML = `
    <div><span class="k">召回的表</span>${r.tables.length ? r.tables.join("、") : "（无）"}</div>
    <div><span class="k">召回的指标</span>${r.metrics.length ? r.metrics.join("、") : "（无）"}</div>
    <div><span class="k">召回的字典</span>${(r.dicts || []).length ? r.dicts.join("、") : "（无）"}</div>
    <div><span class="k">检索方式</span>${esc(r.info)}</div>
    <div><span class="k">尝试次数</span>${r.attempts} 次${r.attempts > 1 ? `<span style="color:var(--color-warning)">（含自愈）</span>` : ""}</div>
    ${r.pipeline === "legacy"
      ? `<div style="color:var(--color-warning)">⚠️ 本次走了旧流水线兜底（企业级流水线异常，已回退保障可用）${r.degrade_reason ? "：" + esc(r.degrade_reason) : ""}</div>`
      : ""}
    ${(!r.ok && r.pipeline === "v2" && r.degrade_reason)
      ? `<div style="color:var(--color-danger)">⛔ 企业级流水线中止（fail-closed）：${esc(r.degrade_reason)}</div>`
      : ""}
    ${r.ok
      ? `<div style="color:var(--color-success)">✅ 执行成功，共 ${r.row_count} 行</div>`
      : `<div style="color:var(--color-danger)">❌ ${esc(r.error)}</div>`}
    ${r.modified_from
      ? `<div style="color:var(--color-primary)">🔁 本次为「基于上一轮的增量修改」结果</div>`
      : ""}
  `;

  // ② SQL —— 显示排版后的版本，一行一个子句，不用左右拉着看
  //    ★ 执行用的是原始 SQL（后端从来没有执行过这个排版版本，语义等价但没必要冒险）
  document.getElementById("sqlBox").textContent =
    r.sql_formatted || r.sql || "（未生成 SQL）";

  // ①.5 换表说明：用户点名了某张表但 SQL 实际没用 → 醒目提示（避免误以为 bug）
  const swapBox = document.getElementById("swapBox");
  if (r.table_swap) {
    swapBox.style.display = "block";
    swapBox.innerHTML = `🔁 <b>换表说明</b>：${esc(r.table_swap)}`;
  } else {
    swapBox.style.display = "none";
  }

  // ②.5 逻辑自审报告（无论成功/失败都渲染；无报告则隐藏）
  renderReview(r);

  // ②.6 企业级流水线新字段：语义路由 / SQL 生成前校验 / 结果校验 / 自然语言回答
  renderUnderstanding(r);
  renderValidation(r);
  renderResultWarnings(r);
  renderNLAnswer(r);

  // ③ 结果表格
  const box = document.getElementById("resultBox");
  const title = document.getElementById("resultTitle");
  if (!r.ok) {
    title.textContent = "查询结果";
    box.innerHTML = `<div class="empty">未执行成功，无结果</div>`;
    return;
  }
  if (!r.rows.length) {
    title.textContent = "查询结果（0 行）";
    box.innerHTML = `<div class="empty">查询成功，但没有匹配的数据</div>`;
    return;
  }
  title.textContent = `查询结果（${r.row_count} 行）`;

  // ── 表头：只显示中文名 ──
  //    ★ 字段名移到 tooltip：视觉上干净（一列一个中文名），
  //      技术同学需要核对时鼠标悬停就能看到真实字段名，信息不丢。
  //    ★ 没有中文名的列照旧显示字段名 —— 但调成灰色，一眼能看出"这列还缺注释"。
  const labels = r.column_labels || {};
  const head = r.columns.map(c => {
    const zh = (labels[c] || {}).zh;
    return zh
      ? `<th title="${esc(c)}"><div class="th-zh">${esc(zh)}</div></th>`
      : `<th title="该字段还没有注释"><div class="th-zh th-missing">${esc(c)}</div></th>`;
  }).join("");

  // ── 单元格：配了字典的字段，在编码后面标注中文含义 ──
  //    ★ 原始编码一定保留 —— 中文只是"帮着读懂"，不能把数据本身替换掉
  const vmaps = r.value_maps || {};
  const body = r.rows.map(row =>
    `<tr>${row.map((v, i) => {
      if (v === null || v === undefined) return `<td><span class="muted">NULL</span></td>`;
      const map = vmaps[r.columns[i]];
      const zh = map ? map[String(v)] : null;
      return `<td>${esc(v)}${zh ? `<span class="cell-zh">${esc(zh)}</span>` : ""}</td>`;
    }).join("")}</tr>`
  ).join("");
  box.innerHTML = `<table class="data"><thead><tr>${head}</tr></thead><tbody>${body}</tbody></table>`;
}

// ═══════════════════════════════════════════════════════════
// 渲染「SQL 逻辑自审」报告（后端逐字段诊断，只诊断不改 SQL）
// ═══════════════════════════════════════════════════════════
function renderReview(r) {
  const box = document.getElementById("reviewBox");
  const rev = r && r.review;
  if (!rev) { box.style.display = "none"; box.innerHTML = ""; return; }
  box.style.display = "block";

  const nf = rev.needs_fix;
  const badge = nf === true
    ? `<span style="color:var(--color-danger);font-weight:600">⚠ 建议复核</span>`
    : nf === false
      ? `<span style="color:var(--color-success);font-weight:600">✓ 逻辑未见明显问题</span>`
      : `<span style="color:var(--color-text-muted)">逻辑自审</span>`;

  const overall = rev.overall
    ? `<div style="margin:6px 0;color:var(--color-text-secondary)">${esc(rev.overall)}</div>` : "";

  const fields = (rev.fields || []).map(f => {
    const ok = f.logic_ok === true ? "✓" : f.logic_ok === false ? "✗" : "•";
    const col = f.logic_ok === true ? "var(--color-success)" : f.logic_ok === false ? "var(--color-danger)" : "var(--color-text-muted)";
    return `<div style="padding:5px 0;border-top:1px dashed var(--color-border);font-size:13px">
      <span style="color:${col};font-weight:700;margin-right:6px">${ok}</span>
      <b>${esc(f.field || "")}</b>
      <span style="color:var(--color-text-secondary);margin-left:6px">${esc(f.comment || "")}</span>
    </div>`;
  }).join("");

  const risks = (rev.risks && rev.risks.length)
    ? `<div style="margin-top:8px;padding:8px;background:var(--color-warning-bg);border-radius:6px;font-size:13px;color:#a85c00">
         <b>风险提示：</b>${rev.risks.map(x => esc(x)).join("；")}</div>` : "";

  box.innerHTML = `<div style="margin:14px 0 4px;font-weight:600">🧐 SQL 逻辑自审 ${badge}</div>
    ${overall}<div>${fields}</div>${risks}`;
}

// ═══════════════════════════════════════════════════════════
// 渲染「查询理解 / 语义路由」结果（Query Understanding）
// ═══════════════════════════════════════════════════════════
function renderUnderstanding(r) {
  const box = document.getElementById("understandingBox");
  const u = r && r.understanding;
  if (!u || !Object.keys(u).length) {
    //  ★ 措辞要分情况，别一句话糊过去：
    //    以前无论什么原因都写"可能走了旧流水线兜底" —— 但拒答/被拦截时根本没生成 SQL，
    //    用户会以为是系统 bug，而真正的原因（这次没能产出理解）被掩盖了。
    box.innerHTML = `<div class="muted">${
      r && r.pipeline === "legacy"
        ? "（本次走了旧流水线兜底，不产出查询理解）"
        : "（本次未能生成 SQL —— 拒答或被拦截，因此没有查询理解结果）"
    }</div>`;
    return;
  }
  // 把数组/字符串统一成"可读文本"，空值显示（无）
  const row = (label, v) => {
    let s;
    if (Array.isArray(v)) s = v.length ? v.join("、") : "（无）";
    else s = (v === "" || v == null) ? "（无）" : String(v);
    return `<div><span class="k">${label}</span>${esc(s)}</div>`;
  };
  const intentMap = { summary: "汇总", trend: "趋势", compare: "对比", detail: "明细", deny: "否定/异常" };
  box.innerHTML = [
    row("意图", intentMap[u.intent] || u.intent),
    row("识别指标", u.metrics),
    row("识别实体", u.entities),
    row("分组维度", u.dimensions),
    row("时间范围", u.time_range),
    row("过滤条件", u.filters),
    u.ambiguity ? `<div><span class="k">歧义/假设</span>${esc(u.ambiguity)}</div>` : "",
  ].join("");
}

// ═══════════════════════════════════════════════════════════
// 渲染「SQL 生成前校验报告」(5 维：P0 规则 / P1 口径 / P2 Join / P3 Schema / 比率写法)
// ═══════════════════════════════════════════════════════════
function renderValidation(r) {
  const box = document.getElementById("validationBox");
  const issues = r && r.validation;
  // 旧流水线兜底 / 检索阶段被拦截 → 没有跑 5 维校验，不要误报"通过"
  if (issues === null || issues === undefined) {
    box.style.display = "block";
    box.innerHTML = `<div class="alert info" style="margin-top:12px">ℹ️ 本次未运行 SQL 生成前 5 维校验（查询在检索阶段被拦截，或走了旧流水线兜底）。</div>`;
    return;
  }
  if (!issues.length) {
    box.style.display = "block";
    box.innerHTML = `<div class="alert ok" style="margin-top:12px">✅ SQL 生成前校验通过（P0 系统规则 / P1 指标口径 / P2 Join / P3 Schema / 比率写法）</div>`;
    return;
  }
  const errors = issues.filter(i => i.severity === "error").length;
  const warns  = issues.filter(i => i.severity === "warning").length;
  // ★ 校验规则 → 中文名，让报告更直观（含 Phase 1 新增的列级校验 column_exist）
  const RULE_ZH = {
    no_dateTrunc: "禁 dateTrunc", no_settings: "禁 SETTINGS", readonly: "只读",
    alias_backtick: "别名引号", metric_status: "指标状态口径", metric_fee_field: "指标费用字段",
    join_pre_agg: "Join 预聚合", table_exist: "表存在性", column_exist: "列存在性",
    ratio_coalesce: "比率 COALESCE", ratio_decimal: "比率小数",
  };
  const items = issues.map(i => {
    const isErr = i.severity === "error";
    const ruleZh = RULE_ZH[i.rule] || "";
    return `<div class="val-item ${i.severity}">
      <span class="status-tag ${isErr ? "danger" : "warning"}">${isErr ? "拦截" : "提醒"}</span>
      <span class="tier">${esc(i.tier || "")}${ruleZh ? " · " + esc(ruleZh) : ""}</span>
      <div class="val-body">
        <div class="val-msg">${esc(i.message || "")}</div>
        ${i.suggestion ? `<div class="val-sug">建议：${esc(i.suggestion)}</div>` : ""}
      </div>
    </div>`;
  }).join("");
  const head = `<div style="margin-top:14px;font-weight:600">🔍 SQL 生成前校验 `
    + (errors ? `<span style="color:var(--color-danger)">${errors} 个拦截</span>` : `<span style="color:var(--color-success)">无拦截</span>`)
    + (warns ? ` ｜ <span style="color:var(--color-warning)">${warns} 个提醒</span>` : "")
    + `</div>`;
  box.style.display = "block";
  box.innerHTML = head + items;
}

// ═══════════════════════════════════════════════════════════
// 渲染「执行后结果校验」warning（比率越界 / 全 NULL 列 / 行数异常 / 全零量级）
// ═══════════════════════════════════════════════════════════
function renderResultWarnings(r) {
  const box = document.getElementById("resultWarnings");
  const ws = r && r.result_warnings;
  if (!ws || !ws.length) { box.style.display = "none"; box.innerHTML = ""; return; }
  box.style.display = "block";
  box.innerHTML = ws.map(w => `<div class="alert warn" style="margin-top:10px">${esc(w)}</div>`).join("");
}

// ═══════════════════════════════════════════════════════════
// 渲染「自然语言回答」（AI 结论，含口径/假设声明）
// ═══════════════════════════════════════════════════════════
function renderNLAnswer(r) {
  const box = document.getElementById("answerBox");
  const a = r && r.answer;
  if (!a) {
    // 默认不生成结论：显示按钮，用户需要时再点（省掉每次取数多一次 LLM 往返）
    box.style.display = "block";
    box.innerHTML = '<button id="genAnswerBtn" class="ghost" onclick="generateAnswer()">生成自然语言回答</button>';
    return;
  }
  box.style.display = "block";
  box.textContent = a;   // 结论为模型文本，用 textContent 防注入；CSS 已 pre-wrap 保留换行
}

// ★ 按需生成自然语言回答：默认不生成，点按钮才调用，不重跑 SQL。
async function generateAnswer() {
  const r = lastAnswer;
  if (!r) { alert("没有可生成结论的结果，请先执行一次取数。"); return; }
  const btn = document.getElementById("genAnswerBtn");
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  try {
    const meta = r.nl_meta || {};
    const res = await api("/api/nl-answer", {
      method: "POST",
      body: JSON.stringify({
        question: r.question || "",
        columns: r.columns || [],
        rows: (r.rows || []).slice(0, 20),   // 只需前几行即可生成结论，避免大结果回传
        metric_defs: meta.metric_defs || "",
        ambiguity: meta.ambiguity || "",
      }),
    });
    if (res && res.answer) {
      r.answer = res.answer;                 // 回填到 lastAnswer，反馈/收藏也能带上
      renderNLAnswer(r);
    } else {
      document.getElementById("answerBox").innerHTML = '<span class="muted">未生成结论，请重试。</span>';
    }
  } catch (e) {
    alert("生成结论失败：" + (e && e.message ? e.message : e));
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = "生成自然语言回答"; }
  }
}

function copySql() {
  const text = document.getElementById("sqlBox").textContent;
  const btn = document.getElementById("copyBtn");
  copyTextToClipboard(text, btn, "复制 SQL");
}

/** 通用复制：按钮文案/颜色反馈一致，收藏页与取数页共用 */
function copyTextToClipboard(text, btn, defaultLabel) {
  const label = defaultLabel || (btn ? btn.textContent : "复制");
  const reset = () => {
    if (!btn) return;
    btn.textContent = label;
    btn.style.color = "";
    btn.style.borderColor = "";
  };
  const ok = () => {
    if (!btn) return;
    btn.textContent = "✓ 复制成功";
    btn.style.color = "var(--color-success)";
    btn.style.borderColor = "var(--color-success)";
    clearTimeout(btn._copyTimer);
    btn._copyTimer = setTimeout(reset, 1800);
  };
  const legacy = () => {
    try {
      const ta = document.createElement("textarea");
      ta.value = text; ta.style.position = "fixed"; ta.style.opacity = "0";
      document.body.appendChild(ta); ta.select();
      document.execCommand("copy"); document.body.removeChild(ta); ok();
    } catch (e) { alert("复制失败"); }
  };
  if (!text) return;
  if (navigator.clipboard && navigator.clipboard.writeText) {
    navigator.clipboard.writeText(text).then(ok, legacy);
  } else {
    legacy();
  }
}

