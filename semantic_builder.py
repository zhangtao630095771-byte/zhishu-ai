# -*- coding: utf-8 -*-
"""
semantic_builder.py —— Semantic Context Builder（统一语义上下文的构建 + 审计）。

★ 定位（增量，不重构）：
  现有组装逻辑已收敛在 `agent._build_system_prompt`（唯一位置）+ `semantic_layer.resolve`
  / `build_prompt_parts`，行为等价、有测试守护，本模块【不重写】它们。
  本模块只做一件它们没做的事：用「统一语义注册表 semantic_registry」对本次命中的
  语义上下文做【治理状态审计】——确认命中知识是否都 approved、是否混入 candidate /
  停用对象。这是把注册表「用起来」的第一处接入，且纯只读、只观测、不阻断取数。

★ 为什么只做审计、不做「替代组装」？
  组装顺序（role→P0→P1→P2→P3→P4→字典→硬约束→经验）由字节序保证、有测试守着，
  擅自搬到新模块只会引入「搬一半」的静默回归。审计是纯新增能力，零破坏。

★ 未来扩展（不在此处实现）：
  新增 dimension / concept 维度时，在 resolve 之后、组装之前插入「注册表补维」，
  使 Builder 真正成为「分层检索 → 注册表补维 → 冲突解决 → 组装」的完整入口。
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CONFIG_DB") or os.path.join(BASE_DIR, "agent_config.db")

# ctx 字段 → stable_key 前缀 的映射（与 _migrate_governance.py / _seed_semantic_registry.py 对齐）
_LEVEL_OF = {
    "metric": 1, "entity": 2, "relationship": 2,
    "table_comment": 3, "column_comment": 3, "dict": 3, "kb_doc": 4,
}


def _collect_keys(ctx):
    """从 SemanticContext 提取命中的知识对象的 stable_key → (entity_type, display)。

    返回 {stable_key: entity_type}；找不到对应 stable_key 前缀的对象不参与审计
    （如 p3 表没有人工注释时，table:{name} 在注册表里本来就不存在）。
    """
    keys = {}
    for m in (getattr(ctx, "p1_metrics", None) or []):
        if m.get("name"):
            keys["metric:%s" % m["name"]] = "metric"
    for e in (getattr(ctx, "p2_entities", None) or []):
        if e.get("name"):
            keys["entity:%s" % e["name"]] = "entity"
    for r in (getattr(ctx, "p2_relationships", None) or []):
        if r.get("id") is not None:
            keys["rel:%s" % r["id"]] = "relationship"
    for t in (getattr(ctx, "p3_tables", None) or []):
        keys["table:%s" % t] = "table_comment"
    for d in (getattr(ctx, "p4_docs", None) or []):
        if d.get("id") is not None:
            keys["doc:%s" % d["id"]] = "kb_doc"
    return keys


def _query_registry(keys):
    """按 stable_key 批量查注册表，返回 {stable_key: {status, enabled, entity_type, level}}。"""
    if not keys:
        return {}
    con = sqlite3.connect(DB_PATH)
    try:
        q = ",".join("?" for _ in keys)
        rows = con.execute(
            "SELECT stable_key, entity_type, knowledge_level, status, enabled "
            "FROM semantic_registry WHERE stable_key IN (%s)" % q, list(keys)
        ).fetchall()
    finally:
        con.close()
    return {
        r[0]: {"entity_type": r[1], "level": r[2], "status": (r[3] or ""), "enabled": bool(r[4])}
        for r in rows
    }


def audit_context(ctx):
    """审计本次命中的语义上下文（只读、不阻断）。

    返回 dict：
      total_hits     命中的知识对象数（有 stable_key 前缀的）
      registered     能在注册表里找到的
      unregistered   注册表里没有的 stable_key（如无人工注释的表）——不算异常
      by_status      {status: 计数}
      by_level       {P1..P4: 计数}
      suspicious     [{key, type, status, enabled}]：命中了却非 approved / 已停用的知识
                     —— 严格模式下这些本不该混入，出现即提示过滤有漏洞或当前为非严格模式。
    """
    keys = _collect_keys(ctx)
    if not keys:
        return {"total_hits": 0, "registered": 0, "unregistered": [],
                "by_status": {}, "by_level": {}, "suspicious": []}

    reg = _query_registry(keys)
    by_status = {}
    by_level = {}
    suspicious = []
    unregistered = []

    for key, etype in keys.items():
        row = reg.get(key)
        if row is None:
            unregistered.append(key)
            continue
        st = row["status"] or "(空)"
        by_status[st] = by_status.get(st, 0) + 1
        lv = row["level"]
        by_level[lv] = by_level.get(lv, 0) + 1
        # 非 approved / 已停用 → 可疑（严格模式下不该混入）
        if st != "approved" or not row["enabled"]:
            suspicious.append({
                "key": key, "type": etype, "status": st,
                "enabled": row["enabled"],
            })

    return {
        "total_hits": len(keys),
        "registered": len(keys) - len(unregistered),
        "unregistered": unregistered,
        "by_status": by_status,
        "by_level": by_level,
        "suspicious": suspicious,
    }


def audit_summary(audit):
    """把审计结果转成一段可拼进 info 的短字符串（无异常时尽量精简，避免刷屏）。"""
    if not audit:
        return ""
    if audit.get("total_hits") == 0:
        return ""
    parts = []
    if audit.get("suspicious"):
        parts.append("⚠️审计:%d条非approved/停用命中" % len(audit["suspicious"]))
    elif audit.get("registered"):
        parts.append("审计✓%d/%d知识已核准" % (audit["registered"], audit["total_hits"]))
    return (" | " + " ".join(parts)) if parts else ""
