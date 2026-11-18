# -*- coding: utf-8 -*-
"""_governance_impact.py —— 评估"严格模式"会排除哪些知识（改开关前先看影响）。

用法：
  python3 _governance_impact.py
说明：
  严格模式 = GOVERNANCE_STRICT_STATUS=1，会把 candidate（AI 生成、未复核）
  全部挡在生产检索之外。本脚本按知识层列出"保留 / 排除"数量，供决策。
"""
import os
import sqlite3

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DB_PATH = os.environ.get("CONFIG_DB") or os.path.join(BASE_DIR, "agent_config.db")

LAYERS = [
    ("metrics", "指标(P1)"),
    ("dict_entries", "字典(P3)"),
    ("entities", "实体(P2)"),
    ("relationships", "关系(P2)"),
    ("table_comments", "表注释(P3)"),
    ("column_comments", "字段注释(P3)"),
]


def main():
    if not os.path.exists(DB_PATH):
        print("✗ 配置库不存在：%s" % DB_PATH)
        return
    con = sqlite3.connect(DB_PATH)
    cur = con.cursor()
    print("配置库：%s" % DB_PATH)
    print("\n严格模式（candidate 不进生产检索）影响评估：\n")
    print("  %-12s %6s %10s %10s %12s" % ("层级", "总数", "approved", "candidate", "严格保留"))
    print("  " + "-" * 56)
    for table, zh in LAYERS:
        try:
            total = cur.execute("SELECT COUNT(*) FROM %s" % table).fetchone()[0]
            appr = cur.execute("SELECT COUNT(*) FROM %s WHERE status='approved'" % table).fetchone()[0]
            cand = cur.execute("SELECT COUNT(*) FROM %s WHERE status='candidate'" % table).fetchone()[0]
        except Exception as e:
            print("  %-12s 跳过（%s）" % (zh, e))
            continue
        print("  %-12s %6d %10d %10d %12d" % (zh, total, appr, cand, appr))
    print("\n提示：GOVERNANCE_STRICT_STATUS=0（默认）时 candidate 仍参与检索；=1 时排除。")
    con.close()


if __name__ == "__main__":
    main()
