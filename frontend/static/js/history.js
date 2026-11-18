/* ══════════════════════════════════════════════════════════════
   history.js · 取数审核 · 个人取数历史
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// ★ 取数审核 + 个人取数历史
// ═══════════════════════════════════════════════════════════
//  【这一块解决两个真实痛点】
//    ① "我上周问过的那条数，SQL 是什么来着？" —— 关掉页面就找不回来了，
//       同一句话重问，AI 未必给出一样的 SQL。所以要有历史。
//    ② "AI 给的 26.5% 到底对不对？" —— 业务同学没有能力判断口径，
//       所以要有"请管理员复核"的通道，以及明确的"未审核，谨慎使用"提示。
//
//  【状态实时更新为什么用轮询？】
//    这套系统是"单进程 + 无消息中间件"，为一条审核状态引入 WebSocket 长连接不划算。
//    审核是"人点一下"的低频事件，2.5 秒轮询已经足够"实时"，
//    而且断线自动续、刷新页面也不丢，比长连接省心得多。
let CURRENT_RECORD = null;      // 当前正在看的记录（新取数 or 从历史点开的）
let REVIEW_TIMER = null;        // 审核状态轮询定时器
let HIST_ITEMS = [];            // 历史列表缓存
let RV_ITEMS = [];              // 审核队列缓存
let FAV_RECORD_IDS = new Set(); // 已收藏的记录 id（字符串）

const RV_ZH = { none: "未提交审核", pending: "待管理员审核",
                approved: "管理员已审核通过", rejected: "审核未通过" };

function rvChip(st) {
  const s = st || "none";
  const live = (s === "pending") ? '<i class="live"></i>' : "";
  return `<span class="rv-chip ${s}">${live}${RV_ZH[s] || s}</span>`;
}

function fmtWhen(s) {
  return s ? String(s).slice(5, 16) : "";      // MM-DD HH:MM
}

/** 把"当前记录的审核状态"同步渲染到三处：结果提示条 / 结果区胶囊 / 左侧面板左上角 */
function renderReviewState() {
  const rec = CURRENT_RECORD;
  const st = rec ? rec.review_status : null;
  const warn = document.getElementById("reviewWarn");
  const topChip = document.getElementById("rvChipTop");
  const btn = document.getElementById("rvSubmitBtn");
  const sideBox = document.getElementById("histStatus");

  // ── 结果区顶部提示条：这个数"能不能用" ──
  if (!rec) {
    warn.innerHTML = "";
  } else if (st === "approved") {
    warn.innerHTML = `<div class="rv-warn approved"><span class="ic">✓</span><div>
      <b>此取数已由管理员审核通过</b>，口径可放心使用。
      <div class="meta">审核人 ${esc(rec.reviewer || "—")} · ${esc(rec.reviewed_at || "")}${rec.review_note ? " · 意见：" + esc(rec.review_note) : ""}</div>
    </div></div>`;
  } else if (st === "rejected") {
    warn.innerHTML = `<div class="rv-warn rejected"><span class="ic">✕</span><div>
      <b>此结果未通过审核，请勿直接用于汇报或决策。</b>
      ${rec.review_note ? `<div class="meta">审核意见：${esc(rec.review_note)}</div>` : ""}
      <div class="meta">建议按意见修正知识库后重新取数。</div>
    </div></div>`;
  } else if (st === "pending") {
    warn.innerHTML = `<div class="rv-warn pending"><span class="ic">◔</span><div>
      <b>已提交审核，等待管理员确认。</b>确认前请把结果当作参考值。
      <div class="meta">提交于 ${esc(rec.submitted_at || "")}${rec.remind_count ? ` · 已提醒 ${rec.remind_count} 次` : ""}</div>
    </div></div>`;
  } else {
    warn.innerHTML = `<div class="rv-warn unreviewed"><span class="ic">!</span><div>
      <b>此结果未经管理员审核，口径可能不准确，请谨慎使用。</b>
      <div class="meta">对外汇报 / 决策前，建议点下方「提交审核」请管理员确认口径。</div>
    </div></div>`;
  }

  // ── 结果区状态胶囊 + 提交按钮 ──
  if (topChip) {
    const s = st || "none";
    topChip.className = "rv-chip " + s;
    topChip.innerHTML = (s === "pending" ? '<i class="live"></i>' : "") + (RV_ZH[s] || s);
  }
  if (btn) {
    const canSubmit = rec && (st === "none" || st === "rejected");
    btn.classList.toggle("hidden", !canSubmit);
    btn.textContent = (st === "rejected") ? "重新提交审核" : "提交审核";
  }

  // ── ★ 左侧面板左上角：实时审核标识 + 提醒审核 ──
  if (sideBox) {
    if (!rec) {
      sideBox.innerHTML = `<div style="font-size:11.5px;color:var(--color-text-muted);line-height:1.6">点一条记录，这里显示它的审核状态</div>`;
    } else {
      sideBox.innerHTML = `
        <div class="rowline">
          ${rvChip(st)}
          ${st === "pending" ? `<button class="ghost" onclick="remindReview()">提醒审核</button>` : ""}
        </div>
        ${rec.review_note ? `<div style="font-size:11.5px;color:var(--color-text-muted);margin-top:6px;line-height:1.6">意见：${esc(rec.review_note)}</div>` : ""}`;
    }
  }

  // ★ 「业务已核」徽章的唯一事实源就是上面的审核状态：状态一变立刻同步。
  //   不同步的后果很具体：轮询拿到"已通过"、上方提示条已经变绿，
  //   而状态链里还写着"业务未核" —— 同一屏两句话互相打脸。
  if (typeof refreshBizChip === "function") refreshBizChip();
}

/** 提交当前结果，请管理员审核 */
async function submitReview() {
  const rec = CURRENT_RECORD;
  if (!rec) { alert("请先取一次数，再提交审核"); return; }
  try {
    const r = await api(`/api/records/${rec.id}/submit`, { method: "POST" });
    CURRENT_RECORD = Object.assign({}, rec, r.record);
    renderReviewState();
    refreshReviewBadge();
    loadHistory();
  } catch (e) {
    alert("提交失败：" + e.message);
  }
}

/** 催管理员审核 */
async function remindReview() {
  const rec = CURRENT_RECORD;
  if (!rec) return;
  try {
    const r = await api(`/api/records/${rec.id}/remind`, { method: "POST" });
    CURRENT_RECORD = Object.assign({}, rec, r.record);
    renderReviewState();
  } catch (e) {
    alert("提醒失败：" + e.message);
  }
}

/** ★ 审核状态实时刷新：当前记录状态 + 导航角标 */
function startReviewPolling() {
  if (REVIEW_TIMER) clearInterval(REVIEW_TIMER);
  REVIEW_TIMER = setInterval(async () => {
    // ① 只在"等待审核"时轮询详情 —— 已经有结论的不需要再查
    if (CURRENT_RECORD && CURRENT_RECORD.review_status === "pending") {
      try {
        const s = await api(`/api/records/${CURRENT_RECORD.id}/review`);
        if (s.review_status !== CURRENT_RECORD.review_status) {
          CURRENT_RECORD = Object.assign({}, CURRENT_RECORD, s);
          renderReviewState();          // 提示条会立刻变成"已通过/未通过"
          loadHistory();
          if (isAdmin()) loadReviewQueue();
        }
      } catch (e) { /* 轮询失败静默重试，不打扰用户 */ }
    }
    // ② 角标
    refreshReviewBadge();
  }, 2500);
}

/** 导航角标：管理员看全公司待审；普通用户看"待审 + 被驳回" */
async function refreshReviewBadge() {
  try {
    const d = await api("/api/review/summary");
    const n = (d.role === "admin")
      ? (d.pending || 0)
      : ((d.pending || 0) + (d.rejected || 0));
    const el = document.getElementById("navReviewCnt");
    if (!el) return;
    el.textContent = n;
    el.classList.toggle("hidden", n === 0);
  } catch (e) { /* 角标失败不影响主流程 */ }
}

// ── 历史面板 ──
async function loadHistory() {
  const box = document.getElementById("histList");
  if (!box) return;
  try {
    const d = await api("/api/history?limit=40");
    HIST_ITEMS = d.items || [];
    renderHistory();
  } catch (e) {
    box.innerHTML = `<div class="hist-empty">历史加载失败：${esc(e.message)}</div>`;
  }
}

function renderHistory() {
  const box = document.getElementById("histList");
  if (!box) return;
  const items = HIST_ITEMS;
  if (!items.length) {
    box.innerHTML = '<div class="hist-empty">还没有取数记录。<br>上面问一个问题就会出现这里。</div>';
    return;
  }
  const curId = CURRENT_RECORD ? CURRENT_RECORD.id : null;
  box.innerHTML = items.map(it => {
    const on = FAV_RECORD_IDS.has(String(it.id));
    return `
    <div class="hist-item ${it.id === curId ? "active" : ""}"
         onclick="openHistoryRecord(${it.id})" title="${esc(it.question || "")}">
      <span class="dot ${esc(it.review_status || "none")}"></span>
      <span class="q">${esc(it.question || "(空问题)")}</span>
      <button class="fav-star ${on ? "on" : ""}" title="${on ? "取消收藏" : "收藏"}"
              onclick="event.stopPropagation(); toggleFavoriteRecord(${it.id})">${on ? "★" : "☆"}</button>
      <span class="when">${esc(fmtWhen(it.created_at))}</span>
    </div>`;
  }).join("");
}

// ── 收藏 ──
async function loadFavorites() {
  try {
    const d = await api("/api/favorites?object_type=record");
    FAV_RECORD_IDS = new Set((d.items || []).map(x => String(x.object_id)));
  } catch (e) {
    FAV_RECORD_IDS = new Set();
  }
  renderHistory();
  updateFavButton();
}

async function toggleFavoriteRecord(rid) {
  const key = String(rid);
  const on = FAV_RECORD_IDS.has(key);
  try {
    if (on) {
      await api(`/api/favorites/record/${encodeURIComponent(key)}`, { method: "DELETE" });
    } else {
      await api("/api/favorites", {
        method: "POST",
        body: JSON.stringify({ object_type: "record", object_id: key }),
      });
    }
    await loadFavorites();
  } catch (e) {
    alert("收藏操作失败：" + e.message);
  }
}

function toggleFavoriteCurrent() {
  if (!CURRENT_RECORD || !CURRENT_RECORD.id) {
    alert("请先完成一次取数");
    return;
  }
  toggleFavoriteRecord(CURRENT_RECORD.id);
}

function updateFavButton() {
  const btn = document.getElementById("favBtn");
  if (!btn) return;
  const on = !!(CURRENT_RECORD && FAV_RECORD_IDS.has(String(CURRENT_RECORD.id)));
  btn.textContent = on ? "★ 已收藏" : "☆ 收藏";
  btn.classList.toggle("on", on);
}

// ── 收藏取数页（独立导航）──
const FAV_STATUS_ZH = {
  none: "未提交审核", pending: "待审核", approved: "审核通过", rejected: "审核未通过",
};

async function loadFavoritePage() {
  const box = document.getElementById("favList");
  if (!box) return;
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api("/api/favorites/records");
    renderFavoritePage(d.items || []);
  } catch (e) {
    box.innerHTML = `<div class="empty">收藏加载失败：${esc(e.message)}</div>`;
  }
}

function renderFavoritePage(items) {
  const box = document.getElementById("favList");
  if (!box) return;
  if (!items.length) {
    box.innerHTML = '<div class="empty">还没有收藏。<br>在智能取数页点结果右上角「☆ 收藏」，或历史条目的星标即可加入这里。</div>';
    return;
  }
  box.innerHTML = items.map(it => `
    <div class="fav-row">
      <div class="fav-q">${esc(it.question || "(历史记录没有保存问题文本)")}</div>
      <div class="fav-meta">
        <span>收藏于 ${esc(fmtWhen(it.favorited_at))}</span>
        <span>取数于 ${esc(fmtWhen(it.created_at))}</span>
        <span>${esc(FAV_STATUS_ZH[it.review_status] || it.review_status || "未提交审核")}</span>
        <span>${it.ok ? "执行成功" : "执行失败"} · ${it.row_count} 行</span>
      </div>
      ${it.sql ? `<div class="fav-sql">${esc(it.sql)}</div>` : ""}
      <div class="fav-actions">
        <button class="ghost" id="favToggle-${it.id}" onclick="toggleFavoriteDetail(${it.id}, this)">查看完整结果</button>
        <button class="ghost" onclick="unfavoriteFromPage(${it.id}, event)">取消收藏</button>
      </div>
      <div class="fav-detail hidden" id="favDetail-${it.id}"></div>
    </div>`).join("");
}

/** 收藏页内联展开详情（不再跳转智能取数页） */
async function toggleFavoriteDetail(id, btn) {
  const box = document.getElementById(`favDetail-${id}`);
  if (!box) return;
  if (!box.classList.contains("hidden")) {
    box.classList.add("hidden");
    btn.textContent = "查看完整结果";
    return;
  }
  btn.disabled = true;
  btn.textContent = "加载中…";
  try {
    if (box.dataset.loaded !== "1") {
      const rec = await api(`/api/records/${id}`);
      box.innerHTML = renderFavDetailHtml(rec);
      box.dataset.loaded = "1";
    }
    box.classList.remove("hidden");
    btn.textContent = "收起";
  } catch (e) {
    box.innerHTML = `<div class="empty">详情加载失败：${esc(e.message)}</div>`;
    box.classList.remove("hidden");
    btn.textContent = "收起";
  } finally {
    btn.disabled = false;
  }
}

function renderFavDetailHtml(rec) {
  return `
    <div class="steps">
      <div><span class="k">记录编号</span>#${rec.id}</div>
      <div><span class="k">取数时间</span>${esc(rec.created_at || "")}</div>
      <div><span class="k">审核状态</span>${esc(FAV_STATUS_ZH[rec.review_status] || rec.review_status || "未提交审核")}</div>
      ${rvStepsHtml(rec)}
    </div>
    <div class="fav-detail-head">
      <div class="fav-detail-label">SQL</div>
      <button class="ghost" onclick="copyFavoriteSql(${rec.id}, this)">复制 SQL</button>
    </div>
    <pre id="favSql-${rec.id}" class="code fav-detail-pre">${esc(rec.sql_formatted || rec.sql || "（无 SQL）")}</pre>
    <div class="fav-detail-head">
      <div class="fav-detail-label">结果摘要</div>
      <div class="fav-detail-acts">
        <button class="ghost" onclick="downloadRecordExport(${rec.id}, 'csv', this)" title="导出当前结果快照">导出 CSV</button>
        <button class="ghost" onclick="downloadRecordExport(${rec.id}, 'xlsx', this)" title="导出当前结果快照">导出 Excel</button>
      </div>
    </div>
    <pre class="code fav-detail-pre">${esc(rec.result_preview || "（无结果摘要）")}</pre>
    ${rec.answer ? `<div class="fav-detail-label">自然语言结论</div>
      <div class="fav-answer">${esc(rec.answer)}</div>` : ""}`;
}

/** 复制收藏详情里的 SQL（和取数页的复制按钮同一套反馈） */
function copyFavoriteSql(id, btn) {
  const el = document.getElementById(`favSql-${id}`);
  copyTextToClipboard(el ? el.textContent : "", btn, "复制 SQL");
}

async function unfavoriteFromPage(id, ev) {
  if (ev) ev.stopPropagation();
  await toggleFavoriteRecord(id);
  await loadFavoritePage();
}

// ── 导出（后端返回结果快照，不在导出时重跑 SQL）──
async function downloadResult(format) {
  if (!CURRENT_RECORD || !CURRENT_RECORD.id) {
    alert("请先完成一次取数");
    return;
  }
  await downloadRecordExport(CURRENT_RECORD.id, format);
}

/** 通用结果导出：取数页与收藏页共用（按记录 id 导出快照） */
async function downloadRecordExport(rid, format, btn) {
  if (!rid) { alert("记录不存在"); return; }
  const label = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "导出中…"; }
  const token = getToken();
  const url = `/api/records/${rid}/export?format=${encodeURIComponent(format)}`;
  try {
    const r = await fetch(url, { headers: token ? { "Authorization": "Bearer " + token } : {} });
    if (r.status === 401) {
      handleUnauthorized("登录已过期，请重新登录");
      throw new Error("登录已过期，请重新登录");
    }
    if (!r.ok) {
      let detail = r.statusText;
      try { detail = (await r.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    const blob = await r.blob();
    const cd = r.headers.get("Content-Disposition") || "";
    let name = `zhishu_${rid}.${format === "xlsx" ? "xlsx" : "csv"}`;
    const m = cd.match(/filename\*=UTF-8''([^;]+)/i) || cd.match(/filename="?([^";]+)"?/i);
    if (m) { try { name = decodeURIComponent(m[1]); } catch (e) {} }
    const a = document.createElement("a");
    a.href = URL.createObjectURL(blob);
    a.download = name;
    document.body.appendChild(a);
    a.click();
    a.remove();
    setTimeout(() => URL.revokeObjectURL(a.href), 1500);
  } catch (e) {
    alert("导出失败：" + e.message);
  } finally {
    if (btn) { btn.disabled = false; btn.textContent = label; }
  }
}

// ── 可信标签：流水线 / 降级 / 校验 / 行数 / 重试 ──
function renderTrustBar(r) {
  const box = document.getElementById("trustBar");
  if (!box) return;
  if (!r) { box.innerHTML = ""; return; }
  const chips = [];
  if (r.pipeline) {
    chips.push(`<span class="trust-chip ${r.pipeline === "legacy" ? "warn" : ""}">${r.pipeline === "legacy" ? "旧流水线" : "企业级流水线"}</span>`);
  }
  if (r.degraded) {
    chips.push(`<span class="trust-chip bad">已降级</span>`);
  }
  if (r.validation) {
    const errs = r.validation.filter(x => x.severity === "error").length;
    const warns = r.validation.filter(x => x.severity === "warning").length;
    if (errs) chips.push(`<span class="trust-chip bad">校验错误 ${errs}</span>`);
    else if (warns) chips.push(`<span class="trust-chip warn">校验提示 ${warns}</span>`);
    else chips.push(`<span class="trust-chip ok">校验通过</span>`);
  }
  if (r.result_warnings && r.result_warnings.length) {
    chips.push(`<span class="trust-chip warn">结果提示 ${r.result_warnings.length}</span>`);
  }
  // ── SQL 经验：本次到底参考了哪条历史案例（可追溯，不满意就直接去「SQL经验」页看） ──
  const xp = r.experience || {};
  const xpCases = xp.cases || [], xpFails = xp.failures || [];
  if (xpCases.length) {
    const tip = xpCases.map(c => `${c.case_no || ("#" + c.id)}（分数 ${c.score}，${c.trust || "-"}）`).join("\n");
    chips.push(`<span class="trust-chip ok" title="${esc(tip)}">参考经验 ${xpCases.length}</span>`);
  }
  if (xpFails.length) {
    const tip = xpFails.map(c => `${c.case_no || ("#" + c.id)}（${c.failure_type || "失败经验"}）`).join("\n");
    chips.push(`<span class="trust-chip" title="${esc(tip)}">避坑 ${xpFails.length}</span>`);
  }
  if (r.experience_warnings && r.experience_warnings.length) {
    const tip = r.experience_warnings.map(w => w.message || w.code).join("\n");
    chips.push(`<span class="trust-chip warn" title="${esc(tip)}">经验预警 ${r.experience_warnings.length}</span>`);
  }
  if (r.row_count !== undefined && r.row_count !== null) {
    chips.push(`<span class="trust-chip">${r.row_count} 行</span>`);
  }
  // ★ 命中缓存要明说：秒回不是模型变快了，用户有权知道这一点再决定信不信这个数
  if (r.cache_hit) {
    chips.push(`<span class="trust-chip" title="同一问题在 120 秒内被再次提出，直接复用上次结果（含权限过滤与脱敏）">缓存命中</span>`);
  }
  if (r.attempts > 1) {
    chips.push(`<span class="trust-chip warn">重试 ${r.attempts} 次</span>`);
  }
  box.innerHTML = chips.join("");
}

function toggleHist() {
  const panel = document.getElementById("histPanel");
  panel.classList.toggle("collapsed");
  document.getElementById("histToggle").textContent =
    panel.classList.contains("collapsed") ? "»" : "«";
  try {
    localStorage.setItem("zhishu_hist_collapsed", panel.classList.contains("collapsed") ? "1" : "0");
  } catch (e) {}
}

/** 点历史条目 / 点"看详情" → 在「智能取数」页展示这条记录 */
async function openHistoryRecord(id) {
  try {
    const rec = await api(`/api/records/${id}`);
    CURRENT_RECORD = rec;
    lastAnswer = null;                      // ★ 历史记录不是"刚取的结果"，避免反馈串味

    //   ★ 必须先切到「智能取数」页再渲染。
    //     详情是渲染进取数页那几个容器的（#steps / #sqlBox / #resultBox…），
    //     如果此刻正停在「取数审核」页，那些容器是 display:none ——
    //     内容写进去了但用户什么都看不到，表现就是"点了没反应"（这就是之前的 bug）。
    switchTab("ask", document.querySelector('.nav-item[data-tab="ask"]'));

    renderReviewState();
    renderHistory();
    renderHistoryDetail(rec);
  } catch (e) {
    alert("打开失败：" + e.message);
  }
}

function renderHistoryDetail(rec) {
  // 历史视图只保留"当时跑了什么"，反馈入口收起来（避免对着旧结果提交新反馈）
  const fbOpen = document.getElementById("fbOpenBtn");
  if (fbOpen) fbOpen.classList.add("hidden");
  document.getElementById("fbBar").style.display = "block";
  document.getElementById("fbForm").style.display = "none";

  document.getElementById("askResult").style.display = "block";
  renderTrustBar(rec);
  // ★ 历史回看也要状态链：后端 config_store._chain_from_record 用落库字段还原了一份
  //   （partial=True，缺失的步骤显示为"未知/未审"，绝不假装当时校验过）
  renderStatusChain(rec);
  renderReqClarify(rec);
  updateFavButton();
  document.getElementById("steps").innerHTML = `
    <div><span class="k">取数问题</span>${esc(rec.question || "（未记录问题）")}</div>
    <div><span class="k">记录编号</span>#${rec.id}</div>
    <div><span class="k">提问人</span>${esc(rec.display_name || rec.username || "—")}${rec.display_name ? `（${esc(rec.username)}）` : ""}</div>
    <div><span class="k">取数时间</span>${esc(rec.created_at || "")}</div>
    ${rvStepsHtml(rec)}
  `;
  // ★ 处理过程和查询理解都从记录里读出来了 —— 回看历史时不用再靠猜
  document.getElementById("understandingBox").innerHTML = rvUnderstandingHtml(rec);
  document.getElementById("sqlBox").textContent =
    rec.sql_formatted || rec.sql || "（无 SQL）";
  document.getElementById("reviewBox").style.display = "none";
  document.getElementById("validationBox").style.display = "none";
  document.getElementById("resultTitle").textContent = "结果摘要";
  document.getElementById("resultBox").innerHTML = rec.result_preview
    ? `<pre class="code" style="margin:0">${esc(rec.result_preview)}</pre>`
    : `<div class="empty">（无结果摘要）</div>`;
  document.getElementById("resultWarnings").style.display = "none";
  const ans = document.getElementById("answerBox");
  if (rec.answer) { ans.style.display = "block"; ans.textContent = rec.answer; }
  else { ans.style.display = "none"; ans.innerHTML = ""; }

  // 重新取数：把原问题填回输入框，用户改一改就能再跑一次
  const hint = document.getElementById("askHint");
  if (hint) {
    hint.innerHTML = `这是历史记录 · <a href="javascript:void(0)" onclick="reAskHistory()">重新取数</a>`;
  }
}

function reAskHistory() {
  const rec = CURRENT_RECORD;
  if (!rec) return;
  const fbOpen = document.getElementById("fbOpenBtn");
  if (fbOpen) fbOpen.classList.remove("hidden");
  document.getElementById("question").value = rec.question || "";
  document.getElementById("askResult").style.display = "none";
  document.getElementById("askHint").textContent = "已填入历史问题，可直接提交或修改后提交";
  window.scrollTo({ top: 0, behavior: "smooth" });
}

// ── 取数审核页 ──
async function loadReviewQueue(force) {
  const box = document.getElementById("rvList");
  if (!box) return;
  if (force) box.innerHTML = `<div class="empty">加载中…</div>`;
  try {
    // 管理员看全公司已提交的；普通用户看自己提交的
    const d = isAdmin()
      ? await api("/api/review/queue?status=all&limit=100")
      : await api("/api/history?limit=100");
    RV_ITEMS = (d.items || []).filter(x => isAdmin() || x.review_status !== "none");
    renderReviewQueue();
    refreshReviewBadge();
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
  }
}

function renderReviewQueue() {
  const box = document.getElementById("rvList");
  const f = (document.getElementById("rvFilter") || {}).value || "pending";
  let items = RV_ITEMS;
  if (f !== "all") items = items.filter(x => x.review_status === f);

  const pend = RV_ITEMS.filter(x => x.review_status === "pending").length;
  const hint = document.getElementById("rvHint");
  if (hint) {
    hint.textContent = isAdmin()
      ? `当前列出 ${items.length} 条 · 全公司待审 ${pend} 条`
      : `我的提交 ${RV_ITEMS.length} 条 · 待审 ${pend} 条`;
  }
  if (!items.length) {
    box.innerHTML = `<div class="empty">${f === "pending" ? "没有待审核的记录 🎉" : "没有符合条件的记录"}</div>`;
    return;
  }
  box.innerHTML = items.map(rvCard).join("");
}

/** 处理过程（与取数页同一套字段，审核时用来定位"哪一步出的问题"） */
function rvStepsHtml(rec) {
  return `
    <div><span class="k">召回的表</span>${(rec.tables || []).map(esc).join("、") || "（无）"}</div>
    <div><span class="k">召回的指标</span>${(rec.metrics || []).map(esc).join("、") || "（无）"}</div>
    <div><span class="k">召回的字典</span>${(rec.dicts || []).map(esc).join("、") || "（无）"}</div>
    <div><span class="k">检索方式</span>${esc(rec.info || "（未记录）")}</div>
    <div><span class="k">尝试次数</span>${rec.attempts || 0} 次${(rec.attempts || 0) > 1
      ? `<span style="color:var(--color-warning)">（含自愈重试 —— 说明第一次生成的 SQL 没通过校验）</span>` : ""}</div>
    ${rec.pipeline === "legacy"
      ? `<div style="color:var(--color-warning)">⚠️ 走了旧流水线兜底（企业级流水线异常回退），审核时请重点看口径</div>` : ""}
    <div>${rec.ok
      ? `<span style="color:var(--color-success)">✅ 执行成功，共 ${rec.row_count} 行</span>`
      : `<span style="color:var(--color-danger)">❌ 未执行成功</span>`}</div>`;
}

/** 查询理解（语义路由）——"模型当时把问题理解成了什么"，是判断谁错了的关键证据 */
function rvUnderstandingHtml(rec) {
  const u = rec.understanding;
  if (!u || !Object.keys(u).length) {
    return `<div class="muted">（这条取数没有记录查询理解：${
      rec.sql ? "产生于该字段上线之前，或走了旧流水线兜底" : "当时未生成 SQL（拒答/被拦截）"
    }）</div>`;
  }
  const row = (label, v) => {
    let s;
    if (Array.isArray(v)) s = v.length ? v.join("、") : "（无）";
    else s = (v === "" || v == null) ? "（无）" : String(v);
    return `<div><span class="k">${label}</span>${esc(s)}</div>`;
  };
  const intentMap = { summary: "汇总", trend: "趋势", compare: "对比", detail: "明细", deny: "否定/异常" };
  return [
    row("意图", intentMap[u.intent] || u.intent),
    row("识别指标", u.metrics),
    row("识别实体", u.entities),
    row("分组维度", u.dimensions),
    row("时间范围", u.time_range),
    row("过滤条件", u.filters),
    u.ambiguity ? `<div><span class="k">歧义/假设</span>${esc(u.ambiguity)}</div>` : "",
  ].join("");
}

function rvCard(rec) {
  const st = rec.review_status || "none";
  const canReview = isAdmin() && st === "pending";
  // 审核人要对上"是谁问的"，只给登录名（liuhao）根本认不出人 → 姓名 +（登录名）
  const who = rec.display_name
    ? `${esc(rec.display_name)}<span class="rv-un">（${esc(rec.username || "—")}）</span>`
    : esc(rec.username || "—");
  return `<div class="rv-item ${esc(st)}">
    <h4>${esc(rec.question || "(空问题)")}</h4>
    <div class="rv-meta">
      ${rvChip(st)}
      <span>提问人 <b>${who}</b></span>
      <span>取数时间 <b>${esc(rec.created_at || "—")}</b></span>
      ${rec.submitted_at ? `<span>提交 <b>${esc(rec.submitted_at)}</b></span>` : ""}
      ${rec.remind_count ? `<span class="rv-recheck on">用户已催 ${rec.remind_count} 次</span>` : ""}
      ${rec.reviewer ? `<span>审核人 <b>${esc(rec.reviewer)}</b> · ${esc(rec.reviewed_at || "")}</span>` : ""}
    </div>

    <!-- ★ 处理过程 + 查询理解：定位"是理解错了还是口径错了"靠这两块 -->
    <div class="rv-sec">
      <div class="rv-sec-t">处理过程</div>
      <div class="steps">${rvStepsHtml(rec)}</div>
    </div>

    <div class="rv-sec">
      <div class="rv-sec-t">查询理解（语义路由）</div>
      <div class="steps">${rvUnderstandingHtml(rec)}</div>
    </div>

    <div class="rv-sec">
      <div class="rv-sec-t">生成的 SQL（已排版，直接看）</div>
      <pre class="code">${esc(rec.sql_formatted || rec.sql || "（无 SQL）")}</pre>
    </div>

    ${rec.result_preview ? `<div class="rv-sec">
      <div class="rv-sec-t">结果摘要</div>
      <pre class="code" style="max-height:none">${esc(rec.result_preview)}</pre>
    </div>` : ""}

    ${rec.answer ? `<div class="rv-sec">
      <div class="rv-sec-t">AI 结论（审核时重点看它有没有胡说）</div>
      <div class="rv-note">${esc(rec.answer)}</div>
    </div>` : ""}

    ${rec.review_note ? `<div class="rv-note"><b>审核意见：</b>${esc(rec.review_note)}</div>` : ""}

    ${canReview ? `
      <div class="rv-acts">
        <input id="rvNote_${rec.id}" placeholder="审核意见（判不通过时必填：哪里不对、该补什么）">
        <button class="btn" onclick="doReview(${rec.id}, 'approved')">审核通过</button>
        <button class="ghost danger" onclick="doReview(${rec.id}, 'rejected')">审核不通过</button>
      </div>` : ""}

    ${isAdmin() ? `<div class="rv-acts">
      <span class="muted" style="font-size:12px">发现问题就去补知识：</span>
      ${(rec.tables || []).length ? `<button class="ghost" onclick="kbGotoTable('${esc(rec.tables[0])}')">补字段注释</button>` : ""}
      <button class="ghost" onclick="gotoMetricEdit()">改指标口径</button>
      <button class="ghost" onclick="gotoIngest()">AI 录入知识</button>
      <button class="ghost" onclick="openHistoryRecord(${rec.id})">在取数页打开</button>
    </div>` : `
      <div class="rv-acts">
        <button class="ghost" onclick="openHistoryRecord(${rec.id})">看详情</button>
        ${st === "pending" ? `<button class="ghost" onclick="remindFromQueue(${rec.id})">提醒审核</button>` : ""}
      </div>`}
  </div>`;
}

/** 审核页里直接催办（不改当前查看的记录） */
async function remindFromQueue(rid) {
  try {
    const r = await api(`/api/records/${rid}/remind`, { method: "POST" });
    alert(r.message);
    loadReviewQueue();
  } catch (e) { alert("提醒失败：" + e.message); }
}

/** 管理员给出审核结论 */
async function doReview(rid, status) {
  const note = ((document.getElementById("rvNote_" + rid) || {}).value || "").trim();
  if (status === "rejected" && !note) {
    alert("判定「不通过」时必须写清原因 —— 否则用户不知道该改什么。");
    return;
  }
  if (status === "approved" && !confirm("确认这条取数的口径没问题？审核通过后用户会看到「可放心使用」。")) return;
  try {
    const r = await api(`/api/review/${rid}`, {
      method: "PUT", body: JSON.stringify({ status, note }),
    });
    alert(r.message);
    // 如果审的正是当前正在看的记录，状态立刻同步过去
    if (CURRENT_RECORD && CURRENT_RECORD.id === rid) {
      CURRENT_RECORD = Object.assign({}, CURRENT_RECORD, r.record);
      renderReviewState();
    }
    loadReviewQueue();
    loadHistory();
  } catch (e) {
    alert("审核失败：" + e.message);
  }
}

/** 跳到「知识管理 → 指标信息」改口径 */
function gotoMetricEdit() {
  switchTab("kb", document.querySelector('.nav-item[data-tab="kb"]'));
  const btn = Array.from(document.querySelectorAll("#tab-kb .subtabs button"))
    .find(b => b.textContent.includes("指标"));
  if (btn) kbSwitchModule("metric", btn);
}

/** 跳到「知识治理 → 知识录入」沉淀知识（原「AI 录入」） */
function gotoIngest() {
  switchTab("gov", document.querySelector('.nav-item[data-tab="gov"]'));
  const btn = Array.from(document.querySelectorAll("#tab-gov .subtabs button"))
    .find(b => b.textContent.includes("知识录入"));
  if (btn) kbSwitchModule("ingest", btn);
}

