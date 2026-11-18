# -*- coding: utf-8 -*-
"""
semantic_modeling.py —— 语义建模（关系模型 / 指标 / 维度）

★ 定位（语义中心重构 · 阶段三核心）：
  语义建模三个 Tab：
    ① 关系模型：一个业务主题 = 一个关系模型（已拍板），管理物理表清单 + 表级 Join。
    ② 指标管理：原子 / 衍生 / 复合三分类，结构化保存（英文标识 + 口径 + 表达式）。
    ③ 维度管理：维度独立成表，从字段维度识别派生候选 + 人工登记。

★ 铁律（不建第二套语义存储）：
    · 关系模型 = 复用 business_subjects（业务主题），不新建模型表；
    · 表清单/Join 的「本体」仍在 entities / relationships，本模块只做「表级显式登记」；
    · 指标本体仍在 metrics，只加 metric_code / metric_category 两列；
    · 维度从 column_comments.dimension_type（人工覆盖）+ detect_dimension（自动识别）派生。
"""
import json
import config_store
import semantic_object_builder


def _now():
    return config_store._now()


# ═══════════════════════════════════════════════════════════
# ① 迁移（幂等，只增不改）
# ═══════════════════════════════════════════════════════════
def migrate_schema(cur):
    """语义建模登记表 + 指标/维度结构化字段（幂等）。"""
    # ① 关系模型的物理表清单（表角色：主表/关联表/卫星表）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS semantic_model_tables (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key     TEXT NOT NULL,          -- 关系模型 = business_subjects.name
            table_name    TEXT NOT NULL,          -- 物理表名
            role          TEXT NOT NULL,          -- primary / related / satellite
            grain         TEXT,
            status        TEXT DEFAULT 'approved',
            source        TEXT DEFAULT 'manual',
            owner_id      TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            updated_by    TEXT,
            version       INTEGER DEFAULT 1,
            UNIQUE(model_key, table_name)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_smt_model ON semantic_model_tables(model_key)")

    # ② 表级 Join（关系模型画布的显式建模）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS semantic_model_joins (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            model_key     TEXT NOT NULL,
            from_table    TEXT NOT NULL,
            to_table      TEXT NOT NULL,
            join_type     TEXT NOT NULL,          -- inner / left / right / full
            cardinality   TEXT NOT NULL,          -- 1:1 / 1:N / N:1 / N:M
            from_columns  TEXT,                   -- 关联键（JSON 数组）
            to_columns    TEXT,
            join_sql      TEXT,
            pre_agg_required INTEGER DEFAULT 0,
            status        TEXT DEFAULT 'approved',
            source        TEXT DEFAULT 'manual',
            owner_id      TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            updated_by    TEXT,
            version       INTEGER DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_smj_model ON semantic_model_joins(model_key)")

    # ③ 维度（独立成表）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS semantic_dimensions (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            dimension_key TEXT NOT NULL UNIQUE,   -- 英文标识（稳定 ID）
            name          TEXT NOT NULL,          -- 维度名称
            dimension_type TEXT,                  -- time / geo / status / type / channel / other
            entity_key    TEXT,                   -- 对应实体
            source_table  TEXT,                   -- 物理表
            source_column TEXT,                   -- 物理字段映射
            model_key     TEXT,                   -- 所属关系模型（业务主题）
            hierarchy     TEXT,                   -- 维度层级（JSON）
            analyzable    INTEGER DEFAULT 1,
            permission_scope TEXT,
            status        TEXT DEFAULT 'approved',
            source        TEXT DEFAULT 'manual',
            owner_id      TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            updated_by    TEXT,
            version       INTEGER DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sd_model ON semantic_dimensions(model_key)")

    # ④ 指标结构化字段：英文标识 + 三分类
    for cname, cdef in (("metric_code", "TEXT"), ("metric_category", "TEXT")):
        try:
            existing = [r[1] for r in cur.execute("PRAGMA table_info(metrics)").fetchall()]
        except Exception:
            existing = []
        if cname not in existing:
            cur.execute(f"ALTER TABLE metrics ADD COLUMN {cname} {cdef}")


# ═══════════════════════════════════════════════════════════
# ② 指标：三分类推导 + 英文标识读写
# ═══════════════════════════════════════════════════════════
_RATIO_WORDS = ("率", "占比", "比例", "百分比")


def derive_metric_category(metric):
    """推导指标三分类：composite（复合）/ derived（衍生）/ atomic（原子）。

    规则（务实，不依赖空的 depends_metrics）：
      ① 名称/类型含「率/占比/比例」→ composite（比率需分子分母，天然复合）；
      ② 有 sql_template（带计算/派生模板）→ derived；
      ③ 其余 → atomic（直接聚合物理字段）。
    """
    name = metric.get("name") or ""
    mt = (metric.get("metric_type") or "").lower()
    if mt in ("ratio", "rate") or any(w in name for w in _RATIO_WORDS):
        return "composite"
    if (metric.get("sql_template") or "").strip():
        return "derived"
    return "atomic"


_CATEGORY_ZH = {"atomic": "原子指标", "derived": "衍生指标", "composite": "复合指标"}


def list_metrics_modeling():
    """指标管理列表：现有指标 + 动态提取表达式 + 三分类 + 英文标识。"""
    rows = config_store.get_metrics_with_governance()
    code_map = get_metric_code_map()
    cat_map = get_metric_category_map()
    for m in rows:
        expr, status, _note = semantic_object_builder.extract_metric_expression(m.get("definition"))
        m["expression"] = expr
        m["extract_status"] = status
        m["metric_code"] = code_map.get(m["name"], "")
        m["metric_category"] = cat_map.get(m["name"]) or derive_metric_category(m)
        m["category_zh"] = _CATEGORY_ZH.get(m["metric_category"], "原子指标")
    return rows


def get_metric_code_map():
    conn = config_store._connect()
    cur = conn.cursor()
    rows = cur.execute("SELECT name, metric_code FROM metrics").fetchall()
    conn.close()
    return {r[0]: (r[1] or "") for r in rows}


def get_metric_category_map():
    conn = config_store._connect()
    cur = conn.cursor()
    rows = cur.execute("SELECT name, metric_category FROM metrics").fetchall()
    conn.close()
    return {r[0]: (r[1] or "") for r in rows}


def set_metric_modeling(name, metric_code="", metric_category="", operator=None):
    """设置指标英文标识 + 三分类（空值不覆盖）。"""
    conn = config_store._connect()
    cur = conn.cursor()
    _ts = _now()
    sets, args = [], []
    if metric_code:
        sets.append("metric_code = ?")
        args.append(metric_code.strip())
    if metric_category in ("atomic", "derived", "composite"):
        sets.append("metric_category = ?")
        args.append(metric_category)
    if not sets:
        conn.close()
        return False, "无有效更新字段"
    sets.append("updated_at = ?")
    args.append(_ts)
    if operator:
        sets.append("updated_by = ?")
        args.append(operator)
    args.append(name)
    cur.execute(f"UPDATE metrics SET {', '.join(sets)} WHERE name = ?", args)
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return bool(affected), "已保存"


# ═══════════════════════════════════════════════════════════
# ③ 维度：派生候选 + CRUD
# ═══════════════════════════════════════════════════════════
def derive_dimension_candidates():
    """从 column_comments 扫描字段，动态识别维度候选（含人工覆盖优先）。

    返回 [{source_table, source_column, comment, dimension_type, entity_key}]。
    ★ 只读派生，不落库；用户「采纳」后才写入 semantic_dimensions。
    """
    conn = config_store._connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT table_name, column_name, comment, dimension_type FROM column_comments ORDER BY table_name, column_name"
    ).fetchall()
    conn.close()
    # 表 → 实体 反查（维度挂到实体）
    ent_by_table = {}
    for e in config_store.get_entities():
        if e.get("table_name"):
            ent_by_table.setdefault(e["table_name"], e["name"])
    out = []
    for r in rows:
        r = dict(r)
        dt = (r.get("dimension_type") or "").strip()
        # 人工覆盖优先；否则动态识别
        if not dt:
            dt = semantic_object_builder.detect_dimension(r.get("column_name") or "")
        if not dt:
            continue
        out.append({
            "source_table": r.get("table_name") or "",
            "source_column": r.get("column_name") or "",
            "comment": r.get("comment") or "",
            "dimension_type": dt,
            "entity_key": ent_by_table.get(r.get("table_name") or "", ""),
        })
    return out


def list_dimensions(model_key=None):
    conn = config_store._connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    if model_key:
        rows = cur.execute(
            "SELECT * FROM semantic_dimensions WHERE model_key = ? ORDER BY dimension_type, name",
            (model_key,)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM semantic_dimensions ORDER BY dimension_type, name").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_dimension(dim, operator=None, source="manual"):
    """登记/更新维度（按 dimension_key upsert）。

    ★ source 护栏（与实体/指标同一套规矩）：
        · 已存在且 source='manual' → AI 不得覆盖（那是人定过的维度）；
        · AI 起草 source='ai' → status=candidate（待复核），人工 → approved。
    """
    dk = (dim.get("dimension_key") or "").strip()
    name = (dim.get("name") or "").strip()
    if not dk or not name:
        return False, "维度英文标识与名称均不能为空"
    # AI 不覆盖人工维度
    if source == "ai":
        existing = list_dimensions()
        old = next((d for d in existing if d["dimension_key"] == dk), None)
        if old and (old.get("source") or "manual") == "manual":
            return False, "已有人工登记的维度，AI 不覆盖"
    conn = config_store._connect()
    cur = conn.cursor()
    _ts = _now()
    st = "candidate" if source == "ai" else "approved"
    cur.execute("""
        INSERT INTO semantic_dimensions
            (dimension_key, name, dimension_type, entity_key, source_table, source_column,
             model_key, hierarchy, analyzable, permission_scope, status, source,
             owner_id, created_at, updated_at, updated_by, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(dimension_key) DO UPDATE SET
            name = excluded.name, dimension_type = excluded.dimension_type,
            entity_key = excluded.entity_key, source_table = excluded.source_table,
            source_column = excluded.source_column, model_key = excluded.model_key,
            hierarchy = excluded.hierarchy, analyzable = excluded.analyzable,
            permission_scope = excluded.permission_scope,
            status = excluded.status, source = excluded.source,
            updated_at = excluded.updated_at, updated_by = excluded.updated_by,
            version = semantic_dimensions.version + 1
    """, (dk, name, dim.get("dimension_type") or "other", dim.get("entity_key") or "",
          dim.get("source_table") or "", dim.get("source_column") or "",
          dim.get("model_key") or "", _json_or_none(dim.get("hierarchy")),
          1 if dim.get("analyzable", True) else 0, dim.get("permission_scope") or "",
          st, source, operator or "manual", _ts, _ts, operator or "manual", 1))
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_dimension(dim_id):
    conn = config_store._connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM semantic_dimensions WHERE id = ?", (dim_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected)}


def _json_or_none(obj):
    if obj is None:
        return None
    if isinstance(obj, str):
        return obj
    try:
        return json.dumps(obj, ensure_ascii=False)
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# ④ 关系模型：表清单 + 表级 Join（登记层）
# ═══════════════════════════════════════════════════════════
def list_model_tables(model_key):
    conn = config_store._connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM semantic_model_tables WHERE model_key = ? ORDER BY role, table_name",
        (model_key,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_model_table(model_key, table_name, role="related", grain="", operator=None):
    if not model_key or not table_name:
        return False, "模型与表名均不能为空"
    conn = config_store._connect()
    cur = conn.cursor()
    _ts = _now()
    cur.execute("""
        INSERT INTO semantic_model_tables
            (model_key, table_name, role, grain, status, source, owner_id,
             created_at, updated_at, updated_by, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(model_key, table_name) DO UPDATE SET
            role = excluded.role, grain = excluded.grain,
            updated_at = excluded.updated_at, updated_by = excluded.updated_by,
            version = semantic_model_tables.version + 1
    """, (model_key, table_name, role, grain or "", "approved", "manual",
          operator or "manual", _ts, _ts, operator or "manual", 1))
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_model_table(table_id):
    conn = config_store._connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM semantic_model_tables WHERE id = ?", (table_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected)}


def list_model_joins(model_key):
    conn = config_store._connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT * FROM semantic_model_joins WHERE model_key = ? ORDER BY from_table, to_table",
        (model_key,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_model_join(model_key, from_table, to_table, join_type="inner",
                      cardinality="1:1", from_columns=None, to_columns=None,
                      join_sql="", pre_agg_required=False, operator=None):
    if not model_key or not from_table or not to_table:
        return False, "模型与两端表名均不能为空"
    conn = config_store._connect()
    cur = conn.cursor()
    _ts = _now()
    cur.execute("""
        INSERT INTO semantic_model_joins
            (model_key, from_table, to_table, join_type, cardinality, from_columns,
             to_columns, join_sql, pre_agg_required, status, source, owner_id,
             created_at, updated_at, updated_by, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (model_key, from_table, to_table, join_type, cardinality,
          _json_or_none(from_columns), _json_or_none(to_columns), join_sql or "",
          1 if pre_agg_required else 0, "approved", "manual", operator or "manual",
          _ts, _ts, operator or "manual", 1))
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_model_join(join_id):
    conn = config_store._connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM semantic_model_joins WHERE id = ?", (join_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected)}


# ═══════════════════════════════════════════════════════════
# ⑤ 关系模型详情聚合（动态派生 + 显式登记合并）
# ═══════════════════════════════════════════════════════════
def get_model_detail(model_key):
    """关系模型详情 = 业务主题 + 表清单 + Join + 指标 + 维度。

    ★ 动态派生（semantic_object_builder 组装）+ 显式登记（semantic_model_*）合并：
      显式登记优先，动态派生兜底，不重复存储。
    """
    subjects = config_store.list_business_subjects()
    subject = next((s for s in subjects if s["name"] == model_key), None)
    if not subject:
        return None

    objs = semantic_object_builder.get_semantic_objects(model_key)

    # 表清单：动态派生（datasets = 实体 → 物理表）+ 显式登记补充
    dyn_tables = {}
    for d in objs.get("datasets") or []:
        tbl = d.get("source") or ""
        if tbl:
            dyn_tables[tbl] = {
                "table_name": tbl, "role": "related", "grain": "",
                "dataset": d.get("name") or "", "fields": len(d.get("fields") or []),
                "source": "derived",
            }
    for t in list_model_tables(model_key):
        if t["table_name"] in dyn_tables:
            dyn_tables[t["table_name"]].update({
                "role": t["role"], "grain": t["grain"], "source": "explicit", "id": t["id"],
            })
        else:
            dyn_tables[t["table_name"]] = {
                "table_name": t["table_name"], "role": t["role"], "grain": t["grain"],
                "dataset": "", "fields": 0, "source": "explicit", "id": t["id"],
            }
    # 主表：显式 primary 优先；否则第一个有主键的实体主表
    tables = list(dyn_tables.values())
    primary = next((t for t in tables if t["role"] == "primary"), None)
    if not primary:
        pk_owner = None
        for e in config_store.get_entities():
            if e.get("primary_key") and e.get("table_name") in dyn_tables:
                pk_owner = e.get("table_name")
                break
        if pk_owner:
            dyn_tables[pk_owner]["role"] = "primary"

    # Join：动态派生（relationships → 实体 → 物理表）+ 显式登记补充
    dyn_joins = {}
    rel_by_entity = {}
    for e in config_store.get_entities():
        if e.get("table_name"):
            rel_by_entity.setdefault(e["name"], e["table_name"])
    for r in objs.get("relationships") or []:
        ft = rel_by_entity.get(r.get("from"), r.get("from"))
        tt = rel_by_entity.get(r.get("to"), r.get("to"))
        key = f"{ft}|{tt}"
        dyn_joins[key] = {
            "from_table": ft, "to_table": tt,
            "join_type": "", "cardinality": "", "join_sql": "",
            "source": "derived",
        }
    # relationships 本体有 join_type/cardinality/join_sql，补上
    for rel in config_store.get_relationships(enabled_only=False):
        ft = rel_by_entity.get(rel.get("from_entity"), rel.get("from_entity"))
        tt = rel_by_entity.get(rel.get("to_entity"), rel.get("to_entity"))
        key = f"{ft}|{tt}"
        if key in dyn_joins:
            dyn_joins[key].update({
                "join_type": rel.get("join_type") or "",
                "cardinality": rel.get("cardinality") or "",
                "join_sql": rel.get("join_sql") or "",
            })
    for j in list_model_joins(model_key):
        key = f"{j['from_table']}|{j['to_table']}"
        if key in dyn_joins:
            dyn_joins[key].update({
                "join_type": j["join_type"], "cardinality": j["cardinality"],
                "join_sql": j["join_sql"], "source": "explicit", "id": j["id"],
            })
        else:
            dyn_joins[key] = {
                "from_table": j["from_table"], "to_table": j["to_table"],
                "join_type": j["join_type"], "cardinality": j["cardinality"],
                "join_sql": j["join_sql"], "source": "explicit", "id": j["id"],
            }

    # 指标（关联主题的）+ 维度（关联模型的）
    metrics = objs.get("metrics") or []
    dims = list_dimensions(model_key)

    return {
        "subject": subject,
        "tables": list(dyn_tables.values()),
        "joins": list(dyn_joins.values()),
        "metrics": metrics,
        "dimensions": dims,
        "stats": semantic_object_builder.subject_stats(model_key),
    }
