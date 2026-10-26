#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
tools/enum_scan.py —— 枚举字段扫描 + 批量入库

★ 用途：扫描 ClickHouse 里「字段注释已写清枚举取值（N=含义）」的字段，
   产出候选清单，供人工确认后批量写入 dict_entries（数据字典），
   让「编码→中文」在结果展示与 SQL 生成时都生效。

★ 为什么注释里已有的枚举要再入一遍字典？
   注释解析（enum_parse）只作用于【结果表翻译】，不参与【SQL 生成的检索】。
   模型生成 SQL 时（WHERE app_entity_source = ?）看不到注释里的取值，仍会猜错编码。
   入成字典后，retrieve_dicts 能按关键词召回，生成阶段也能拿到权威取值。

用法：
    .venv/bin/python tools/enum_scan.py              # 只扫描，打印候选清单
    .venv/bin/python tools/enum_scan.py --write      # 扫描并写入字典（field_hint=字段名）
    .venv/bin/python tools/enum_scan.py --top 50     # 只看前 50 个
"""
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import config
import db_clickhouse as dbc
import config_store
import enum_parse


def scan():
    """
    扫描 system.columns，返回「注释含至少 2 对枚举取值」的字段清单。
    ★ 全库字段注释 16000+ 行，一次查会撞 max_result_rows(10000) 被 code 396 杀掉，
      故按表分批调 fetch_comments（它内部已按表收窄 + 过滤空注释）。
    """
    import db as db_facade
    import knowledge
    all_tables = knowledge.business_tables()      # 排除 _local/_mv 等噪声表
    results = []
    batch_size = 20
    for i in range(0, len(all_tables), batch_size):
        batch = all_tables[i:i + batch_size]
        try:
            comments = dbc.fetch_comments(batch)
        except Exception as e:
            print(f"  [warn] 批次 {i} 抓取失败：{e}")
            continue
        for key, comment in (comments.get("columns") or {}).items():
            if "." not in key:
                continue
            table, col = key.split(".", 1)
            pairs = enum_parse.parse_values(comment or "")
            if pairs:
                results.append({
                    "table": table, "column": col, "comment": comment,
                    "values": pairs,
                })
    return results


def to_entry(item):
    """把一个候选字段转成 dict_entries 条目"""
    col = item["column"]
    items = [{"value": str(k), "label": v} for k, v in sorted(item["values"].items())]
    return {
        "name": f"{col} 枚举",
        "dict_type": "",                    # 无来源字典表，留空
        "field_hint": col,                  # ★ 精确匹配列名，跨表复用
        "keywords": [col, item["table"]],
        "items": items,
        "enabled": True,
    }


def main():
    write = "--write" in sys.argv
    top = None
    if "--top" in sys.argv:
        i = sys.argv.index("--top")
        top = int(sys.argv[i + 1])

    print("正在扫描 ClickHouse system.columns 中的枚举字段注释...")
    results = scan()
    if top:
        results = results[:top]

    print(f"\n发现 {len(results)} 个「注释含枚举取值」的字段：\n")
    for it in results:
        vals = ", ".join(f"{k}={v}" for k, v in sorted(it["values"].items()))
        print(f"  {it['table']}.{it['column']}  →  {vals[:80]}")

    if not write:
        print("\n提示：加 --write 可把上述字段批量写入数据字典（field_hint=字段名）。")
        return

    existing = {d["field_hint"] for d in config_store.get_dict_entries(enabled_only=False)}
    written = skipped = 0
    for it in results:
        if it["column"] in existing:
            skipped += 1
            continue
        config_store.upsert_dict_entry(to_entry(it))
        written += 1
    print(f"\n写入完成：新增 {written} 个字典，跳过 {skipped} 个（已存在同 field_hint）。")
    print("★ 记得调 /api/index/rebuild 重建索引后生效。")


if __name__ == "__main__":
    main()
