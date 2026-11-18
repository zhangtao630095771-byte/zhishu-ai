# -*- coding: utf-8 -*-
"""
migrate_sql_experience.py —— SQL 经验中心 数据库迁移 / 存量导入 / 回滚

★ 为什么要有独立脚本（而不是只靠服务启动时自动迁移）？
  ① 生产上"改库"和"发版"要能分开做，出问题能单独回滚；
  ② 迁移前必须能先备份（服务启动时自动迁移不方便强制备份）；
  ③ 存量导入是一次性动作，不应该混在每次启动路径里。

用法：
    # 只看会做什么（不改库）
    .venv/bin/python migrate_sql_experience.py --dry-run

    # 迁移 + 导入存量（默认会先备份）
    .venv/bin/python migrate_sql_experience.py --backup

    # 只迁移结构，不导入
    .venv/bin/python migrate_sql_experience.py --no-import

    # 回滚（删 3 张表 + 索引；不动任何既有业务表）
    .venv/bin/python migrate_sql_experience.py --rollback
"""
import argparse
import os
import shutil
import sqlite3
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)

import config_store          # noqa: E402
import sql_experience as exp  # noqa: E402


def _db_path():
    return config_store.CONFIG_DB


def _backup(path):
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    dst = f"{path}.bak-exp-{ts}"
    shutil.copy2(path, dst)
    return dst


def _exists(cur, table):
    return cur.execute(
        "SELECT COUNT(*) FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone()[0] > 0


def _has_column(cur, table, col):
    return any(r[1] == col for r in cur.execute(f"PRAGMA table_info({table})").fetchall())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true", help="只打印计划，不改库")
    ap.add_argument("--backup", action="store_true", help="迁移前备份配置库（生产必做）")
    ap.add_argument("--no-import", action="store_true", help="只迁移结构，不导入存量")
    ap.add_argument("--rollback", action="store_true", help="回滚：删除本模块新建的表与索引")
    ap.add_argument("--records", action="store_true", help="连同用户反馈一起导入失败经验")
    a = ap.parse_args()

    path = _db_path()
    if not os.path.exists(path):
        print(f"❌ 配置库不存在：{path}")
        return 2
    print(f"配置库：{path}（{os.path.getsize(path) / 1024 / 1024:.1f} MB）")

    conn = sqlite3.connect(path, timeout=10)
    cur = conn.cursor()

    if a.rollback:
        print("\n⚠️ 回滚会删除：sql_experience / sql_experience_rules / sql_experience_usage 及其索引")
        print("   （lesson: 这三张表是本模块新建的，删除不影响取数/审核/知识库任何既有数据）")
        if a.dry_run:
            print("dry-run：未执行")
            return 0
        if a.backup:
            print(f"已备份：{_backup(path)}")
        for stmt in exp.rollback_sql():
            cur.execute(stmt)
        conn.commit()
        print("✅ 已回滚。query_records.error 列保留（纯新增，对旧代码无影响）。")
        conn.close()
        return 0

    plan = []
    for t in ("sql_experience", "sql_experience_rules", "sql_experience_usage"):
        plan.append(f"{'已存在' if _exists(cur, t) else '将创建'} 表 {t}")
    plan.append(f"{'已有' if _has_column(cur, 'query_records', 'error') else '将新增'} 列 query_records.error")
    n_rec = cur.execute("SELECT COUNT(*) FROM query_records").fetchone()[0]
    plan.append(f"存量取数记录：{n_rec} 条待导入")
    if a.records:
        n_fb = cur.execute("SELECT COUNT(*) FROM feedbacks").fetchone()[0]
        plan.append(f"用户反馈：{n_fb} 条待导入为失败经验")
    print("\n迁移计划：")
    for p in plan:
        print("  ·", p)

    if a.dry_run:
        print("\ndry-run：未执行任何修改")
        conn.close()
        return 0

    if a.backup:
        print(f"\n已备份：{_backup(path)}")

    exp.migrate_schema(cur)
    conn.commit()
    print("\n✅ 结构迁移完成（幂等：重复执行不会重复建表/丢数据）")

    if not a.no_import:
        stats = exp.import_from_records(operator="migration")
        print(f"✅ 取数记录导入：扫描 {stats['scanned']} 条 → 新建 {stats['created']} / "
              f"更新 {stats['updated']} / 失败 {stats['skipped']}；按类型 {stats['by_kind']}")
        if a.records:
            fstats = exp.import_from_feedbacks(operator="migration")
            print(f"✅ 用户反馈导入：扫描 {fstats['scanned']} 条 → 新建 {fstats['created']} / "
                  f"更新 {fstats['updated']}")

    ov = exp.overview()
    print("\n当前经验库概览：")
    print(f"  案例 {ov['cases_total']} 条（成功 {ov['by_kind']['success']} / "
          f"失败 {ov['by_kind']['failure']} / 修正 {ov['by_kind']['correction']}）")
    print(f"  可信等级：{ov['by_trust']}")
    print(f"  审核状态：{ov['by_review']}")
    print(f"  验证状态：{ov['by_verify']}")
    print(f"  ★ 审核通过但执行失败的案例：{ov['approved_but_failed']} 条"
          f"（这些永远不会成为黄金 SQL）")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
