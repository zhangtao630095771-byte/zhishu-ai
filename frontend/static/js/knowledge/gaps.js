/* ══════════════════════════════════════════════════════════════
   gaps.js · 知识缺口扫描与补录
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════
// Tab 2 · 模块四：知识缺口（主动发现该补哪里）
// ═══════════════════════════════════════════════════════════
let gapData = null;          // 扫描结果缓存（模块间切来切去不重复请求）

async function kbLoadGaps(force) {
  const statsBox = document.getElementById("gapStats");
  const listBox  = document.getElementById("gapList");
  const hint     = document.getElementById("gapHint");
  if (gapData && !force) { kbRenderGaps(gapData); return; }
  statsBox.innerHTML = `<div class="empty">扫描中…要查每张表的真实取值分布，约 5 秒</div>`;
  listBox.innerHTML = "";
  hint.textContent = "";
  try {
    const d = await api("/api/kb/gaps" + (force ? "?refresh=true" : ""));
    gapData = d;
    kbRenderGaps(d);
  } catch (e) {
    statsBox.innerHTML = `<div class="alert err">扫描失败：${esc(e.message)}</div>`;
  }
}

function kbRenderGaps(d) {
  const s = d.summary;
  document.getElementById("gapHint").textContent =
    `共扫 ${s.tables} 张表 / ${s.total_fields} 个字段`;
  document.getElementById("gapStats").innerHTML =
    kbStat("枚举型字段", s.enum_fields, s.total_fields, "需要说清取值含义的字段") +
    kbStat("完全无注释", s.danger, s.enum_fields, "最危险：模型只能靠字段名猜") +
    kbStat("注释缺取值", s.incomplete, s.enum_fields, "知道字段是什么，不知道值是什么") +
    kbStat("注释有错值", s.stale, s.enum_fields, "注释声称的值数据里查不到");

  kbRenderTableGaps(d.table_items || [], s);

  const box = document.getElementById("gapList");
  if (!d.items.length) {
    box.innerHTML = `<div class="empty">没有发现缺口 —— 枚举字段的取值含义都写清楚了</div>`;
    const all = document.getElementById("gapFillAllBtn");
    if (all) { all.style.display = "none"; }
    return;
  }

  const rows = d.items.map(it => {
    const lvl = it.severity === "danger"
      ? '<span class="status-tag danger">无注释</span>'
      : it.severity === "stale"
      ? '<span class="status-tag danger">有错值</span>'
      : '<span class="status-tag warning">缺取值</span>';
    const shown = (it.values || []).slice(0, 12);
    const vals = shown.map(v => `<code>${esc(v)}</code>`).join(" ")
      + (it.values.length > 12 ? " …" : "");

    //   ★ 补完之后这一行要"变脸"：原来的危级别标签换成结果，
    //     让人一眼看出哪几条补上了、哪几条 AI 也搞不定（后者必须留给人）
    let fieldCell = `<span style="font-weight:600">${esc(it.column)}</span>`;
    let cmtCell = esc(it.comment) || '<span class="muted">（无）</span>';
    let opsCell = `<div class="row">
        <button class="ghost" data-t="${esc(it.table)}" data-c="${esc(it.column)}"
                onclick="gapFillOne(this.dataset.t, this.dataset.c, this)">AI 补全</button>
        <button class="ghost" onclick="kbGotoField('${esc(it.table)}','${esc(it.column)}')">去改</button>
      </div>`;

    if (it._ok) {
      fieldCell = `<span style="font-weight:600">${esc(it.column)}</span> <span class="status-tag info">AI</span>`;
      cmtCell = esc(it._comment);
      opsCell = `<div class="row"><button class="ghost"
          onclick="kbGotoField('${esc(it.table)}','${esc(it.column)}')">去复核</button></div>`;
    } else if (it._fail) {
      //   失败原因必须显示出来：不然用户只看到"没反应"，会以为功能坏了
      cmtCell = `${esc(it.comment) || '<span class="muted">（无）</span>'}
        <div style="color:var(--color-warning);font-size:12px;margin-top:4px">${esc(it._reason)}</div>`;
    } else if (it.ghost && it.ghost.length) {
      //   ★ 幽灵值：注释说有、数据没有的编码。这是更隐蔽的坑 ——
      //     用户按注释里的值去查，结果 0 行还以为"确实没这种数据"。
      //     也可能是历史值（老订单用过，如今没数据了），所以文案是"确认一下"，不是断言"注释错了"。
      cmtCell = `${esc(it.comment)}
        <div style="color:var(--color-danger);font-size:12px;margin-top:4px">
          ⚠️ 注释里提到的 ${it.ghost.map(g => `<code>${esc(g)}</code>`).join(" ")}
          在当前数据里查不到 —— 确认是不是写错 / 已停用（${esc(it.source == "manual" ? "你手写的" : it.source == "ai" ? "AI 生成的" : "库自带的")}）</div>`;
    }

    return `<tr>
      <td>${lvl}</td>
      <td class="mono" style="font-size:12px">${esc(it.table)}</td>
      <td>${fieldCell}</td>
      <td style="color:var(--color-text-muted);font-size:12px">${esc(it.type)}</td>
      <td class="gap-vals">${vals}</td>
      <td style="color:var(--color-text-secondary)">${cmtCell}</td>
      <td class="ops">${opsCell}</td>
    </tr>`;
  }).join("");
  box.innerHTML = `<table class="data">
      <thead><tr>
        <th style="width:70px">级别</th><th style="width:150px">表</th><th style="width:200px">字段</th>
        <th style="width:106px">类型</th><th style="width:196px">实际取值</th>
        <th>当前注释</th><th style="width:140px">操作</th>
      </tr></thead><tbody>${rows}</tbody></table>`;

  const all = document.getElementById("gapFillAllBtn");
  if (all) {
    all.style.display = "";
    all.textContent = `一键 AI 补全 ${d.items.length} 条`;
  }
}

// ═══════════════════════════════════════════════════════════
// 表级缺口：业务注释 / 业务实体
// ═══════════════════════════════════════════════════════════
/**
 * ★ 为什么这两样要单列一块，而且放在字段级缺口上面？
 *   它们比"某个字段的取值没说清"更【前置】：
 *   一张表如果连注释和实体都没有，模型根本认不出它 —— 字段补得再全也用不上。
 *   所以按"从粗到细"的顺序摆：先看哪张表还没搭好，再看哪一列要怎么补。
 */
function kbRenderTableGaps(items, s) {
  const statsBox = document.getElementById("gapTableStats");
  const box = document.getElementById("gapTableList");
  const btn = document.getElementById("gapFillTableBtn");
  if (btn) {
    btn.style.display = items.length ? "" : "none";
    btn.disabled = false;
    btn.textContent = `一键补全 ${items.length} 项`;
  }
  statsBox.innerHTML =
    kbStat("缺业务注释", s.missing_comment || 0, s.tables || 0, "检索只能靠英文表名匹配") +
    kbStat("缺业务实体", s.missing_entity || 0, s.tables || 0, "语义路由认不出业务对象");

  if (!items.length) {
    box.innerHTML = `<tbody><tr><td><div class="empty">
      没有表级缺口 —— 可见表都有业务注释和业务实体</div></td></tr></tbody>`;
    return;
  }

  const rows = items.map(it => {
    const lvl = it.severity === "danger"
      ? '<span class="status-tag danger">缺</span>'
      : '<span class="status-tag warning">不全</span>';
    //   ★ 补完之后这一行要"变脸"：显示生成了什么 + 业务俗称，
    //     否则用户只看到列表少了一行，不知道 AI 到底写了啥、该不该信
    let valCell = esc(it.current) || '<span class="muted">—</span>';
    if (it._ok) {
      valCell = `<span style="color:var(--color-success)">✅ ${esc(it._value || "")}</span>` +
        ((it._kw && it._kw.length)
          ? `<div class="muted" style="font-size:12px">业务俗称：${it._kw.map(esc).join("、")}</div>`
          : "");
    } else if (it._fail) {
      //   失败原因必须显示出来，否则用户只看到"没反应"，会以为功能坏了
      valCell = `<span style="color:var(--color-warning);font-size:12px">${esc(it._reason)}</span>`;
    }
    const ops = it._ok
      ? `<button class="ghost" onclick="gotoTableDoc('${esc(it.table)}')">去复核</button>`
      : `<button class="ghost" data-t="${esc(it.table)}" data-k="${esc(it.kind)}"
                 onclick="gapFillTableOne(this.dataset.t, this.dataset.k, this)">AI 补全</button>
         <button class="ghost" onclick="gotoTableDoc('${esc(it.table)}')">去改</button>`;
    return `<tr>
      <td>${lvl}</td>
      <td class="mono" style="font-size:12px">${esc(it.table)}</td>
      <td>${esc(it.title)}</td>
      <td style="color:var(--color-text-secondary)">${valCell}</td>
      <td class="ops">${ops}</td>
    </tr>`;
  }).join("");

  box.innerHTML = `<colgroup>
      <col style="width:56px"><col style="width:300px">
      <col style="width:130px"><col><col style="width:180px">
    </colgroup>
    <thead><tr>
      <th>级别</th><th>表</th><th>缺口</th><th>现状 / 生成结果</th><th>操作</th>
    </tr></thead><tbody>${rows}</tbody>`;
}

function gapTableBar(html, kind) {
  const box = document.getElementById("gapTableBar");
  if (!box) return;
  if (!html) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="alert ${kind || "info"}" style="margin-top:10px">${html}</div>`;
}

/** 轮询表级补全任务，返回第一条明细 */
async function gapPollTableTask(taskId) {
  for (let i = 0; i < 80; i++) {
    await new Promise(r => setTimeout(r, 1500));
    const t = await api(`/api/gaps/auto-fill/${taskId}`);
    if (t.finished) return (t.items || [])[0] || null;
  }
  return null;
}

function gapTableKindZh(kind) {
  return kind === "entity" ? "业务实体" : "业务注释";
}

/** 单条：给一张表补 业务注释 / 业务实体（生成后待复核，复核通过才进检索） */
async function gapFillTableOne(table, kind, btn) {
  if (btn) { btn.disabled = true; btn.textContent = "生成中…"; }
  gapTableBar(`⏳ 正在为「${esc(table)}」生成${gapTableKindZh(kind)}…
    <span class="muted">（要读它的字段清单再问模型，约 5~15 秒）</span>`);
  try {
    const r = await api("/api/gaps/fill-table", {
      method: "POST", body: JSON.stringify({ items: [{ table, kind }] }),
    });
    const item = await gapPollTableTask(r.task_id);
    if (item && item.ok) {
      gapTableBar(`✅ 「${esc(table)}」已生成${gapTableKindZh(kind)}：<b>${esc(item.value)}</b>` +
        ((item.keywords || []).length
          ? `（业务俗称：${item.keywords.map(esc).join("、")}）` : "") +
        `<div class="muted" style="font-size:12px;margin-top:4px">
          <b>已记为「待复核」，不进检索</b>；点「去复核」改一遍并保存即核准生效（之后 AI 不再覆盖）。</div>`);
      await kbLoadGaps(true);        // 缺口少了一项，重扫一次让列表如实反映
    } else {
      gapTableBar(`「${esc(table)}」没补上：<b>${esc((item && item.reason) || "未知原因")}</b>`, "warn");
      if (btn) { btn.disabled = false; btn.textContent = "AI 补全"; }
    }
  } catch (e) {
    gapTableBar(`生成失败：${esc(e.message)}`, "err");
    if (btn) { btn.disabled = false; btn.textContent = "AI 补全"; }
  }
}

/** 批量：把当前列出的表级缺口全部补一遍 */
async function gapAutoFillTable() {
  const items = ((gapData && gapData.table_items) || []).filter(i => !i._ok);
  if (!items.length) { alert("没有待补的表级缺口"); return; }
  if (!confirm(`将对 ${items.length} 项表级缺口生成草稿。\n` +
               `草稿先生成并标为【待复核】，不进检索；之后到「知识治理 → 待复核」逐条通过。继续？`)) return;
  const btn = document.getElementById("gapFillTableBtn");
  btn.disabled = true;
  try {
    const r = await api("/api/gaps/fill-table", {
      method: "POST",
      body: JSON.stringify({ items: items.map(i => ({ table: i.table, kind: i.kind })) }),
    });
    for (let i = 0; i < 240; i++) {
      await new Promise(x => setTimeout(x, 1500));
      const t = await api(`/api/gaps/auto-fill/${r.task_id}`);
      btn.textContent = `补全中… ${t.done}/${t.total}（成功 ${t.ok}，跳过 ${t.failed}）`;
      if (t.finished) {
        gapTableBar(`✅ 表级缺口补全完成：成功 <b>${t.ok}</b>、跳过/失败 <b>${t.failed}</b>
          <div class="muted" style="font-size:12px;margin-top:4px">
            「跳过」通常是这张表已有人工登记的实体 —— AI 不覆盖，属正常保护，
            不是失败。生成结果都<b>已标为「待复核」</b>，去「知识治理 → 待复核」通过后才进检索。</div>`);
        await kbLoadGaps(true);
        break;
      }
    }
  } catch (e) {
    gapTableBar(`启动失败：${esc(e.message)}`, "err");
  } finally {
    btn.disabled = false;
  }
}

/**
 * ═══════════ 缺口的 AI 补全 ═══════════
 *
 * ★ 为什么这一步值得做成"一键"？
 *   「知识缺口」页算出 17 条该补的字段之后，以前只能把这个清单摆给人，
 *   人得逐个去看真实取值、想含义、手打注释 —— 所以大多数缺口一直空着。
 *   而生成注释需要的【真实取值】这一页早就扫出来了，AI 拿去归纳即可，
 *   人只剩下"复核"这一件事。摩擦从"打几十个字"降到"扫一眼对不对"。
 *
 * ★ 为什么不直接生效？和表注释同一套治理逻辑：
 *     写进 column_comments(source='ai') → 落 candidate（待复核），复核通过后才进 RAG
 *     用户觉得不对去改 → 保存时 source 变 'manual' → AI 永远不会再覆盖它
 *   再加上服务端三道校验（漏取值 / 编造取值 / 空输出 一律拒绝写入），
 *   补一条假注释比留一条空注释有害得多，所以宁可不上。
 */

/** 缺口页的进度条 / 结果提示（不打断操作，跑完了给个总结） */
function gapFillBar(html, kind) {
  const box = document.getElementById("gapFillBar");
  if (!box) return;
  if (!html) { box.innerHTML = ""; return; }
  box.innerHTML = `<div class="alert ${kind || "info"}" style="margin-top:12px">${html}</div>`;
}

/** 单条：给一个字段生成 AI 注释并立刻生效 */
async function gapFillOne(table, column, btn) {
  const it = (gapData.items || []).find(x => x.table === table && x.column === column);
  //   ★ 你手写的那条，后端默认不让批量改 —— 这里单点也要先问一句，
  //     因为 AI 会在保留原意的基础上【改写措辞】、并把来源从「人工」降成「AI 生成」
  let overwrite = false;
  if (it && it.comment_source === "manual" && it.comment) {
    overwrite = confirm(
      `「${column}」的注释是你手写的：\n\n    ${it.comment}\n\n`
      + `AI 补全会在【保留这段业务含义】的基础上补上取值说明，但会重排措辞、`
      + `并把来源改成「AI 生成」。\n\n确定要改吗？（改完还是可以再手动调整）`);
    if (!overwrite) return;
  }

  btn.disabled = true;
  btn.textContent = "生成中…";
  try {
    const qs = overwrite ? "?overwrite=true" : "";
    const r = await api(`/api/kb/column/${encodeURIComponent(table)}`
                        + `/${encodeURIComponent(column)}/suggest-comment${qs}`,
                        { method: "POST" });
    if (r.ok) {
      if (it) { it._ok = true; it._comment = r.suggested; it._fail = false; }
      kbRenderGaps(gapData);
      kbLoadTables();                       // 字段注释数变了，列表上的统计要跟着更新
      gapFillBar(`✅ 已补上 <b>${esc(column)}</b>：${esc(r.suggested)}
        <div class="muted" style="font-size:12px;margin-top:4px">
          已写进字段注释并标为「待复核」（不进检索）。点「去复核」改一遍保存即核准生效 ——
          改过之后它就不会再被覆盖了。★ 点『重建索引』后检索效果最佳
        </div>`);
    } else {
      //   ★ AI 搞不定时必须把原因说出来 —— 静默无反应是最伤信任的
      if (it) { it._fail = true; it._reason = r.reason || "未知原因"; }
      kbRenderGaps(gapData);
      const adviceHint = r.advice === "dict"
        ? `这个字段取值太多，AI 硬补容易编错 —— <b>建议改成配数据字典</b>
             （「数据字典」模块可从库的字典源表导入真实编码含义）。`
        : r.advice === "manual"
        ? `这条注释你手写过，批量补全不会自动改它 —— 要改请在上面点「AI 补全」单独确认。`
        : `推断不出各取值的业务含义，只能靠你手动补 —— AI 编不了的不硬编。`;
      gapFillBar(`「${esc(column)}」没自动补：<b>${esc(r.reason)}</b>
        <div class="muted" style="font-size:12px;margin-top:4px">${adviceHint}</div>`, "warn");
    }
  } catch (e) {
    gapFillBar(`生成失败：${esc(e.message)}`, "err");
  } finally {
    btn.disabled = false;
    btn.textContent = "AI 补全";
  }
}

/** 批量：把整张缺口清单交给后台一条条补 */
async function gapAutoFillAll() {
  const items = (gapData && gapData.items) || [];
  if (!items.length) return;
  const btn = document.getElementById("gapFillAllBtn");
  //   ★ 一次 LLM 调用约 2 秒，先把预计耗时说清楚，别让人干等着以为卡死
  if (!confirm(`将用 AI 补全这 ${items.length} 个字段的取值含义。\n`
      + `预计需要 ${Math.ceil(items.length * 2)} 秒左右，期间可以切换页面做别的。\n\n`
      + `补全结果会标成「AI 生成·待复核」，不进检索；复核通过后才生效。确定开始？`)) return;

  btn.disabled = true;
  gapFillBar(`⏳ 正在后台补全 ${items.length} 个字段…（每条要读一次真实取值再问模型）`);
  try {
    const r = await api("/api/gaps/auto-fill", {
      method: "POST",
      body: JSON.stringify({ items: items.map(x => ({ table: x.table, column: x.column })) }),
    });
    gapPollFill(r.task_id, items);
  } catch (e) {
    gapFillBar(`启动失败：${esc(e.message)}`, "err");
    btn.disabled = false;
  }
}

/** 轮询批量任务进度，跑完了把结果回填到清单上 */
function gapPollFill(taskId, pending) {
  let tries = 0;
  const tick = async () => {
    tries++;
    try {
      const t = await api(`/api/gaps/auto-fill/${taskId}`);
      gapFillBar(`⏳ 正在补全… ${t.done}/${t.total}（成功 ${t.ok}，跳过 ${t.failed}）`);
      if (t.finished || tries >= 120) {
        //   把结果标回本地数据，清单上立刻呈现"哪几条补上了"
        for (const r of t.items) {
          const it = (gapData.items || []).find(x => x.table === r.table && x.column === r.column);
          if (!it) continue;
          if (r.ok) { it._ok = true; it._comment = r.comment; }
          else if (r.reason) { it._fail = true; it._reason = r.reason; }
        }
        kbRenderGaps(gapData);
        kbLoadTables();
      const involved = t.items.filter(x => !x.ok);
        const needDict = involved.filter(x => x.advice === "dict");
        gapFillBar(
          `✅ 补全完成：<b>${t.ok}</b> 条已标为待复核`
          + (involved.length ? `，${involved.length} 条 AI 没自动补` : "") + "。"
          //   ★ 取值多的字段不能就这么算了 —— 它们是【该配字典】的信号。
          //     字典是从库里的字典表导入的真实数据，不是推断来的；
          //     拿字典管多值分类，比让 AI 编一套"看着合理"的翻译可靠得多。
          + (needDict.length
              ? `<div style="margin-top:8px">其中 <b>${needDict.length}</b> 条取值过多
                   （如 <span class="mono">${esc(needDict.slice(0, 3).map(x => x.column).join("、"))}</span>），
                   AI 硬补容易编错 —— <b>建议改成配数据字典</b>：
                   「数据字典」模块可以从库里的字典源表导入真实编码含义。</div>`
              : "")
          + (involved.length > needDict.length
              ? `<div style="margin-top:6px">其余 AI 也推断不出含义，需要你手动补：
                   <span class="mono">${esc(involved.filter(x => x.advice !== "dict")
                          .map(x => x.column).join("、"))}</span></div>`
              : "")
          + `<div class="muted" style="font-size:12px;margin-top:6px">
               ★ 补上的都标为「AI 生成」：能用，但没经人核实 ——
               抽空点点「去复核」，改过的就锁定成人工版不会被覆盖。记得『重建索引』。</div>`);
        document.getElementById("gapFillAllBtn").disabled = false;
        return;
      }
    } catch (e) {
      gapFillBar(`轮询失败：${esc(e.message)}`, "err");
      document.getElementById("gapFillAllBtn").disabled = false;
      return;
    }
    setTimeout(tick, 2000);
  };
  setTimeout(tick, 2000);
}

/**
 * 从缺口清单跳到「表结构信息」并定位到那个字段
 *
 * ★ 为什么值得做这个跳转？
 *   发现缺口只是第一步，真正减少幻觉靠"把注释补上"。
 *   如果用户还得自己记住表名、切模块、搜字段，摩擦一大就没人补了。
 *   一步直达（还自动清掉字段过滤、聚焦到输入框），补一条只要几秒。
 */
async function kbGotoField(table, column) {
  gotoKbSchema();                                      // 切到「知识管理 → 表结构信息」
  await kbOpenTable(table);                            // 打开这张表
  const s = document.getElementById("kbColSearch");
  if (s) { s.value = ""; kbRenderColumns(); }          // 清掉过滤，免得目标字段被藏起来
  setTimeout(() => {
    const el = document.getElementById("kbCol_" + column);
    if (el) {
      el.scrollIntoView({ block: "center", behavior: "smooth" });
      el.focus();
    }
  }, 150);
}

