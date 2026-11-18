/* ══════════════════════════════════════════════════════════════
   core/state.js · 统一前端状态 store（P0：刷新恢复 + 状态收口）

   ★ 背景：此前 lastQueryContextId / lastAnswer 等散落在各文件顶层 let，
     页面一刷新就丢 —— "当前窗口增量修改"的锚点（query_context_id）丢失后，
     用户刷新后无法继续上一轮查询，且与后端已落库的 query_context 脱节。

   ★ 职责边界：本模块【只做存储】——把"最近一次取数上下文"持久化到
     sessionStorage（关标签页即清），并提供 save/restore/clear 三个动作；
     业务侧的"何时保存 / 何时恢复"由 ask.js / core.js 各自触发，本文件不越界。

   ★ 为什么用 sessionStorage 而不是 localStorage：
     上轮查询上下文是"本次会话"的临时工作台，跨会话（甚至跨天）恢复意义不大，
     还可能恢复到一个后端早已失效的 query_context_id；sessionStorage 生命周期刚好。
   ══════════════════════════════════════════════════════════════ */
window.AppState = (function () {
  const KEY = "zhishu_last_query";     // sessionStorage 键
  const MAX_AGE = 3600 * 1000;         // 1 小时过期（过久的上轮上下文多半已失效）

  /** 保存最近一次取数上下文（在取数成功后调用） */
  function save(queryContextId, question) {
    try {
      sessionStorage.setItem(KEY, JSON.stringify({
        queryContextId: queryContextId || null,
        question: question || "",
        at: Date.now(),
      }));
    } catch (e) { /* 隐私模式 / 超限时静默失败，绝不影响主流程 */ }
  }

  /** 读回上下文；无 / 过期返回 null */
  function restore() {
    try {
      const raw = sessionStorage.getItem(KEY);
      if (!raw) return null;
      const d = JSON.parse(raw);
      if (!d || Date.now() - (d.at || 0) > MAX_AGE) {
        sessionStorage.removeItem(KEY);
        return null;
      }
      return d;
    } catch (e) { return null; }
  }

  /** 清除（新开一个全新查询时调用） */
  function clear() {
    try { sessionStorage.removeItem(KEY); } catch (e) {}
  }

  return { save, restore, clear };
})();
