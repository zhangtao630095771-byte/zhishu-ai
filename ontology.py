# -*- coding: utf-8 -*-
"""
ontology.py —— 本体建模（Object / Attribute / Relation / Rule / Action）

★ 定位（语义中心重构 · 阶段二核心）：
  本体建模把「业务实体 → 属性 → 关系 → 规则」组织成可维护的本体。
  铁律（与语义对象组装一致）：**实体/关系/属性本体不复制存储**——
    · Object（业务实体）  = 复用 `entities`
    · Relation（实体关系）= 复用 `relationships`
    · Attribute 本体      = 复用 `column_comments` / `dict_entries`（只做「实体↔属性」登记）
    · Rule 本体           = 复用 `metric_constraint` / prompts.sql_rules（只做「实体↔规则」登记）
  本模块只负责：① 建登记表 ② 实体英文标识 ③ 属性/规则登记 CRUD ④ 实体详情聚合。

★ 与 config_store 的关系：复用 config_store 的 _connect / _now / _ensure_ready 等
  底层辅助，不重复造连接。DDL 由 config_store.init_store 在启动时调用 migrate_schema。
"""
import config_store


# ═══════════════════════════════════════════════════════════
# ① 迁移（幂等，只增不改）
# ═══════════════════════════════════════════════════════════
def migrate_schema(cur):
    """本体建模登记表 + 实体英文标识列（幂等，只 CREATE IF NOT EXISTS + ADD COLUMN）。"""
    # ① 实体属性映射（属性本体在 column_comments / dict_entries，这里只做「实体↔属性」登记）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ontology_attributes (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_key    TEXT NOT NULL,          -- 关联实体 name（ID 引用，非模糊名称）
            attr_type     TEXT NOT NULL,          -- column / dict / derived
            attr_key      TEXT NOT NULL,          -- 属性本体键（column 用 "table.column"，dict 用 name）
            label         TEXT,                   -- 属性业务名
            is_key        INTEGER DEFAULT 0,      -- 是否主键 / 关联键
            status        TEXT DEFAULT 'approved',
            source        TEXT DEFAULT 'manual',
            owner_id      TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            updated_by    TEXT,
            version       INTEGER DEFAULT 1,
            UNIQUE(entity_key, attr_type, attr_key)
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_oa_entity ON ontology_attributes(entity_key)")

    # ② 业务规则登记（规则本体引用 metric_constraint / sql_rules / join 规则，不复制）
    cur.execute("""
        CREATE TABLE IF NOT EXISTS ontology_rules (
            id            INTEGER PRIMARY KEY AUTOINCREMENT,
            entity_key    TEXT,                   -- 关联实体 name（空 = 全局规则）
            rule_type     TEXT NOT NULL,          -- metric_constraint / sql_rule / join_rule / biz_rule
            rule_ref      TEXT NOT NULL,          -- 引用的规则本体键
            description   TEXT,
            status        TEXT DEFAULT 'approved',
            source        TEXT DEFAULT 'manual',
            owner_id      TEXT,
            created_at    TEXT,
            updated_at    TEXT,
            updated_by    TEXT,
            version       INTEGER DEFAULT 1
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_or_entity ON ontology_rules(entity_key)")

    # ③ 实体英文标识（entity_code）—— 加列，历史行回填 NULL（诚实标记「未填」，不凭空造）
    try:
        existing = [r[1] for r in cur.execute("PRAGMA table_info(entities)").fetchall()]
    except Exception:
        existing = []
    if "entity_code" not in existing:
        cur.execute("ALTER TABLE entities ADD COLUMN entity_code TEXT")


def _connect():
    """复用 config_store 的连接（同一配置库，同一份连接语义）。"""
    config_store._ensure_ready()
    return config_store._connect()


def _now():
    return config_store._now()


# ═══════════════════════════════════════════════════════════
# ② 实体英文标识（独立读写，不改现有 upsert_entity 逻辑）
# ═══════════════════════════════════════════════════════════
def set_entity_code(name, code, operator=None):
    """设置实体英文标识。code 空串视为清空（NULL）。"""
    conn = _connect()
    cur = conn.cursor()
    cur.execute(
        "UPDATE entities SET entity_code = ?, updated_at = ?, updated_by = ? WHERE name = ?",
        ((code or "").strip() or None, _now(), operator or "manual", name))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected), "name": name, "entity_code": (code or "").strip() or None}


def get_entity_code_map():
    """实体名 → 英文标识（供列表展示）。"""
    conn = _connect()
    cur = conn.cursor()
    rows = cur.execute("SELECT name, entity_code FROM entities").fetchall()
    conn.close()
    return {r[0]: (r[1] or "") for r in rows}


# ═══════════════════════════════════════════════════════════
# ③ 实体属性登记 CRUD
# ═══════════════════════════════════════════════════════════
def list_attributes(entity_key=None):
    """列出属性映射登记；可按实体过滤。"""
    conn = _connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    if entity_key:
        rows = cur.execute(
            "SELECT * FROM ontology_attributes WHERE entity_key = ? ORDER BY id", (entity_key,)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM ontology_attributes ORDER BY entity_key, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_attribute(entity_key, attr_type, attr_key, label="", is_key=False, operator=None):
    """登记/更新实体属性映射（有则更新）。返回 (ok, msg)。"""
    entity_key = (entity_key or "").strip()
    attr_type = (attr_type or "").strip()
    attr_key = (attr_key or "").strip()
    if not entity_key or not attr_type or not attr_key:
        return False, "实体、属性类型、属性键均不能为空"
    conn = _connect()
    cur = conn.cursor()
    _ts = _now()
    cur.execute("""
        INSERT INTO ontology_attributes
            (entity_key, attr_type, attr_key, label, is_key, status, source,
             owner_id, created_at, updated_at, updated_by, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        ON CONFLICT(entity_key, attr_type, attr_key) DO UPDATE SET
            label = excluded.label, is_key = excluded.is_key,
            updated_at = excluded.updated_at, updated_by = excluded.updated_by,
            version = ontology_attributes.version + 1
    """, (entity_key, attr_type, attr_key, (label or "").strip(), 1 if is_key else 0,
          "approved", "manual", operator or "manual", _ts, _ts, operator or "manual", 1))
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_attribute(attr_id):
    """删除属性映射登记（只删登记，不删属性本体）。"""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM ontology_attributes WHERE id = ?", (attr_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected)}


# ═══════════════════════════════════════════════════════════
# ④ 业务规则登记 CRUD
# ═══════════════════════════════════════════════════════════
def list_rules(entity_key=None):
    """列出规则登记；可按实体过滤（含空实体的全局规则）。"""
    conn = _connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    if entity_key:
        rows = cur.execute(
            "SELECT * FROM ontology_rules WHERE entity_key = ? OR entity_key IS NULL OR entity_key = '' "
            "ORDER BY entity_key, id", (entity_key,)).fetchall()
    else:
        rows = cur.execute("SELECT * FROM ontology_rules ORDER BY entity_key, id").fetchall()
    conn.close()
    return [dict(r) for r in rows]


def upsert_rule(entity_key, rule_type, rule_ref, description="", operator=None):
    """登记/更新业务规则。"""
    rule_type = (rule_type or "").strip()
    rule_ref = (rule_ref or "").strip()
    if not rule_type or not rule_ref:
        return False, "规则类型、规则引用均不能为空"
    entity_key = (entity_key or "").strip() or None
    conn = _connect()
    cur = conn.cursor()
    _ts = _now()
    cur.execute("""
        INSERT INTO ontology_rules
            (entity_key, rule_type, rule_ref, description, status, source,
             owner_id, created_at, updated_at, updated_by, version)
        VALUES (?,?,?,?,?,?,?,?,?,?,?)
    """, (entity_key, rule_type, rule_ref, (description or "").strip(),
          "approved", "manual", operator or "manual", _ts, _ts, operator or "manual", 1))
    conn.commit()
    conn.close()
    return True, "已保存"


def delete_rule(rule_id):
    """删除规则登记（只删登记，不删规则本体）。"""
    conn = _connect()
    cur = conn.cursor()
    cur.execute("DELETE FROM ontology_rules WHERE id = ?", (rule_id,))
    affected = cur.rowcount
    conn.commit()
    conn.close()
    return {"ok": bool(affected)}


# ═══════════════════════════════════════════════════════════
# ⑤ 实体详情聚合（Object + Attribute + Relation + Rule）
# ═══════════════════════════════════════════════════════════
def _column_attrs_for_table(table_name):
    """从 column_comments 派生实体的字段属性（属性本体，只读派生）。"""
    if not table_name:
        return []
    conn = _connect()
    conn.row_factory = __import__("sqlite3").Row
    cur = conn.cursor()
    rows = cur.execute(
        "SELECT table_name, column_name, comment, dimension_type, stable_key, status "
        "FROM column_comments WHERE table_name = ? ORDER BY column_name", (table_name,)).fetchall()
    conn.close()
    return [dict(r) for r in rows]


def get_entity_detail(name):
    """实体详情：实体 + 字段属性（派生）+ 属性登记 + 关系 + 规则。"""
    ent = None
    for e in config_store.get_entities_with_governance():
        if e["name"] == name:
            ent = e
            break
    if not ent:
        return None
    code_map = get_entity_code_map()
    ent["entity_code"] = code_map.get(name, "")
    ent["primary_key"] = ent.get("primary_key") or []

    table_name = ent.get("table_name") or ""
    # 字段属性（从 column_comments 派生，含维度类型）
    field_attrs = _column_attrs_for_table(table_name)
    # 属性登记（自定义映射，可编辑）
    reg_attrs = list_attributes(name)
    # 关系（端点命中本实体）
    rels = [r for r in config_store.get_relationships(enabled_only=False)
            if r.get("from_entity") == name or r.get("to_entity") == name]
    # 规则（本实体 + 全局）
    rules = list_rules(name)

    return {
        "entity": ent,
        "attributes": field_attrs,
        "registered_attributes": reg_attrs,
        "relations": rels,
        "rules": rules,
    }


def get_graph():
    """关系画布数据：实体节点 + 关系边（含关联键与基数）。"""
    entities = config_store.get_entities_with_governance()
    rels = config_store.get_relationships(enabled_only=False)
    code_map = get_entity_code_map()
    nodes = [{
        "name": e["name"], "code": code_map.get(e["name"], ""),
        "table_name": e.get("table_name") or "", "grain": e.get("grain") or "",
        "status": e.get("status") or "approved",
    } for e in entities]
    edges = [{
        "from": r.get("from_entity") or "", "to": r.get("to_entity") or "",
        "join_type": r.get("join_type") or "", "cardinality": r.get("cardinality") or "",
        "from_columns": r.get("from_columns") or [], "to_columns": r.get("to_columns") or [],
        "join_sql": r.get("join_sql") or "", "enabled": bool(r.get("enabled", True)),
    } for r in rels]
    return {"nodes": nodes, "edges": edges}
