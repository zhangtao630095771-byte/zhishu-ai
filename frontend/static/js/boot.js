/* ══════════════════════════════════════════════════════════════
   boot.js · 重建索引 · 页面启动
   由 index.html 内联脚本按行拆分而来，代码逐字节未改动。
   加载顺序见 index.html 末尾的 <script> 列表，勿随意调换。
   ══════════════════════════════════════════════════════════════ */
// ═══════════════════════════════════════════════════════════
// 重建索引
// ═══════════════════════════════════════════════════════════
async function rebuildIndex() {
  if (!confirm("重建索引会重新加载模型并重算向量，约需 5~15 秒。继续？")) return;
  document.getElementById("statusBar").textContent = "正在重建索引…";
  try {
    const r = await api("/api/index/rebuild", { method: "POST" });
    alert(`索引已重建：${r.tables} 张可见表 + ${r.metrics} 个指标`);
    loadStatus();
  } catch (e) {
    alert("重建失败：" + e.message);
    loadStatus();
  }
}

// ═══════════════════════════════════════════════════════════
// 页面加载完成时：先确认登录态，再决定进登录页还是主界面
// ═══════════════════════════════════════════════════════════
// 恢复上次「最近」面板的收起状态（收起后不占地方，是常用偏好）
try {
  if (localStorage.getItem("zhishu_hist_collapsed") === "1") {
    document.getElementById("histPanel").classList.add("collapsed");
    document.getElementById("histToggle").textContent = "»";
  }
} catch (e) {}

bootstrapAuth();
