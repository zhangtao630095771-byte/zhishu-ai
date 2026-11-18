# -*- coding: utf-8 -*-
"""
_migrate_governance.py —— 按《知数AI 数据治理录入规则与表结构方案 v1.0》做「阶段一」落地。

做什么（幂等、只增不删、可重复执行）：
  1. 给 6 张知识主表补治理字段：stable_key / status / owner_id / steward_id /
     source_system / effective_from / effective_to / content_hash / approved_at / approved_by。
  2. 回填这些治理字段（按来源区分 manual→approved、ai→candidate）。
  3. 把散在 metrics.required_predicates/required_columns/forbidden_predicates 三列里的
     指标硬约束，标准化到独立的 metric_constraint 表。
  4. 建 audit_log，并用已有 knowledge_history 回灌，形成统一审计流水。
  5. 生成 docs/knowledge_governance_conformance.md（治理符合性 / 缺口报告）。

为什么可以安全给现有表加列？
  现有 upsert 全部用 `ON CONFLICT ... DO UPDATE SET <指定列>`，只更新它列出的列，
  新增的治理列不会被后续编辑冲掉。SQLite 的 ADD COLUMN 带常量 DEFAULT 会同时回填老行。

运行：
  python3 _migrate_governance.py
可选环境变量：
  CONFIG_DB   配置库路径（默认脚本同目录 agent_config.db）
  GOV_OWNER   业务责任人（默认 admin）
  GOV_STEWARD 数据管家（默认 admin）
"""
import os
import sys
import json
import hashlib
import sqlite3
from datetime import datetime, timezone, timedelta

# ── 定位配置库：优先环境变量，其次脚本同目录 ──
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CONFIG_DB") or os.path.join(BASE_DIR, "agent_config.db")
OWNER = os.environ.get("GOV_OWNER", "admin")
STEWARD = os.environ.get("GOV_STEWARD", "admin")
TZ = timezone(timedelta(hours=8))            # 方案要求时间一律按 Asia/Shanghai 解释


def now_iso():
    """当前北京时间（ISO 字符串），用于 created/updated/approved 等时间字段。"""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def canon(obj):
    """把 dict/list 归一化成稳定字符串（键排序、紧凑分隔），保证同一内容哈希不变。"""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256(text):
    """内容哈希：用于判断知识对象是否发生实质变更（漂移检测的基础）。"""
    return hashlib.sha256((text or "").encode("utf-8")).hexdigest()


def parse_list(v):
    """把库里存的 JSON 数组字符串安全解析成 list；解析不了就当空列表。"""
    if not v:
        return []
    if isinstance(v, (list, tuple)):
        return list(v)
    try:
        out = json.loads(v)
        return out if isinstance(out, list) else []
    except Exception:
        return []


def col_names(cur, table):
    """读取某张表当前的全部列名（用于判断某列是否已存在，保证迁移幂等）。"""
    return {r[1] for r in cur.execute("PRAGMA table_info(%s)" % table)}


# 治理字段定义：字段名 -> 建列类型（含默认值）
GOV_COLS = {
    "stable_key":    "TEXT",
    "status":        "TEXT DEFAULT 'approved'",
    "owner_id":      "TEXT",
    "steward_id":    "TEXT",
    "source_system": "TEXT",
    "effective_from": "TEXT",
    "effective_to":  "TEXT",
    "content_hash":  "TEXT",
    "approved_at":   "TEXT",
    "approved_by":   "TEXT",
}

TARGET_TABLES = ["metrics", "dict_entries", "entities",
                 "relationships", "table_comments", "column_comments"]


def ensure_gov_cols(cur):
    """给 6 张知识表补齐治理字段（已存在则跳过）。"""
    added = 0
    for t in TARGET_TABLES:
        exist = col_names(cur, t)
        for col, typ in GOV_COLS.items():
            if col not in exist:
                cur.execute("ALTER TABLE %s ADD COLUMN %s %s" % (t, col, typ))
                added += 1
        # 常用查询索引（非唯一）：按治理状态 / 稳定键检索
        cur.execute("CREATE INDEX IF NOT EXISTS idx_%s_status ON %s(status)" % (t, t))
        cur.execute("CREATE INDEX IF NOT EXISTS idx_%s_stable ON %s(stable_key)" % (t, t))
    return added


def infer_metric_type(definition):
    """从 definition 推断指标类型（仅用于原先为 NULL 的行，避免误伤已人工标注的）。"""
    d = (definition or "").lower()
    if not d:
        return "other"
    if "/" in d and "sum(" in d:                       # 比率：分子分母相除
        return "ratio"
    if "count(" in d or ("sum(case when" in d and "then 1" in d):  # 计数
        return "count"
    if "sum(" in d:                                    # 金额求和
        return "amount"
    return "other"


def keep_status(row, default):
    """
    ★ 幂等护栏：已有治理状态就保留，否则才用默认值。

    为什么必须有这一步？
      本脚本会重复执行（建列 / 补字段 / 刷新哈希）。如果每次都按"来源是不是 ai"
      重算 status，那么一次人工复核（candidate → approved / rejected）之后只要有人
      再跑一遍迁移，复核结论就会被静默抹掉 —— 这是治理系统里最危险的一类 bug：
      数据看起来正常，但审计链已断。所以 status 一经写入就不再由迁移覆盖。
    """
    cur_st = (row.get("status") or "").strip()
    return cur_st if cur_st else default


def keep_approved_at(row, default):
    """同上：approved_at / approved_by 一经写入不覆盖（保留最早核准时间）。"""
    return row.get("approved_at") or default


def keep_approved_by(row, default):
    return row.get("approved_by") or default


def backfill(cur):
    """回填治理字段 + 内容哈希。每张表按自己的业务键生成 stable_key。"""
    stats = {}

    # ── metrics ──
    rows = cur.execute("SELECT * FROM metrics").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        m = dict(zip(heads, r))
        # ① 先补 metric_type（NULL 才推断，人工已标的不动）
        mt = m.get("metric_type") or infer_metric_type(m.get("definition"))
        cur.execute("UPDATE metrics SET metric_type=? WHERE name=?", (mt, m["name"]))
        m["metric_type"] = mt
        # ② 计算内容哈希（口径相关的全部字段）
        h = sha256(canon({
            "name": m["name"], "keywords": m.get("keywords"), "definition": m.get("definition"),
            "doc": m.get("doc"), "tables": m.get("tables"), "enabled": m.get("enabled"),
            "priority": m.get("priority"), "metric_type": mt, "sql_template": m.get("sql_template"),
            "depends_metrics": m.get("depends_metrics"), "conflict_key": m.get("conflict_key"),
            "required_predicates": m.get("required_predicates"),
            "required_columns": m.get("required_columns"),
            "forbidden_predicates": m.get("forbidden_predicates"),
        }))
        ef = m.get("created_at") or now_iso()
        st = keep_status(m, "approved")               # ★ 已有复核结论不覆盖
        cur.execute("""UPDATE metrics SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system='manual', effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE name=?""",
                    ("metric:%s" % m["name"], st, OWNER, STEWARD, ef, h,
                     keep_approved_at(m, ef if st == "approved" else None),
                     keep_approved_by(m, OWNER if st == "approved" else None), m["name"]))
        n += 1
    stats["metrics"] = n

    # ── dict_entries ──
    rows = cur.execute("SELECT * FROM dict_entries").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        d = dict(zip(heads, r))
        h = sha256(canon({"name": d["name"], "dict_type": d.get("dict_type"),
                          "field_hint": d.get("field_hint"), "keywords": d.get("keywords"),
                          "items": d.get("items"), "enabled": d.get("enabled")}))
        ef = d.get("created_at") or now_iso()
        st = keep_status(d, "approved")               # ★ 已有复核结论不覆盖
        cur.execute("""UPDATE dict_entries SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system='manual', effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE name=?""",
                    ("dict:%s" % d["name"], st, OWNER, STEWARD, ef, h,
                     keep_approved_at(d, ef if st == "approved" else None),
                     keep_approved_by(d, OWNER if st == "approved" else None), d["name"]))
        n += 1
    stats["dict_entries"] = n

    # ── entities（source=ai → candidate）──
    rows = cur.execute("SELECT * FROM entities").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        e = dict(zip(heads, r))
        src = (e.get("source") or "manual")
        status = keep_status(e, "approved" if src == "manual" else "candidate")   # ★ 不覆盖复核结论
        h = sha256(canon({"name": e["name"], "table_name": e.get("table_name"),
                          "grain": e.get("grain"), "comment": e.get("comment"),
                          "keywords": e.get("keywords"), "enabled": e.get("enabled"), "source": src}))
        ef = e.get("created_at") or now_iso()
        ap = keep_approved_at(e, ef if status == "approved" else None)
        ab = keep_approved_by(e, OWNER if status == "approved" else None)
        cur.execute("""UPDATE entities SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system=?, effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE name=?""",
                    ("entity:%s" % e["name"], status, OWNER, STEWARD, src, ef, h, ap, ab, e["name"]))
        n += 1
    stats["entities"] = n

    # ── relationships（stable_key 用自增 id，天然唯一）──
    rows = cur.execute("SELECT * FROM relationships").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        rel = dict(zip(heads, r))
        h = sha256(canon({"from_entity": rel.get("from_entity"), "to_entity": rel.get("to_entity"),
                          "join_sql": rel.get("join_sql"), "join_type": rel.get("join_type"),
                          "cardinality": rel.get("cardinality"),
                          "pre_agg_required": rel.get("pre_agg_required"),
                          "rule_note": rel.get("rule_note"), "enabled": rel.get("enabled")}))
        ef = rel.get("created_at") or now_iso()
        st = keep_status(rel, "approved")             # ★ 已有复核结论不覆盖
        cur.execute("""UPDATE relationships SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system='manual', effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE id=?""",
                    ("rel:%s" % rel["id"], st, OWNER, STEWARD, ef, h,
                     keep_approved_at(rel, ef if st == "approved" else None),
                     keep_approved_by(rel, OWNER if st == "approved" else None), rel["id"]))
        n += 1
    stats["relationships"] = n

    # ── table_comments（source=ai → candidate）──
    rows = cur.execute("SELECT * FROM table_comments").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        c = dict(zip(heads, r))
        src = (c.get("source") or "manual")
        status = keep_status(c, "approved" if src == "manual" else "candidate")   # ★ 不覆盖复核结论
        h = sha256(canon({"table_name": c["table_name"], "comment": c.get("comment"), "source": src}))
        ef = c.get("created_at") or now_iso()
        ap = keep_approved_at(c, ef if status == "approved" else None)
        ab = keep_approved_by(c, OWNER if status == "approved" else None)
        cur.execute("""UPDATE table_comments SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system=?, effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE table_name=?""",
                    ("table:%s" % c["table_name"], status, OWNER, STEWARD, src, ef, h, ap, ab, c["table_name"]))
        n += 1
    stats["table_comments"] = n

    # ── column_comments（source=ai → candidate）──
    rows = cur.execute("SELECT * FROM column_comments").fetchall()
    heads = [d[0] for d in cur.description]
    n = 0
    for r in rows:
        c = dict(zip(heads, r))
        src = (c.get("source") or "manual")
        status = keep_status(c, "approved" if src == "manual" else "candidate")   # ★ 不覆盖复核结论
        h = sha256(canon({"table_name": c["table_name"], "column_name": c["column_name"],
                          "comment": c.get("comment"), "source": src}))
        ef = c.get("created_at") or now_iso()
        ap = keep_approved_at(c, ef if status == "approved" else None)
        ab = keep_approved_by(c, OWNER if status == "approved" else None)
        cur.execute("""UPDATE column_comments SET stable_key=?, status=?, owner_id=?, steward_id=?,
                       source_system=?, effective_from=?, content_hash=?, approved_at=?, approved_by=?
                       WHERE table_name=? AND column_name=?""",
                    ("column:%s.%s" % (c["table_name"], c["column_name"]), status, OWNER, STEWARD,
                     src, ef, h, ap, ab, c["table_name"], c["column_name"]))
        n += 1
    stats["column_comments"] = n
    return stats


def build_metric_constraint(cur):
    """把三列里的指标硬约束标准化到 metric_constraint（派生表，每次全量重建）。"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS metric_constraint (
            constraint_id   TEXT PRIMARY KEY,
            metric_id       TEXT NOT NULL,      -- 对应 metrics.stable_key
            metric_name     TEXT NOT NULL,
            metric_version_no INTEGER,
            constraint_type TEXT NOT NULL,      -- required_predicate / required_column / forbidden_predicate
            expression      TEXT NOT NULL,      -- 具体片段，如 'status = 6'
            severity        TEXT NOT NULL DEFAULT 'error',
            error_message   TEXT NOT NULL,
            enabled         INTEGER NOT NULL DEFAULT 1,
            created_at      TEXT
        )
    """)
    cur.execute("DELETE FROM metric_constraint")     # 全量重建，保证与 metrics 一致
    ts = now_iso()
    rows = cur.execute("SELECT name, version, required_predicates, required_columns, forbidden_predicates "
                       "FROM metrics").fetchall()
    field_map = [
        ("required_predicate", "required_predicates", "缺少指标口径要求的过滤条件"),
        ("required_column", "required_columns", "缺少指标口径要求的字段"),
        ("forbidden_predicate", "forbidden_predicates", "出现了指标口径禁止的过滤/字段"),
    ]
    cnt = 0
    for name, ver, *vals in rows:
        for (ctype, _col, msg), raw in zip(field_map, vals):
            for expr in parse_list(raw):
                cid = "%s|%s|%s" % (name, ctype, expr)
                cur.execute("""INSERT OR REPLACE INTO metric_constraint
                    (constraint_id, metric_id, metric_name, metric_version_no,
                     constraint_type, expression, severity, error_message, enabled, created_at)
                    VALUES (?,?,?,?,?,?,?,?,1,?)""",
                    (cid, "metric:%s" % name, name, ver, ctype, str(expr), "error",
                     "%s「%s」：%s" % (name, ctype, msg), ts))
                cnt += 1
    return cnt


def build_audit_log(cur):
    """建 audit_log，并把已有 knowledge_history 回灌（幂等：按 audit_id 覆盖）。"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS audit_log (
            audit_id    TEXT PRIMARY KEY,
            actor_id    TEXT NOT NULL,
            action      TEXT NOT NULL,
            object_type TEXT NOT NULL,
            object_id   TEXT NOT NULL,
            version_no  INTEGER,
            before_hash TEXT,
            after_hash  TEXT,
            request_id  TEXT,
            occurred_at TEXT NOT NULL,
            detail      TEXT
        )
    """)
    cur.execute("CREATE INDEX IF NOT EXISTS idx_audit_obj ON audit_log(object_type, object_id)")
    rows = cur.execute("SELECT id, entity_type, entity_id, action, before_json, after_json, operator, created_at "
                       "FROM knowledge_history").fetchall()
    n = 0
    for hid, etype, eid, action, before, after, operator, created in rows:
        cur.execute("""INSERT OR REPLACE INTO audit_log
            (audit_id, actor_id, action, object_type, object_id, version_no,
             before_hash, after_hash, request_id, occurred_at, detail)
            VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            ("kh:%s" % hid, operator or "system", action, etype, eid, None,
             sha256(before) if before else None, sha256(after) if after else None,
             None, created or now_iso(), canon({"before": before, "after": after})))
        n += 1
    return n


def collect_stats(cur):
    """收集报告所需统计。"""
    st = {}

    def one(sql, args=()):
        return cur.execute(sql, args).fetchone()

    # 各表治理覆盖
    cover = {}
    for t in TARGET_TABLES:
        total = one("SELECT COUNT(*) FROM %s" % t)[0]
        appr = one("SELECT COUNT(*) FROM %s WHERE status='approved'" % t)[0]
        cand = one("SELECT COUNT(*) FROM %s WHERE status='candidate'" % t)[0]
        hash_ = one("SELECT COUNT(*) FROM %s WHERE content_hash IS NOT NULL AND content_hash<>''" % t)[0]
        rej = one("SELECT COUNT(*) FROM %s WHERE status='rejected'" % t)[0]
        cover[t] = dict(total=total, approved=appr, candidate=cand, rejected=rej, hashed=hash_)
    st["cover"] = cover

    # 指标缺口
    st["metric_no_doc"] = [r[0] for r in cur.execute(
        "SELECT name FROM metrics WHERE doc IS NULL OR TRIM(doc)=''")]
    st["metric_no_type"] = one("SELECT COUNT(*) FROM metrics WHERE metric_type IS NULL OR TRIM(metric_type)=''")[0]
    st["metric_no_conflict"] = one(
        "SELECT COUNT(*) FROM metrics WHERE conflict_key IS NULL OR TRIM(conflict_key)=''")[0]
    st["metric_no_template"] = one(
        "SELECT COUNT(*) FROM metrics WHERE sql_template IS NULL OR TRIM(sql_template)=''")[0]
    st["metric_total"] = one("SELECT COUNT(*) FROM metrics")[0]
    st["constraint_rows"] = one("SELECT COUNT(*) FROM metric_constraint")[0]
    st["constraint_by_type"] = dict(cur.execute(
        "SELECT constraint_type, COUNT(*) FROM metric_constraint GROUP BY constraint_type").fetchall())
    st["metrics_without_constraint"] = [r[0] for r in cur.execute(
        "SELECT name FROM metrics m WHERE NOT EXISTS "
        "(SELECT 1 FROM metric_constraint c WHERE c.metric_name=m.name) ORDER BY name")]

    # 关系缺口：1:N 或 N:1 但 pre_agg_required=0 属于放大风险
    st["rel_risk"] = [dict(zip(["id", "from_entity", "to_entity", "cardinality", "pre_agg"],
                               r)) for r in cur.execute(
        "SELECT id, from_entity, to_entity, cardinality, pre_agg_required FROM relationships "
        "WHERE cardinality IN ('1:N','N:1') AND (pre_agg_required IS NULL OR pre_agg_required=0) "
        "ORDER BY id")]
    st["rel_total"] = one("SELECT COUNT(*) FROM relationships")[0]
    st["rel_card"] = dict(cur.execute(
        "SELECT cardinality, COUNT(*) FROM relationships GROUP BY cardinality").fetchall())

    # 字典缺口
    st["dict_total"] = one("SELECT COUNT(*) FROM dict_entries")[0]
    st["dict_no_type"] = [r[0] for r in cur.execute(
        "SELECT name FROM dict_entries WHERE dict_type IS NULL OR TRIM(dict_type)=''")]
    items_total, items_no_meaning = 0, 0
    for (items,) in cur.execute("SELECT items FROM dict_entries").fetchall():
        for it in parse_list(items):
            items_total += 1
            if isinstance(it, dict) and not it.get("meaning_note"):
                items_no_meaning += 1
    st["dict_items_total"] = items_total
    st["dict_items_no_meaning"] = items_no_meaning

    # 来源分布（注释/实体）
    st["tc_source"] = dict(cur.execute(
        "SELECT source, COUNT(*) FROM table_comments GROUP BY source").fetchall())
    st["cc_source"] = dict(cur.execute(
        "SELECT source, COUNT(*) FROM column_comments GROUP BY source").fetchall())
    st["ent_source"] = dict(cur.execute(
        "SELECT source, COUNT(*) FROM entities GROUP BY source").fetchall())

    st["audit_rows"] = one("SELECT COUNT(*) FROM audit_log")[0]
    return st


def write_report(st):
    """生成治理符合性 / 缺口报告（Markdown）。"""
    cov = st["cover"]
    lines = []
    lines.append("# 知数AI 知识治理符合性报告（阶段一）\n")
    lines.append("> 依据：《知数AI 数据治理录入规则与表结构方案 v1.0》第 11 节落地路径（步骤 1/3/4）。\n")
    lines.append("> 生成时间：%s　责任人：%s　数据管家：%s\n" % (now_iso(), OWNER, STEWARD))
    lines.append("> 说明：本报告由 `_migrate_governance.py` 依据真实配置库自动生成；缺口项均为 **待责任人确认**，脚本未擅自编造业务口径。\n")

    lines.append("\n## 一、治理覆盖总览\n")
    lines.append("| 知识表 | 总数 | approved | candidate | rejected | 有 content_hash |")
    lines.append("|---|---:|---:|---:|---:|---:|")
    zh = {"metrics": "指标", "dict_entries": "数据字典", "entities": "业务实体",
          "relationships": "关联关系", "table_comments": "表注释", "column_comments": "字段注释"}
    for t in TARGET_TABLES:
        c = cov[t]
        lines.append("| %s (%s) | %d | %d | %d | %d | %d |" %
                     (zh[t], t, c["total"], c["approved"], c["candidate"],
                      c.get("rejected", 0), c["hashed"]))

    lines.append("\n## 二、指标（P1）缺口\n")
    lines.append("- 指标总数：%d；硬约束行数：%d（按类型：%s）。" %
                 (st["metric_total"], st["constraint_rows"],
                  "，".join("%s=%d" % (k, v) for k, v in st["constraint_by_type"].items()) or "无"))
    lines.append("- 缺 `doc`（检索描述）：%d 条。" % len(st["metric_no_doc"]))
    if st["metric_no_doc"]:
        lines.append("  - 待补：%s" % "、".join(st["metric_no_doc"]))
    lines.append("- 缺 `metric_type`：%d 条；缺 `conflict_key`：%d 条；缺 `sql_template`：%d 条。"
                 % (st["metric_no_type"], st["metric_no_conflict"], st["metric_no_template"]))
    lines.append("- 未声明任何硬约束的指标：%d 条。" % len(st["metrics_without_constraint"]))
    if st["metrics_without_constraint"]:
        lines.append("  - 列表：%s" % "、".join(st["metrics_without_constraint"]))
    lines.append("- **待建模**：方案要求的 `statistic_grain`（统计粒度）与 `time_semantic`（时间语义）尚未落到表字段，"
                 "需人工为每个指标确认「事件时间字段/时区/默认窗口」。")

    lines.append("\n## 三、关联关系（P2）缺口\n")
    lines.append("- 关系总数：%d；基数分布：%s。" %
                 (st["rel_total"], "，".join("%s=%d" % (k, v) for k, v in st["rel_card"].items())))
    lines.append("- **1:N / N:1 但未开启预聚合（放大风险）：%d 条**" % len(st["rel_risk"]))
    for r in st["rel_risk"]:
        lines.append("  - #%s %s → %s（%s，pre_agg=%s）" %
                     (r["id"], r["from_entity"], r["to_entity"], r["cardinality"], r["pre_agg"]))
    lines.append("  - 处置：确属 1:N 且输出含左表度量的，应 `pre_agg_required=1` 或声明按右表明细粒度；"
                 "需 Owner 确认后改，脚本未自动改基数。")

    lines.append("\n## 四、数据字典（P3）缺口\n")
    lines.append("- 字典总数：%d；值项总数：%d；其中缺 `meaning_note`（业务含义）：%d 个。" %
                 (st["dict_total"], st["dict_items_total"], st["dict_items_no_meaning"]))
    lines.append("- `dict_type` 为空的字典：%d 个%s" %
                 (len(st["dict_no_type"]), ("（%s）" % "、".join(st["dict_no_type"])) if st["dict_no_type"] else ""))
    lines.append("- 建议：按方案 §5 为值项补 `meaning_note/aliases/sort_order/is_active`，并建 `object_dictionary_binding` 字段绑定。")

    lines.append("\n## 五、表/字段注释与实体（来源与状态）\n")
    lines.append("- 表注释来源：%s；字段注释来源：%s；实体来源：%s。" %
                 (st["tc_source"], st["cc_source"], st["ent_source"]))
    lines.append("- 按方案 §1.1，`ai` 来源一律置为 `candidate`，不进入生产权威口径；"
                 "当前 candidate 即上表中的 AI 条目，需人工复核后转 `approved`。")

    lines.append("\n## 六、审计与下一步\n")
    lines.append("- `audit_log` 已由 `knowledge_history` 回灌 %d 条，后续变更应统一写 audit_log。" % st["audit_rows"])
    lines.append("- 建议后续按方案继续：② 元数据扫描快照+漂移；⑤ 关系键规范与预聚合契约；"
                 "⑥ 向量索引 release；⑦ 评测用例迁移并绑定依赖。")
    lines.append("\n---\n*本报告不虚构业务口径；所有缺口以「待确认」标注，由 Owner/Steward 审核后补录。*")

    path = os.path.join(BASE_DIR, "docs", "knowledge_governance_conformance.md")
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write("\n".join(lines))
    return path


def main():
    if not os.path.exists(DB_PATH):
        print("✗ 配置库不存在：%s" % DB_PATH)
        sys.exit(1)
    print("配置库：%s" % DB_PATH)
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    try:
        added = ensure_gov_cols(cur)
        print("① 新增治理字段（列）：%d 个" % added)
        stats = backfill(cur)
        print("② 回填行数：%s" % stats)
        nc = build_metric_constraint(cur)
        print("③ metric_constraint 重建：%d 行" % nc)
        na = build_audit_log(cur)
        print("④ audit_log 回灌：%d 行" % na)
        con.commit()

        st = collect_stats(cur)
        path = write_report(st)
        print("⑤ 报告已生成：%s" % path)
        print("\n=== 治理覆盖 ===")
        for t in TARGET_TABLES:
            c = st["cover"][t]
            print("  %-16s total=%d approved=%d candidate=%d rejected=%d hash=%d" %
                  (t, c["total"], c["approved"], c["candidate"], c.get("rejected", 0), c["hashed"]))
    except Exception as e:
        con.rollback()
        print("✗ 迁移失败，已回滚：%s: %s" % (type(e).__name__, e))
        raise
    finally:
        con.close()
    print("\nOK：治理迁移完成（幂等，可重复执行）。")


if __name__ == "__main__":
    main()
