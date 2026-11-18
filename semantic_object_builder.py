# -*- coding: utf-8 -*-
"""
semantic_object_builder.py —— 统一语义对象组装层（融合式改造 · 第二阶段核心）

★ 定位（融合式）：把【现有 7 张知识表】动态映射为 OSSIE 标准对象。
  铁律：知识表是唯一事实源，OSSIE 对象是【派生视图】，绝不重复存储。
  本模块【只读】知识表，实时组装，不落库、不写任何数据。

★ 与 ossie_adapter.py 的分工：
  ossie_adapter     —— OSSIE 结构校验 / YAML-JSON 序列化 / 扩展属性打包（与数据来源无关）
  semantic_object_builder —— 从知识表【实时读取】并组装成 OSSIE 对象（数据来源唯一在此）

★ 核心能力：
  ① 指标 expression 提取（definition → SQL 表达式，三分类）
  ② 字段维度识别（时间/地域/状态/类型/渠道）
  ③ 动态组装完整 Semantic Model（Dataset/Field/Metric/Dimension/Relationship/AI Context）
"""
import json
import re

import config_store


# ═══════════════════════════════════════════════════════════
# ① 指标 expression 提取（决策 5：提取 + 人工复核，不自动视为权威口径）
# ═══════════════════════════════════════════════════════════
_SQL_FUNC_START = ('COUNT(', 'SUM(', 'COALESCE(', 'COUNT(DISTINCT', 'COUNT (DISTINCT',
                   'AVG(', 'MAX(', 'MIN(', 'ROUND(', 'UNIQ', 'COUNTIF', 'COUNTDISTINCT')

# 提取状态 → 审核建议
EXTRACT_STATUS = {
    "extracted": "candidate",   # 已提取，待人工复核确认
    "ambiguous": "supplement",  # 含 SQL 片段但无法干净提取，待人工补 SQL
    "missing": "text_only",     # 纯文本口径，保留 description，不生成 expression
}


def _strip_star_notes(text):
    """剥离 ★ 开头的注释行与行尾 ★ 注释（口径说明，非 SQL 正文）。"""
    lines = []
    for line in (text or "").split("\n"):
        idx = line.find("★")
        if idx >= 0:
            line = line[:idx]
        line = line.strip()
        if line:
            lines.append(line)
    return "\n".join(lines)


def extract_metric_expression(definition):
    """从指标 definition 提取 SQL expression。

    返回 (expression, status, note)：
      status = extracted（可提取）/ ambiguous（含片段需人工）/ missing（纯文本）
    ★ 提取结果【不直接视为已审核口径】：expression 需人工复核；
      原始 definition 与提取来源始终保留（见 metrics 原表），审核状态由治理字段承载。
    """
    d = (definition or "").strip()
    if not d:
        return "", "missing", "定义为空"
    clean = _strip_star_notes(d)

    # 取「=」右侧
    right = clean
    for sep in ("=", "＝"):
        idx = clean.find(sep)
        if idx >= 0:
            right = clean[idx + 1:].strip()
            break
    right = right.strip()
    if not right:
        return "", "missing", "无口径内容"
    upper = right.upper()

    # ① 完整 CTE SQL（含 WITH ... SELECT）：从 WITH 开始提取
    with_m = re.search(r"\bWITH\b", upper)
    if with_m and re.search(r"\bSELECT\b", upper):
        sql = right[with_m.start():].rstrip()
        return sql, "extracted", "完整CTE_SQL"

    # ② 纯聚合表达式（以聚合函数开头）
    if any(upper.startswith(k) for k in _SQL_FUNC_START):
        expr = _cut_tail(right)
        return expr, "extracted", "纯表达式"

    # ③ 「，即 SQL」模式（自然语言 + 逗号 + 即 + SQL）
    m = re.search(r"[，,]\s*即\s*", upper)
    if m:
        tail = right[m.start():].lstrip("，,即 ")
        tu = tail.upper()
        if any(tu.startswith(k) for k in _SQL_FUNC_START):
            return _cut_tail(tail), "extracted", "即SQL模式"

    # ④ 含 SQL 片段但混在自然语言里 → ambiguous（待人工补标准 SQL）
    if any(k in upper for k in ("COUNT", "SUM(", "UNIQ", "COUNTDISTINCT")):
        return right, "ambiguous", "描述性口径含SQL片段"

    # ⑤ 纯文本口径
    return "", "missing", "纯文本口径"


def _cut_tail(expr):
    """截断表达式尾部的自然语言说明（保留纯 SQL）。"""
    s = (expr or "").strip()
    # 在中文句号/分号处截断（SQL 表达式内部无中文标点）
    for ch in ("。", "；", ";"):
        idx = s.find(ch)
        if idx >= 0:
            s = s[:idx]
            break
    # 尾部「，即...」式说明截断
    s = re.sub(r"[，,]\s*即.*$", "", s)
    return s.strip()


# ═══════════════════════════════════════════════════════════
# ② 字段维度识别（决策 2：Field 维度属性模型，不新增独立维度表）
# ═══════════════════════════════════════════════════════════
# 维度类型 → 字段名/类型关键词（时间维度优先，其余按字段名启发式）
_DIM_HINTS = {
    "time":    ("time", "date", "ctime", "dtime", "atime", "day", "month", "year",
                "datetime", "timestamp"),
    "geo":     ("province", "city", "region", "area", "district", "address", "county"),
    "status":  ("status", "state"),
    "type":    ("type", "category"),
    "channel": ("channel", "source", "entity_source", "app_source"),
}


def detect_dimension(field_name, dtype=""):
    """识别字段的维度类型（返回维度类型名或空串）。

    ★ 时间维度：字段名或类型含时间关键词 → time；
      其余维度：字段名启发式 → geo/status/type/channel；
      识别不到 → 空串（普通字段，非维度）。
    """
    f = (field_name or "").lower()
    d = (dtype or "").lower()
    # 时间维度优先（字段名 + 类型双判断）
    if any(k in f or k in d for k in _DIM_HINTS["time"]):
        return "time"
    for dim, hints in _DIM_HINTS.items():
        if dim == "time":
            continue
        if any(h in f for h in hints):
            return dim
    return ""


# ═══════════════════════════════════════════════════════════
# ③ 动态组装 OSSIE 标准对象（只读知识表，实时派生）
# ═══════════════════════════════════════════════════════════
def build_metrics_with_expression():
    """遍历 metrics，返回 [{name, metric_type, definition, expression, extract_status, note}]。"""
    out = []
    for m in config_store.get_metrics(enabled_only=False):
        expr, status, note = extract_metric_expression(m.get("definition"))
        out.append({
            "name": m.get("name"),
            "metric_type": m.get("metric_type") or "",
            "definition": m.get("definition") or "",
            "expression": expr,
            "extract_status": status,
            "note": note,
            "audit_status": EXTRACT_STATUS.get(status, "text_only"),
            "knowledge_status": m.get("status") or "",
        })
    return out


def build_metric_object(m):
    """单个指标 → OSSIE Metric（dataset 作用域或模型作用域由 expression 决定，此处输出模型作用域）。"""
    metric = {"name": m.get("name")}
    expr = m.get("expression")
    if expr:
        metric["expression"] = {"sql": expr}
    if m.get("definition"):
        metric["description"] = m["definition"]
    syns = m.get("keywords") or []
    if syns:
        metric["ai_context"] = {"synonyms": list(syns)}
    return metric


def build_dataset_object(entity, columns_with_dim):
    """单个实体 + 字段 → OSSIE Dataset。"""
    d = {"name": entity.get("name"), "source": entity.get("table_name")}
    if entity.get("comment"):
        d["description"] = entity["comment"]
    pk = entity.get("primary_key") or []
    if pk:
        d["primary_key"] = list(pk)          # ★ OSSIE Dataset.primary_key（人工登记）
    syns = entity.get("keywords") or []
    if syns:
        d["ai_context"] = {"synonyms": list(syns)}
    fields = []
    for col in columns_with_dim:
        f = {"name": col["name"], "expression": {"sql": col["name"]}}
        if col.get("comment"):
            f["description"] = col["comment"]
        dim = col.get("dimension") or ""
        if dim == "time":
            f["dimension"] = {"is_time": True}
        elif dim:
            # 非时间维度：用 label 表达维度角色（OSSIE 无专门维度类型字段）
            f["label"] = dim
        fields.append(f)
    if fields:
        d["fields"] = fields
    return d


def audit_metrics_summary():
    """汇总 57 个指标的提取分类统计（供报告/前端展示）。"""
    items = build_metrics_with_expression()
    summary = {"extracted": 0, "ambiguous": 0, "missing": 0, "total": len(items)}
    for it in items:
        summary[it["extract_status"]] = summary.get(it["extract_status"], 0) + 1
    return summary, items


# ═══════════════════════════════════════════════════════════
# ④ 统一语义读取接口 + 完整 Semantic Model 组装（动态映射，不落库）
# ═══════════════════════════════════════════════════════════
def _columns_with_dimension(table_name):
    """读一张表的字段注释，返回 [{name, comment, dimension}]。

    ★ 维度属性：优先取 column_comments.dimension_type（人工覆盖），
      空则用 detect_dimension 自动识别（字段名 + 类型启发式）。
    """
    comments = config_store.get_column_comments(table_name) or {}
    out = []
    for key, comment in comments.items():
        col = key.rsplit(".", 1)[-1]
        dim = _manual_dimension(table_name, col) or detect_dimension(col)
        out.append({"name": col, "comment": comment or "", "dimension": dim})
    return out


def _manual_dimension(table_name, column_name):
    """读 column_comments 里人工指定的 dimension_type（为空返回 None）。"""
    try:
        conn = config_store._connect()
        cur = conn.cursor()
        cur.execute("SELECT dimension_type FROM column_comments WHERE table_name=? AND column_name=?",
                    (table_name, column_name))
        row = cur.fetchone()
        conn.close()
        if row and row[0]:
            return row[0]
    except Exception:
        pass
    return None


def _topic_entity_names(topic_name):
    """取某业务主题/域的实体名集合（未指定返回 None，表示用全部实体）。

    ★ 资产关联按 scope 区分：主题用 'subject'，域用 'domain'。
    """
    if not topic_name:
        return None
    try:
        assets = config_store.list_scope_assets(topic_name, scope="subject")
        return {a["asset_key"] for a in assets if a["asset_type"] == "entity"}
    except Exception:
        return set()


def _topic_metric_names(topic_name):
    """取某业务主题/域的指标名集合（未指定返回 None，表示用全部指标）。"""
    if not topic_name:
        return None
    try:
        assets = config_store.list_scope_assets(topic_name, scope="subject")
        return {a["asset_key"] for a in assets if a["asset_type"] == "metric"}
    except Exception:
        return set()


def build_datasets(topic=None):
    """组装 Dataset 列表（从 entities + column_comments 动态映射）。

    topic 为空 → 全部实体；指定主题 → 该主题的实体（含关系端点补全）。
    """
    entities = [e for e in config_store.get_entities() if e.get("enabled", True)]
    names = _topic_entity_names(topic)
    if names is not None:
        entities = [e for e in entities if e.get("name") in names]
    datasets = []
    for e in entities:
        cols = _columns_with_dimension(e.get("table_name") or "")
        datasets.append(build_dataset_object(e, cols))
    return datasets


def build_metrics(topic=None, only_approved_expression=False):
    """组装 Metric 列表（从 metrics + expression 动态映射）。

    only_approved_expression=True 时，只有 expression_status='approved' 的指标才带 expression.sql。
    ★ 无 expression 的指标（纯文本口径）【不映射为 OSSIE Metric】——OSSIE Metric 的
      expression 是必填字段，决策 5 明确「不自动编造 SQL」。它们保留在内部 description，
      导出时由 build_semantic_model 记入 un_mapped_metrics（兼容性报告）。
    返回 (metrics, un_mapped)：metrics 为可映射列表，un_mapped 为纯文本指标名列表。
    """
    items = build_metrics_with_expression()
    names = _topic_metric_names(topic)
    if names is not None:
        items = [it for it in items if it["name"] in names]
    out, un_mapped = [], []
    for it in items:
        if not it["expression"]:
            un_mapped.append(it["name"])
            continue
        m = {"name": it["name"]}
        if it["definition"]:
            m["description"] = it["definition"]
        syns = []
        for raw in config_store.get_metrics(enabled_only=False):
            if raw.get("name") == it["name"]:
                syns = raw.get("keywords") or []
                break
        if syns:
            m["ai_context"] = {"synonyms": list(syns)}
        if not (only_approved_expression and it["audit_status"] != "approved"):
            m["expression"] = {"sql": it["expression"]}
        out.append(m)
    return out, un_mapped


def build_relationships(topic=None):
    """组装 Relationship 列表（从 relationships 动态映射）。"""
    rels = config_store.get_relationships(enabled_only=True)
    # 端点实体名集合（用于校验 from/to 是否指向已组装的 dataset）
    ent_names = {e.get("name") for e in config_store.get_entities()}
    names = _topic_entity_names(topic)
    out = []
    for r in rels:
        fe, te = r.get("from_entity"), r.get("to_entity")
        # 主题过滤：端点至少一端在主题实体集合里
        if names is not None and not (fe in names or te in names):
            continue
        # 端点实体缺失的关系跳过（引用完整性）
        if fe not in ent_names or te not in ent_names:
            continue
        # ★ from_columns/to_columns：优先用【人工登记】的显式字段，否则从 join_sql 提取兜底
        from_cols = r.get("from_columns") or []
        to_cols = r.get("to_columns") or []
        if not from_cols or not to_cols:
            fc, tc = _extract_join_columns(r.get("join_sql") or "")
            from_cols = from_cols or fc
            to_cols = to_cols or tc
        out.append({
            "name": f"{fe}->{te}",
            "from": fe, "to": te,
            "from_columns": from_cols or [fe],
            "to_columns": to_cols or [te],
        })
    return out


def _extract_join_columns(join_sql):
    """从 join_sql 提取 from/to 列名（复用 ossie_adapter 逻辑，避免重复实现）。"""
    try:
        import ossie_adapter
        return ossie_adapter._extract_join_columns(join_sql)
    except Exception:
        return [], []


def build_semantic_model(topic=None, name=None, description=None):
    """组装完整 OSSIE Semantic Model（动态映射，可直接给 ossie_adapter 校验/序列化）。

    返回 OSSIE 文档 dict（version/name/datasets/metrics/relationships）。
    ★ 不落库、不写数据，纯派生视图。
    ★ 纯文本口径指标（无 expression）不映射为 OSSIE Metric，记入 nonportable_extensions
      的 un_mapped_metrics（决策 5：不编造 SQL）。
    """
    import ossie_adapter
    datasets = build_datasets(topic)
    metrics, un_mapped = build_metrics(topic, only_approved_expression=False)
    relationships = build_relationships(topic)
    model_name = name or topic or "语义模型"
    doc = {
        "version": ossie_adapter.OSSIE_VERSION,
        "name": model_name,
        "datasets": datasets,
        "metrics": metrics,
        "relationships": relationships,
    }
    if description:
        doc["description"] = description
    if un_mapped:
        doc["nonportable_extensions"] = [{
            "vendor_name": "zhishu-ai",
            "data": {"un_mapped_metrics": un_mapped,
                     "note": "纯文本口径指标，无结构化 SQL，保留在知数AI 内部 description"},
        }]
    return doc


def get_semantic_objects(topic=None):
    """统一语义读取接口（Agent 优先使用审核通过的语义对象）。

    返回 {datasets, metrics, relationships, un_mapped_metrics} —— 结构化语义对象的统一出口。
    ★ 这是「两条流水线统一读取语义知识」的唯一实现；Multi-RAG 仍补充非结构化知识。
    """
    metrics, un_mapped = build_metrics(topic, only_approved_expression=True)
    return {
        "datasets": build_datasets(topic),
        "metrics": metrics,
        "relationships": build_relationships(topic),
        "un_mapped_metrics": un_mapped,
    }


# ═══════════════════════════════════════════════════════════
# ⑤ 语义对象 → 写回知识表（只新增不覆盖；source 决定审核状态）
# ═══════════════════════════════════════════════════════════
def _sync_internal_to_kb(internal, source="ai", operator=None):
    """把内部语义模型（datasets/metrics/relationships）写回知识表（只新增，绝不覆盖）。

    ★ source 决定审核状态（复用 config_store._derive_gov_status）：
      'ai'     → candidate（未审核，需人工复核）—— OSSIE 直接导入；
      'manual' → approved（已审核）—— 审核通过的语义模型应用到知识库。
    返回统计 {datasets:{created,skipped}, metrics:{...}, relationships:{...}, skipped_names:[]}
    """
    result = {
        "datasets": {"created": 0, "skipped": 0},
        "metrics": {"created": 0, "skipped": 0},
        "relationships": {"created": 0, "skipped": 0},
        "skipped_names": [],
    }

    # dataset → entity
    existing_entities = {e.get("name") for e in config_store.get_entities()}
    for d in (internal.get("datasets") or []):
        name = (d.get("name") or "").strip()
        if not name or not d.get("source"):
            continue
        if name in existing_entities:
            result["datasets"]["skipped"] += 1
            result["skipped_names"].append(f"entity:{name}")
            continue
        config_store.upsert_entity({
            "name": name, "table_name": d.get("source"),
            "grain": d.get("grain") or "", "comment": d.get("description") or "",
            "keywords": d.get("synonyms") or [], "enabled": True,
        }, source=source, operator=operator)
        result["datasets"]["created"] += 1

    # metric → metric
    existing_metrics = {m.get("name") for m in config_store.get_metrics(enabled_only=False)}
    for m in (internal.get("metrics") or []):
        name = (m.get("name") or "").strip()
        if not name:
            continue
        if name in existing_metrics:
            result["metrics"]["skipped"] += 1
            result["skipped_names"].append(f"metric:{name}")
            continue
        metric = {
            "name": name,
            "definition": m.get("definition") or "",
            "keywords": m.get("keywords") or [],
            "tables": m.get("tables") or [],
        }
        if m.get("sql_template"):
            metric["sql_template"] = m["sql_template"]
        if m.get("metric_type"):
            metric["metric_type"] = m["metric_type"]
        config_store.upsert_metric(metric, operator=operator, source=source)
        result["metrics"]["created"] += 1

    # relationship → relationship
    existing_rels = {(r.get("from_entity"), r.get("to_entity"))
                     for r in config_store.get_relationships(enabled_only=False)}
    for r in (internal.get("relationships") or []):
        fe, te = r.get("from_entity"), r.get("to_entity")
        if not fe or not te:
            continue
        if (fe, te) in existing_rels:
            result["relationships"]["skipped"] += 1
            result["skipped_names"].append(f"relationship:{fe}->{te}")
            continue
        config_store.upsert_relationship({
            "from_entity": fe, "to_entity": te,
            "join_sql": r.get("join_sql") or "",
            "cardinality": r.get("cardinality") or "1:1",
            "pre_agg_required": r.get("pre_agg_required") or 0,
            "rule_note": r.get("rule_note") or "",
            "enabled": True,
        }, operator=operator, source=source)
        result["relationships"]["created"] += 1

    return result


def import_ossie_to_kb(doc, operator=None):
    """OSSIE 文档 → 写回知识表（source='ai'，未审核，走治理）。"""
    import ossie_adapter
    return _sync_internal_to_kb(ossie_adapter.parse_model(doc), source="ai", operator=operator)


def apply_approved_model_to_kb(model, operator=None):
    """审核通过的语义模型 → 写回知识表（source='manual'，直接 approved）。

    ★ 替代原 semantic_model_service.apply_model_to_kb（旧服务已废弃）。
    """
    return _sync_internal_to_kb(model, source="manual", operator=operator)


# ═══════════════════════════════════════════════════════════
# ⑥ 资产统计（业务域 / 业务主题的语义资产计数，口径复用 build_* 派生逻辑）
# ═══════════════════════════════════════════════════════════
def _scope_entity_metric_names(scope_key, scope="subject"):
    """取某主题/域的关联实体名集合 + 指标名集合（来自 topic_assets）。"""
    ent, met = set(), set()
    for a in config_store.list_scope_assets(scope_key, scope=scope):
        if a["asset_type"] == "entity":
            ent.add(a["asset_key"])
        elif a["asset_type"] == "metric":
            met.add(a["asset_key"])
    return ent, met


def _stats_from_names(entity_names, metric_names):
    """给定实体名/指标名集合 → 语义资产统计（口径与 build_datasets/build_metrics/
    build_relationships 严格一致，保证数字与语义组装层一致）。

    返回 {dataset, entity, metric, dimension, relationship}。
    """
    # 实体：过滤 enabled（与 build_datasets 一致）
    entities = [e for e in config_store.get_entities()
                if e.get("enabled", True) and e.get("name") in entity_names]
    ent_names = {e["name"] for e in entities}
    # 指标：主题关联 ∩ 全部指标（含 disabled，与 build_metrics 的 get_metrics(enabled_only=False) 一致）
    all_metric_names = {m["name"] for m in config_store.get_metrics(enabled_only=False)}
    metric_count = len(metric_names & all_metric_names)
    # 关系：端点命中实体名 + 引用完整性（端点实体存在）+ enabled（与 build_relationships 一致）
    all_ent_names = {e.get("name") for e in config_store.get_entities()}
    rel_count = 0
    for r in config_store.get_relationships(enabled_only=True):
        fe, te = r.get("from_entity"), r.get("to_entity")
        if not (fe in ent_names or te in ent_names):
            continue
        if fe not in all_ent_names or te not in all_ent_names:
            continue
        rel_count += 1
    # 维度：实体物理表字段维度（按 表.字段 去重，人工 dimension_type 优先 + 自动识别）
    dim_fields = set()
    for e in entities:
        for c in _columns_with_dimension(e.get("table_name") or ""):
            if c.get("dimension"):
                dim_fields.add((e.get("table_name"), c["name"]))
    return {
        "dataset": len(entities),
        "entity": len(entities),
        "metric": metric_count,
        "dimension": len(dim_fields),
        "relationship": rel_count,
    }


def subject_stats(subject_name):
    """主题级资产统计。★ 口径与语义组装 build_* 完全一致，全部来自实际关联数据。"""
    ent, met = _scope_entity_metric_names(subject_name, scope="subject")
    return _stats_from_names(ent, met)


def _count_sql_cases_by_entities(entity_names):
    """统计涉及指定实体的 SQL 案例数（按 sql_experience.entities_json 反查）。"""
    if not entity_names:
        return 0
    try:
        conn = config_store._connect()
        cur = conn.cursor()
        rows = cur.execute(
            "SELECT entities_json FROM sql_experience "
            "WHERE entities_json IS NOT NULL AND entities_json != ''").fetchall()
        conn.close()
    except Exception:
        return 0
    cnt = 0
    for (ej,) in rows:
        try:
            names = json.loads(ej)
        except Exception:
            continue
        if isinstance(names, list) and any(n in entity_names for n in names):
            cnt += 1
    return cnt


def domain_stats(domain_name):
    """域级资产统计（聚合域下所有主题，实体/指标/关系/维度去重）。

    返回 {subject, dataset, entity, metric, dimension, relationship, sql_case}：
      · subject = 域下业务主题数
      · sql_case = 涉及域内关联实体的 SQL 案例数
    其余同 subject_stats，但按【域内实体集合】去重聚合。
    """
    subjects = [s["name"] for s in config_store.list_business_subjects(domain_name)]
    ent, met = set(), set()
    for sn in subjects:
        e2, m2 = _scope_entity_metric_names(sn, scope="subject")
        ent |= e2
        met |= m2
    stats = _stats_from_names(ent, met)
    stats["subject"] = len(subjects)
    stats["sql_case"] = _count_sql_cases_by_entities(ent)
    return stats
