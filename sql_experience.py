# -*- coding: utf-8 -*-
"""
sql_experience.py —— SQL 经验中心（经验沉淀 / 检索复用 / 归纳提炼）

★ 这个模块解决什么问题？
  知数AI 已经有「生成 → 校验 → 执行 → 审核 → 反馈」的完整链路，
  但审核通过的 SQL 停在 query_records 里，**下一次生成时完全用不上**。
  于是同类问题反复踩同一个坑，经验不累积。

★ 本模块的定位：只做「经验」这一层，不重写任何取数逻辑。
  · 不自己执行 SQL —— 唯一执行出口仍然是 agent._exec_sql；
  · 不自己做安全校验 —— 复用 agent._pre_execute_check / guard / permission；
  · 不自己造检索框架 —— 复用 rag 的 embedding 模型与余弦相似度；
  · 不自己造状态机 —— 沿用知识治理的 candidate/approved/rejected 约定。

★ 三张表（见 SCHEMA）：
  sql_experience        案例（成功 / 失败 / 修正），一个案例 = 一次「问题→SQL」的事实
  sql_experience_rules  由多条案例归纳出的规则 / SQL 模板（带出处、版本、生效状态）
  sql_experience_usage  召回 / 采用 / 放弃 / 复用 / 验证 / 冲突事件（效果归因用）

★ 一条铁律（写在代码里，不是写在文档里）：
  **审核通过 ≠ 结果正确**。
  review_status 与 verify_status 是两个独立字段，
  trust_level 由两者联合推出，且 approved 但执行失败的案例永远不可能是 golden。
"""

import hashlib
import json
import os
import re
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:                 # 保证命令行直接跑也能 import 同目录模块
    sys.path.insert(0, HERE)

import config                            # noqa: E402  只读开关，不产生副作用


# ═══════════════════════════════════════════════════════════
# ① 枚举与常量
# ═══════════════════════════════════════════════════════════

# 失败类型：任务书 §4.B 的 13 类 + 「待分析」
#   ★ 为什么不直接用 feedbacks.reason？两者口径不完全一致（反馈面向用户描述，
#     失败类型面向根因归类），所以做一张映射表（_FEEDBACK_TO_FAILURE），
#     不硬凑成同一个枚举 —— 硬凑的结果是两边都不好用。
FAILURE_TYPES = {
    "wrong_table":     "表选择错误",
    "wrong_field":     "字段选择错误",
    "wrong_metric":    "指标定义/业务口径错误",
    "wrong_time":      "时间字段或时间范围错误",
    "wrong_status":    "状态条件错误",
    "join_relation":   "JOIN 关系或关联基数错误",
    "wrong_grain":     "数据粒度错误",
    "double_count":    "重复计数或聚合错误",
    "sql_error":       "SQL 语法/方言/执行错误",
    "permission":      "权限或安全规则问题",
    "missing_filter":  "查询条件遗漏",
    # ★ 需求一致性审核判不通过时沉淀的类型（"能跑、口径也对，但不是用户要的那件事"）。
    #   与 wrong_grain / missing_filter 的区别：那些是"SQL 层面具体错了"，
    #   这个是"SQL 与用户本次的修改要求对不上"（漏改、替换写成追加、删了不该删的）。
    "requirement_mismatch": "未满足用户本次查询需求",
    "unverifiable":    "查询结果无法验证",
    "other":           "其他待分类",
    "unknown":         "待分析（根因未确认）",
}

# 反馈原因 → 失败类型（只做「能确定的映射」，不确定的一律 other/unknown）
_FEEDBACK_TO_FAILURE = {
    "wrong_table":      "wrong_table",
    "wrong_field":      "wrong_field",
    "wrong_filter":     "wrong_field",
    "wrong_metric":     "wrong_metric",
    "wrong_time":       "wrong_time",
    "wrong_enum":       "wrong_status",
    "missing_filter":   "missing_filter",
    "join_dup":         "join_relation",
    "agg_grain":        "wrong_grain",
    "sql_error":        "sql_error",
    "permission_denied": "permission",
    "data_error":       "unverifiable",
    "result_mismatch":  "unknown",     # 「与业务认知不符」但没说清为什么 → 待分析
    "missing_data":     "other",
    "knowledge_missing": "other",
    "other":            "other",
}

# 结果验证状态（★ 与 review_status 完全独立）
VERIFY_STATUS_ZH = {
    "unverified":       "未验证",
    "reexec_ok":        "重跑可执行（无业务确认）",
    "manual_confirmed": "人工确认业务含义",
    "result_verified":  "人工确认 + 机器证据",
    "verify_failed":    "验证不通过",
}

# 可信等级
TRUST_ZH = {
    "candidate":       "普通案例",
    "golden_candidate": "黄金候选",
    "golden":          "正式黄金SQL",
    "revoked":         "已撤销/失效",
}

# 规则层级
RULE_LAYERS = {
    "metric_rule":     "指标计算规则",
    "table_selection": "表与字段选择规则",
    "entity":          "业务实体识别规则",
    "join_rule":       "JOIN 基数与关联规则",
    "sql_template":    "SQL 模板",
    "error_pattern":   "典型错误模式",
}

KIND_ZH = {"success": "成功案例", "failure": "失败经验", "correction": "修正案例"}
RULE_STATUS_ZH = {
    "draft": "草稿（存在冲突待处理）",
    "candidate": "候选规则（待确认）",
    "approved": "已发布",
    "rejected": "已拒绝",
    "revoked": "已撤销",
}


# ═══════════════════════════════════════════════════════════
# ② 表结构（唯一事实源；config_store.init_store 与独立迁移脚本都调这里）
# ═══════════════════════════════════════════════════════════

SCHEMA = [
    # ── 案例主表 ──
    """
    CREATE TABLE IF NOT EXISTS sql_experience (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        case_no             TEXT,                 -- EXP-20261029-0001（人可读编号）
        kind                TEXT NOT NULL DEFAULT 'success',   -- success / failure / correction
        source_type         TEXT,                 -- query_record / feedback / manual / correct / import
        source_id           TEXT,                 -- 原始记录 id（★ 反向追溯的唯一凭证）
        question            TEXT,                 -- 原始用户问题
        question_norm       TEXT,                 -- 归一化问题（去标点空白，用于精确复用判定）
        intent              TEXT,                 -- 标准化业务意图（来自查询理解）
        sql_raw             TEXT,                 -- SQL 原文
        sql_norm            TEXT,                 -- 规范化 SQL（去注释/压空白）
        sql_hash            TEXT,                 -- 规范化指纹（去重/同一错误识别）
        tables_json         TEXT,                 -- 涉及的表
        metrics_json        TEXT,                 -- 涉及的指标
        dicts_json          TEXT,                 -- 涉及的字典
        entities_json       TEXT,                 -- 涉及的实体
        rel_json            TEXT,                 -- 涉及的关联规则 id
        grain               TEXT,                 -- 统计粒度（维度）
        time_field          TEXT,                 -- 时间字段
        filters             TEXT,                 -- 关键过滤条件（文本，便于人看）
        dialect             TEXT,                 -- SQL 方言
        exec_ok             INTEGER,              -- 当时是否执行成功（来自 query_records.ok）
        exec_error          TEXT,                 -- 执行错误原文（★ 存量数据没有，属已知限制）
        review_status       TEXT DEFAULT 'none',  -- none/pending/approved/rejected（分类审核）
        reviewer            TEXT,
        review_note         TEXT,
        reviewed_at         TEXT,
        verify_status       TEXT DEFAULT 'unverified',  -- ★ 结果验证（独立字段）
        verify_method       TEXT,                 -- reexec / manual / rule / eval
        verify_evidence     TEXT,                 -- JSON：验证证据（快照行数、重跑差异、确认人）
        verified_by         TEXT,
        last_verified_at    TEXT,
        trust_level         TEXT DEFAULT 'candidate',  -- candidate/golden_candidate/golden/revoked
        golden_published    INTEGER DEFAULT 0,    -- 是否已正式发布为黄金 SQL
        failure_type        TEXT,                 -- 失败类型（仅 kind=failure/correction）
        failure_symptom     TEXT,                 -- 失败现象 / 审核驳回原因
        root_cause          TEXT,                 -- 已知根因
        root_cause_confirmed INTEGER DEFAULT 0,   -- 0 待分析 / 1 已确认（★ 不允许 AI 自动置 1）
        corrected_sql       TEXT,                 -- 正确修正 SQL（若存在）
        correction_note     TEXT,                 -- 修正说明
        correction_of       INTEGER,              -- 修正案例指向的原始失败案例 id
        dup_of              INTEGER,              -- 重复案例指向的规范案例 id（去重但保留引用）
        scope               TEXT,                 -- 适用条件
        limits              TEXT,                 -- 已知限制
        status              TEXT DEFAULT 'active',-- active / stale / revoked
        version             INTEGER DEFAULT 1,
        reuse_count         INTEGER DEFAULT 0,    -- 被召回并被采用的次数
        reuse_success       INTEGER DEFAULT 0,    -- 采用后执行成功的次数
        reuse_fail          INTEGER DEFAULT 0,
        last_used_at        TEXT,
        created_by          TEXT,
        created_at          TEXT,
        updated_at          TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_kind ON sql_experience(kind, status)",
    "CREATE INDEX IF NOT EXISTS idx_exp_trust ON sql_experience(trust_level, status)",
    "CREATE INDEX IF NOT EXISTS idx_exp_hash ON sql_experience(sql_hash)",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_source "
    "ON sql_experience(source_type, source_id, kind) WHERE source_id IS NOT NULL AND source_id <> ''",
    "CREATE UNIQUE INDEX IF NOT EXISTS idx_exp_no ON sql_experience(case_no)",

    # ── 规则 / 模板 ──
    """
    CREATE TABLE IF NOT EXISTS sql_experience_rules (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        rule_no         TEXT,
        topic           TEXT,                 -- 主题（按指标或主表分组）
        layer           TEXT,                 -- RULE_LAYERS 的 key
        title           TEXT,
        content         TEXT,                 -- 规则正文（人可读）
        template_sql    TEXT,                 -- SQL 模板（可含 {参数}）
        params_json     TEXT,                 -- 模板参数说明
        tables_json     TEXT,                 -- 规则适用的表（★ 适用性判定要用）
        metrics_json    TEXT,                 -- 规则适用的指标
        scope           TEXT,                 -- 适用范围
        exceptions      TEXT,                 -- 例外情况
        source_case_ids TEXT,                 -- JSON：来源案例 id（★ 可追溯的硬约束）
        source_count    INTEGER DEFAULT 0,
        conflict_note   TEXT,                 -- 冲突说明（非空则不允许直接发布）
        confidence      TEXT DEFAULT 'low',   -- low / medium / high
        status          TEXT DEFAULT 'candidate',  -- draft/candidate/approved/rejected/revoked
        version         INTEGER DEFAULT 1,
        supersedes      INTEGER,              -- 取代的旧规则 id
        reuse_count     INTEGER DEFAULT 0,
        created_by      TEXT,
        created_at      TEXT,
        updated_at      TEXT,
        published_by    TEXT,
        published_at    TEXT,
        revoked_reason  TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_rule_status ON sql_experience_rules(status, layer)",

    # ── 使用 / 验证事件 ──
    """
    CREATE TABLE IF NOT EXISTS sql_experience_usage (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        ts          TEXT,
        event       TEXT,        -- recall / adopt / skip / reuse / verify / conflict / feedback
        case_id     INTEGER,
        rule_id     INTEGER,
        record_id   INTEGER,     -- 对应 query_records.id（能串起来就能端到端复盘）
        question    TEXT,
        username    TEXT,
        score       REAL,
        rank        INTEGER,
        decision    TEXT,        -- direct_reuse / reference / rejected / none
        reason      TEXT,        -- 未采用的原因（★ 排查误召回最需要的就是这个）
        detail_json TEXT
    )
    """,
    "CREATE INDEX IF NOT EXISTS idx_exp_usage_ts ON sql_experience_usage(ts)",
    "CREATE INDEX IF NOT EXISTS idx_exp_usage_case ON sql_experience_usage(case_id)",
]


def migrate_schema(cur):
    """幂等迁移：只建表 / 建索引 / 补列，不删不重建。

    ★ 为什么把 DDL 放在本模块而不是 config_store？
      「经验」的三张表属于本模块的领域数据，DDL 写在业务模块里，
      改字段时只改一处；config_store 只负责在初始化时调用它。
    """
    for stmt in SCHEMA:
        cur.execute(stmt)
    # query_records 补 error 列：现有表没有执行错误原文，导致失败案例无法归因
    cols = [r[1] for r in cur.execute("PRAGMA table_info(query_records)").fetchall()]
    if "error" not in cols:
        cur.execute("ALTER TABLE query_records ADD COLUMN error TEXT")


def rollback_sql():
    """回滚语句（只删本模块新建的对象，不碰任何既有业务表/数据）"""
    return [
        "DROP INDEX IF EXISTS idx_exp_kind",
        "DROP INDEX IF EXISTS idx_exp_trust",
        "DROP INDEX IF EXISTS idx_exp_hash",
        "DROP INDEX IF EXISTS idx_exp_source",
        "DROP INDEX IF EXISTS idx_exp_no",
        "DROP INDEX IF EXISTS idx_rule_status",
        "DROP INDEX IF EXISTS idx_exp_usage_ts",
        "DROP INDEX IF EXISTS idx_exp_usage_case",
        "DROP TABLE IF EXISTS sql_experience_usage",
        "DROP TABLE IF EXISTS sql_experience_rules",
        "DROP TABLE IF EXISTS sql_experience",
        # 注：query_records.error 列不自动删除（SQLite 早期版本不支持 DROP COLUMN，
        #     且该列纯新增、对旧代码无影响）。需要彻底回退时见 migrate 脚本 --purge-column。
    ]


# ═══════════════════════════════════════════════════════════
# ③ 连库与通用工具
# ═══════════════════════════════════════════════════════════

def _conn():
    """连配置库。★ 复用 config_store 的连接（含目录自愈 + 10s 锁等待），不另开一套。"""
    import config_store
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


def normalize_sql(sql):
    """规范化 SQL：去注释、压空白。★ 只做文本归一，不做任何语义改写（绝不改可执行内容）。"""
    s = (sql or "").strip()
    s = re.sub(r"/\*.*?\*/", " ", s, flags=re.S)
    s = re.sub(r"--[^\n]*", " ", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


def sql_hash(sql):
    """SQL 指纹：规范化 + 小写后取 sha1 前 16 位。

    ★ 用途不是「判定两段 SQL 等价」，而是「识别同一段 SQL」：
      失败经验里最有用的一类检查就是「你这次生成的 SQL 和历史上被判错的那条一模一样」。
    """
    return hashlib.sha1(normalize_sql(sql).lower().encode("utf-8")).hexdigest()[:16]


def norm_question(q):
    """问题归一化：去标点/空白/大小写差异，用于「同一问题」判定。"""
    s = (q or "").lower()
    s = re.sub(r"[\s，。、？！；：,.\?!;:'\"“”‘’（）()【】\[\]《》<>~\-—_/\\|]+", "", s)
    return s


def _bare(t):
    """去掉库名前缀：ctc_data_warehouse.ods_x → ods_x"""
    return (t or "").split(".")[-1].strip().lower()


def _table_set(tables):
    return {_bare(t) for t in (tables or []) if t}


def _row_dict(cols, row):
    return dict(zip(cols, row)) if row else None


def _decode_case(d):
    """把库里的行转成「好用的 dict」：JSON 列解出来 + 补中文标签 + 重算可信度。"""
    if not d:
        return None
    for k in ("tables_json", "metrics_json", "dicts_json", "entities_json", "rel_json"):
        d[k.replace("_json", "")] = _unjson(d.get(k))
    d["verify_evidence"] = _unjson(d.get("verify_evidence"), {}) or {}
    d["failure_type_zh"] = FAILURE_TYPES.get(d.get("failure_type") or "", "")
    d["kind_zh"] = KIND_ZH.get(d.get("kind"), d.get("kind"))
    d["trust_zh"] = TRUST_ZH.get(d.get("trust_level"), d.get("trust_level"))
    d["verify_zh"] = VERIFY_STATUS_ZH.get(d.get("verify_status"), d.get("verify_status"))
    d["status_zh"] = {"active": "有效", "stale": "待复核", "revoked": "已撤销"}.get(
        d.get("status"), d.get("status"))
    return d


# ═══════════════════════════════════════════════════════════
# ④ 可信度计算（纯函数 —— 好测、且「规则写在代码里」）
# ═══════════════════════════════════════════════════════════

def compute_trust(case):
    """由「分类审核 + 结果验证 + 是否发布」推出可信等级。

    ★ 这个函数是整套机制的安全底座，四条硬规则不可绕过：
      1. 只有 success 才可能成为黄金（失败 SQL 永不作正向答案）；
      2. approved 但 exec_ok=0 → 永远是 candidate（实测存在这种脏数据）；
      3. 验证状态为 unverified / verify_failed → 永远不是 golden；
      4. golden 必须人工发布（golden_published=1），AI/导入不能自动升级。
    """
    if (case.get("status") or "active") != "active":
        return "revoked"
    if (case.get("kind") or "") != "success":
        return "candidate"
    if not case.get("exec_ok"):
        return "candidate"
    if (case.get("review_status") or "") != "approved":
        return "candidate"
    vs = case.get("verify_status") or "unverified"
    if vs in ("manual_confirmed", "result_verified"):
        return "golden" if case.get("golden_published") else "golden_candidate"
    if vs == "reexec_ok":
        return "golden_candidate"
    return "candidate"


def confidence_of(case):
    """可信度标签（给人看的粗粒度分级，与 trust_level 分开：一个是资格，一个是程度）"""
    if case.get("trust_level") == "golden":
        return "high"
    if case.get("trust_level") == "golden_candidate":
        return "medium"
    if case.get("review_status") == "approved" or case.get("verify_status") in (
            "reexec_ok", "manual_confirmed", "result_verified"):
        return "medium"
    return "low"


def _apply_trust(d):
    """写库前统一重算 trust_level，避免各处手写导致状态不一致。"""
    if not d:
        return d
    d["trust_level"] = compute_trust(d)
    return d


# ═══════════════════════════════════════════════════════════
# ⑤ 写入：案例
# ═══════════════════════════════════════════════════════════

def _next_no(cur, table, col, prefix, width=4):
    """生成人可读编号：PREFIX-YYYYMMDD-NNNN。

    ★ 为什么不用 COUNT(*)+1？
      删过一条记录后 COUNT 会回退，编号就会撞上已有的（撞了 UNIQUE 索引直接报错）。
      取「当天最大号 + 1」永远单调，删了也不会回退。
    """
    day = datetime.now().strftime("%Y%m%d")
    like = f"{prefix}-{day}-%"
    row = cur.execute(
        f"SELECT {col} FROM {table} WHERE {col} LIKE ? ORDER BY {col} DESC LIMIT 1",
        (like,)).fetchone()
    seq = 1
    if row and row[0]:
        try:
            seq = int(str(row[0]).rsplit("-", 1)[1]) + 1
        except Exception:
            seq = 1
    return f"{prefix}-{day}-{seq:0{width}d}"


def _next_case_no(cur):
    return _next_no(cur, "sql_experience", "case_no", "EXP")


def _next_rule_no(cur):
    return _next_no(cur, "sql_experience_rules", "rule_no", "RULE")


def _history(cur, entity_type, entity_id, action, before=None, after=None, operator=None):
    """写 knowledge_history（与现有知识治理同一张表、同一套语义）"""
    try:
        cur.execute(
            "INSERT INTO knowledge_history (entity_type, entity_id, action, before_json, "
            "after_json, operator, created_at) VALUES (?,?,?,?,?,?,?)",
            (entity_type, str(entity_id), action,
             _json(before) if before else None, _json(after) if after else None,
             operator or "system", _now()))
    except Exception as e:
        print(f"⚠️ 经验变更历史写入失败（不影响主流程）：{e}")


def _audit(cur, actor, action, object_type, object_id, detail=None):
    """写 audit_log（与权限/知识审计同一张表）"""
    ts = _now()
    try:
        cur.execute(
            "INSERT OR REPLACE INTO audit_log (audit_id, actor_id, action, object_type, "
            "object_id, version_no, before_hash, after_hash, request_id, occurred_at, detail) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (f"exp:{object_type}:{object_id}:{action}:{ts}", actor or "system", action,
             object_type, str(object_id), None, None, None, None, ts,
             _json(detail or {})))
    except Exception as e:
        print(f"⚠️ 经验审计写入失败（不影响主流程）：{e}")


def upsert_case(item, operator="system"):
    """写入一条案例（按 source_type+source_id+kind 幂等）。

    ★ 幂等更新的关键：**已有人工裁决的字段不被覆盖**。
      导入历史数据时如果每次都把 review_status 重置回 none，
      管理员审过的结论会被下一次导入静默抹掉 —— 这是知识治理那轮踩过的坑。
    """
    now = _now()
    rec = {
        "kind": item.get("kind") or "success",
        "source_type": item.get("source_type") or "manual",
        "source_id": str(item.get("source_id") or ""),
        "question": item.get("question") or "",
        "question_norm": norm_question(item.get("question")),
        "intent": item.get("intent") or "",
        "sql_raw": item.get("sql_raw") or item.get("sql") or "",
        "dialect": item.get("dialect") or getattr(config, "SQL_DIALECT", ""),
        "tables_json": _json(item.get("tables") or []),
        "metrics_json": _json(item.get("metrics") or []),
        "dicts_json": _json(item.get("dicts") or []),
        "entities_json": _json(item.get("entities") or []),
        "rel_json": _json(item.get("relationships") or []),
        "grain": item.get("grain") or "",
        "time_field": item.get("time_field") or "",
        "filters": item.get("filters") or "",
        "exec_ok": 1 if item.get("exec_ok") else 0,
        "exec_error": (item.get("exec_error") or "")[:2000],
        "review_status": item.get("review_status") or "none",
        "reviewer": item.get("reviewer") or "",
        "review_note": item.get("review_note") or "",
        "reviewed_at": item.get("reviewed_at") or "",
        "verify_status": item.get("verify_status") or "unverified",
        "verify_method": item.get("verify_method") or "",
        "verify_evidence": _json(item.get("verify_evidence") or {}),
        "verified_by": item.get("verified_by") or "",
        "last_verified_at": item.get("last_verified_at") or "",
        "golden_published": 1 if item.get("golden_published") else 0,
        "failure_type": item.get("failure_type") or "",
        "failure_symptom": item.get("failure_symptom") or "",
        "root_cause": item.get("root_cause") or "",
        "root_cause_confirmed": 1 if item.get("root_cause_confirmed") else 0,
        "corrected_sql": item.get("corrected_sql") or "",
        "correction_note": item.get("correction_note") or "",
        "correction_of": item.get("correction_of"),
        "scope": item.get("scope") or "",
        "limits": item.get("limits") or "",
        "status": item.get("status") or "active",
        "created_by": operator,
        "created_at": now,
        "updated_at": now,
    }
    rec["sql_norm"] = normalize_sql(rec["sql_raw"])
    rec["sql_hash"] = sql_hash(rec["sql_raw"])
    _apply_trust(rec)
    rec["dup_of"] = None

    conn = _conn()
    cur = conn.cursor()
    try:
        existing = None
        if rec["source_id"]:
            existing = cur.execute(
                "SELECT * FROM sql_experience WHERE source_type=? AND source_id=? AND kind=?",
                (rec["source_type"], rec["source_id"], rec["kind"])).fetchone()
            cols = [c[0] for c in cur.description]
            existing = _row_dict(cols, existing)

        if existing:
            # 只更新「原料字段」，人工裁决字段一律保留
            keep = ("review_status", "reviewer", "review_note", "reviewed_at",
                    "verify_status", "verify_method", "verify_evidence", "verified_by",
                    "last_verified_at", "golden_published", "failure_type", "failure_symptom",
                    "root_cause", "root_cause_confirmed", "corrected_sql", "correction_note",
                    "scope", "limits", "status")
            upd = {k: v for k, v in rec.items() if k not in keep and k != "dup_of"}
            upd["version"] = int(existing.get("version") or 1) + 1
            sets = ", ".join(f"{k}=?" for k in upd)
            cur.execute(f"UPDATE sql_experience SET {sets} WHERE id=?",
                        tuple(upd.values()) + (existing["id"],))
            cid = existing["id"]
            created = False
        else:
            # 重复检测：同问题 + 同 SQL 指纹 → 挂到已有案例上（去重但保留引用）
            dup = cur.execute(
                "SELECT id FROM sql_experience WHERE sql_hash=? AND question_norm=? "
                "AND status='active' ORDER BY id LIMIT 1",
                (rec["sql_hash"], rec["question_norm"])).fetchone()
            if dup:
                rec["dup_of"] = dup[0]
            rec["case_no"] = _next_case_no(cur)
            cols = list(rec.keys())
            cur.execute(
                f"INSERT INTO sql_experience ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})", tuple(rec[c] for c in cols))
            cid = cur.lastrowid
            created = True
        conn.commit()
    finally:
        conn.close()
    return cid, created


def update_case(case_id, fields, operator="system", note=""):
    """更新案例的人工字段（失败类型/根因/修正/适用条件等），并强制重算可信度。"""
    allowed = {"failure_type", "failure_symptom", "root_cause", "root_cause_confirmed",
               "corrected_sql", "correction_note", "scope", "limits", "grain",
               "time_field", "filters", "intent", "status", "question", "sql_raw"}
    patch = {k: v for k, v in (fields or {}).items() if k in allowed}
    if not patch:
        return False, "没有可更新的字段"
    conn = _conn()
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        if not row:
            return False, f"案例不存在：{case_id}"
        cols = [c[0] for c in cur.description]
        before = _decode_case(_row_dict(cols, row))

        if "sql_raw" in patch or "question" in patch:
            sqlv = patch.get("sql_raw", before.get("sql_raw"))
            patch["sql_norm"] = normalize_sql(sqlv)
            patch["sql_hash"] = sql_hash(sqlv)
            patch["question_norm"] = norm_question(patch.get("question", before.get("question")))

        merged = dict(before)
        merged.update(patch)
        patch["trust_level"] = compute_trust(merged)
        if patch["trust_level"] != "golden":
            patch["golden_published"] = 0        # 失去 golden 资格时同步撤销发布标记
        patch["updated_at"] = _now()
        patch["version"] = int(before.get("version") or 1) + 1

        sets = ", ".join(f"{k}=?" for k in patch)
        cur.execute(f"UPDATE sql_experience SET {sets} WHERE id=?",
                    tuple(patch.values()) + (case_id,))
        after = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        after = _decode_case(_row_dict(cols, after))
        _history(cur, "sql_experience", case_id, "update", before, after, operator)
        _audit(cur, operator, "update", "sql_experience", case_id,
               {"fields": list(patch.keys()), "note": note})
        conn.commit()
    finally:
        conn.close()
    return True, "已更新"


def set_case_review(case_id, status, reviewer, note=""):
    """人工分类审核（approved / rejected / none）。

    ★ 只改 review_status，**绝不顺带改 verify_status** ——
      两者混在一起，正是「审核通过被当成结果正确」的根因。
    """
    st = (status or "").strip().lower()
    if st not in ("approved", "rejected", "none", "pending"):
        return False, f"非法审核状态：{status}"
    if st == "rejected" and not (note or "").strip():
        return False, "判定「不通过」必须写清原因"
    conn = _conn()
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        if not row:
            return False, f"案例不存在：{case_id}"
        cols = [c[0] for c in cur.description]
        before = _decode_case(_row_dict(cols, row))
        merged = dict(before)
        merged.update({"review_status": st, "reviewer": reviewer or "",
                       "review_note": note or "", "reviewed_at": _now()})
        trusted = compute_trust(merged)
        cur.execute(
            "UPDATE sql_experience SET review_status=?, reviewer=?, review_note=?, "
            "reviewed_at=?, trust_level=?, golden_published=?, version=version+1, updated_at=? "
            "WHERE id=?",
            (st, reviewer or "", note or "", _now(), trusted,
             0 if trusted != "golden" else before.get("golden_published", 0), _now(), case_id))
        after = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        after = _decode_case(_row_dict(cols, after))
        _history(cur, "sql_experience", case_id,
                 "approve" if st == "approved" else "reject", before, after, reviewer)
        _audit(cur, reviewer, "approve" if st == "approved" else "reject",
               "sql_experience", case_id, {"note": note, "to": st})
        conn.commit()
    finally:
        conn.close()
    return True, ("已审核通过（★ 这只是口径审核，结果正确性仍需单独验证）"
                  if st == "approved" else "已记录审核结论")


def set_case_verify(case_id, verify_status, method="manual", evidence=None, operator="system"):
    """记录结果验证结论（★ 与审核独立）。

    verify_status 只能由「人工确认」或「机器重跑」两条路径产生；
    机器重跑只能给到 reexec_ok，**不能直接给 result_verified**。
    """
    vs = (verify_status or "").strip().lower()
    if vs not in VERIFY_STATUS_ZH:
        return False, f"非法验证状态：{verify_status}"
    conn = _conn()
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        if not row:
            return False, f"案例不存在：{case_id}"
        cols = [c[0] for c in cur.description]
        before = _decode_case(_row_dict(cols, row))
        if before.get("kind") != "success" and vs not in ("verify_failed", "unverified"):
            return False, "失败/修正案例不能标记为「结果已验证」"
        merged = dict(before)
        merged.update({"verify_status": vs, "verify_method": method,
                       "verify_evidence": evidence or {}, "last_verified_at": _now(),
                       "verified_by": operator})
        trusted = compute_trust(merged)
        cur.execute(
            "UPDATE sql_experience SET verify_status=?, verify_method=?, verify_evidence=?, "
            "verified_by=?, last_verified_at=?, trust_level=?, golden_published=?, "
            "version=version+1, updated_at=? WHERE id=?",
            (vs, method, _json(evidence or {}), operator, _now(), trusted,
             0 if trusted != "golden" else before.get("golden_published", 0), _now(), case_id))
        after = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        after = _decode_case(_row_dict(cols, after))
        _history(cur, "sql_experience", case_id, "verify", before, after, operator)
        _audit(cur, operator, "verify", "sql_experience", case_id,
               {"to": vs, "method": method, "evidence": evidence or {}})
        _log_event_direct(cur, "verify", case_id=case_id, decision=vs,
                          reason=(evidence or {}).get("summary", ""), detail=evidence)
        conn.commit()
    finally:
        conn.close()
    return True, f"验证状态已更新为：{VERIFY_STATUS_ZH[vs]}"


def promote_case(case_id, target, operator="system", note=""):
    """提交黄金候选 / 正式发布 / 撤销。

    ★ 这里是「资格闸门」：不满足 compute_trust 的硬规则，一律拒绝。
    """
    target = (target or "").strip().lower()
    if target not in ("golden_candidate", "golden", "revoked"):
        return False, f"非法目标状态：{target}"
    conn = _conn()
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        if not row:
            return False, f"案例不存在：{case_id}"
        cols = [c[0] for c in cur.description]
        case = _decode_case(_row_dict(cols, row))

        if target == "golden_candidate":
            if case.get("kind") != "success":
                return False, "只有成功案例才能成为黄金候选"
            if case.get("review_status") != "approved":
                return False, "需先通过分类审核（approved）"
            if not case.get("exec_ok"):
                return False, "该案例当时执行失败，不能作为黄金候选"
            if case.get("verify_status") == "unverified":
                return False, ("结果尚未验证：请先做「重跑验证」或「人工确认」，"
                               "否则只是「审核通过」，不等于结果正确")
            if case.get("verify_status") == "verify_failed":
                return False, "该案例结果验证不通过，不能升级"
            if case.get("trust_level") not in ("golden_candidate", "golden"):
                return False, "当前不满足黄金候选条件"
            msg = "已提交为黄金候选（仍未进入正式黄金集）"
        elif target == "golden":
            if case.get("verify_status") not in ("manual_confirmed", "result_verified"):
                return False, "正式黄金 SQL 需要人工确认业务含义（或更高等级的验证）"
            if compute_trust(dict(case, golden_published=1)) != "golden":
                return False, "不满足正式黄金 SQL 的准入条件"
            if case.get("status") != "active":
                return False, "案例已失效/撤销，需先恢复"
            msg = "已发布为正式黄金 SQL（将进入正向召回）"
        else:
            if not (note or "").strip():
                return False, "撤销必须写清原因"
            msg = "已撤销（退出正向召回，历史记录保留）"

        before = dict(case)
        if target == "revoked":
            cur.execute(
                "UPDATE sql_experience SET status='revoked', trust_level='revoked', "
                "golden_published=0, review_note=?, version=version+1, updated_at=? WHERE id=?",
                ((case.get("review_note") or "") + f" ｜ 撤销：{note}", _now(), case_id))
        elif target == "golden":
            cur.execute(
                "UPDATE sql_experience SET trust_level='golden', golden_published=1, "
                "version=version+1, updated_at=? WHERE id=?", (_now(), case_id))
        else:
            cur.execute(
                "UPDATE sql_experience SET trust_level='golden_candidate', version=version+1, "
                "updated_at=? WHERE id=?", (_now(), case_id))
        after = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
        after = _decode_case(_row_dict(cols, after))
        _history(cur, "sql_experience", case_id, "promote", before, after, operator)
        _audit(cur, operator, "promote", "sql_experience", case_id,
               {"to": target, "note": note})
        conn.commit()
    finally:
        conn.close()
    return True, msg


def attach_correction(failure_case_id, corrected_sql, note="", operator="system",
                      root_cause="", failure_type=None):
    """把「修正后的 SQL」挂到失败案例上，并生成一条修正案例（kind=correction）。

    ★ 为什么要单独一条 correction 案例，而不是只往失败案例里塞个字段？
      因为「原始 SQL → 修正 SQL」是一组**对照样本**：
      检索时要能同时看到错的和对的，归纳时要能算出「改了什么」。
      塞进同一个字段，对照关系就丢了。
    """
    conn = _conn()
    cur = conn.cursor()
    try:
        row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (failure_case_id,)).fetchone()
        if not row:
            return False, f"案例不存在：{failure_case_id}", None
        cols = [c[0] for c in cur.description]
        base = _decode_case(_row_dict(cols, row))
    finally:
        conn.close()

    if base.get("kind") != "failure":
        return False, "只能给失败案例挂修正 SQL", None
    if not (corrected_sql or "").strip():
        return False, "修正 SQL 不能为空", None

    ok, msg = update_case(failure_case_id, {
        "corrected_sql": corrected_sql,
        "correction_note": note,
        "failure_type": failure_type or base.get("failure_type") or "unknown",
        "root_cause": root_cause or base.get("root_cause") or "",
        "root_cause_confirmed": 1 if (root_cause or "").strip() else base.get("root_cause_confirmed", 0),
    }, operator=operator, note="attach_correction")
    if not ok:
        return False, msg, None

    cid, _ = upsert_case({
        "kind": "correction",
        "source_type": "correct",
        "source_id": str(failure_case_id),
        "question": base.get("question"),
        "intent": base.get("intent"),
        "sql_raw": corrected_sql,
        "tables": base.get("tables"),
        "metrics": base.get("metrics"),
        "dicts": base.get("dicts"),
        "entities": base.get("entities"),
        "grain": base.get("grain"),
        "exec_ok": 1,
        "review_status": "none",
        "failure_type": failure_type or base.get("failure_type") or "",
        "failure_symptom": base.get("failure_symptom"),
        "root_cause": root_cause or base.get("root_cause"),
        "root_cause_confirmed": 1 if (root_cause or "").strip() else 0,
        "correction_of": failure_case_id,
        "correction_note": note,
        "scope": base.get("scope"),
    }, operator=operator)
    return True, "已建立修正对照关系", cid


def record_requirement_failure(question, sql, issues, tables=None, metrics=None,
                               understanding=None, operator="req_review"):
    """把"执行没问题、但没满足用户需求"的 SQL 沉淀为失败经验。

    ★ 这是需求审核的【自我增强】闭环：这次判不通过的原因，下次遇到同类问题
      会作为"已知错误模式"出现在生成 prompt 与审核 prompt 里。
    ★ 只写 failure，绝不自动升 golden（沿用 sql_experience_design.md 的非目标）。
    ★ 幂等：source_id 用 SQL 指纹，同一条错 SQL 反复出现只更新、不膨胀。
    ★ tables/metrics 必须一起存：检索有适用性硬校验（_applicable 要求表/指标有交集），
      不存这两项，这条经验以后【永远召不回来】—— 等于白记。
    """
    if not (sql or "").strip():
        return None, "空 SQL"
    try:
        symptom = "；".join(str(i.get("message") or "") for i in (issues or [])
                            if i.get("message"))[:500]
        rules = ",".join(sorted({str(i.get("rule") or "") for i in (issues or [])
                                 if i.get("rule")}))[:200]
        return upsert_case({
            "kind": "failure",
            "source_type": "req_review",
            "source_id": sql_hash(sql)[:24],
            "question": question or "",
            "intent": (understanding or {}).get("intent") or "",
            "sql_raw": sql,
            "tables": tables or [],
            "metrics": metrics or [],
            # ★ exec_ok=True 是刻意的：它的失败不是"跑不出来"，而是"跑出来但不对"。
            #   这类案例恰恰最该被后续查询参考（因为 SQL 看起来完全正常）。
            "exec_ok": True,
            "failure_type": "requirement_mismatch",
            "failure_symptom": symptom,
            "root_cause": rules,
            "root_cause_confirmed": False,
            "limits": "需求一致性审核判定：未满足本次查询要求",
        }, operator=operator)
    except Exception as e:
        print(f"⚠️ 需求失败经验写入失败（不影响取数）：{e}")
        return None, str(e)


def revoke_case(case_id, reason, operator="system"):
    return promote_case(case_id, "revoked", operator, reason)


# ═══════════════════════════════════════════════════════════
# ⑥ 导入：把存量取数记录 / 用户反馈变成案例
# ═══════════════════════════════════════════════════════════

def _grain_of(understanding):
    if not understanding:
        return ""
    dims = understanding.get("dimensions") or understanding.get("group_by") or []
    if isinstance(dims, list) and dims:
        return "、".join(str(d) for d in dims)
    return ""


def import_from_records(limit=None, dry_run=False, operator="system", only_ids=None):
    """把 query_records 导入经验库。

    ★ 三条「不许」：
      1. 不许把历史记录自动标记为已验证（verify_status 一律 unverified）；
      2. 不许把 approved 当成结果正确（trust 由 compute_trust 算）；
      3. 不许让导入覆盖人工已做的裁决（upsert_case 已保留人工字段）。
    ★ 分类规则（按真实状态，不猜）：
      review_status=rejected → failure（审核驳回）
      ok=0                  → failure（执行失败）
      ok=1                  → success
    """
    conn = _conn()
    cur = conn.cursor()
    rows = cur.execute("SELECT * FROM query_records ORDER BY id").fetchall()
    cols = [c[0] for c in cur.description]
    conn.close()

    items = []
    for r in rows:
        d = dict(zip(cols, r))
        if only_ids and str(d["id"]) not in {str(x) for x in only_ids}:
            continue
        ok = bool(d.get("ok"))
        review = d.get("review_status") or "none"
        if review == "rejected":
            kind = "failure"
            ftype = "unknown"          # ★ 驳回原因在 note 里，但那是自由文本，不能 AI 反推类型
        elif not ok:
            kind = "failure"
            ftype = "sql_error"
        else:
            kind = "success"
            ftype = ""
        und = _unjson(d.get("understanding"), {}) or {}
        items.append({
            "kind": kind,
            "source_type": "query_record",
            "source_id": str(d["id"]),
            "question": d.get("question"),
            "intent": (und.get("intent") if isinstance(und, dict) else "") or "",
            "sql_raw": d.get("sql"),
            "tables": _unjson(d.get("tables")),
            "metrics": _unjson(d.get("metrics")),
            "dicts": _unjson(d.get("dicts")),
            "entities": (und.get("entities") if isinstance(und, dict) else []) or [],
            "grain": _grain_of(und),
            "exec_ok": ok,
            "exec_error": d.get("error") or "",
            "review_status": review,
            "reviewer": d.get("reviewer") or "",
            "review_note": d.get("review_note") or "",
            "reviewed_at": d.get("reviewed_at") or "",
            "failure_type": ftype,
            "failure_symptom": (d.get("review_note") if kind == "failure" else "") or "",
            "root_cause_confirmed": 0,
            "status": "active",
        })

    stats = {"scanned": len(items), "created": 0, "updated": 0, "skipped": 0, "by_kind": {}}
    if dry_run:
        for it in items:
            stats["by_kind"][it["kind"]] = stats["by_kind"].get(it["kind"], 0) + 1
        return stats
    for it in items:
        try:
            _, created = upsert_case(it, operator=operator)
            stats["created" if created else "updated"] += 1
            stats["by_kind"][it["kind"]] = stats["by_kind"].get(it["kind"], 0) + 1
        except Exception as e:
            print(f"⚠️ 案例导入失败（record={it['source_id']}）：{e}")
            stats["skipped"] += 1
    return stats


def import_from_feedbacks(operator="system"):
    """把用户反馈导入为失败经验。

    ★ 反馈与审核是两条独立的信号：用户说「数不对」不等于管理员驳回了，
      所以这里只写 failure_type / symptom，review_status 保持 none，
      由管理员后续判断是否要升级为「已确认的失败经验」。
    """
    import config_store
    stats = {"scanned": 0, "created": 0, "updated": 0}
    for f in config_store.get_feedbacks(limit=1000):
        stats["scanned"] += 1
        ftype = _FEEDBACK_TO_FAILURE.get(f.get("reason") or "", "other")
        try:
            _, created = upsert_case({
                "kind": "failure",
                "source_type": "feedback",
                "source_id": str(f.get("id")),
                "question": f.get("question"),
                "sql_raw": f.get("sql"),
                "tables": _unjson(f.get("tables")),
                "metrics": _unjson(f.get("metrics")),
                "dicts": _unjson(f.get("dicts")),
                "exec_ok": bool(f.get("exec_ok")),
                "review_status": "none",
                "failure_type": ftype,
                "failure_symptom": (f.get("detail") or "") + (
                    f"（用户反馈：{config_store.FEEDBACK_REASONS.get(f.get('reason'), '')}）"),
                "root_cause": f.get("root_cause") or "",
                "root_cause_confirmed": 1 if (f.get("root_cause") or "").strip() else 0,
            }, operator=operator)
            stats["created" if created else "updated"] += 1
        except Exception as e:
            print(f"⚠️ 反馈导入失败（feedback={f.get('id')}）：{e}")
    return stats


# ═══════════════════════════════════════════════════════════
# ⑦ 重跑验证（机器证据，只能给到 reexec_ok）
# ═══════════════════════════════════════════════════════════

def verify_by_reexec(case_id, allowed_tables, operator="system"):
    """重新执行缓存 SQL，与当时的结果快照比对。

    ★ 这是「机器能给出的最强证据」，但仍然不等于业务正确：
      它只能证明「现在跑和当时跑一致 / 仍然可执行」。
      所以结论最高只到 reexec_ok，要升到 result_verified 必须人工确认。

    ★ 安全：走 guard 只读 + 表白名单 + agent._exec_sql（唯一执行出口）。
    """
    import guard
    import agent
    import config_store

    conn = _conn()
    cur = conn.cursor()
    row = cur.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
    if not row:
        conn.close()
        return False, "案例不存在", {}
    cols = [c[0] for c in cur.description]
    case = _decode_case(_row_dict(cols, row))
    conn.close()

    if case.get("kind") != "success":
        return False, "只有成功案例可做重跑验证", {}
    sql = case.get("sql_raw") or ""
    ok, why = guard.sql_guard(sql)
    if not ok:
        set_case_verify(case_id, "verify_failed", "reexec",
                        {"summary": f"只读护栏拦截：{why}"}, operator)
        return False, f"只读护栏拦截：{why}", {}
    if not allowed_tables:
        return False, "缺少当前可用表白名单，拒绝验证（fail-closed）", {}
    r = guard.check_tables_ex(sql, allowed_tables)
    if not r["ok"]:
        set_case_verify(case_id, "verify_failed", "reexec",
                        {"summary": r["message"]}, operator)
        return False, r["message"], {}

    t0 = datetime.now()
    try:
        columns, rows = agent._exec_sql(sql)
    except Exception as e:
        ev = {"summary": f"重跑失败：{e}", "at": _now()}
        set_case_verify(case_id, "verify_failed", "reexec", ev, operator)
        return False, f"重跑失败：{e}", ev

    elapsed = (datetime.now() - t0).total_seconds()
    evidence = {"summary": "重跑可执行", "row_count": len(rows), "columns": list(columns),
                "elapsed_sec": round(elapsed, 2), "at": _now()}

    # 与当时快照比对（有快照才算「一致」，没快照只能说「可执行」）
    snap = None
    if case.get("source_type") == "query_record" and case.get("source_id"):
        try:
            snap = config_store.get_result_snapshot(int(case["source_id"]))
        except Exception:
            snap = None
    if snap and snap.get("rows"):
        same_cols = list(snap.get("columns") or []) == list(columns)
        def _norm_rows(rs):
            return [tuple("" if v is None else str(v) for v in r_) for r_ in rs]
        same = same_cols and _norm_rows(snap["rows"]) == _norm_rows(rows)
        evidence["snapshot_rows"] = len(snap["rows"])
        evidence["snapshot_match"] = bool(same)
        evidence["summary"] = ("重跑与历史快照一致" if same
                              else "重跑可执行，但与历史快照不一致（数据可能已变化）")
    else:
        evidence["snapshot_match"] = None
        evidence["summary"] = "重跑可执行（无历史快照可比对）"

    set_case_verify(case_id, "reexec_ok", "reexec", evidence, operator)
    return True, evidence["summary"], evidence


# ═══════════════════════════════════════════════════════════
# ⑧ 检索：成功案例 / 失败经验 / 规则
# ═══════════════════════════════════════════════════════════

# 进程内缓存：{"表名|where": {"key":..., "items":[...], "vectors":[...]}}
#   ★ 缓存键必须带 where：同一个 sql_experience 表要分别取「成功池」和「失败池」，
#     只用表名做键会让两个池子互相覆盖 → 每次查询都重算 embedding（慢且毫无必要）。
_CACHE = {}


def _cache_key(conn, table, where):
    row = conn.execute(f"SELECT COUNT(*), COALESCE(MAX(updated_at),'') FROM {table} WHERE {where}").fetchone()
    return f"{row[0]}:{row[1]}"


def _load_pool(index, table, where, text_fn):
    """加载候选并算向量（按「行数 + 最大更新时间」失效）。返回 (items, vectors)。"""
    model = index.get("model")
    if model is None:
        return [], []
    ckey = f"{table}|{where}"
    conn = _conn()
    key = _cache_key(conn, table, where)
    cached = _CACHE.get(ckey)
    if cached and cached["key"] == key:
        conn.close()
        return cached["items"], cached["vectors"]
    rows = conn.execute(f"SELECT * FROM {table} WHERE {where}").fetchall()
    cols = [c[0] for c in conn.execute(f"SELECT * FROM {table} LIMIT 1").description] if rows else []
    conn.close()
    items = []
    for r in rows:
        d = _row_dict(cols, r)
        d = _decode_case(d) if table == "sql_experience" else _decode_rule(d)
        items.append(d)
    texts = [text_fn(d) for d in items]
    vectors = list(model.embed(texts)) if texts else []
    _CACHE[ckey] = {"key": key, "items": items, "vectors": vectors}
    return items, vectors


def _case_text(c):
    parts = [c.get("question") or "", c.get("intent") or "",
             " ".join(c.get("metrics") or []), " ".join(c.get("tables") or [])]
    return " ".join(p for p in parts if p)


def _cos(a, b):
    import rag
    try:
        # ★ 必须转成 Python float：embedding 库返回的是 numpy.float32，
        #   直接塞进响应体时标准 json 不认识（FastAPI 序列化会报错）。
        return float(rag.cosine_similarity(list(a), list(b)))
    except Exception:
        return 0.0


def _recency_score(updated_at):
    """越新越可信（30 天内 1.0 → 一年前 0.2）"""
    try:
        t = datetime.strptime((updated_at or "")[:19], "%Y-%m-%d %H:%M:%S")
    except Exception:
        return 0.3
    days = max(0.0, (datetime.now() - t).total_seconds() / 86400.0)
    return max(0.2, 1.0 - days / 365.0)


def _overlap(a, b):
    sa, sb = _table_set(a), _table_set(b)
    if not sa or not sb:
        return None
    return len(sa & sb) / float(len(sa | sb))


def _metric_overlap(a, b):
    sa = {str(x).strip() for x in (a or []) if str(x).strip()}
    sb = {str(x).strip() for x in (b or []) if str(x).strip()}
    if not sa or not sb:
        return None
    return len(sa & sb) / float(len(sa | sb))


TRUST_SCORE = {"golden": 1.0, "golden_candidate": 0.6, "candidate": 0.25, "revoked": 0.0}
VERIFY_SCORE = {"result_verified": 1.0, "manual_confirmed": 0.85,
                "reexec_ok": 0.6, "unverified": 0.0, "verify_failed": 0.0}


class ExperienceMatch:
    """一次检索的完整结果（含被拒原因 —— 排查误召回最需要它）。"""

    def __init__(self):
        self.cases = []          # 正向案例（含 score / reasons）
        self.failures = []       # 失败经验（含 score / reasons）
        self.rules = []          # 已发布的规则
        self.rejected = []       # [{case_no, reason, stage}] —— 被适用性校验挡掉的
        self.decision = "none"   # direct_reuse / reference / none
        self.best_case = None
        self.prompt_block = ""
        self.elapsed_ms = 0

    def summary(self):
        return {
            "decision": self.decision,
            "cases": [{"id": c["id"], "case_no": c["case_no"], "score": c["score"],
                       "trust": c["trust_level"], "verify": c["verify_status"],
                       "reasons": c["reasons"]} for c in self.cases],
            "failures": [{"id": c["id"], "case_no": c["case_no"], "score": c["score"],
                          "failure_type": c.get("failure_type")} for c in self.failures],
            "rules": [{"id": r["id"], "rule_no": r["rule_no"], "title": r["title"]}
                      for r in self.rules],
            "rejected": self.rejected,
            "elapsed_ms": self.elapsed_ms,
        }

    def audit_digest(self, max_cases=2, max_failures=2):
        """把检索结果压成"审核时可用的对照证据"（给需求审核的模型看）。

        ★ 与 build_prompt_block 的区别：
          那个是给【生成】用的（教模型怎么写，篇幅可以长）；
          这个是给【审核】用的（判"这条 SQL 是否与历史正确写法/已知错误一致"），
          必须短，否则审核 prompt 会盖过 SQL 本身。
        ★ 措辞必须防"机械照搬"：历史案例只在同题同口径时才有对照价值，
          所以明确写"若与本次要求不一致，以本次要求为准"。
        """
        if not (self.cases or self.failures):
            return ""
        lines = ["# 历史经验（仅作对照证据，若与本次用户要求不一致，一律以本次要求为准）"]

        # ── 已审核且验证通过的写法：审核时拿它比对"结构是否一致" ──
        for c in (self.cases or [])[:max_cases]:
            lines.append(f"\n【可对照的成功写法 {c.get('case_no')}｜"
                         f"{c.get('trust_zh') or c.get('trust_level')}】")
            lines.append(f"它当时回答的问题：{c.get('question')}")
            if c.get("grain"):
                lines.append(f"粒度：{c['grain']}")
            lines.append("SQL：\n" + _short_sql(c.get("sql_raw"), 500))

        # ── 已知错误模式：审核时逐条对照"这次有没有重犯" ──
        for f in (self.failures or [])[:max_failures]:
            lines.append(f"\n【已知错误模式 {f.get('case_no')}｜"
                         f"{FAILURE_TYPES.get(f.get('failure_type'), f.get('failure_type'))}】")
            if f.get("failure_symptom"):
                lines.append(f"当时的现象：{str(f['failure_symptom'])[:160]}")
            if f.get("root_cause"):
                lines.append(f"已知根因：{str(f['root_cause'])[:160]}")
            if f.get("corrected_sql"):
                lines.append("已验证的修正写法（可对照，不要照抄）：\n"
                             + _short_sql(f["corrected_sql"], 400))
        return "\n".join(lines)


def _applicable(case, cur_tables, cur_metrics, allowed, dialect):
    """适用性硬校验。返回 (ok, reason, downgrade_only)。

    ★ 这一段是「不许误召回」的核心：宁可少召回，也不要把口径不同的案例塞给模型。
      排序分数只能决定「谁排前面」，不能决定「能不能用」。
    """
    if (case.get("status") or "active") != "active":
        return False, "案例已失效/撤销", False
    c_tables = _table_set(case.get("tables"))
    allowed_bare = {_bare(t) for t in (allowed or [])}
    if c_tables and allowed_bare and not c_tables <= allowed_bare:
        return False, f"涉及当前不可访问的表：{sorted(c_tables - allowed_bare)}", False
    if c_tables and cur_tables:
        if not (c_tables & {_bare(t) for t in cur_tables}):
            return False, "与本次召回的表集合无交集", False
    mo = _metric_overlap(case.get("metrics"), cur_metrics)
    if mo == 0:
        return False, "指标口径与本次识别不一致", False
    downgrade = False
    if dialect and case.get("dialect") and case["dialect"] != dialect:
        downgrade = True        # 方言不同 → 只能作参考，不参与精确复用
    return True, "", downgrade


def _retrieval_text(question, context_question=""):
    """拼"用于向量检索"的文本：本轮问题 (+ 上一轮问题)。

    ★ 为什么把上下文拼在【同一句】里而不是做两次检索再合并？
      两次检索会引入"以谁为主"的权重问题，还得再定一套合并规则；
      拼成一句交给同一个 embedding，天然就是"这段对话在问什么"的表示，
      实现最省、也最贴近"多轮修改"的真实语义。
    """
    base = (question or "").strip()                 # 本轮原话
    ctx = (context_question or "").strip()           # 上一轮原话（可为空）
    text = base if not ctx else (base + " " + ctx)   # 没有上下文就只用本轮
    return getattr(config, "QUERY_INSTRUCTION", "") + text


def retrieve(index, question, understanding=None, resolved=None,
             allowed_tables=None, user=None, context_question=""):
    """检索经验：成功案例 / 失败经验 / 规则。

    ★ 为什么要传 allowed_tables / user？
      历史案例可能涉及当前用户无权访问的表。**经验检索不能成为越权通道**：
      召回阶段就要过滤，不能等到执行阶段 —— 因为注入 prompt 的 SQL 文本本身就是信息泄露。

    ★ context_question 是"上一轮问题"（增量修改场景专用）：
      本轮问句常常只有"再加完成率"五个字，语义信息不足以召回到真正的基线案例。
      把它拼进 embedding 文本，检索才找得到"上一轮那个查询"的历史经验。
      ★ 只影响【向量相似度】；"同一问题精确复用"的判定仍用本轮原话（norm_question），
        所以不会因为拼了上下文而误判成"同题可复用"。
    """
    import time as _time
    t0 = _time.time()
    m = ExperienceMatch()
    if not getattr(config, "EXP_ENABLED", True):
        return m

    cur_tables = (resolved or {}).get("tables") or []
    cur_metrics = (resolved or {}).get("metrics") or []
    dialect = getattr(config, "SQL_DIALECT", "")
    allowed = allowed_tables or []
    weights = getattr(config, "EXP_RANK_WEIGHTS", {})

    try:
        qvec = None
        # ── 正向案例：只有「通过审核 + 已验证」的候选/黄金案例才进池 ──
        #   ★ EXP_SUCCESS_RECALL 是 B/C/D 组对照的开关：关掉 = 回到「不参考历史成功经验」。
        if getattr(config, "EXP_SUCCESS_RECALL", True):
            where = ("kind='success' AND status='active' AND review_status='approved' "
                     "AND exec_ok=1 AND trust_level IN ('golden_candidate','golden')")
            pool, pvecs = _load_pool(index, "sql_experience", where, _case_text)
            if pool and index.get("model") is not None:
                qtext = _retrieval_text(question, context_question)
                qvec = list(index["model"].embed([qtext]))[0]

            scored = []
            for c, vec in zip(pool, pvecs):
                ok, why, downgrade = _applicable(c, cur_tables, cur_metrics, allowed, dialect)
                if not ok:
                    m.rejected.append({"case_no": c.get("case_no"), "reason": why,
                                       "stage": "applicability"})
                    continue
                sim = _cos(qvec, vec) if qvec is not None else 0.0
                to = _overlap(c.get("tables"), cur_tables)
                mo = _metric_overlap(c.get("metrics"), cur_metrics)
                score = (weights.get("sim", 0.45) * sim
                         + weights.get("table", 0.15) * (to if to is not None else 0.5)
                         + weights.get("metric", 0.20) * (mo if mo is not None else 0.5)
                         + weights.get("trust", 0.12) * TRUST_SCORE.get(c.get("trust_level"), 0)
                         + weights.get("verify", 0.05) * VERIFY_SCORE.get(c.get("verify_status"), 0)
                         + weights.get("recency", 0.03) * _recency_score(c.get("updated_at")))
                scored.append((score, sim, c, downgrade))

            scored.sort(key=lambda x: -x[0])
            max_cases = int(getattr(config, "EXP_MAX_CASES", 2))
            min_score = float(getattr(config, "EXP_MIN_SCORE", 0.55))
            for score, sim, c, downgrade in scored[:max_cases]:
                if score < min_score:
                    m.rejected.append({"case_no": c.get("case_no"),
                                       "reason": f"分数 {score:.3f} 低于阈值 {min_score}",
                                       "stage": "score"})
                    continue
                c = dict(c)
                c["score"] = round(float(score), 4)
                c["reasons"] = [f"语义相似 {sim:.3f}",
                                f"表重合 {(_overlap(c.get('tables'), cur_tables) or 0):.2f}",
                                f"可信 {c.get('trust_level')}",
                                f"验证 {c.get('verify_status')}"]
                if downgrade:
                    c["reasons"].append("方言不同 → 仅作参考")
                    c["reference_only"] = True
                m.cases.append(c)

        # ── 失败经验：只要能定位到同类表/指标的「已分类」失败 ──
        #   ★ EXP_FAIL_PREVENT 是 C/D 组对照的开关：关掉 = 不做错误预防。
        #   ★ 三个排除条件都是实测出来的噪声源：
        #     · failure_type 为空 / unknown / other → 等于没分类，注进去只是占 token；
        #     · 既没有 SQL 也没有现象描述 → 模型看不到任何可学的东西；
        #     · 已撤销/待复核 → 不参与。
        if getattr(config, "EXP_FAIL_PREVENT", True):
            fwhere = ("kind='failure' AND status='active' "
                      "AND failure_type NOT IN ('', 'unknown', 'other') "
                      "AND (COALESCE(sql_raw,'') <> '' OR COALESCE(failure_symptom,'') <> '')")
            fpool, fvecs = _load_pool(index, "sql_experience", fwhere, _case_text)
            if fpool and qvec is None and index.get("model") is not None:
                qtext = _retrieval_text(question, context_question)
                qvec = list(index["model"].embed([qtext]))[0]
            fscored = []
            for c, vec in zip(fpool, fvecs):
                ok, why, _dg = _applicable(c, cur_tables, cur_metrics, allowed, None)
                if not ok:
                    m.rejected.append({"case_no": c.get("case_no"), "reason": why,
                                       "stage": "applicability_failure"})
                    continue
                sim = _cos(qvec, vec) if qvec is not None else 0.0
                to = _overlap(c.get("tables"), cur_tables)
                mo = _metric_overlap(c.get("metrics"), cur_metrics)
                score = (0.55 * sim + 0.25 * (to if to is not None else 0.4)
                         + 0.20 * (mo if mo is not None else 0.4))
                fscored.append((score, c))
            fscored.sort(key=lambda x: -x[0])
            max_f = int(getattr(config, "EXP_MAX_FAILURES", 3))
            min_f = float(getattr(config, "EXP_FAIL_MIN_SCORE", 0.5))
            for score, c in fscored[:max_f]:
                if score < min_f:
                    continue
                c = dict(c)
                c["score"] = round(float(score), 4)
                m.failures.append(c)

        # ── 规则/模板：只有已发布的才用 ──
        if getattr(config, "EXP_RULES", True):
            rwhere = "status='approved'"
            rpool, rvecs = _load_pool(index, "sql_experience_rules", rwhere, _rule_text)
            rscored = []
            for r, vec in zip(rpool, rvecs):
                ok, _why, _dg = _applicable(r, cur_tables, cur_metrics, allowed, None)
                if not ok:
                    continue
                sim = _cos(qvec, vec) if qvec is not None else 0.0
                rscored.append((sim, r))
            rscored.sort(key=lambda x: -x[0])
            for sim, r in rscored[: int(getattr(config, "EXP_MAX_RULES", 2))]:
                if sim < float(getattr(config, "EXP_RULE_MIN_SCORE", 0.55)):
                    continue
                r = dict(r)
                r["score"] = round(float(sim), 4)
                m.rules.append(r)

        # ── 复用决策 ──
        if m.cases:
            m.best_case = m.cases[0]
            exact = (norm_question(question) == m.best_case.get("question_norm")
                     and m.best_case.get("trust_level") == "golden"
                     and not m.best_case.get("reference_only"))
            if exact and getattr(config, "EXP_EXACT_REUSE", False):
                m.decision = "direct_reuse"
            else:
                m.decision = "reference"
        m.prompt_block = build_prompt_block(m)
    except Exception as e:
        # ★ 经验检索是「加分项」：任何异常都不能拖垮取数主流程
        print(f"⚠️ 经验检索失败（不影响取数）：{e}")
    m.elapsed_ms = int((_time.time() - t0) * 1000)
    return m


# ═══════════════════════════════════════════════════════════
# ⑨ Prompt 组装（★ 结构上保证不覆盖 P0~P2）
# ═══════════════════════════════════════════════════════════

def _short_sql(sql, limit=1200):
    s = normalize_sql(sql)
    return s if len(s) <= limit else s[:limit] + " …（已截断）"


def build_prompt_block(match):
    """把检索结果拼成 Prompt 段。

    ★ 三个措辞上的讲究（都是为了防止「经验压过规则」）：
      1. 标题明确「仅作参考，不得覆盖 P0~P2 规则与指标口径」；
      2. 每条案例都标注可信度与验证状态，让模型知道哪些只是候选；
      3. 失败经验单列一节，并写明「禁止作为答案参考」——
         否则模型会把「错误示范」当成「写法示范」。
    """
    parts = []
    if match.cases:
        lines = ["# 历史成功案例（仅作参考，不得覆盖上述 P0/P1/P2 规则与指标口径）",
                 "使用方式：参考其**表选择、字段、JOIN 与过滤写法**；"
                 "若与本问题的指标口径、时间范围、统计粒度不一致，必须按本问题的要求改写，不要照抄。"]
        for c in match.cases:
            tag = [f"案例 {c.get('case_no')}",
                   f"可信度：{c.get('trust_zh') or c.get('trust_level')}",
                   f"审核：{c.get('review_status')}",
                   f"验证：{c.get('verify_zh') or c.get('verify_status')}"]
            lines.append("\n【" + "｜".join(tag) + "】")
            lines.append(f"问题：{c.get('question')}")
            if c.get("grain"):
                lines.append(f"统计粒度：{c['grain']}")
            if c.get("scope"):
                lines.append(f"适用条件：{c['scope']}")
            if c.get("limits"):
                lines.append(f"已知限制：{c['limits']}")
            lines.append("SQL：\n" + _short_sql(c.get("sql_raw")))
        parts.append("\n".join(lines))

    if match.failures:
        lines = ["# 已知错误模式（★ 历史踩坑记录，禁止作为答案参考，只能用来避开错误）"]
        for f in match.failures:
            # 没有 SQL 也没有现象描述的失败案例，对模型没有信息量 —— 宁可不说
            if not (f.get("sql_raw") or f.get("failure_symptom") or f.get("root_cause")):
                continue
            confirmed = "根因已确认" if f.get("root_cause_confirmed") else "根因待分析"
            lines.append(
                f"\n- [{f.get('case_no')}｜{FAILURE_TYPES.get(f.get('failure_type'), f.get('failure_type'))}"
                f"｜{confirmed}]")
            lines.append(f"  当时的问题：{f.get('question')}")
            if f.get("failure_symptom"):
                lines.append(f"  错误现象：{str(f['failure_symptom'])[:200]}")
            if f.get("root_cause"):
                lines.append(f"  已知根因：{str(f['root_cause'])[:200]}")
            if f.get("corrected_sql"):
                lines.append("  正确写法（修正后）：\n  " + _short_sql(f["corrected_sql"], 800)
                             .replace("\n", "\n  "))
            else:
                lines.append("  错误 SQL（切勿模仿）：\n  " + _short_sql(f.get("sql_raw"), 600)
                             .replace("\n", "\n  "))
        if len(lines) > 1:      # 只有标题 = 没有可用的失败经验，不要往 prompt 里塞空段落
            lines.append("\n要求：生成 SQL 时必须避开上述错误；若无法确定，宁可少写条件也不要凭空猜测。")
            parts.append("\n".join(lines))

    if match.rules:
        lines = ["# 经验规则与模板（来自已验证案例的归纳，仅作参考）"]
        for r in match.rules:
            lines.append(f"\n- [{r.get('rule_no')}｜{RULE_LAYERS.get(r.get('layer'), r.get('layer'))}"
                         f"｜{r.get('title')}]")
            if r.get("content"):
                lines.append("  " + str(r["content"])[:600].replace("\n", "\n  "))
            if r.get("template_sql"):
                lines.append("  模板：\n  " + _short_sql(r["template_sql"], 800).replace("\n", "\n  "))
            if r.get("scope"):
                lines.append(f"  适用范围：{r['scope']}")
            if r.get("exceptions"):
                lines.append(f"  例外：{r['exceptions']}")
        parts.append("\n".join(lines))
    return "\n\n".join(p for p in parts if p)


# ═══════════════════════════════════════════════════════════
# ⑩ 生成后检查（失败经验的「辅助校验」）
# ═══════════════════════════════════════════════════════════

_STATUS_LITERAL = re.compile(r"status\s*(?:=|in)\s*\(?\s*'?\"?([0-9A-Za-z_]+)", re.I)


def check_sql(sql, match):
    """用失败经验检查刚生成的 SQL，返回问题列表（不改 SQL、不阻断）。

    ★ 只做**确定性**判断，不调模型、不猜测：
      ① 与历史被判错的 SQL 指纹完全相同 → error（最硬的证据）
      ② 失败案例里有修正 SQL：命中了「错表」却没命中「正表」→ warning
      ③ 失败类型是「状态条件错误/条件遗漏」：修正 SQL 里的状态字面量在新 SQL 里没有 → warning
    这三条都能解释「为什么提示」，而不是给模型一句模糊的「可能有问题」。
    """
    issues = []
    if not match or not match.failures:
        return issues
    h = sql_hash(sql)
    used = _table_set(_extract_tables(sql))
    for f in match.failures:
        cid = f.get("id")
        if f.get("sql_hash") and f["sql_hash"] == h:
            issues.append({
                "severity": "error", "code": "same_as_known_bad_sql", "case_id": cid,
                "message": f"本次 SQL 与历史被判错的 SQL（{f.get('case_no')}）完全相同，"
                           f"错误类型：{FAILURE_TYPES.get(f.get('failure_type'), '未知')}",
            })
            continue
        corrected = f.get("corrected_sql") or ""
        if corrected:
            bad_t = _table_set(_extract_tables(f.get("sql_raw") or ""))
            good_t = _table_set(_extract_tables(corrected))
            wrong_tables = bad_t - good_t
            if wrong_tables & used and not (good_t & used):
                issues.append({
                    "severity": "warning", "code": "known_wrong_table", "case_id": cid,
                    "message": (f"命中了历史案例（{f.get('case_no')}）中的错误表 "
                                f"{sorted(wrong_tables & used)}，而修正写法用的是 {sorted(good_t)}"),
                })
            if f.get("failure_type") in ("wrong_status", "missing_filter"):
                want = set(_STATUS_LITERAL.findall(corrected))
                got = set(_STATUS_LITERAL.findall(sql))
                missing = {w for w in want if w not in got}
                if want and missing:
                    issues.append({
                        "severity": "warning", "code": "missing_status_filter", "case_id": cid,
                        "message": (f"历史案例（{f.get('case_no')}）因缺少状态条件被判错，"
                                    f"修正写法包含 status ∈ {sorted(want)}，本次缺少 {sorted(missing)}"),
                    })
    return issues


def _extract_tables(sql):
    try:
        import guard
        return guard.extract_tables(sql)
    except Exception:
        return []


def build_repair_hint(issues):
    """把经验检查结果转成给模型的一句话修正提示（仅在开启自动修复时使用）"""
    lines = [f"- {i['message']}" for i in issues if i.get("severity") in ("error", "warning")]
    return "\n".join(lines)


# ═══════════════════════════════════════════════════════════
# ⑪ 使用日志（召回/采用/放弃 —— 效果归因的唯一来源）
# ═══════════════════════════════════════════════════════════

def _log_event_direct(cur, event, case_id=None, rule_id=None, record_id=None,
                      question="", username="", score=None, rank=None,
                      decision="", reason="", detail=None):
    try:
        cur.execute(
            "INSERT INTO sql_experience_usage (ts, event, case_id, rule_id, record_id, "
            "question, username, score, rank, decision, reason, detail_json) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
            (_now(), event, case_id, rule_id, record_id, question, username,
             score, rank, decision, reason, _json(detail or {})))
    except Exception as e:
        print(f"⚠️ 经验使用日志写入失败（不影响取数）：{e}")


def log_usage(match, record_id=None, question="", username="", ok=None, adopted=True):
    """把一次检索的召回/采用情况落库。

    ★ 为什么连「未采用的原因」也要记？
      评估「误召回率」和「不适用案例复用率」时，只有召回记录是不够的 ——
      必须知道当时**为什么没用它**，否则无法区分「没召回」和「召回了但被正确拒绝」。
    """
    if not match:
        return
    if not (match.cases or match.failures or match.rules or match.rejected):
        return
    conn = _conn()
    cur = conn.cursor()
    try:
        for rank, c in enumerate(match.cases, 1):
            _log_event_direct(cur, "recall", case_id=c["id"], record_id=record_id,
                              question=question, username=username, score=c.get("score"),
                              rank=rank, decision=match.decision,
                              reason="; ".join(c.get("reasons") or []))
            if adopted:
                cur.execute(
                    "UPDATE sql_experience SET reuse_count=reuse_count+1, last_used_at=?, "
                    "reuse_success=reuse_success+?, reuse_fail=reuse_fail+? WHERE id=?",
                    (_now(), 1 if ok else 0, 0 if ok else 1, c["id"]))
        for f in match.failures:
            _log_event_direct(cur, "recall", case_id=f["id"], record_id=record_id,
                              question=question, username=username, score=f.get("score"),
                              decision="prevent", reason="failure-pattern")
        for r in match.rules:
            _log_event_direct(cur, "recall", rule_id=r["id"], record_id=record_id,
                              question=question, username=username, score=r.get("score"),
                              decision="reference", reason="rule")
        # ★ 未采用的案例按「原因」聚合成一条，而不是每条案例一行：
        #   一次查询可能有 30+ 个候选被挡掉，逐条记会把日志表撑爆，
        #   而排查误召回真正需要的是「因为什么被挡、挡了多少条」。
        by_reason = {}
        for rj in match.rejected:
            key = (rj.get("stage") or "", rj.get("reason") or "")
            by_reason.setdefault(key, []).append(rj.get("case_no"))
        for (stage, reason), nos in list(by_reason.items())[:8]:
            _log_event_direct(cur, "skip", record_id=record_id, question=question,
                              username=username, reason=reason, decision=stage,
                              detail={"count": len(nos), "cases": nos[:20]})
        conn.commit()
    except Exception as e:
        print(f"⚠️ 经验使用日志落库失败（不影响取数）：{e}")
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════
# ⑫ 规则：读取 / 发布 / 撤销
# ═══════════════════════════════════════════════════════════

def _decode_rule(d):
    if not d:
        return None
    d["tables"] = _unjson(d.get("tables_json"))
    d["metrics"] = _unjson(d.get("metrics_json"))
    d["params"] = _unjson(d.get("params_json"), {})
    d["source_case_ids"] = _unjson(d.get("source_case_ids"))
    d["layer_zh"] = RULE_LAYERS.get(d.get("layer"), d.get("layer"))
    d["status_zh"] = RULE_STATUS_ZH.get(d.get("status"), d.get("status"))
    return d


def _rule_text(r):
    return " ".join(x for x in [r.get("title") or "", r.get("content") or "",
                                r.get("scope") or "", " ".join(r.get("metrics") or []),
                                " ".join(r.get("tables") or [])] if x)


def list_rules(status=None, layer=None, limit=200):
    where, args = [], []
    if status:
        where.append("status=?")
        args.append(status)
    if layer:
        where.append("layer=?")
        args.append(layer)
    sql = "SELECT * FROM sql_experience_rules"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = _conn()
    rows = conn.execute(sql, args).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience_rules LIMIT 1").description] \
        if rows else []
    conn.close()
    return [_decode_rule(_row_dict(cols, r)) for r in rows]


def get_rule(rule_id):
    conn = _conn()
    row = conn.execute("SELECT * FROM sql_experience_rules WHERE id=?", (rule_id,)).fetchone()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience_rules LIMIT 1").description] \
        if row else []
    conn.close()
    return _decode_rule(_row_dict(cols, row))


def upsert_rule(rule, operator="ai", status="candidate"):
    """写一条规则（AI 归纳产物一律 candidate）。"""
    now = _now()
    conn = _conn()
    cur = conn.cursor()
    try:
        rule_no = _next_rule_no(cur)
        cur.execute(
            "INSERT INTO sql_experience_rules (rule_no, topic, layer, title, content, "
            "template_sql, params_json, tables_json, metrics_json, scope, exceptions, "
            "source_case_ids, source_count, conflict_note, confidence, status, version, "
            "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (rule_no, rule.get("topic") or "", rule.get("layer") or "sql_template",
             rule.get("title") or "", rule.get("content") or "",
             rule.get("template_sql") or "", _json(rule.get("params") or {}),
             _json(rule.get("tables") or []), _json(rule.get("metrics") or []),
             rule.get("scope") or "", rule.get("exceptions") or "",
             _json(rule.get("source_case_ids") or []), len(rule.get("source_case_ids") or []),
             rule.get("conflict_note") or "", rule.get("confidence") or "low",
             status, 1, operator, now, now))
        rid = cur.lastrowid
        _history(cur, "sql_experience_rules", rid, "create", None, rule, operator)
        _audit(cur, operator, "create", "sql_experience_rules", rid,
               {"status": status, "sources": rule.get("source_case_ids")})
        conn.commit()
    finally:
        conn.close()
    return rid


def publish_rule(rule_id, operator="system", note="", force=False):
    """发布规则（进检索）。★ 有冲突未处理的规则默认不许发布。"""
    r = get_rule(rule_id)
    if not r:
        return False, f"规则不存在：{rule_id}"
    if r.get("status") == "approved":
        return False, "该规则已发布"
    if r.get("conflict_note") and not force:
        return False, f"存在未处理的冲突：{r['conflict_note']}（需显式确认后才能发布）"
    if not r.get("source_case_ids"):
        return False, "规则缺少来源案例，禁止发布（无法追溯）"
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sql_experience_rules SET status='approved', published_by=?, "
            "published_at=?, updated_at=?, version=version+1, revoked_reason=NULL WHERE id=?",
            (operator, _now(), _now(), rule_id))
        _history(cur, "sql_experience_rules", rule_id, "approve", r,
                 dict(r, status="approved"), operator)
        _audit(cur, operator, "publish", "sql_experience_rules", rule_id,
               {"note": note, "force": force})
        conn.commit()
    finally:
        conn.close()
    return True, "已发布（下次检索生效）"


def revoke_rule(rule_id, reason, operator="system"):
    if not (reason or "").strip():
        return False, "撤销必须写清原因"
    r = get_rule(rule_id)
    if not r:
        return False, f"规则不存在：{rule_id}"
    conn = _conn()
    cur = conn.cursor()
    try:
        cur.execute(
            "UPDATE sql_experience_rules SET status='revoked', revoked_reason=?, "
            "updated_at=?, version=version+1 WHERE id=?", (reason, _now(), rule_id))
        _history(cur, "sql_experience_rules", rule_id, "revoke", r,
                 dict(r, status="revoked"), operator)
        _audit(cur, operator, "revoke", "sql_experience_rules", rule_id, {"reason": reason})
        conn.commit()
    finally:
        conn.close()
    return True, "已撤销"


# ═══════════════════════════════════════════════════════════
# ⑬ AI 归纳（P1）：只产出候选，绝不自动发布
# ═══════════════════════════════════════════════════════════

_MINE_SYSTEM = """你是企业数据团队的 SQL 经验归纳助手。
你的任务：从给定的**已审核案例**中归纳可复用的规则，而不是复述某一条 SQL。

硬性要求：
1. 只能依据输入中给出的案例编号（case_no）进行归纳，禁止引用不存在的案例。
2. 每条规则必须写成「一句话规则 + 适用范围 + 例外」，能被另一个人直接照着写 SQL。
3. 如果输入案例之间存在口径冲突或反例，必须在 conflict_note 里写清楚，不许掩盖。
4. 不确定的内容写「待确认」，禁止编造表名、字段名、指标公式。
5. 只输出 JSON，不要解释，不要 markdown 代码块。

输出格式：
{"rules":[{"title":"...","layer":"metric_rule|table_selection|entity|join_rule|sql_template|error_pattern",
"content":"...","template_sql":"... 或 空","scope":"...","exceptions":"...",
"source_case_nos":["EXP-..."],"conflict_note":"...","confidence":"low|medium|high"}]}"""


def _group_cases(cases):
    """按「指标优先、否则主表」分组（主题 = 归纳的最小单位）。"""
    groups = {}
    for c in cases:
        metrics = [m for m in (c.get("metrics") or []) if m]
        tables = [t for t in (c.get("tables") or []) if t]
        topic = metrics[0] if metrics else (tables[0] if tables else "未归类")
        groups.setdefault(topic, []).append(c)
    return groups


def _conflict_of(rule, metrics_map):
    """确定性冲突检测：规则涉及的表/指标，与已核准指标定义里的表是否矛盾。

    ★ 不用模型判断冲突 —— 冲突必须是可解释、可复核的。
    """
    notes = []
    for mname in rule.get("metrics") or []:
        m = metrics_map.get(mname)
        if not m:
            continue
        m_tables = _table_set(m.get("tables"))
        r_tables = _table_set(rule.get("tables"))
        if m_tables and r_tables and not (m_tables & r_tables):
            notes.append(f"规则用表 {sorted(r_tables)} 与指标「{mname}」的核准用表 "
                         f"{sorted(m_tables)} 不一致")
    return "；".join(notes)


def mine_rules(topic=None, limit=40, dry_run=False, operator="ai",
               min_support=2, incremental=True):
    """按主题分组 → 去重 → 归纳候选规则。

    ★ 三条门槛（防止「一条未验证 SQL 就产出全局规则」）：
      成功规则：≥ min_support 条「已审核+已验证」案例，或 1 条 result_verified；
      失败规则：≥ min_support 条同类失败且根因已确认，或 1 条根因已确认 + 有修正 SQL；
      所有产物 status=candidate，必须人工发布。
    """
    import llm
    import config_store

    conn = _conn()
    rows = conn.execute(
        "SELECT * FROM sql_experience WHERE status='active' AND kind IN ('success','failure') "
        "ORDER BY id DESC LIMIT 2000").fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience LIMIT 1").description]
    cases = [_decode_case(_row_dict(cols, r)) for r in rows]
    conn.close()

    # 只看「够格」的案例：成功要 approved 且已验证；失败要已分类
    good = [c for c in cases if c["kind"] == "success"
            and c.get("review_status") == "approved" and c.get("exec_ok")
            and c.get("verify_status") in ("reexec_ok", "manual_confirmed", "result_verified")]
    bad = [c for c in cases if c["kind"] == "failure" and c.get("failure_type")
           and c["failure_type"] != "unknown"
           and (c.get("root_cause_confirmed") or c.get("corrected_sql"))]

    # 去重：同一 SQL 指纹只留一条（dup_of 指向规范案例）
    def _dedup(lst):
        seen, out = set(), []
        for c in lst:
            h = c.get("sql_hash")
            if h and h in seen:
                continue
            seen.add(h)
            out.append(c)
        return out

    good, bad = _dedup(good), _dedup(bad)
    groups = _group_cases(good + bad)
    metrics_map = {m["name"]: m for m in config_store.get_metrics(enabled_only=False)}

    results = []
    for gtopic, items in groups.items():
        if topic and topic != gtopic:
            continue
        g_good = [c for c in items if c["kind"] == "success"]
        g_bad = [c for c in items if c["kind"] == "failure"]
        if len(g_good) < min_support and not any(
                c.get("verify_status") == "result_verified" for c in g_good):
            g_good = []
        if len(g_bad) < min_support and not any(
                c.get("corrected_sql") and c.get("root_cause_confirmed") for c in g_bad):
            g_bad = []
        if not g_good and not g_bad:
            continue

        payload = []
        for c in (g_good + g_bad)[:limit]:
            payload.append({
                "case_no": c.get("case_no"), "kind": c["kind"],
                "question": c.get("question"),
                "review": c.get("review_status"), "verify": c.get("verify_status"),
                "failure_type": c.get("failure_type"),
                "root_cause": c.get("root_cause"),
                "sql": _short_sql(c.get("sql_raw"), 900),
                "corrected_sql": _short_sql(c.get("corrected_sql"), 900) if c.get("corrected_sql") else "",
                "tables": c.get("tables"), "metrics": c.get("metrics"), "grain": c.get("grain"),
            })
        user = ("主题：" + str(gtopic) + "\n\n案例：\n" +
                json.dumps(payload, ensure_ascii=False, indent=1) +
                "\n\n请只输出 JSON。")

        if dry_run:
            results.append({"topic": gtopic, "cases": len(payload), "status": "dry_run"})
            continue
        try:
            raw = llm.chat(_MINE_SYSTEM, user)
        except Exception as e:
            results.append({"topic": gtopic, "error": f"模型调用失败：{e}"})
            continue
        obj = _parse_json_obj(raw)
        if not obj or not isinstance(obj.get("rules"), list):
            results.append({"topic": gtopic, "error": "模型输出无法解析为 JSON"})
            continue

        by_no = {c.get("case_no"): c for c in (g_good + g_bad)}
        for r in obj["rules"][:5]:
            # ★ 出处校验：模型提到的案例编号必须真实存在，否则拒绝该条（防幻觉）
            nos = [n for n in (r.get("source_case_nos") or []) if n in by_no]
            if not nos:
                results.append({"topic": gtopic,
                                "error": f"规则「{r.get('title')}」没有有效来源案例，已丢弃"})
                continue
            ids = [by_no[n]["id"] for n in nos]
            rule = {
                "topic": gtopic,
                "layer": r.get("layer") if r.get("layer") in RULE_LAYERS else "sql_template",
                "title": (r.get("title") or "")[:200],
                "content": r.get("content") or "",
                "template_sql": r.get("template_sql") or "",
                "scope": r.get("scope") or "",
                "exceptions": r.get("exceptions") or "",
                "source_case_ids": ids,
                "tables": sorted({t for n in nos for t in (by_no[n].get("tables") or [])}),
                "metrics": sorted({m for n in nos for m in (by_no[n].get("metrics") or [])}),
                "confidence": r.get("confidence") if r.get("confidence") in ("low", "medium", "high") else "low",
            }
            rule["conflict_note"] = _conflict_of(rule, metrics_map) or (r.get("conflict_note") or "")
            rid = upsert_rule(rule, operator=operator,
                              status="draft" if rule["conflict_note"] else "candidate")
            results.append({"rule_id": rid, "topic": gtopic, "title": rule["title"],
                            "status": "draft" if rule["conflict_note"] else "candidate",
                            "sources": nos, "conflict": rule["conflict_note"]})

    # 增量游标：记录本次归纳时间（下次可只处理更新的案例）
    try:
        conn = _conn()
        conn.execute("INSERT OR REPLACE INTO index_state (key, status, counts_json, built_at) "
                     "VALUES ('exp_mine', 'ok', ?, ?)",
                     (_json({"rules": len(results), "topic": topic or "all"}), _now()))
        conn.commit()
        conn.close()
    except Exception:
        pass
    return results


def _parse_json_obj(raw):
    if not raw:
        return None
    try:
        m = re.search(r"\{.*\}", raw, re.S)
        return json.loads(m.group(0)) if m else None
    except Exception:
        return None


# ═══════════════════════════════════════════════════════════
# ⑭ 查询 / 统计 / 影响面
# ═══════════════════════════════════════════════════════════

def list_cases(kind=None, trust=None, review=None, verify=None, status=None,
               failure_type=None, q=None, source_type=None, limit=100, offset=0):
    where, args = [], []
    for col, val in (("kind", kind), ("trust_level", trust), ("review_status", review),
                     ("verify_status", verify), ("status", status),
                     ("failure_type", failure_type), ("source_type", source_type)):
        if val:
            where.append(f"{col}=?")
            args.append(val)
    if q:
        where.append("(question LIKE ? OR sql_raw LIKE ? OR case_no LIKE ?)")
        args += [f"%{q}%"] * 3
    sql = "SELECT * FROM sql_experience"
    if where:
        sql += " WHERE " + " AND ".join(where)
    sql += " ORDER BY id DESC LIMIT ? OFFSET ?"
    args += [limit, offset]
    conn = _conn()
    rows = conn.execute(sql, args).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience LIMIT 1").description] \
        if rows else []
    total = conn.execute(
        "SELECT COUNT(*) FROM sql_experience" + (" WHERE " + " AND ".join(where) if where else ""),
        args[:-2]).fetchone()[0]
    conn.close()
    return {"total": total, "items": [_decode_case(_row_dict(cols, r)) for r in rows]}


def get_case(case_id):
    conn = _conn()
    row = conn.execute("SELECT * FROM sql_experience WHERE id=?", (case_id,)).fetchone()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience LIMIT 1").description] \
        if row else []
    case = _decode_case(_row_dict(cols, row)) if row else None
    if not case:
        conn.close()
        return None
    rel = conn.execute(
        "SELECT id, case_no, kind, question, sql_raw, trust_level FROM sql_experience "
        "WHERE correction_of=? OR id=? OR dup_of=?", (case_id, case.get("correction_of") or -1, case_id)
    ).fetchall()
    conn.close()
    case["related"] = [{"id": r[0], "case_no": r[1], "kind": r[2], "question": r[3],
                        "trust": r[5]} for r in rel if r[0] != case_id]
    return case


def list_usage(event=None, limit=100):
    sql = "SELECT * FROM sql_experience_usage"
    args = []
    if event:
        sql += " WHERE event=?"
        args.append(event)
    sql += " ORDER BY id DESC LIMIT ?"
    args.append(limit)
    conn = _conn()
    rows = conn.execute(sql, args).fetchall()
    cols = [c[0] for c in conn.execute("SELECT * FROM sql_experience_usage LIMIT 1").description] \
        if rows else []
    conn.close()
    return [_row_dict(cols, r) for r in rows]


def overview():
    """管理页/验收用的总览（所有数字都来自真实库，不做估算）"""
    conn = _conn()
    def _count(sql, args=()):
        return conn.execute(sql, args).fetchone()[0]
    d = {
        "cases_total": _count("SELECT COUNT(*) FROM sql_experience"),
        "by_kind": {k: _count("SELECT COUNT(*) FROM sql_experience WHERE kind=?", (k,))
                    for k in ("success", "failure", "correction")},
        "by_trust": {k: _count("SELECT COUNT(*) FROM sql_experience WHERE trust_level=?", (k,))
                     for k in ("candidate", "golden_candidate", "golden", "revoked")},
        "by_review": {k: _count("SELECT COUNT(*) FROM sql_experience WHERE review_status=?", (k,))
                      for k in ("none", "pending", "approved", "rejected")},
        "by_verify": {k: _count("SELECT COUNT(*) FROM sql_experience WHERE verify_status=?", (k,))
                      for k in VERIFY_STATUS_ZH},
        "kind_x_review": [list(r) for r in conn.execute(
            "SELECT kind, review_status, COUNT(*) FROM sql_experience GROUP BY 1,2").fetchall()],
        "approved_but_failed": _count(
            "SELECT COUNT(*) FROM sql_experience WHERE review_status='approved' AND exec_ok=0"),
        "rules_total": _count("SELECT COUNT(*) FROM sql_experience_rules"),
        "by_rule_status": {k: _count("SELECT COUNT(*) FROM sql_experience_rules WHERE status=?", (k,))
                           for k in RULE_STATUS_ZH},
        "usage_total": _count("SELECT COUNT(*) FROM sql_experience_usage"),
        "recall_total": _count("SELECT COUNT(*) FROM sql_experience_usage WHERE event='recall'"),
        "reuse_total": _count(
            "SELECT COALESCE(SUM(reuse_count),0) FROM sql_experience WHERE trust_level IN "
            "('golden','golden_candidate')"),
    }
    conn.close()
    return d


def affected_cases(table=None, metric=None, limit=200):
    """表结构/口径变化时，找出可能受影响的案例（供「过期识别」用）。

    ★ 只做「可能受影响」的提示，不自动改状态 ——
      自动判定失效很容易误伤（同名字段换了含义这种事只有人看得出来）。
    """
    where, args = [], []
    if table:
        where.append("tables_json LIKE ?")
        args.append(f'%"{table}"%')
    if metric:
        where.append("metrics_json LIKE ?")
        args.append(f'%"{metric}"%')
    if not where:
        return []
    conn = _conn()
    sql = ("SELECT id, case_no, kind, question, trust_level, verify_status, updated_at "
           "FROM sql_experience WHERE status='active' AND (" + " OR ".join(where) + ") "
           "ORDER BY trust_level DESC LIMIT ?")
    args.append(limit)
    rows = conn.execute(sql, args).fetchall()
    conn.close()
    return [{"id": r[0], "case_no": r[1], "kind": r[2], "question": r[3],
             "trust": r[4], "verify": r[5], "updated_at": r[6]} for r in rows]


def mark_stale(case_ids, reason, operator="system"):
    """把案例标记为待复核（退出正向召回，但保留历史）"""
    if not case_ids:
        return 0
    conn = _conn()
    cur = conn.cursor()
    n = 0
    for cid in case_ids:
        cur.execute("UPDATE sql_experience SET status='stale', trust_level=?, "
                    "golden_published=0, review_note=COALESCE(review_note,'')||?, "
                    "updated_at=? WHERE id=? AND status='active'",
                    ("candidate", f" ｜ 待复核：{reason}", _now(), cid))
        n += cur.rowcount
    _history(cur, "sql_experience", ",".join(str(x) for x in case_ids), "stale",
             None, {"reason": reason}, operator)
    conn.commit()
    conn.close()
    return n
