# -*- coding: utf-8 -*-
"""
_seed_order_semantic_model.py —— 订单主题试点：从真实知识表组装语义模型（幂等）

★ 用途（任务书 §9.1 订单主题试点）：
  从现有 config_store 的【真实】实体 / 指标 / 关系里，提取「订单」业务主题，
  组装成一个语义模型写入 semantic_models（默认 candidate，需人工审核 approve）。
  不编造任何数据 —— 所有表名 / 字段名 / 指标口径 / 关联关系均来自现有知识库。

★ 幂等：若已存在同名模型（任意状态），跳过不覆盖（避免反复 +1 版本）。

★ 运行：.venv/bin/python _seed_order_semantic_model.py
  产物：semantic_models 里一条「订单主题」模型（candidate）。
  之后：到「语义模型」页点「审核通过」，再开 SEMANTIC_MODEL_ENABLED=1 做对照评测。

★ 提取规则（可读、可回滚）：
  ① 实体：name 或 主表 含「订单/order」→ dataset（含该表的人工字段注释）
  ② 指标：依赖表与上述实体主表有交集 → metric
  ③ 关系：from/to 端点命中上述实体 → relationship
"""
import config_store
import db


# ── 时间字段启发式（拿不到真实类型时兜底）──
_TIME_HINTS = ("time", "date", "ctime", "dtime", "atime", "day", "month", "year")


def _is_time(field_name, dtype=""):
    """判断字段是否为时间维度：真实类型优先，否则按字段名启发式。"""
    d = (dtype or "").lower()
    if any(k in d for k in ("date", "datetime", "timestamp", "time")):
        return True
    f = (field_name or "").lower()
    return any(k in f for k in _TIME_HINTS)


def _order_entities():
    """提取订单相关【启用】实体（停用的冗余实体不纳入）。"""
    out = []
    for e in config_store.get_entities():
        if not e.get("enabled", True):          # 停用实体（去重冗余）跳过
            continue
        name = e.get("name") or ""
        table = e.get("table_name") or ""
        if "订单" in name or "order" in table.lower():
            out.append(e)
    return out


def _columns_of(table_name):
    """拿一张表的字段清单 [{name,type,comment}]：真实类型优先，失败用启发式。"""
    cols = {}
    # 字段注释（人工维护，{"表.字段": 注释}）
    comments = config_store.get_column_comments(table_name) or {}
    # 真实字段类型（连库成功才有）
    dtypes = {}
    try:
        for col, dtype in db.list_columns(table_name):
            dtypes[col] = dtype
    except Exception:
        pass
    # 以人工注释的字段为准（这些才是"喂给模型"的字段），类型缺失就用空串
    for key, comment in comments.items():
        col = key.rsplit(".", 1)[-1]
        cols[col] = {"name": col, "type": dtypes.get(col, ""), "comment": comment or ""}
    # 若人工注释为空，退回数据库字段清单（至少字段名全）
    if not cols and dtypes:
        for col, dtype in dtypes.items():
            cols[col] = {"name": col, "type": dtype, "comment": ""}
    return list(cols.values())


def collect():
    """组装「订单主题」内部语义模型。"""
    entities = _order_entities()
    if not entities:
        return None

    # ★ 关系端点补全：关系的 from/to 必须是已声明的 dataset（OSSIE 引用完整性）。
    #   订单关系（如「代驾订单→代驾费用」）的端点「代驾费用」不是订单实体，
    #   但它是关系的另一端，必须一并纳入 dataset，否则导出的模型引用不完整。
    entity_names = {e.get("name") for e in entities if e.get("name")}
    all_rel = config_store.get_relationships(enabled_only=False)
    rel_endpoints = set()
    for r in all_rel:
        fe, te = r.get("from_entity"), r.get("to_entity")
        if fe in entity_names or te in entity_names:
            rel_endpoints.add(fe)
            rel_endpoints.add(te)
    # 端点实体若存在于知识库且未纳入，补进来
    all_entities = {e.get("name"): e for e in config_store.get_entities()
                    if e.get("enabled", True)}
    for ep in rel_endpoints:
        if ep and ep not in entity_names and ep in all_entities:
            entities.append(all_entities[ep])
            entity_names.add(ep)

    tables = {e.get("table_name") for e in entities if e.get("table_name")}

    # ① datasets（实体 → dataset + 字段）
    datasets = []
    for e in entities:
        t = e.get("table_name") or ""
        cols = _columns_of(t) if t else []
        fields = [{"name": c["name"], "type": c["type"], "comment": c["comment"],
                   "source": "manual", "is_time": _is_time(c["name"], c["type"])}
                  for c in cols]
        datasets.append({
            "name": e.get("name"),
            "source": t,
            "description": e.get("comment") or "",
            "grain": e.get("grain") or "",
            "synonyms": e.get("keywords") or [],
            "fields": fields,
            "extensions": {"knowledge_level": e.get("knowledge_level"),
                           "status": e.get("status"), "source": e.get("source")},
        })

    # ② metrics（依赖表命中订单实体主表的指标）
    metrics = []
    for m in config_store.get_metrics(enabled_only=False):
        mtables = set(m.get("tables") or [])
        if mtables & tables:
            metrics.append({
                "name": m.get("name"),
                "definition": m.get("definition") or "",
                "sql_template": m.get("sql_template") or "",
                "keywords": m.get("keywords") or [],
                "metric_type": m.get("metric_type") or "",
                "tables": m.get("tables") or [],
                "extensions": {"knowledge_level": m.get("knowledge_level"),
                               "status": m.get("status"), "source": m.get("source"),
                               "metric_type": m.get("metric_type"),
                               "tables": m.get("tables")},
            })

    # ③ relationships（端点命中订单实体的关系）
    #   ★ 端点实体仍缺失的关系（如「大板订单」实体已被改名/停用）跳过，并打印提示，
    #     保证导出的模型引用完整 —— 这也是引用完整性校验的价值：暴露既有数据问题。
    relationships = []
    skipped_rels = []
    for r in config_store.get_relationships(enabled_only=False):
        fe, te = r.get("from_entity"), r.get("to_entity")
        if fe not in entity_names or te not in entity_names:
            skipped_rels.append(f"{fe}->{te}（缺端点实体）")
            continue
        relationships.append({
            "name": f"{fe}->{te}",
            "from_entity": fe, "to_entity": te,
            "join_sql": r.get("join_sql") or "",
            "cardinality": r.get("cardinality") or "",
            "pre_agg_required": r.get("pre_agg_required") or 0,
            "rule_note": r.get("rule_note") or "",
            "extensions": {"cardinality": r.get("cardinality"),
                           "join_sql": r.get("join_sql"),
                           "pre_agg_required": r.get("pre_agg_required")},
        })
    if skipped_rels:
        print(f"⚠️ 跳过关系（端点实体缺失，属既有数据不一致，建议在「语义注册表」补实体）：{', '.join(skipped_rels)}")

    return {
        "name": "订单主题",
        "description": "订单业务主题语义模型（由真实知识表自动组装，供 OSSIE 导入导出与语义接入评测）",
        "datasets": datasets,
        "metrics": metrics,
        "relationships": relationships,
        "status": "candidate",               # ★ 顶层 status：AI/自动组装 → candidate（待审核）
        "source": "seed",
        "model_extensions": {"knowledge_level": 1, "source": "seed"},
    }


def main():
    model = collect()
    if not model:
        print("未提取到订单相关实体，跳过。")
        return 0

    # 幂等：已存在同名模型则跳过（避免反复 +1 版本）
    existing = config_store.get_semantic_model_by_name(model["name"])
    if existing:
        print(f"已存在模型「{model['name']}」（id={existing['id']}，状态={existing['status']}），跳过不覆盖。")
        return 0

    m = config_store.upsert_semantic_model(model, operator="seed", note="订单主题试点（真实知识表自动组装）")
    print(f"已创建模型「{model['name']}」（id={m['id']}，状态={m['status']}）")
    print(f"  datasets={m['counts']['datasets']}  metrics={m['counts']['metrics']}  relationships={m['counts']['relationships']}")
    print("★ 请到「语义模型」页点「审核通过」，再开 SEMANTIC_MODEL_ENABLED=1 做对照评测。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
