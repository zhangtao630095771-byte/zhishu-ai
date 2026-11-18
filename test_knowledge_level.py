# -*- coding: utf-8 -*-
"""
test_knowledge_level.py —— 知识分层显式建模（knowledge_level 字段）回归测试

★ 验证：
    ① 迁移幂等：7 张知识表都有 knowledge_level 列，默认值符合 KNOWLEDGE_LEVEL 映射；
    ② 二次初始化（幂等）不报错、不重复加列；
    ③ get_knowledge_level_counts() 返回 {0..4} 五层计数，且与表实际行数一致；
    ④ P3 层（table_comments/column_comments/dict_entries）确实归到 level 3。

★ 运行：.venv/bin/python test_knowledge_level.py
    （在 import config_store 之前自建临时库，不碰生产 agent_config.db）
"""
import os
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

# ★ 在 import config_store 之前指向临时库，避免污染生产 agent_config.db
_TMP_DB = os.path.join(tempfile.mkdtemp(prefix="kblvl_"), "agent_config.db")
os.environ["CONFIG_DB"] = _TMP_DB

import config_store  # noqa: E402   ← 必须在设置 CONFIG_DB 之后 import

PASSED, FAILED = [], []


def check(label, cond, detail=""):
    (PASSED if cond else FAILED).append(label)
    print(f"  {'✅' if cond else '❌'} {label}")
    if not cond and detail:
        print(f"       实际: {detail}")
    return cond


def _cur():
    import sqlite3
    return sqlite3.connect(_TMP_DB).cursor()


def test_columns():
    print("\n【字段】7 张知识表有 knowledge_level 列，默认值按层级正确")
    cur = _cur()
    for tbl, lvl in config_store.KNOWLEDGE_LEVEL.items():
        cols = [r[1] for r in cur.execute(f"PRAGMA table_info({tbl})").fetchall()]
        ok = "knowledge_level" in cols
        check(f"{tbl} 有 knowledge_level 列", ok, f"列={cols}")
        if not ok:
            continue
        # 所有行都应该是本层（ALTER 的 DEFAULT 会回填老行）
        wrong = cur.execute(
            f"SELECT COUNT(*) FROM {tbl} WHERE knowledge_level != ?", (lvl,)
        ).fetchone()[0]
        check(f"{tbl} 全部归 level {lvl}", wrong == 0, f"{wrong} 行不符")


def test_idempotent():
    print("\n【幂等】二次初始化不报错、不重复加列")
    try:
        config_store._ensure_ready()
        check("二次 _ensure_ready 不报错", True)
    except Exception as e:  # noqa: BLE001
        check("二次 _ensure_ready 不报错", False, str(e))
    # 幂等：列只加一次，重复 init 不会多出重复列
    cur = _cur()
    cols = [r[1] for r in cur.execute("PRAGMA table_info(metrics)").fetchall()]
    check("metrics 无重复 knowledge_level 列", cols.count("knowledge_level") == 1,
          f"出现 {cols.count('knowledge_level')} 次")


def test_counts():
    print("\n【计数】get_knowledge_level_counts() 返回五层且与表行数一致")
    c = config_store.get_knowledge_level_counts()
    check("返回 0~4 五层", set(c.keys()) == {0, 1, 2, 3, 4}, str(c))
    cur = _cur()
    # P0 = prompts 行数
    p0 = cur.execute("SELECT COUNT(*) FROM prompts").fetchone()[0]
    check("P0 计数 == prompts 行数", c[0] == p0, f"counts[0]={c[0]}, prompts={p0}")
    # P1 = metrics 行数
    m = cur.execute("SELECT COUNT(*) FROM metrics").fetchone()[0]
    check("P1 计数 == metrics 行数", c[1] == m, f"counts[1]={c[1]}, metrics={m}")
    # P2 = entities + relationships
    p2 = (cur.execute("SELECT COUNT(*) FROM entities").fetchone()[0]
          + cur.execute("SELECT COUNT(*) FROM relationships").fetchone()[0])
    check("P2 计数 == entities + relationships", c[2] == p2,
          f"counts[2]={c[2]}, p2={p2}")
    # P3 = 三张 P3 表行数之和
    p3 = sum(cur.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
             for t in ("table_comments", "column_comments", "dict_entries"))
    check("P3 计数 == 三张 P3 表行数之和", c[3] == p3, f"counts[3]={c[3]}, p3={p3}")
    # P4 = kb_docs 行数
    d = cur.execute("SELECT COUNT(*) FROM kb_docs").fetchone()[0]
    check("P4 计数 == kb_docs 行数", c[4] == d, f"counts[4]={c[4]}, kb_docs={d}")


def main():
    config_store._ensure_ready()   # 先建表 + 迁移（幂等），否则下面 PRAGMA 查不到表
    test_columns()
    test_idempotent()              # 再 ensure 一次 = 二次初始化，验证幂等不报错
    test_counts()
    print(f"\n{'=' * 40}\n通过 {len(PASSED)}，失败 {len(FAILED)}")
    if FAILED:
        print("失败项：", FAILED)
        sys.exit(1)


if __name__ == "__main__":
    main()
