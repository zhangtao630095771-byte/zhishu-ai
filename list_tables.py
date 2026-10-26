# -*- coding: utf-8 -*-
"""
list_tables.py —— 从真实 CK 里筛出"值得开放给 AI"的候选表。

真实库有 741 张表，很多是：
  · _local          各分片的本地表（查询请走 _all 分布式表）
  · .inner_id.* / *_mv   物化视图内部表（不该暴露）
  · *_bak / *_local_bak  备份表

本脚本：
  1) 过滤掉上述噪声表，只留"业务表"
  2) 按数据分层（dim/dwd/dws/ads/mid/ods 等）分组
  3) 给出每个分层的候选，并估算行数（仅对 _all 分布式表）

用法：
  cd ~/Downloads/autosql/ai-data-agent
  .venv/bin/python list_tables.py            # 全部分层概览
  .venv/bin/python list_tables.py order      # 只看名字含 order 的（可传多个关键词，空格分隔）
"""
import os
import sys
import clickhouse_connect


def main():
    from dotenv import load_dotenv
    load_dotenv(os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env"))

    db_name = os.getenv("CK_DATABASE", "ctc_data_warehouse")
    c = clickhouse_connect.get_client(
        host=os.getenv("CK_HOST", "127.0.0.1"),
        port=int(os.getenv("CK_PORT", "8123")),
        username=os.getenv("CK_USER"),
        password=os.getenv("CK_PASSWORD"),
        database=db_name,
    )

    rows = c.query(
        "SELECT name FROM system.tables WHERE database = %(db)s",
        parameters={"db": db_name},
    ).result_rows
    all_names = [r[0] for r in rows]

    def is_noise(n):
        if n.startswith(".inner_id") or n.endswith("_mv") or n.endswith("_mv_local"):
            return True
        if n.endswith("_local") or n.endswith("_bak") or n.endswith("_local_bak"):
            return True
        return False

    biz = [n for n in all_names if not is_noise(n)]
    noise = [n for n in all_names if is_noise(n)]

    layers = {}
    for n in biz:
        prefix = n.split("_", 1)[0]
        layers.setdefault(prefix, []).append(n)
    layers = {k: sorted(v) for k, v in sorted(layers.items())}

    # 关键词过滤（命令行参数）
    kw = [a.lower() for a in sys.argv[1:]]
    if kw:
        filtered = {}
        for k, v in layers.items():
            fv = [n for n in v if any(kk in n.lower() for kk in kw)]
            if fv:
                filtered[k] = fv
        biz_filtered = [n for ns in filtered.values() for n in ns]
        layers = filtered
        print(f"\n=== 关键词过滤：{', '.join(sys.argv[1:])} → {len(biz_filtered)} 张 ===\n")
    else:
        print(f"\n=== {db_name}：业务表 {len(biz)} 张（已过滤 {len(noise)} 张噪声/_local/_mv/bak）===\n")

    for prefix in ["dim", "dwd", "dws", "ads", "mid", "ods"]:
        if prefix in layers:
            print(f"── {prefix} ({len(layers[prefix])} 张) ──")
            for n in layers[prefix]:
                print(f"   {n}")
            print()

    other = {k: v for k, v in layers.items() if k not in
             ("dim", "dwd", "dws", "ads", "mid", "ods")}
    if other:
        for k, v in other.items():
            print(f"── {k} ({len(v)} 张) ──")
            for n in v:
                print(f"   {n}")
            print()

    # 给推荐白名单：每个业务主题的 _all 表（可建索引、可查询）
    all_tables = [n for n in biz if n.endswith("_all")]
    print(f"\n💡 候选开放表（_all 分布式表，共 {len(all_tables)} 张）。"
          "开放它们给 AI 即可，不必开放 _local。")
    print("   授权命令：.venv/bin/python admin.py set-acl <表名> allow \"备注\"")


if __name__ == "__main__":
    main()
