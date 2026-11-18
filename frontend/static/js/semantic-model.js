/* ══════════════════════════════════════════════════════════════
   semantic-model.js · 标准配置预览 + OSSIE 动态导入导出（融合式）
   ★ 对应 index.html 的 tab-semantic 页面。
   ★ 融合式：知识资产是唯一事实源，OSSIE 是派生视图 —— 本页只做「预览 + 导入导出」，
     不再维护独立的语义模型快照。
   ★ 依赖全局：api()、esc()、getToken()、handleUnauthorized()。
   ══════════════════════════════════════════════════════════════ */

let smPreviewContent = null;   // 导入预览：文件原文（确认导入时复用）

/** 页面懒加载入口：拉 OSSIE 版本 + 初始化语义建模三 Tab */
async function smLoadPage() {
  try {
    const info = await api("/api/semantic-models/ossie/info");
    const el = document.getElementById("smOssieVersion");
    if (el) el.textContent = `${info.version}（commit ${(info.commit || "").slice(0, 8)}）`;
  } catch (e) { /* 版本号拉不到不阻塞 */ }
  // ★ 语义建模三 Tab（关系模型 / 指标 / 维度）初始化
  if (typeof smModelLoad === "function") await smModelLoad();
  // OSSIE 预览懒加载：默认停在「关系模型」Tab，切到 OSSIE 才拉预览
  const ossiePane = document.getElementById("smPane-ossie");
  if (ossiePane && ossiePane.classList.contains("active")) await smRefresh();
}

/** 刷新标准配置预览（从后端动态组装 OSSIE YAML） */
async function smRefresh() {
  const box = document.getElementById("smPreview");
  if (!box) return;
  box.textContent = "加载中…";
  try {
    const token = getToken();
    const res = await fetch("/api/semantic/export?format=yaml",
      { headers: token ? { Authorization: "Bearer " + token } : {} });
    if (res.status === 401) { handleUnauthorized("登录已过期，请重新登录"); throw new Error("登录已过期"); }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      box.textContent = "（当前无标准配置可预览）\n" + detail;
      return;
    }
    box.textContent = await res.text();
  } catch (e) {
    box.textContent = "预览加载失败：" + e.message;
  }
}

/** 导出 OSSIE 文件（动态组装，yaml/json） */
async function smExport(format) {
  try {
    const token = getToken();
    const res = await fetch(`/api/semantic/export?format=${encodeURIComponent(format)}`,
      { headers: token ? { Authorization: "Bearer " + token } : {} });
    if (res.status === 401) { handleUnauthorized("登录已过期，请重新登录"); throw new Error("登录已过期"); }
    if (!res.ok) {
      let detail = res.statusText;
      try { detail = (await res.json()).detail || detail; } catch (e) {}
      throw new Error(detail);
    }
    const blob = await res.blob();
    const cd = res.headers.get("Content-Disposition") || "";
    let filename = `semantic.${format}`;
    const mm = /filename="([^"]+)"/.exec(cd);
    if (mm) filename = mm[1];
    const url = URL.createObjectURL(blob);
    const a = document.createElement("a");
    a.href = url; a.download = filename;
    document.body.appendChild(a); a.click(); a.remove();
    setTimeout(() => URL.revokeObjectURL(url), 1500);
  } catch (e) { alert("导出失败：" + e.message); }
}

/** 导入：读取文件 → 后端校验 → 写回知识库（source=ai，待审核） */
async function smPreviewImport() {
  const fileInput = document.getElementById("smImportFile");
  const box = document.getElementById("smImportPreview");
  if (!fileInput || !fileInput.files || !fileInput.files.length) return;
  const file = fileInput.files[0];
  const text = await file.text();
  box.innerHTML = `<div class="empty">校验中…</div>`;
  try {
    const r = await api("/api/semantic/import", {
      method: "POST",
      body: JSON.stringify({ content: text }),
    });
    const res = r.result || {};
    const skipped = (r.result?.skipped_names || []).length;
    box.innerHTML = `
      <div class="alert info" style="margin:0">
        ✅ 导入成功：新增 ${res.datasets?.created ?? 0} 实体、${res.metrics?.created ?? 0} 指标、
        ${res.relationships?.created ?? 0} 关系；跳过 ${skipped} 个已存在对象。
        <br>导入对象为<b>「待审核」</b>，请到「知识治理 → 待复核」人工通过后生效。
      </div>`;
    fileInput.value = "";
    await smRefresh();
  } catch (e) {
    box.innerHTML = `<div class="alert warn" style="margin:0">导入失败：${esc(e.message)}</div>`;
  }
}
