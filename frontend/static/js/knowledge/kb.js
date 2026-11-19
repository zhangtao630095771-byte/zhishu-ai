/* ══════════════════════════════════════════════════════════════
   kb.js · 知识治理（复核队列）+ AI 录入 + 知识库概览搜索
   ★ P1 知识域重排：原「Prompt 配置」已拆到同目录 prompt.js。
   依赖：core.js 的 api()、experience.js 的 esc()、registry.js 的 kbLoad* 等全局函数。
   ══════════════════════════════════════════════════════════════ */

// ═══════════════════════════════════════════════════════════
// Tab 2：RAG 知识库
//   ★ 拆成两个独立模块（二级标签切换）：
//       表结构信息 —— 表注释 + 字段注释
//       指标信息   —— 业务口径
//     两个模块是"切换"关系，不是上下堆叠，互不干扰。
// ═══════════════════════════════════════════════════════════

/** 二级模块切换（作用域 = 当前所在的一级页面） */
// ═══════════════════════════════════════════════════════════
// 知识复核队列（治理准入）
// ═══════════════════════════════════════════════════════════
//   ★ 为什么要有这一段？
//     严格模式下"AI 生成的知识不进检索"，这是治理要求；但对使用的人来说，
//     如果页面只显示"已录入"、却查不到、也不告诉你去哪通过，那就是黑洞。
//     所以必须有一个地方能看见「待复核」并一键裁决。
let GOV_PENDING = [];                 // 当前待复核列表（按钮按下标取，避免字符串转义问题）

async function kbLoadPending() {
  const box = document.getElementById("govPendBox");
  const sumBox = document.getElementById("govSummaryBox");
  if (!box) return;
  box.innerHTML = `<div class="empty">加载中…</div>`;
  let d;
  try {
    d = await api("/api/kb/governance/pending");
  } catch (e) {
    box.innerHTML = `<div class="empty">加载失败：${esc(e.message)}</div>`;
    return;
  }
  GOV_PENDING = d.items || [];
  const cnt = document.getElementById("govPendCount");
  if (cnt) cnt.textContent = `（${d.count} 条）`;
  const badge = document.getElementById("govPendBadge");
  if (badge) {
    badge.textContent = d.count ? ` ${d.count}` : "";
    badge.style.cssText = d.count
      ? "display:inline-block;min-width:16px;padding:0 5px;margin-left:4px;border-radius:9px;background:var(--color-danger);color:#fff;font-size:11px;line-height:16px;text-align:center"
      : "";
  }

  // ① 待复核清单
  if (!GOV_PENDING.length) {
    box.innerHTML = `<div class="empty">🎉 没有待复核的知识。AI 新写入的知识会先出现在这里，等你裁决后才进检索。</div>`;
  } else {
    box.innerHTML = GOV_PENDING.map((it, i) => {
      const srcTag = it.source === "ai"
        ? `<span class="status-tag info" style="margin-left:6px">AI 生成</span>` : "";
      return `<div class="list-item" style="align-items:flex-start;gap:10px">
        <div style="flex:1;min-width:0">
          <div><b>${esc(it.label)}</b>
            <span class="muted" style="font-size:12px;margin-left:6px">${esc(it.key)}</span>${srcTag}
          </div>
          <div class="muted" style="font-size:12px;margin-top:3px;word-break:break-all">${esc(it.preview || "（无内容）")}</div>
        </div>
        <div class="row" style="gap:6px;flex:0 0 auto">
          <button class="btn" onclick="kbGovAction(${i},'approve',this)">通过</button>
          <button class="ghost" onclick="kbGovAction(${i},'reject',this)">拒绝</button>
        </div>
      </div>`;
    }).join("");
  }

  // ② 各层状态概览
  const sum = d.summary || {};
  const LAYER_ORDER = ["metrics", "entities", "table_comments", "column_comments",
                       "dict_entries", "relationships", "kb_docs"];
  if (sumBox) {
    sumBox.innerHTML = LAYER_ORDER.filter(k => sum[k]).map(k => {
      const v = sum[k];
      // 有待复核时描个琥珀色边，让"这儿还有活没干完"一眼可见
      const style = v.candidate > 0 ? "border:1px solid var(--color-warning-border)" : "";
      return `<div class="kb-stat" style="${style}">
        <div class="num">${v.approved}<small> / ${v.total}</small></div>
        <div class="lbl">${esc(v.label)}　待复核 ${v.candidate}　已拒绝 ${v.rejected}</div>
      </div>`;
    }).join("");
  }
}

async function kbGovAction(idx, action, btn) {
  const it = GOV_PENDING[idx];
  if (!it) return;
  const verb = action === "approve" ? "通过" : "拒绝";
  const tip = action === "approve"
    ? "核准后它会进入生产检索（重建索引后生效）。"
    : "拒绝后它不会进入检索，相当于把这条知识判为错。";
  if (!confirm(`确定${verb}这条知识？\n\n【${it.label}】${it.key}\n${(it.preview || "").slice(0, 120)}\n\n${tip}`)) return;

  const old = btn ? btn.textContent : "";
  if (btn) { btn.disabled = true; btn.textContent = "处理中…"; }
  try {
    const r = await api("/api/kb/governance/action", {
      method: "POST",
      body: JSON.stringify({ layer: it.layer, key: it.key, action: action }),
    });
    alert(r.message || "已处理");
    kbLoadPending();                 // 重新拉一遍，保证计数与列表同步
    refreshGovBadge();               // 导航角标也要跟着减
  } catch (e) {
    alert("处理失败：" + e.message);
    if (btn) { btn.disabled = false; btn.textContent = old; }
  }
}

function kbSwitchModule(name, btn) {
  //   ★ 用 btn.closest('.tab') 限定范围：现在"二级模块"分布在三个一级页面里
  //     （知识管理 / 知识治理 / 反馈处理），写死 #tab-kb 会切错页。
  const sec = (btn && btn.closest(".tab")) || document;
  sec.querySelectorAll(".kb-module").forEach(el => el.classList.remove("active"));
  sec.querySelectorAll(".subtabs button").forEach(el => el.classList.remove("active"));
  document.getElementById("kbMod-" + name).classList.add("active");
  if (btn) btn.classList.add("active");
  // 懒加载：切到哪个模块才去拉哪个模块的数据
  if (name === "metric") loadMetrics();
  if (name === "dict")   kbLoadDicts();
  if (name === "gap")    kbLoadGaps(false);      // 缺口扫描较慢，函数内部有缓存
  if (name === "fb")     kbLoadFeedback();
  if (name === "overview") kbLoadKbOverview();    // 知识总览（P0~P4 分层 + 检索）
  if (name === "registry") kbLoadRegistry();
  if (name === "review") kbLoadPending();        // ★ 复核队列（治理准入）
  // ★ 上面的 querySelectorAll(".subtabs button") 会把"知识录入"页里的嵌套子标签
  //   active 一并清掉 → 这里重置回默认子页（文字口径）。
  if (name === "ingest")   kbSwitchIngestTab("text");
}

// ═══════════════════════════════════════════════════════════
// Tab 2 · AI 自动整合录入 + 全局知识检索
// ═══════════════════════════════════════════════════════════
const INGEST_TYPE_ZH = {
  metric: "指标(P1)", entity: "实体(P2)", relationship: "关联(P2)",
  column_comment: "字段注释(P3)", dict: "字典(P3)", doc: "文档(P4)",
};

let ingestResults = [];   // 最近一次 AI 录入结果（编辑/删除回显）
let ingestResultsBox = "ingestResult";   // 结果渲染到哪个容器（文字口径/ SQL 投喂 两个子页共用一套渲染）

/** 子标签切换：文字口径 / SQL 投喂 */
function kbSwitchIngestTab(which) {
  const isSql = which === "sql";
  document.getElementById("ingPaneText").style.display = isSql ? "none" : "";
  document.getElementById("ingPaneSql").style.display = isSql ? "" : "none";
  document.getElementById("ingTabText").classList.toggle("active", !isSql);
  document.getElementById("ingTabSql").classList.toggle("active", isSql);
}

function kbRenderIngestResults(boxId) {
  if (boxId) ingestResultsBox = boxId;                  // 记住"本轮结果该渲染到哪"
  const box = document.getElementById(ingestResultsBox);
  if (!box) return;
  const rs = ingestResults;
  if (!rs.length) { box.innerHTML = '<div class="empty">AI 未识别出可录入的知识，请换更明确的表述。</div>'; return; }
  box.innerHTML = `<div style="margin-bottom:8px;font-weight:600">解析出 ${rs.length} 条：</div>` +
    rs.map((r, i) => {
      const del = r.status === "deleted";
      const skipped = r.status === "skipped";
      const badge = del
        ? `<span class="status-tag danger">已删除</span>`
        : skipped
          ? `<span class="status-tag warning">已跳过</span>`
          : `<span class="status-tag ${r.status === 'ok' ? 'success' : 'danger'}">${r.status === 'ok' ? '已录入' : '失败'}</span>`;
      return `<div style="padding:6px 0;border-top:1px dashed var(--color-border);display:flex;justify-content:space-between;align-items:center">
        <div>
          ${badge}
          <span class="tier">${esc(INGEST_TYPE_ZH[r.type] || r.type || '')}</span>
          <b>${esc(r.name || '')}</b>
          ${!del ? `<span class="muted">${esc(r.message || '')}</span>` : ""}
        </div>
        ${r.status === 'ok' && !del ? `<div>
          <button class="ghost" onclick="kbEditIngestItem(${i})">编辑</button>
          <button class="ghost" onclick="kbDelIngestItem(${i})">删除</button>
        </div>` : ""}
      </div>`;
    }).join("") +
    `<div class="alert warn" style="margin-top:10px">★ 已写入知识库，点「重建索引」后生效。</div>`;
}

async function kbRunIngest() {
  const box = document.getElementById("ingestResult");
  const btn = document.getElementById("ingestBtn");
  const text = document.getElementById("ingestInput").value.trim();
  if (!text) { alert("请输入口径逻辑"); return; }
  btn.disabled = true; btn.textContent = "AI 解析中…";
  box.innerHTML = '<div class="muted">AI 正在解析并录入…</div>';
  try {
    const d = await api("/api/kb/ai-ingest", { method: "POST", body: JSON.stringify({ text }) });
    ingestResults = d.results || [];
    kbRenderIngestResults("ingestResult");
  } catch (e) {
    box.innerHTML = `<div class="alert err">录入失败：${esc(e.message || e)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "AI 解析并录入";
  }
}

/** ★ SQL 投喂：取数问题 + 标准 SQL → AI 提炼为可复用知识 */
async function kbRunSqlIngest() {
  const box = document.getElementById("sqlIngestResult");
  const btn = document.getElementById("sqlIngestBtn");
  const question = document.getElementById("sqlIngestQuestion").value.trim();
  const sql = document.getElementById("sqlIngestSQL").value.trim();
  const note = document.getElementById("sqlIngestNote").value.trim();
  if (!question || !sql) { alert("取数问题与标准 SQL 都不能为空"); return; }
  btn.disabled = true; btn.textContent = "AI 提炼中…";
  box.innerHTML = '<div class="muted">AI 正在分析这条 SQL 并提炼可复用知识…（约 10~30 秒）</div>';
  try {
    const d = await api("/api/kb/ingest-sql", { method: "POST", body: JSON.stringify({ question, sql, note }) });
    ingestResults = d.results || [];
    kbRenderIngestResults("sqlIngestResult");
  } catch (e) {
    box.innerHTML = `<div class="alert err">投喂失败：${esc(e.message || e)}</div>`;
  } finally {
    btn.disabled = false; btn.textContent = "AI 提炼并录入";
  }
}

function kbClearSqlIngest() {
  ["sqlIngestQuestion", "sqlIngestSQL", "sqlIngestNote"].forEach(id => {
    const el = document.getElementById(id); if (el) el.value = "";
  });
  document.getElementById("sqlIngestResult").innerHTML = "";
}

function kbEditIngestItem(i) {
  const r = ingestResults[i];
  if (!r || !r.data) return;
  document.getElementById("ingestEditType").value = r.type;
  document.getElementById("ingestEditJson").value = JSON.stringify(r.data, null, 2);
  document.getElementById("ingestEditModal").style.display = "block";
}

async function kbSaveIngestEdit() {
  const type = document.getElementById("ingestEditType").value;
  try {
    const data = JSON.parse(document.getElementById("ingestEditJson").value);
    const d = await api("/api/kb/ingest-item", { method: "PUT", body: JSON.stringify({ type, data }) });
    alert(d.message);
    document.getElementById("ingestEditModal").style.display = "none";
  } catch (e) {
    alert("保存失败（JSON 格式可能不对）：" + (e.message || e));
  }
}

async function kbDelIngestItem(i) {
  const r = ingestResults[i];
  if (!r) return;
  if (!confirm(`删除「${r.name}」？`)) return;
  try {
    const d = await api(`/api/kb/ingest-item?type=${encodeURIComponent(r.type)}&key=${encodeURIComponent(r.key || "")}`, { method: "DELETE" });
    alert(d.message);
    ingestResults[i].status = "deleted";
    kbRenderIngestResults();
  } catch (e) {
    alert("删除失败：" + (e.message || e));
  }
}

/** 知识总览 · 检索：显示命中的知识；关键词为空 → 回到 P0~P4 分层视图 */
async function kbRunOverviewSearch() {
  const box = document.getElementById("ovBox");
  const q = document.getElementById("ovSearchInput").value.trim();
  if (!q) { kbLoadKbOverview(); return; }           // 空 → 回到总览
  box.innerHTML = '<div class="muted">搜索中…</div>';
  try {
    const d = await api(`/api/kb/search?q=${encodeURIComponent(q)}`);
    const rs = d.results || [];
    if (!rs.length) {
      box.innerHTML = `<div class="empty">「${esc(q)}」没有匹配任何知识。点「显示全部」回到分层总览。</div>`;
      return;
    }
    box.innerHTML = `<div style="margin-bottom:8px">命中 ${rs.length} 条：</div>` +
      rs.map(r => `<div style="padding:6px 0;border-top:1px dashed var(--color-border)">
        <span class="tier">${esc(INGEST_TYPE_ZH[r.type] || r.type || '')}</span>
        <b>${esc(r.name || '')}</b>
        ${r.desc ? `<span class="muted"> — ${esc(r.desc)}</span>` : ""}
      </div>`).join("");
  } catch (e) {
    box.innerHTML = `<div class="alert err">搜索失败：${esc(e.message || e)}</div>`;
  }
}

/** 知识总览 · 清空检索，回到 P0~P4 分层视图 */
function kbClearOverviewSearch() {
  const el = document.getElementById("ovSearchInput");
  if (el) el.value = "";
  kbLoadKbOverview();
}

/** 知识总览：拉取 P0~P4 分层内容并渲染（点「显示全部」或进本页时调用）
    ★ 函数名带 Kb 前缀是为了避开已存在的 kbLoadOverview()（表结构覆盖率概览）。 */
async function kbLoadKbOverview() {
  const box = document.getElementById("ovBox");
  if (!box) return;
  box.innerHTML = '<div class="empty">加载中…</div>';
  try {
    const d = await api("/api/kb/priorities");
    const tiers = d.tiers || [];
    if (!tiers.length) { box.innerHTML = '<div class="empty">暂无知识</div>'; return; }
    const esc = s => (s == null ? "" : String(s)).replace(/[&<>]/g,
      c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;" }[c]));
    let html = '<div class="prio-wrap">';
    for (const tr of tiers) {
      html += `<div class="prio-tier">
        <div class="prio-head prio-lv${tr.tier}">
          <span class="lv">${esc(tr.name)}</span>
          <span class="cnt">${tr.items.length} 条</span>
        </div>
        <div class="prio-desc">${esc(tr.desc)}</div>
        <div class="prio-items">`;
      if (!tr.items.length) {
        html += '<div class="prio-empty">（该层级暂无内容）</div>';
      } else {
        for (const it of tr.items) {
          const meta = it.meta || {};
          let badges = "";
          for (const [k, v] of Object.entries(meta)) {
            if (v == null) continue;
            if (Array.isArray(v)) {
              if (!v.length) continue;
              for (const x of v) badges += `<span class="b">${esc(k)}: ${esc(x)}</span>`;
            } else {
              badges += `<span class="b">${esc(k)}: ${esc(v)}</span>`;
            }
          }
          html += `<div class="prio-item">
            <div class="t">${esc(it.title)}</div>
            <div class="tx">${esc(it.text)}</div>
            ${badges ? `<div class="m">${badges}</div>` : ""}
          </div>`;
        }
      }
      html += "</div></div>";
    }
    html += "</div>";
    box.innerHTML = html;
  } catch (e) {
    box.innerHTML = `<div class="alert err">加载失败：${esc(e.message || e)}</div>`;
  }
}

