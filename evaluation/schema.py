# -*- coding: utf-8 -*-
"""
evaluation/schema.py —— Golden Dataset 标准结构审计工具（GAP-4）

★ 职责：不生成、不虚构任何基准值；只【审计】现有样例的字段覆盖情况，
  输出哪些题目缺「标准 SQL / 期望结果 / 关联知识 / 审核状态」等关键字段，
  供「补齐 Golden 基准」时按优先级推进。

用法：
    .venv/bin/python evaluation/schema.py                    # 审计全部数据集
    .venv/bin/python evaluation/schema.py golden             # 只审计 golden 集
"""
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

DATASETS_DIR = os.path.join(HERE, "datasets")

# 标准结构字段（对齐 docs/golden_dataset_schema.md）
REQUIRED = ["id", "question", "expected_ok"]
RECOMMENDED = ["name", "category", "difficulty", "intent",
               "expected_metrics", "expected_tables",
               "expected_sql_contains", "expected_sql_not_contains"]
# 本阶段升级重点：标准 SQL 与结果校验
UPGRADE = ["expected_sql", "expected_result", "result_check",
           "sql_dialect", "knowledge_ids", "acceptance",
           "review_status", "failure_type", "version"]


def load_dataset(path):
    items = []
    if os.path.isdir(path):
        for fn in sorted(os.listdir(path)):
            if fn.endswith((".json", ".jsonl")):
                items.extend(load_dataset(os.path.join(path, fn)))
        return items
    with open(path, encoding="utf-8") as f:
        text = f.read().strip()
    if not text:
        return []
    if text.startswith("["):
        items = json.loads(text)
    else:
        for line in text.splitlines():
            line = line.strip()
            if line and not line.startswith("//"):
                items.append(json.loads(line))
    return items


def audit(items):
    """统计字段覆盖：返回 {字段: 覆盖条数}，以及缺关键字段的样例 id 列表。"""
    n = len(items)
    if n == 0:
        return {}, [], []
    keys = REQUIRED + RECOMMENDED + UPGRADE
    cover = {k: sum(1 for c in items if c.get(k) not in (None, "", [])) for k in keys}
    missing_sql = [c.get("id") for c in items if not c.get("expected_sql")]
    missing_result = [c.get("id") for c in items if not c.get("expected_result")]
    return cover, missing_sql, missing_result


def _report(name, items):
    cover, ms, mr = audit(items)
    n = len(items)
    print(f"\n== {name}（{n} 条）==")
    if n == 0:
        print("  （空）")
        return
    def _line(k):
        c = cover.get(k, 0)
        mark = "✅" if c == n else ("⚠️" if c > 0 else "❌")
        return f"  {mark} {k:<26} {c}/{n}"
    print("  必填：")
    for k in REQUIRED:
        print(_line(k))
    print("  建议：")
    for k in RECOMMENDED:
        print(_line(k))
    print("  本阶段升级重点（标准 SQL / 结果校验）：")
    for k in UPGRADE:
        print(_line(k))
    if ms:
        m = ", ".join(str(x) for x in ms[:8])
        print(f"  → 缺 expected_sql：{len(ms)} 条（{m}{'…' if len(ms) > 8 else ''}）")
    if mr:
        m = ", ".join(str(x) for x in mr[:8])
        print(f"  → 缺 expected_result：{len(mr)} 条（{m}{'…' if len(mr) > 8 else ''}）")


def main():
    target = sys.argv[1] if len(sys.argv) > 1 else None
    total = 0
    if target:
        path = os.path.join(DATASETS_DIR, target)
        items = load_dataset(path) if os.path.exists(path) else []
        _report(target, items)
        total = len(items)
    else:
        for d in sorted(os.listdir(DATASETS_DIR)):
            path = os.path.join(DATASETS_DIR, d)
            if os.path.isdir(path):
                items = load_dataset(path)
                _report(d, items)
                total += len(items)
    print(f"\n总计样例：{total} 条")


if __name__ == "__main__":
    main()
