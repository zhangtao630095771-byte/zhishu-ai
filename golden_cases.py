# -*- coding: utf-8 -*-
"""
golden_cases.py —— 标准问题集管理（P0-1）

★ 职责：把「标准业务问题 → 标准 SQL → 标准结果」从散落的 JSONL 文件，
  升级为可管理的 DB 实体：CRUD + 审核发布 + 版本历史 + 停用归档 + 标准 SQL 安全验证。

★ 为什么单独成模块（不塞进 config_store 的千行上帝文件）？
  「标准问题集」是评测/治理领域的独立数据，DDL 与业务逻辑归本模块；
  config_store.init_store() 只在初始化时调用本模块的 migrate_schema(cur)。

★ 安全铁律（对齐任务书 §3.2-B）：
  标准 SQL 不能因为是管理员录入的就绕过安全验证。
  validate_expected_sql 复用【现有闸门】（guard.sql_guard / guard.check_tables_ex /
  sql_validator.validate_sql_ex / permission.evaluate），与模型生成的 SQL 走同一套判定，
  不另造执行入口、不新开旁路。
"""
import json
import os
import re
from datetime import datetime

import config_store


# ═══════════════════════════════════════════════════════════
# ① 数据模型（DDL）
# ═══════════════════════════════════════════════════════════
SCHEMA = [
    """
    CREATE TABLE IF NOT EXISTS golden_cases (
        id                TEXT PRIMARY KEY,          -- case_id（人可读）
        question          TEXT,                      -- 标准业务问题
        business_line     TEXT,                      -- 业务线
        roles_json        TEXT,                      -- JSON：适用角色
        expected_metrics  TEXT,                      -- JSON：标准指标
        expected_entities TEXT,                      -- JSON：标准实体
        time_caliber      TEXT,                      -- 标准时间口径
        expected_dimensions TEXT,                    -- JSON：标准维度
        expected_sql      TEXT,                      -- 标准 SQL（必须过闸门验证）
        expected_result   TEXT,                      -- JSON：{"columns":[],"rows":[],"precision":N}
        result_check      TEXT,                      -- JSON：规则化验收（行数/列名/关键单元格）
        data_snapshot     TEXT,                      -- 数据快照/测试数据版本标识
        snapshot_type     TEXT DEFAULT 'production', -- fixed / production / history
        acceptance        TEXT DEFAULT 'manual',     -- auto / rule / manual
        review_status     TEXT DEFAULT 'draft',      -- draft / pending / approved / rejected
        verify_status     TEXT DEFAULT 'unverified', -- unverified / verified（独立于审核）
        reviewer          TEXT,
        review_note       TEXT,
        reviewed_at       TEXT,
        status            TEXT DEFAULT 'active',     -- active / archived / disabled
        version           INTEGER DEFAULT 1,
        source            TEXT DEFAULT 'manual',     -- manual / import / feedback / seed-experience
        created_by        TEXT,
        created_at        TEXT,
        updated_at        TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gc_review ON golden_cases(review_status, status)",
    "CREATE INDEX IF NOT EXISTS idx_gc_biz ON golden_cases(business_line, status)",

    # ── 版本历史：每次改 expected_sql / expected_result 都 +1 版本并留痕 ──
    """
    CREATE TABLE IF NOT EXISTS golden_case_versions (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id         TEXT,
        version         INTEGER,
        expected_sql    TEXT,
        expected_result TEXT,
        changed_by      TEXT,
        change_note     TEXT,
        created_at      TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gcv_case ON golden_case_versions(case_id, version)",

    # ── 标准 SQL 验证记录（谁、何时、结果如何）──
    """
    CREATE TABLE IF NOT EXISTS golden_case_verify_log (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        case_id     TEXT,
        result      TEXT,             -- passed / failed / error
        issues_json TEXT,             -- JSON：校验问题列表
        checked_by  TEXT,
        created_at  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_gcvl_case ON golden_case_verify_log(case_id)",
]


def migrate_schema(cur):
    """幂等迁移：只建表 / 建索引 / 补列，不删不重建。"""
    for stmt in SCHEMA:
        cur.execute(stmt)
    # query_records 补 confirm_reason 列（P0-2 L4 业务确认分类）
    cols = [r[1] for r in cur.execute("PRAGMA table_info(query_records)").fetchall()]
    if "confirm_reason" not in cols:
        cur.execute("ALTER TABLE query_records ADD COLUMN confirm_reason TEXT")


# ═══════════════════════════════════════════════════════════
# ② 连库与工具
# ═══════════════════════════════════════════════════════════
def _conn():
    config_store._ensure_ready()
    return config_store._connect()


def _now():
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _json(v):
    return json.dumps(v if v is not None else [], ensure_ascii=False)


def _unjson(text, default=None):
    if not text:
        return default if default is not None else []
    try:
        return json.loads(text)
    except Exception:
        return default if default is not None else []


def default_allowed_tables():
    """从表白名单取可见表（visible=1），作为标准 SQL 验证的表白名单。"""
    rows = config_store.list_table_acl()
    return [r[0] for r in rows if len(r) > 1 and r[1]]


# ═══════════════════════════════════════════════════════════
# ③ 标准 SQL 安全验证（复用现有闸门，绝不另造旁路）
# ═══════════════════════════════════════════════════════════
def validate_expected_sql(sql, allowed_tables=None, user=None, meta=None):
    """
    对管理员录入的 expected_sql 做与模型生成 SQL 相同的安全验证。

    检查顺序（与 _pre_execute_check 对齐，不交换）：
      ① guard.sql_guard            只读 / 禁 DDL·DML·SETTINGS
      ② guard.check_tables_ex      表白名单
      ③ sql_validator.validate_sql_ex 五维业务校验（P0 + P3 表存在性 + 口径/JOIN 若可判）
      ④ permission.evaluate        字段级权限（user 非空时）

    返回：{ok, hard, issues, error, status, tables}
      硬安全失败（只读/表/字段权限）→ hard=True；业务口径失败 → hard=False。
    """
    import guard
    import sql_validator
    import semantic_layer

    allowed = list(allowed_tables or []) or default_allowed_tables()

    ok, why = guard.sql_guard(sql)
    if not ok:
        return {"ok": False, "hard": True, "issues": [],
                "error": f"安全护栏拦截：{why}", "status": "blocked", "tables": []}

    r = guard.check_tables_ex(sql, allowed)
    if not r["ok"]:
        return {"ok": False, "hard": True, "issues": [],
                "error": r.get("message") or f"越权访问被拦截：{r.get('illegal')}",
                "status": "blocked", "tables": []}

    # 构造最小语义上下文：P3 表存在性用「SQL 引用表」校验，P1/P2 无上下文时跳过。
    used = guard.extract_tables(sql)
    ctx = semantic_layer.SemanticContext()
    ctx.p3_tables = list(allowed)
    ctx.p3_table_objs = []      # 无索引则跳过列级校验（degraded，不阻断）
    resolved = {}
    v = sql_validator.validate_sql_ex(sql, ctx, resolved, allowed)
    if sql_validator._has_error(v["issues"]) or v["status"] == "error":
        return {"ok": False, "hard": False, "issues": v["issues"],
                "error": "SQL 未通过校验", "status": v["status"], "tables": used}

    # 字段级权限（可选，user 提供时才启用）
    if user is not None:
        try:
            import permission
            pd = permission.evaluate(sql, user, meta)
            if not pd.get("allowed"):
                return {"ok": False, "hard": True, "issues": v["issues"],
                        "error": "字段级权限拦截：" + "；".join(pd.get("deny_reasons", [])),
                        "status": "blocked", "tables": used}
        except Exception as e:
            # fail-closed：字段权限判定异常，不能当通过
            return {"ok": False, "hard": True, "issues": v["issues"],
                    "error": f"字段级权限判定异常：{e}", "status": "error", "tables": used}

    return {"ok": True, "hard": False, "issues": v["issues"],
            "error": "", "status": v["status"], "tables": used}


def _log_verify(cur, case_id, result, issues, operator):
    cur.execute("""
        INSERT INTO golden_case_verify_log (case_id, result, issues_json, checked_by, created_at)
        VALUES (?,?,?,?,?)
    """, (case_id, result, _json(issues), operator, _now()))


# ═══════════════════════════════════════════════════════════
# ④ CRUD
# ═══════════════════════════════════════════════════════════
# 允许写入的业务字段 → 列名映射（防 SQL 注入式字段名）
_FIELDS = {
    "question": "question", "business_line": "business_line",
    "time_caliber": "time_caliber", "data_snapshot": "data_snapshot",
    "snapshot_type": "snapshot_type", "acceptance": "acceptance",
    "source": "source",
}
_JSON_FIELDS = {"roles": "roles_json", "expected_metrics": "expected_metrics",
                "expected_entities": "expected_entities",
                "expected_dimensions": "expected_dimensions",
                "result_check": "result_check", "expected_result": "expected_result"}


def _row_dict(cols, row):
    d = {}
    for c, v in zip(cols, row):
        if c in ("roles_json", "expected_metrics", "expected_entities",
                 "expected_dimensions", "result_check", "expected_result"):
            d[c[:-5] if c.endswith("_json") else c] = _unjson(v, None if c == "expected_result" else [])
        else:
            d[c] = v
    # 别名：roles_json → roles
    return d


def _decode(row, cols):
    d = _row_dict(cols, row)
    if "roles_json" in d:
        d["roles"] = d.pop("roles_json")
    return d


def create_case(item, operator="admin", validate=True, allowed_tables=None):
    """新增标准问题。若带 expected_sql，先做安全验证；auto 验收必须带 expected_result。"""
    cid = item.get("id") or item.get("case_id")
    if not cid:
        return {"ok": False, "msg": "缺少 case_id"}
    sql = (item.get("expected_sql") or "").strip()
    # ★ 验收方式约束：auto 必须有 expected_result，不能只有 SQL
    acceptance = item.get("acceptance", "manual")
    has_result = bool(item.get("expected_result"))
    if acceptance == "auto" and not has_result:
        return {"ok": False, "msg": "acceptance=auto 时必须提供 expected_result"}
    ver = None
    if sql and validate:
        ver = validate_expected_sql(sql, allowed_tables=allowed_tables)
        if not ver["ok"]:
            return {"ok": False, "msg": "expected_sql 未通过安全验证：" + ver["error"],
                    "issues": ver["issues"]}

    conn = _conn()
    cur = conn.cursor()
    now = _now()
    cur.execute("""
        INSERT INTO golden_cases
          (id, question, business_line, roles_json, expected_metrics, expected_entities,
           time_caliber, expected_dimensions, expected_sql, expected_result, result_check,
           data_snapshot, snapshot_type, acceptance, review_status, verify_status,
           status, version, source, created_by, created_at, updated_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
    """, (
        cid, item.get("question", ""), item.get("business_line", ""),
        _json(item.get("roles")), _json(item.get("expected_metrics")),
        _json(item.get("expected_entities")), item.get("time_caliber", ""),
        _json(item.get("expected_dimensions")), sql,
        _json(item.get("expected_result")) if has_result else None,
        _json(item.get("result_check")), item.get("data_snapshot", ""),
        item.get("snapshot_type", "production"), acceptance,
        item.get("review_status", "draft"), item.get("verify_status", "unverified"),
        item.get("status", "active"), 1, item.get("source", "manual"),
        operator, now, now,
    ))
    if ver is not None:
        _log_verify(cur, cid, ver["status"], ver["issues"], operator)
    conn.commit()
    conn.close()
    return {"ok": True, "id": cid, "validation": (ver or {}).get("status")}


def _versioned_sql_change(conn, cur, cid, old_sql, old_result, new_sql, new_result, operator, note):
    """若标准 SQL / 标准结果发生变化，写版本历史 + 版本号 +1。"""
    if (old_sql or "").strip() == (new_sql or "").strip() and \
       _json(old_result) == _json(new_result):
        return
    cur.execute("SELECT COALESCE(MAX(version),1) FROM golden_cases WHERE id=?", (cid,))
    next_v = cur.fetchone()[0] + 1
    cur.execute("""
        INSERT INTO golden_case_versions (case_id, version, expected_sql, expected_result,
                                          changed_by, change_note, created_at)
        VALUES (?,?,?,?,?,?,?)
    """, (cid, next_v, new_sql, _json(new_result) if new_result else None,
          operator, note, _now()))
    cur.execute("UPDATE golden_cases SET version=? WHERE id=?", (next_v, cid))


def update_case(cid, fields, operator="admin", validate=True, allowed_tables=None):
    """编辑标准问题。改 expected_sql 会重新安全验证并 +1 版本。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT expected_sql, expected_result FROM golden_cases WHERE id=?", (cid,))
    row = cur.fetchone()
    if not row:
        conn.close()
        return {"ok": False, "msg": f"找不到案例 {cid}"}

    # 组装更新（只更新传入的字段，白名单列名）
    sets, vals = [], []
    for k, col in _FIELDS.items():
        if k in fields:
            sets.append(f"{col}=?")
            vals.append(fields[k])
    for k, col in _JSON_FIELDS.items():
        if k in fields:
            sets.append(f"{col}=?")
            vals.append(_json(fields[k]) if fields[k] is not None else None)
    if "expected_sql" in fields:
        new_sql = (fields.get("expected_sql") or "").strip()
        if new_sql and validate:
            ver = validate_expected_sql(new_sql, allowed_tables=allowed_tables)
            if not ver["ok"]:
                conn.close()
                return {"ok": False, "msg": "expected_sql 未通过安全验证：" + ver["error"],
                        "issues": ver["issues"]}
            _log_verify(cur, cid, ver["status"], ver["issues"], operator)
        sets.append("expected_sql=?")
        vals.append(new_sql)
    if "expected_result" in fields:
        sets.append("expected_result=?")
        vals.append(_json(fields["expected_result"]) if fields["expected_result"] else None)

    if not sets:
        conn.close()
        return {"ok": False, "msg": "没有可更新的字段"}

    sets.append("updated_at=?")
    vals.append(_now())
    sets.append("review_status=?")  # ★ 修改后强制回退审核态，防止"改了没重审"
    vals.append("draft")
    vals.append(cid)
    cur.execute(f"UPDATE golden_cases SET {', '.join(sets)} WHERE id=?", vals)

    # 版本留痕（仅当 SQL/结果变化）
    _versioned_sql_change(conn, cur, cid, row[0], row[1],
                          fields.get("expected_sql", row[0]),
                          fields.get("expected_result", row[1]),
                          operator, fields.get("change_note", ""))
    conn.commit()
    conn.close()
    return {"ok": True, "id": cid}


def get_case(cid):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT * FROM golden_cases WHERE id=?", (cid,))
    cols = [d[0] for d in cur.description]
    row = cur.fetchone()
    conn.close()
    if not row:
        return None
    return _decode(row, cols)


def list_cases(status=None, review=None, business_line=None, limit=500):
    conn = _conn()
    cur = conn.cursor()
    q = "SELECT * FROM golden_cases WHERE 1=1"
    args = []
    if status:
        q += " AND status=?"
        args.append(status)
    if review:
        q += " AND review_status=?"
        args.append(review)
    if business_line:
        q += " AND business_line=?"
        args.append(business_line)
    q += " ORDER BY created_at DESC LIMIT ?"
    args.append(limit)
    cur.execute(q, args)
    cols = [d[0] for d in cur.description]
    rows = cur.fetchall()
    conn.close()
    return [_decode(r, cols) for r in rows]


def set_review(cid, status, reviewer="", note="", validate=True, allowed_tables=None):
    """审核。approve 前必须再验一次 expected_sql（不能拿旧验证当终身免检）。"""
    case = get_case(cid)
    if not case:
        return {"ok": False, "msg": f"找不到案例 {cid}"}
    if status == "approved":
        sql = (case.get("expected_sql") or "").strip()
        if not sql:
            return {"ok": False, "msg": "approve 前必须有 expected_sql"}
        if validate:
            ver = validate_expected_sql(sql, allowed_tables=allowed_tables)
            if not ver["ok"]:
                return {"ok": False, "msg": "expected_sql 未通过安全验证，拒绝审核通过：" + ver["error"]}
    conn = _conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE golden_cases SET review_status=?, reviewer=?, review_note=?, reviewed_at=?,
                               updated_at=? WHERE id=?
    """, (status, reviewer, note, _now(), _now(), cid))
    conn.commit()
    conn.close()
    return {"ok": True, "id": cid, "review_status": status}


def verify_case(cid, verify_status="verified", operator="admin"):
    """验证状态独立于审核状态（对齐任务书 §5.3）。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("UPDATE golden_cases SET verify_status=?, updated_at=? WHERE id=?",
                (verify_status, _now(), cid))
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return {"ok": ok, "id": cid, "verify_status": verify_status}


def archive_case(cid, status="archived", operator="admin", note=""):
    """停用 / 归档（不物理删除，保留版本历史）。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("UPDATE golden_cases SET status=?, updated_at=?, review_note=? WHERE id=?",
                (status, _now(), note, cid))
    ok = cur.rowcount > 0
    conn.commit()
    conn.close()
    return {"ok": ok, "id": cid, "status": status}


def list_versions(cid):
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT version, expected_sql, expected_result, changed_by, change_note, created_at "
                "FROM golden_case_versions WHERE case_id=? ORDER BY version DESC", (cid,))
    rows = [{"version": r[0], "expected_sql": r[1],
             "expected_result": _unjson(r[2], None), "changed_by": r[3],
             "change_note": r[4], "created_at": r[5]}
            for r in cur.fetchall()]
    conn.close()
    return rows


def list_verify_log(cid):
    """标准 SQL 验证记录（谁、何时、结果如何），供详情页展示。"""
    conn = _conn()
    cur = conn.cursor()
    cur.execute("SELECT result, issues_json, checked_by, created_at "
                "FROM golden_case_verify_log WHERE case_id=? ORDER BY id DESC LIMIT 20", (cid,))
    rows = [{"result": r[0], "issues": _unjson(r[1], []),
             "checked_by": r[2], "created_at": r[3]} for r in cur.fetchall()]
    conn.close()
    return rows


def import_cases(items, operator="admin", validate=True, allowed_tables=None, source="import"):
    """批量导入（list of dict，字段同 create_case）。逐条校验，失败不中断、汇总返回。"""
    results = {"ok": 0, "failed": []}
    for it in items:
        it.setdefault("source", source)
        r = create_case(it, operator=operator, validate=validate, allowed_tables=allowed_tables)
        if r["ok"]:
            results["ok"] += 1
        else:
            results["failed"].append({"id": it.get("id"), "msg": r.get("msg")})
    return results


def overview():
    """标准问题集概览（供看板聚合 + 列表页统计卡片）。"""
    conn = _conn()
    cur = conn.cursor()
    def one(q, args=()):
        cur.execute(q, args)
        return cur.fetchone()[0]
    total = one("SELECT COUNT(*) FROM golden_cases WHERE status!='archived'")
    approved = one("SELECT COUNT(*) FROM golden_cases WHERE review_status='approved' AND status!='archived'")
    verified = one("SELECT COUNT(*) FROM golden_cases WHERE verify_status='verified' AND status!='archived'")
    with_result = one("SELECT COUNT(*) FROM golden_cases WHERE expected_result IS NOT NULL AND status!='archived'")
    unverified = total - verified
    # ★ 状态分布：启用 / 停用 / 归档（统计卡片「已停用」用）
    disabled = one("SELECT COUNT(*) FROM golden_cases WHERE status='disabled'")
    archived = one("SELECT COUNT(*) FROM golden_cases WHERE status='archived'")
    conn.close()
    return {"total": total, "approved": approved, "verified": verified,
            "with_expected_result": with_result, "unverified": unverified,
            "disabled": disabled, "archived": archived}
