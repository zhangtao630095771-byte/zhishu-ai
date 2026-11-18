# -*- coding: utf-8 -*-
"""
_seed_semantic_registry.py —— 统一语义注册表（Semantic Registry）建表 + 幂等填充。

★ 背景（企业业务语义知识库建设 · P2 第一步）：
  七类知识分散在 7 张表（metrics/entities/relationships/table_comments/column_comments/
  dict_entries/kb_docs），各自的主键形态、状态字段、层级都不同。Semantic Context Builder
  需要一个「统一登记口」——只读一张表就能拿到「某类知识有哪些、稳定 ID、层级、状态、哈希」。

★ 定位（与 metric_constraint 一致：派生注册表，每次全量重建）：
  - 事实源仍是 7 张原表，本表只是它们的只读投影。
  - stable_key 直接复用各表的 stable_key（已于 P0 阶段回填到 100%）。
  - 幂等：DELETE + 全量 INSERT，可重复执行。

★ 为什么不是 SQL VIEW 而是物化表？
  ① 未来要新增 dimension / concept / process 等维度，它们没有原表，VIEW 装不下；
  ② 注册表要作为 Semantic Context Builder 的单一查询入口，物化后可直接加索引、
     做跨层统计（如「P1 层 candidate 有多少条」），不用每次现 UNION。
  （这是注册表与 metric_constraint 的不同：metric_constraint 是纯派生、无消费者，
   而 semantic_registry 是给后续 Builder 用的正向依赖。）

★ 设计原则：不推倒重建、不引入第二套取数流水线、不绕过权限与 SQL 安全。
  本表只登记元数据（ID/类型/层级/状态/哈希），不承载口径正文；口径仍从原表读。
"""
import os
import sqlite3
from datetime import datetime, timezone, timedelta

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CONFIG_DB") or os.path.join(BASE_DIR, "agent_config.db")
TZ = timezone(timedelta(hours=8))


def _now():
    """当前北京时间（ISO 字符串），与 _migrate_governance.py 对齐。"""
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S")


def _conn():
    con = sqlite3.connect(DB_PATH)
    return con, con.cursor()


def ensure_table(cur):
    """建注册表（幂等）。stable_key 为统一主键。"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS semantic_registry (
            stable_key      TEXT PRIMARY KEY,        -- 统一稳定 ID（复用各表 stable_key）
            entity_type     TEXT NOT NULL,           -- metric/entity/relationship/dict/table_comment/column_comment/kb_doc
            knowledge_level INTEGER NOT NULL,        -- 1~4（P1~P4）
            display_name    TEXT NOT NULL,           -- 人类可读名
            source_table    TEXT NOT NULL,           -- 源表名
            source_pk       TEXT NOT NULL,           -- 源表主键（字符串化，便于回查原表）
            status          TEXT,                    -- approved/candidate/rejected/...
            source          TEXT,                    -- manual/ai
            enabled         INTEGER DEFAULT 1,       -- 是否启用
            content_hash    TEXT,                    -- 内容哈希（漂移检测）
            updated_at      TEXT,                    -- 源表最近更新时间
            created_at      TEXT                     -- 本表登记时间
        )
    """)
    # 常用查询索引：按类型 / 层级 / 状态检索
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sr_type ON semantic_registry(entity_type)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sr_level ON semantic_registry(knowledge_level)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sr_status ON semantic_registry(status)")
    cur.execute("CREATE INDEX IF NOT EXISTS idx_sr_src_table ON semantic_registry(source_table)")


# ── 每类知识的抽取规则：SQL 模板 + 映射函数 ──
#   为避免写 7 段近乎重复的代码，用「(sql, 行→(stable_key, ...))」描述每个源，
#   统一走 _insert 循环。字段顺序与 semantic_registry 列一致（除 created_at）。
SOURCES = [
    {
        "table": "metrics", "etype": "metric", "level": 1,
        "sql": ("SELECT stable_key, name, status, source, enabled, content_hash, updated_at "
                "FROM metrics WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": r[1], "status": r[2], "source": r[3],
            "enabled": r[4], "content_hash": r[5], "updated_at": r[6],
            "source_pk": r[1],
        },
    },
    {
        "table": "entities", "etype": "entity", "level": 2,
        "sql": ("SELECT stable_key, name, status, source, enabled, content_hash, updated_at "
                "FROM entities WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": r[1], "status": r[2], "source": r[3],
            "enabled": r[4], "content_hash": r[5], "updated_at": r[6],
            "source_pk": r[1],
        },
    },
    {
        "table": "relationships", "etype": "relationship", "level": 2,
        "sql": ("SELECT stable_key, from_entity, to_entity, status, source, enabled, content_hash, updated_at, id "
                "FROM relationships WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": "%s → %s" % (r[1] or "?", r[2] or "?"),
            "status": r[3], "source": r[4], "enabled": r[5], "content_hash": r[6],
            "updated_at": r[7], "source_pk": str(r[8]),
        },
    },
    {
        "table": "dict_entries", "etype": "dict", "level": 3,
        "sql": ("SELECT stable_key, name, status, source, enabled, content_hash, updated_at "
                "FROM dict_entries WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": r[1], "status": r[2], "source": r[3],
            "enabled": r[4], "content_hash": r[5], "updated_at": r[6],
            "source_pk": r[1],
        },
    },
    {
        "table": "table_comments", "etype": "table_comment", "level": 3,
        "sql": ("SELECT stable_key, table_name, status, source, content_hash, updated_at "
                "FROM table_comments WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": r[1], "status": r[2], "source": r[3],
            "enabled": 1, "content_hash": r[4], "updated_at": r[5],
            "source_pk": r[1],
        },
    },
    {
        "table": "column_comments", "etype": "column_comment", "level": 3,
        "sql": ("SELECT stable_key, table_name, column_name, status, source, content_hash, updated_at "
                "FROM column_comments WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": "%s.%s" % (r[1], r[2]), "status": r[3],
            "source": r[4], "enabled": 1, "content_hash": r[5], "updated_at": r[6],
            "source_pk": "%s|%s" % (r[1], r[2]),
        },
    },
    {
        "table": "kb_docs", "etype": "kb_doc", "level": 4,
        "sql": ("SELECT stable_key, title, status, source, enabled, updated_at, id "
                "FROM kb_docs WHERE stable_key IS NOT NULL AND stable_key <> ''"),
        "map": lambda r: {
            "stable_key": r[0], "display_name": r[1] or ("doc#%s" % r[6]), "status": r[2],
            "source": r[3], "enabled": r[4], "content_hash": None, "updated_at": r[5],
            "source_pk": str(r[6]),
        },
    },
]


def rebuild(cur, now):
    """DELETE + 全量重建注册表。返回各类型登记行数。"""
    cur.execute("DELETE FROM semantic_registry")
    stats = {}
    for src in SOURCES:
        rows = cur.execute(src["sql"]).fetchall()
        n = 0
        for row in rows:
            m = src["map"](row)
            cur.execute(
                """INSERT INTO semantic_registry
                   (stable_key, entity_type, knowledge_level, display_name,
                    source_table, source_pk, status, source, enabled,
                    content_hash, updated_at, created_at)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?)""",
                (m["stable_key"], src["etype"], src["level"], m["display_name"],
                 src["table"], m["source_pk"], m["status"], m["source"],
                 m["enabled"], m["content_hash"], m["updated_at"], now),
            )
            n += 1
        stats[src["etype"]] = n
    return stats


def main():
    if not os.path.exists(DB_PATH):
        print("✗ 配置库不存在：%s" % DB_PATH)
        return 1
    con, cur = _conn()
    now = _now()
    try:
        ensure_table(cur)
        stats = rebuild(cur, now)
        con.commit()
        total = sum(stats.values())
        print("统一语义注册表填充完成，共 %d 行：" % total)
        for k, v in stats.items():
            print("  %-16s %d" % (k, v))
        # 汇总：按层级 / 状态
        print("\n按层级：")
        for row in cur.execute(
                "SELECT knowledge_level, COUNT(*) FROM semantic_registry GROUP BY knowledge_level ORDER BY knowledge_level"):
            print("  P%d  %d" % (row[0], row[1]))
        print("按状态：")
        for row in cur.execute(
                "SELECT COALESCE(status,'(空)'), COUNT(*) FROM semantic_registry GROUP BY status ORDER BY 2 DESC"):
            print("  %-12s %d" % (row[0], row[1]))
        # 唯一性自检：stable_key 是否重复（PRIMARY KEY 会兜底，但这里显式核对更稳）
        dup = cur.execute(
            "SELECT COUNT(*) - COUNT(DISTINCT stable_key) FROM semantic_registry").fetchone()[0]
        print("\nstable_key 重复数（应为 0）：%d" % dup)
    except Exception as e:
        con.rollback()
        print("✗ 填充失败，已回滚：%s: %s" % (type(e).__name__, e))
        raise
    finally:
        con.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
