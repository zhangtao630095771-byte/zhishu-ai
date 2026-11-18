/* ══════════════════════════════════════════════════════════════
   metrics.js · 指标信息（业务口径）
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// ═══════════════════════════════════════════════════════════
let metricCache = {};
let currentMetricName = null;

async function loadMetrics() {
  const list = await api("/api/metrics");
  metricCache = {};
  const box = document.getElementById("metricList");
  box.innerHTML = "";
  list.forEach(m => {
    metricCache[m.name] = m;
    const div = document.createElement("div");
    div.className = "list-item";
    // 停用的指标在左侧列表里标个红点，一眼能看出来
    div.innerHTML = `<span>${m.enabled ? "" : "🔴 "}${esc(m.name)}</span>`;
    div.onclick = () => selectMetric(m.name, div);
    box.appendChild(div);
  });
  if (list.length) selectMetric(list[0].name, box.firstChild);
}

function selectMetric(name, el) {
  currentMetricName = name;
  document.querySelectorAll("#metricList .list-item").forEach(e => e.classList.remove("active"));
  if (el) el.classList.add("active");
  const m = metricCache[name];
  document.getElementById("metricTitle").textContent = name;
  document.getElementById("mName").value = m.name;
  document.getElementById("mKeywords").value = (m.keywords || []).join(",");
  document.getElementById("mDefinition").value = m.definition || "";
  document.getElementById("mExpression").value = m.expression || "";
  document.getElementById("mDoc").value = m.doc || "";
  document.getElementById("mTables").value = (m.tables || []).join(",");
  renderMetricStatus(m);
}

/** 把开放状态渲染到卡片上 */
function renderMetricStatus(m) {
  document.getElementById("mStatus").textContent = m.enabled
    ? "🟢 已开放 —— AI 可以使用这个指标"
    : "🔴 已停用 —— AI 看不到；用户问到会被直接拒绝";
  document.getElementById("mToggleBtn").textContent = m.enabled ? "停用" : "开放";
}

/** 切换指标的开放 / 停用 */
async function toggleMetricEnabled() {
  if (!currentMetricName) { alert("请先从左侧选择一个指标"); return; }
  const m = metricCache[currentMetricName];
  const next = !m.enabled;
  const r = await api(`/api/metrics/${encodeURIComponent(currentMetricName)}/enabled`, {
    method: "PUT",
    body: JSON.stringify({ enabled: next }),
  });
  m.enabled = next;
  renderMetricStatus(m);
  loadMetrics();       // 刷新左侧列表的红点标记
  alert(r.message);
}

function newMetric() {
  currentMetricName = null;
  document.querySelectorAll("#metricList .list-item").forEach(e => e.classList.remove("active"));
  document.getElementById("metricTitle").textContent = "新增指标";
  ["mName", "mKeywords", "mDefinition", "mExpression", "mDoc", "mTables"].forEach(id => document.getElementById(id).value = "");
}

async function saveMetric() {
  const name = document.getElementById("mName").value.trim();
  if (!name) { alert("指标名不能为空"); return; }
  // 逗号分隔的字符串 → 数组（顺便去掉空白项）
  const split = s => s.split(",").map(x => x.trim()).filter(Boolean);
  const body = {
    keywords: split(document.getElementById("mKeywords").value),
    definition: document.getElementById("mDefinition").value,
    expression: document.getElementById("mExpression").value,
    doc: document.getElementById("mDoc").value,
    tables: split(document.getElementById("mTables").value),
  };
  const r = await api(`/api/metrics/${encodeURIComponent(name)}`, {
    method: "PUT", body: JSON.stringify(body),
  });
  alert(r.message);
  currentMetricName = name;
  loadMetrics();
}

async function deleteMetric() {
  if (!currentMetricName) { alert("请先从左侧选择要删除的指标"); return; }
  if (!confirm(`确定删除指标「${currentMetricName}」？`)) return;
  await api(`/api/metrics/${encodeURIComponent(currentMetricName)}`, { method: "DELETE" });
  alert("已删除");
  currentMetricName = null;
  loadMetrics();
}

