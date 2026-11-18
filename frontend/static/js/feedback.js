/* ══════════════════════════════════════════════════════════════
   feedback.js · 问题反馈 · 优化建议
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// Tab 2 · 模块五：问题反馈（错例复盘）
// ═══════════════════════════════════════════════════════════
let fbListFilter = "pending";   // 默认只看待处理（已处理的不用天天翻）
let fbListData = null;

async function kbLoadFeedback() {
  const statsBox = document.getElementById("fbStats");
  const listBox  = document.getElementById("fbList");
  try {
    fbListData = await api("/api/feedback");
    kbRenderFeedback();
  } catch (e) {
    statsBox.innerHTML = `<div class="alert err">加载失败：${esc(e.message)}</div>`;
    listBox.innerHTML = "";
  }
}

function kbRenderFeedback() {
  const d = fbListData;
  if (!d) return;
  const s = d.stats;
  document.getElementById("fbStats").innerHTML =
    kbStat("待处理", s.pending, s.total, "还没补知识的") +
    kbStat("已处理", s.resolved, s.total, "已补注释 / 字典 / 口径") +
    kbStat("不处理", s.ignored, s.total, "误报，或不是知识能解决的");

  const filters = [["pending", "待处理"], ["resolved", "已处理"],
                   ["ignored", "不处理"], ["all", "全部"]];
  document.getElementById("fbFilter").innerHTML = filters.map(([k, label]) => {
    const n = (k === "all") ? s.total : s[k];
    const cls = (fbListFilter === k) ? "btn" : "ghost";
    return `<button class="${cls}" onclick="kbSetFbFilter('${k}')">${label}（${n}）</button>`;
  }).join("");

  const items = (fbListFilter === "all")
    ? d.items : d.items.filter(i => i.status === fbListFilter);
  const box = document.getElementById("fbList");
  if (!items.length) {
    box.innerHTML = `<div class="empty">${fbListFilter === "pending"
      ? "没有待处理的反馈 —— 取数页答错时点一下「结果不对」就会记到这里"
      : "这一类还没有记录"}</div>`;
    return;
  }
  box.innerHTML = items.map(it => kbFbCard(it, d.reasons)).join("");
}

function kbFbCard(it, reasons) {
  const tag = it.status === "pending" ? '<span class="status-tag warning">待处理</span>'
            : it.status === "resolved" ? '<span class="status-tag success">已处理</span>'
            : '<span class="status-tag gray">不处理</span>';
  const reasonCn = esc(reasons[it.reason] || it.reason);
  // 当时命中的表/指标/字典 —— 表名可点，直接跳过去看（复盘时最需要的就是这个）
  const hits = [
    ...(it.tables || []).map(t =>
      `<a class="fb-jump" onclick="kbGotoTable('${esc(t)}')">${esc(t)}</a>`),
    ...(it.metrics || []).map(m => `<span class="tag layer">指标 ${esc(m)}</span>`),
    ...(it.dicts || []).map(x => `<span class="tag layer">字典 ${esc(x)}</span>`),
  ].join(" ");

  return `<div class="card fb-card">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        ${tag}<b>${esc(it.question)}</b>
        <span class="status-tag warning">${reasonCn}</span>
      </div>
      <span class="muted" style="font-size:12px">#${it.id} · ${esc(it.created_at)}</span>
    </div>
    <div class="fb-detail">${esc((it.detail || "").replace(/\n/g, " "))}</div>
    <pre class="fb-sql">${esc(it.sql || "（无 SQL）")}</pre>
    ${it.result ? `<div class="kb-tip">结果摘要：<span class="mono">${esc(it.result)}</span></div>` : ""}
    ${hits ? `<div class="kb-tip" style="margin-top:6px">当时命中：${hits}</div>` : ""}
    ${it.note ? `<div class="kb-tip" style="margin-top:6px">处理备注：<b>${esc(it.note)}</b></div>` : ""}
    <div class="row fb-acts">
      <input class="fb-note" id="fbNote_${it.id}" value="${esc(it.note || "")}"
             placeholder="处理备注：改了哪条知识？">
      <button class="btn" onclick="kbFbSet(${it.id}, 'resolved')">标记已处理</button>
      <button class="ghost" onclick="kbFbSet(${it.id}, 'ignored')">不处理</button>
      <button class="ghost" onclick="kbFbSet(${it.id}, 'pending')">退回待处理</button>
      <button class="ghost danger" onclick="kbFbDelete(${it.id})">删除</button>
    </div>
  </div>`;
}

function kbSetFbFilter(k) {
  fbListFilter = k;
  kbRenderFeedback();
}

async function kbFbSet(fid, status) {
  const noteEl = document.getElementById("fbNote_" + fid);
  const note = noteEl ? noteEl.value.trim() : "";
  // ★ 标记「已处理」必须写一句改了什么：
  //   只改状态不留痕迹，过两个月没人记得当时改的是哪条知识、为什么这么改，
  //   这条记录就白存了。
  if (status === "resolved" && !note) {
    alert("标记「已处理」前请先写一句处理备注（改了哪条知识）。\n"
        + "只改状态不留痕迹，过两个月就没人记得当时改了什么了。");
    if (noteEl) noteEl.focus();
    return;
  }
  try {
    await api(`/api/feedback/${fid}`, {
      method: "PUT", body: JSON.stringify({ status: status, note: note }),
    });
    await kbLoadFeedback();
  } catch (e) {
    alert("操作失败：" + e.message);
  }
}

async function kbFbDelete(fid) {
  if (!confirm("确定删除这条反馈？")) return;
  try {
    await api(`/api/feedback/${fid}`, { method: "DELETE" });
    await kbLoadFeedback();
  } catch (e) {
    alert("删除失败：" + e.message);
  }
}

/** 从反馈跳到「表结构信息」并打开那张表（复盘时不用自己找回来） */
async function kbGotoTable(table) {
  gotoKbSchema();
  await kbOpenTable(table);
}

// ═══════════════════════════════════════════════════════════
// ★ 优化建议 —— 把「反馈」翻译成「该改哪条知识、改成什么样」
// ═══════════════════════════════════════════════════════════
//  建议不落库：它是草稿。用户看过、改过、应用之后，
//  它就以正式知识的形式存在了（写进了 prompt / 字段注释 / 指标口径），
//  再单独存一份"曾经建议过什么"只会变成没人看的历史垃圾。
let sgData = null;

async function sgGenerate() {
  const box = document.getElementById("sgBox");
  const btn = document.getElementById("sgBtn");
  //   只针对【待处理】的反馈生成 —— 已经处理过的再建议一次是噪音
  const pending = ((fbListData && fbListData.items) || [])
    .filter(i => i.status === "pending").map(i => i.id);
  if (!pending.length) {
    box.innerHTML = `<div class="alert warn" style="margin-top:12px">
      没有待处理的反馈 —— 先去「取数」页用一用，答错了点一下「结果不对」。</div>`;
    return;
  }
  btn.disabled = true;
  btn.textContent = "分析中…";
  box.innerHTML = `<div class="empty" style="margin-top:12px">模型正在读这 ${pending.length} 条反馈…（约 10 秒）</div>`;
  try {
    sgData = await api("/api/suggest", {
      method: "POST", body: JSON.stringify({ feedback_ids: pending }),
    });
    sgRender();
  } catch (e) {
    box.innerHTML = `<div class="alert err" style="margin-top:12px">生成失败：${esc(e.message)}</div>`;
  } finally {
    btn.disabled = false;
    btn.textContent = "根据反馈生成建议";
  }
}

function sgRender() {
  const box = document.getElementById("sgBox");
  const d = sgData;
  if (!d) return;
  if (d.error) {
    box.innerHTML = `<div class="alert err" style="margin-top:12px">${esc(d.error)}
      <div class="kb-tip">模型原始输出：${esc(d.raw_preview || "")}</div></div>`;
    return;
  }
  if (!d.suggestions || !d.suggestions.length) {
    box.innerHTML = `<div class="alert info" style="margin-top:12px">
      ${esc(d.message || "没有值得给的建议")} —— 这其实是好消息：说明现有知识已经能覆盖这些案例。</div>`;
    return;
  }
  box.innerHTML = d.suggestions.map(sgCard).join("");
  //  记下原建议，用来判断"用户到底改过没有"（改过再应用要二次确认）
  d.suggestions.forEach(s => {
    const ta = document.getElementById("sgVal_" + s.sid);
    if (ta) ta.dataset.orig = s.proposed;
  });
}

function sgCard(s) {
  const same = s.current && s.current.trim() === s.proposed.trim();
  return `<div class="sg-card" id="sgCard_${s.sid}">
    <div class="row" style="justify-content:space-between;gap:8px;flex-wrap:wrap">
      <div class="row" style="gap:8px;flex-wrap:wrap;align-items:center">
        <span class="tag layer">${esc(s.kind_label)}</span>
        <b class="mono">${esc(s.target)}</b>
        <span class="muted" style="font-size:12px">来自反馈 ${
          (s.feedback_ids || []).map(i => "#" + i).join("、") || "—"}</span>
      </div>
      ${same ? '<span class="status-tag gray">与当前内容相同</span>' : ""}
    </div>
    <div class="sg-reason">理由：${esc(s.reason)}</div>
    ${s.current ? `<details><summary>看当前内容（${s.current.length} 字符）</summary>
        <pre class="fb-sql">${esc(s.current)}</pre></details>` : ""}
    <label>建议内容（可以直接改）</label>
    <textarea class="sg-edit" id="sgVal_${s.sid}">${esc(s.proposed)}</textarea>
    <div class="row" style="margin-top:10px;gap:8px;flex-wrap:wrap">
      <button class="btn" onclick="sgApply('${s.sid}')">确认应用</button>
      <button class="ghost" onclick="sgDrop('${s.sid}')">忽略这条</button>
      <span class="muted" style="font-size:12px">应用后，来源反馈会自动标成「已处理」</span>
    </div>
  </div>`;
}

function sgDrop(sid) {
  const card = document.getElementById("sgCard_" + sid);
  if (card) card.remove();
  if (sgData) sgData.suggestions = sgData.suggestions.filter(x => x.sid !== sid);
}

async function sgApply(sid) {
  const s = (sgData && sgData.suggestions || []).find(x => x.sid === sid);
  if (!s) return;
  const ta = document.getElementById("sgVal_" + sid);
  const val = (ta ? ta.value : "").trim();
  if (!val) { alert("内容不能为空"); return; }
  //   改过就再确认一次：这条要写进知识库，落笔前让人再看一眼自己的改动
  if (ta.dataset.orig && val !== ta.dataset.orig
      && !confirm("你改过这条建议 —— 确认按【你改后的版本】应用？")) return;

  try {
    const r = await api("/api/suggest/apply", {
      method: "POST",
      body: JSON.stringify({
        kind: s.kind, target: s.target, value: val,
        feedback_ids: s.feedback_ids,
        note: "由优化建议应用" + (ta.dataset.orig && val !== ta.dataset.orig ? "（人工修改过）" : ""),
      }),
    });
    const card = document.getElementById("sgCard_" + sid);
    if (card) {
      card.classList.add("done");
      card.insertAdjacentHTML("afterbegin",
        `<div class="alert ok">已应用${
          (r.marked_feedback || []).length
            ? `，并把反馈 ${r.marked_feedback.map(i => "#" + i).join("、")} 标为已处理` : ""}。
         <b>记得点右上角「重建索引」</b> —— 不改索引，模型还是看旧知识。</div>`);
    }
    await kbLoadFeedback();          // 反馈状态变了，列表和计数要刷新
  } catch (e) {
    alert("应用失败：" + e.message);
  }
}

// Tab 2 · 模块二：指标信息（业务口径）
