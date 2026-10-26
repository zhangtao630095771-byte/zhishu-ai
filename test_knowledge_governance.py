# -*- coding: utf-8 -*-
"""
test_knowledge_governance.py —— 知识治理升级回归测试（GAP-2 / GAP-3）

★ 验证：
    ① 迁移幂等：knowledge_history / index_state 表存在，7 张知识表有元数据列；
    ② upsert_* 写变更历史（create/update/delete + before/after 快照）；
    ③ version 在 update 时自增、create 时为 1；
    ④ delete_* 记录删除前快照；
    ⑤ set_metric_enabled / set_dict_enabled 记录 enable/disable；
    ⑥ 注释（表/字段）写历史；
    ⑦ index_state 落库与读取；
    ⑧ 清理自身测试数据，不污染库。

★ 运行：CONFIG_DB=/tmp/... .venv/bin/python test_knowledge_governance.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_store

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'✅' if cond else '❌'} {label}")
    if not cond and detail:
        print(f"       实际: {detail}")
    return cond


# 用唯一前缀避免与真实知识冲突
P = "__gov_test__"
TBL = "ods_gov_test_all"


def _cleanup():
    """清理本次测试写入的知识，避免污染。"""
    for name in (f"{P}metric",):
        try:
            config_store.delete_metric(name)
        except Exception:
            pass
    for name in (f"{P}entity",):
        try:
            config_store.delete_entity(name)
        except Exception:
            pass
    for fe in (f"{P}from",):
        try:
            for r in config_store.get_relationships(enabled_only=False):
                if r.get("from_entity") == fe:
                    config_store.delete_relationship(r["id"])
        except Exception:
            pass
    try:
        config_store.delete_dict_entry(f"{P}dict")
    except Exception:
        pass
    try:
        config_store.delete_column_comment(TBL, "c1")
    except Exception:
        pass


def test_migration():
    print("\n【GAP-2/3】迁移幂等")
    import sqlite3
    db = os.getenv("CONFIG_DB", "agent_config.db")
    cur = sqlite3.connect(db).cursor()
    for t in ("knowledge_history", "index_state"):
        n = cur.execute("SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?",
                        (t,)).fetchone()[0]
        check(f"表 {t} 存在", n == 1)
    for t in ("metrics", "entities", "relationships", "kb_docs",
              "dict_entries", "table_comments", "column_comments"):
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({t})").fetchall()]
        ok = all(x in cols for x in ("created_at", "updated_at", "version", "source_ref"))
        check(f"{t} 有元数据列", ok, str(cols))


def test_metric_history_version():
    print("\n【GAP-2】指标版本 + 变更历史")
    name = f"{P}metric"
    _cleanup()
    config_store.upsert_metric({"name": name, "definition": "v1", "tables": []}, operator="tester")
    h = config_store.list_knowledge_history("metric", name, limit=10)
    check("create 记录（最新为 create）", h[0]["action"] == "create", str(h[:2]))
    check("create 后有 after 快照", h[0]["after"] is not None)
    check("create 前 before 为 None", h[0]["before"] is None)

    config_store.upsert_metric({"name": name, "definition": "v2", "tables": []}, operator="tester")
    h = config_store.list_knowledge_history("metric", name, limit=10)
    check("update 记录（最新为 update）", h[0]["action"] == "update", str(h[0]))
    check("update 有 before+after 快照",
          h[0]["before"] is not None and h[0]["after"] is not None)
    check("before 快照是 v1", (h[0]["before"] or {}).get("definition") == "v1",
          str(h[0]["before"]))
    check("after 快照是 v2", (h[0]["after"] or {}).get("definition") == "v2",
          str(h[0]["after"]))

    import sqlite3
    db = os.getenv("CONFIG_DB", "agent_config.db")
    v = sqlite3.connect(db).cursor().execute(
        "SELECT version FROM metrics WHERE name=?", (name,)).fetchone()
    check("version 自增到 2", v and v[0] == 2, str(v))

    config_store.delete_metric(name, operator="tester")
    h = config_store.list_knowledge_history("metric", name, limit=10)
    check("delete 记录（最新为 delete）", h[0]["action"] == "delete", str(h[0]))
    check("delete 有 before 快照", h[0]["before"] is not None)


def test_entity_relationship_history():
    print("\n【GAP-2】实体 / 关系变更历史")
    _cleanup()
    config_store.upsert_entity({"name": f"{P}entity", "table_name": TBL, "grain": "一行=一条",
                                "comment": "t", "keywords": []}, source="manual", operator="tester")
    h = config_store.list_knowledge_history("entity", f"{P}entity", limit=5)
    check("实体 create 记录（最新为 create）", h[0]["action"] == "create", str(h[:2]))

    config_store.upsert_relationship({"from_entity": f"{P}from", "to_entity": f"{P}to",
                                      "join_sql": "a JOIN b ON a.id=b.id",
                                      "cardinality": "1:N", "pre_agg_required": True},
                                     operator="tester")
    rels = [r for r in config_store.get_relationships(enabled_only=False)
            if r.get("from_entity") == f"{P}from"]
    check("关系已写入", len(rels) == 1)
    h = config_store.list_knowledge_history("relationship", rels[0]["id"], limit=5)
    check("关系 create 记录", len(h) >= 1 and h[0]["action"] == "create", str(h))
    config_store.delete_relationship(rels[0]["id"], operator="tester")
    h = config_store.list_knowledge_history("relationship", rels[0]["id"], limit=5)
    check("关系 delete 记录", h[0]["action"] == "delete", str(h[0]))


def test_enable_disable_history():
    print("\n【GAP-2】启停状态变更历史")
    name = f"{P}metric"
    _cleanup()
    config_store.upsert_metric({"name": name, "definition": "x", "tables": []})
    config_store.set_metric_enabled(name, False, operator="tester")
    h = config_store.list_knowledge_history("metric", name, limit=5)
    check("disable 记录", h[0]["action"] == "disable", str(h[0]))
    config_store.set_metric_enabled(name, True, operator="tester")
    h = config_store.list_knowledge_history("metric", name, limit=5)
    check("enable 记录", h[0]["action"] == "enable", str(h[0]))
    config_store.delete_metric(name)


def test_comment_history():
    print("\n【GAP-2】表/字段注释变更历史")
    _cleanup()
    config_store.set_table_comment(TBL, "测试注释", source="manual", operator="tester")
    h = config_store.list_knowledge_history("table_comment", TBL, limit=5)
    check("表注释 create/update 记录", len(h) >= 1, str(h))

    config_store.set_column_comment(TBL, "c1", "字段说明", source="manual", operator="tester")
    h = config_store.list_knowledge_history("column_comment", f"{TBL}.c1", limit=5)
    check("字段注释记录", len(h) >= 1, str(h))
    config_store.delete_column_comment(TBL, "c1", operator="tester")
    h = config_store.list_knowledge_history("column_comment", f"{TBL}.c1", limit=5)
    check("字段注释 delete 记录", h[0]["action"] == "delete", str(h[0]))


def test_index_state():
    print("\n【GAP-3】索引重建状态落库")
    config_store.set_index_state("ok", {"tables": 3, "metrics": 5})
    s = config_store.get_index_state()
    check("index_state 可读", s and s["status"] == "ok", str(s))
    check("计数正确", (s or {}).get("counts", {}).get("metrics") == 5, str(s))
    config_store.set_index_state("failed", error="模拟失败")
    s = config_store.get_index_state()
    check("failed 状态 + 错误摘要", s and s["status"] == "failed" and s.get("error"), str(s))


if __name__ == "__main__":
    _cleanup()
    test_migration()
    test_metric_history_version()
    test_entity_relationship_history()
    test_enable_disable_history()
    test_comment_history()
    test_index_state()
    _cleanup()
    print("\n" + "=" * 60)
    print(f"🎉 全部通过（{len(PASSED)}/{len(PASSED) + len(FAILED)}）"
          if not FAILED else f"❌ 存在失败项（{len(FAILED)}）")
    print("=" * 60)
    raise SystemExit(1 if FAILED else 0)
