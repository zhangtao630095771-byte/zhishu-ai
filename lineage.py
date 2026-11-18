# -*- coding: utf-8 -*-
"""
lineage.py —— 逻辑视图（统一语义资产血缘，只读）

★ 定位（语义中心重构 · 阶段四核心）：
  把「业务域 → 主题(关系模型) → 实体 → 物理表 → 字段」与「指标 / 维度 / 关系」
  的血缘关系可视化，供查看与分析。**只读**，不重复实现任何编辑功能。

★ 铁律：
  ① 全部只读派生，不写任何表、不缓存快照（实时反映知识库）；
  ② 血缘关系来自现有引用（topic_assets / relationships / metrics.tables / column_comments），
     不做模糊推断；
  ③ 冲突/缺失提醒是「体检报告」，只提示不自动改。
"""
import sqlite3
import config_store


def _connect():
    config_store._ensure_ready()
    return config_store._connect()


def _rows(sql, args=()):
    conn = _connect()
    conn.row_factory = sqlite3.Row
    cur = conn.cursor()
    rows = cur.execute(sql, args).fetchall()
    conn.close()
    return [dict(r) for r in rows]


# ═══════════════════════════════════════════════════════════
# ① 全局概览
# ═══════════════════════════════════════════════════════════
def get_overview():
    domains = config_store.list_business_domains()
    subjects = config_store.list_business_subjects()
    entities = config_store.get_entities()
    metrics = config_store.get_metrics(enabled_only=False)
    rels = config_store.get_relationships(enabled_only=False)
    dims = _rows("SELECT * FROM semantic_dimensions")
    # 物理表 = 实体主表去重
    tables = sorted({e.get("table_name") for e in entities if e.get("table_name")})
    col_comments = _rows("SELECT COUNT(*) AS c FROM column_comments")
    conflicts = get_conflicts()
    return {
        "domains": len(domains),
        "subjects": len(subjects),          # = 关系模型数
        "entities": len(entities),
        "metrics": len(metrics),
        "dimensions": len(dims),
        "relationships": len(rels),
        "tables": len(tables),
        "column_comments": (col_comments[0]["c"] if col_comments else 0),
        "conflicts": len(conflicts),
    }


# ═══════════════════════════════════════════════════════════
# ② 全局语义关系图（分层：域 → 主题 → 实体 → 物理表）
# ═══════════════════════════════════════════════════════════
def get_global_graph():
    domains = config_store.list_business_domains()
    subjects = config_store.list_business_subjects()
    entities = config_store.get_entities()

    # 实体名 → 物理表
    ent_tbl = {e["name"]: (e.get("table_name") or "") for e in entities}
    # 主题 → 关联实体（topic_assets scope=subject, asset_type=entity）
    subject_entities = {}
    for s in subjects:
        assets = config_store.list_scope_assets(s["name"], scope="subject")
        subject_entities[s["name"]] = [a["asset_key"] for a in assets if a["asset_type"] == "entity"]
    # 实体 → 归属主题（反查）
    ent_subjects = {}
    for s in subjects:
        for en in subject_entities.get(s["name"], []):
            ent_subjects.setdefault(en, []).append(s["name"])

    # 节点分层
    domain_nodes = [{"id": f"domain:{d['name']}", "label": d.get("display_name") or d["name"],
                     "kind": "domain", "name": d["name"]} for d in domains]
    subject_nodes = [{"id": f"subject:{s['name']}", "label": s.get("display_name") or s["name"],
                      "kind": "subject", "name": s["name"]} for s in subjects]
    entity_nodes = [{"id": f"entity:{e['name']}", "label": e["name"],
                     "kind": "entity", "name": e["name"]} for e in entities]
    tbl_names = sorted({t for t in ent_tbl.values() if t})
    table_nodes = [{"id": f"table:{t}", "label": t, "kind": "table", "name": t} for t in tbl_names]

    # 边
    edges = []
    # 域 → 主题
    for s in subjects:
        if s.get("domain_name"):
            edges.append({"from": f"domain:{s['domain_name']}", "to": f"subject:{s['name']}", "kind": "contains"})
    # 主题 → 实体
    for s in subjects:
        for en in subject_entities.get(s["name"], []):
            edges.append({"from": f"subject:{s['name']}", "to": f"entity:{en}", "kind": "contains"})
    # 实体 → 物理表
    for en, tbl in ent_tbl.items():
        if tbl:
            edges.append({"from": f"entity:{en}", "to": f"table:{tbl}", "kind": "maps_to"})

    return {
        "nodes": domain_nodes + subject_nodes + entity_nodes + table_nodes,
        "edges": edges,
        "kinds": {"domain": len(domain_nodes), "subject": len(subject_nodes),
                  "entity": len(entity_nodes), "table": len(table_nodes)},
    }


# ═══════════════════════════════════════════════════════════
# ③ 单个实体上下游
# ═══════════════════════════════════════════════════════════
def get_entity_lineage(name):
    entities = config_store.get_entities_with_governance()
    ent = next((e for e in entities if e["name"] == name), None)
    if not ent:
        return None
    tbl = ent.get("table_name") or ""
    pk = ent.get("primary_key") or []

    # 上游：归属主题 + 域
    subj_map = config_store.get_subjects_by_assets([name]).get(name, [])
    domain_map = config_store.get_subject_domain_map(subj_map)
    upstream = [{"subject": s, "domain": domain_map.get(s, "")} for s in subj_map]

    # 下游：物理表字段（属性）
    fields = _rows(
        "SELECT column_name, comment, dimension_type, status FROM column_comments "
        "WHERE table_name = ? ORDER BY column_name", (tbl,)) if tbl else []

    # 同层：关系（端点命中本实体）
    rels = [r for r in config_store.get_relationships(enabled_only=False)
            if r.get("from_entity") == name or r.get("to_entity") == name]

    # 关联指标（依赖该物理表）
    metrics = []
    for m in config_store.get_metrics(enabled_only=False):
        if tbl and tbl in (m.get("tables") or []):
            metrics.append({"name": m["name"], "metric_type": m.get("metric_type") or "",
                            "definition": m.get("definition") or ""})

    # 关联维度（entity_key = name）
    dims = _rows("SELECT * FROM semantic_dimensions WHERE entity_key = ?", (name,))

    return {
        "entity": ent,
        "upstream": upstream,
        "fields": fields,
        "relations": rels,
        "metrics": metrics,
        "dimensions": dims,
    }


# ═══════════════════════════════════════════════════════════
# ④ 指标引用关系
# ═══════════════════════════════════════════════════════════
def get_metric_lineage(name):
    metrics = config_store.get_metrics_with_governance()
    m = next((x for x in metrics if x["name"] == name), None)
    if not m:
        return None
    tables = m.get("tables") or []

    # 归属主题 + 域
    subj_map = config_store.get_subjects_by_assets([name]).get(name, [])
    domain_map = config_store.get_subject_domain_map(subj_map)

    # 依赖物理表 → 对应实体
    ent_by_table = {e.get("table_name"): e["name"] for e in config_store.get_entities()
                    if e.get("table_name")}
    table_refs = [{"table": t, "entity": ent_by_table.get(t, "")} for t in tables]

    # 关联维度（维度所在表 ∈ 指标依赖表）
    dims = [d for d in _rows("SELECT * FROM semantic_dimensions")
            if d.get("source_table") in set(tables)]

    return {
        "metric": m,
        "subjects": [{"subject": s, "domain": domain_map.get(s, "")} for s in subj_map],
        "tables": table_refs,
        "dimensions": dims,
    }


# ═══════════════════════════════════════════════════════════
# ⑤ 缺失关系 / 冲突提醒（体检报告，只提示不自动改）
# ═══════════════════════════════════════════════════════════
def get_conflicts():
    issues = []
    entities = config_store.get_entities_with_governance()
    metrics = config_store.get_metrics(enabled_only=False)
    rels = config_store.get_relationships(enabled_only=False)
    ent_names = {e["name"] for e in entities}
    ent_tables = {e.get("table_name") for e in entities if e.get("table_name")}

    # 1) 实体无主表
    for e in entities:
        if not e.get("table_name"):
            issues.append({"type": "entity_no_table", "level": "warn",
                           "title": f"实体「{e['name']}」未登记物理表",
                           "detail": "该实体无法映射到物理表，取数时无法注入表结构。"})

    # 2) 实体主表无字段注释
    tbl_with_comments = {r["table_name"] for r in
                         _rows("SELECT DISTINCT table_name FROM column_comments")}
    for e in entities:
        t = e.get("table_name")
        if t and t not in tbl_with_comments:
            issues.append({"type": "table_no_comment", "level": "info",
                           "title": f"物理表「{t}」无字段注释",
                           "detail": f"实体「{e['name']}」的主表缺少字段注释，模型可能误解字段含义。"})

    # 3) 指标无定义（纯文本口径缺失）
    for m in metrics:
        if not (m.get("definition") or "").strip():
            issues.append({"type": "metric_no_definition", "level": "warn",
                           "title": f"指标「{m['name']}」无口径定义",
                           "detail": "指标缺少权威口径，模型只能靠字段名猜测计算方式。"})

    # 4) 关系端点实体不存在
    for r in rels:
        fe, te = r.get("from_entity"), r.get("to_entity")
        missing = [x for x in (fe, te) if x and x not in ent_names]
        if missing:
            issues.append({"type": "relation_dangling", "level": "error",
                           "title": f"关系 {fe} → {te} 端点实体不存在：{', '.join(missing)}",
                           "detail": "关系的端点实体未登记，该 Join 规则将无法被命中。"})

    # 5) 指标依赖表未登记为实体
    for m in metrics:
        for t in (m.get("tables") or []):
            if t and t not in ent_tables:
                issues.append({"type": "metric_table_unregistered", "level": "info",
                               "title": f"指标「{m['name']}」依赖的表「{t}」未登记为实体",
                               "detail": "该表不是任何实体的主表，建议登记实体或修正指标来源表。"})

    return issues
