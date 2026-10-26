# -*- coding: utf-8 -*-
"""
evaluation/run_consistency.py —— 一致性评测（Consistency Test）

同一问题连续运行 N 次，检查 SQL / 指标 / 表 / 结果是否稳定。
temperature=0 理论上应完全一致；RAG 排序、检索并发等仍可能引入抖动。

用法：
    .venv/bin/python evaluation/run_consistency.py --n 3 --limit 10
"""
import argparse
import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
sys.path.insert(0, ROOT)

from evaluation import runner  # noqa: E402


def norm_sql(s):
    return re.sub(r"\s+", " ", (s or "")).strip().lower()


def run(dataset, n=3, limit=None):
    import rag
    cases = runner.load_dataset(dataset)
    if limit:
        cases = cases[:limit]
    index = rag.build_index()
    allowed = index.get("allowed_tables") or []

    rows = []
    for c in cases:
        runs = [runner.run_one(c, index, allowed) for _ in range(n)]
        sqls = {norm_sql(r.get("sql")) for r in runs}
        metrics = {tuple(sorted(r.get("metrics") or [])) for r in runs}
        tables = {tuple(sorted(r.get("tables") or [])) for r in runs}
        results = {str(r.get("rows_preview")) for r in runs}
        rows.append({
            "id": c.get("id"), "name": c.get("name"),
            "consistent": len(sqls) == 1,
            "sql_variants": len(sqls),
            "metric_variants": len(metrics),
            "table_variants": len(tables),
            "result_variants": len(results),
        })
    rate = round(sum(1 for r in rows if r["consistent"]) / len(rows), 4) if rows else None
    return {"n": n, "consistency_rate": rate, "rows": rows}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(ROOT, "evaluation/datasets/golden"))
    ap.add_argument("--n", type=int, default=3)
    ap.add_argument("--limit", type=int, default=None)
    a = ap.parse_args()
    res = run(a.dataset, n=a.n, limit=a.limit)
    print(f"Consistency Rate = {res['consistency_rate']}（n={res['n']}）")
    for r in res["rows"]:
        mark = "✅" if r["consistent"] else "❌"
        print(f"  {mark} [{r['id']}] {r['name']}  SQL变体={r['sql_variants']} 指标变体={r['metric_variants']} "
              f"表变体={r['table_variants']} 结果变体={r['result_variants']}")
