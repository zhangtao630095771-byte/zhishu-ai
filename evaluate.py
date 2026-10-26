#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
evaluate.py —— 知数AI 统一评测入口（Evaluation Pipeline）

用法：
    .venv/bin/python evaluate.py                       # 跑 golden 数据集，生成报告
    .venv/bin/python evaluate.py --limit 10            # 只跑前 10 条
    .venv/bin/python evaluate.py --save-baseline       # 把本次结果保存为 baseline
    .venv/bin/python evaluate.py --regression --gate   # 回归门禁 + 发布门禁
"""
import argparse
import datetime
import json
import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, ROOT)

from evaluation import runner                      # noqa: E402
from evaluation import report as report_mod        # noqa: E402
from evaluation import gate as gate_mod            # noqa: E402


def main():
    ap = argparse.ArgumentParser(description="知数AI Evaluation Pipeline")
    ap.add_argument("--dataset", default=os.path.join(ROOT, "evaluation/datasets/golden"),
                    help="数据集路径（目录 / .json / .jsonl）")
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default=None, help="本次实验标签（报告命名）")
    ap.add_argument("--baseline", default=os.path.join(ROOT, "evaluation/reports/baseline.json"))
    ap.add_argument("--gate", action="store_true", help="执行 Release Gate")
    ap.add_argument("--regression", action="store_true", help="执行 Regression Gate（对比 baseline）")
    ap.add_argument("--save-baseline", action="store_true", help="把本次结果保存为 baseline.json")
    ap.add_argument("--quiet", action="store_true")
    args = ap.parse_args()

    tag = args.tag or datetime.datetime.now().strftime("%Y%m%d-%H%M")
    exp_id = "EXP-" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")

    res = runner.run(args.dataset, limit=args.limit, quiet=args.quiet)
    rep = {
        "experiment_id": exp_id,
        "tag": tag,
        "generated_at": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "dataset": res["dataset"],
        "metrics": res["metrics"],
        "results": res["results"],
    }

    baseline = None
    if args.baseline and os.path.exists(args.baseline):
        try:
            baseline = json.load(open(args.baseline, encoding="utf-8")).get("metrics")
        except Exception:
            baseline = None

    gate = gate_mod.check_release_gate(res["metrics"]) if args.gate else None
    reg = (gate_mod.compare_baseline(res["metrics"], baseline)
           if (args.regression and baseline) else None)

    md = report_mod.to_markdown(rep, gate=gate, baseline=baseline)
    if reg:
        md += ("\n## Regression Gate\n\n**%s**\n\n"
               "| 指标 | Baseline | 当前 | 结果 | 备注 |\n|---|---|---|---|---|\n"
               % ("PASS" if reg["passed"] else "FAIL"))
        for r in reg["rows"]:
            md += f"| {r['name']} | {r['baseline']} | {r['current']} | {'✅' if r['ok'] else '❌'} | {r.get('note','')} |\n"

    rp = report_mod.save(rep)
    mdp = os.path.splitext(rp)[0] + ".md"
    open(mdp, "w", encoding="utf-8").write(md)
    if args.save_baseline:
        if not res["metrics"].get("_valid", True):
            print(f"⚠️  本次运行不合格（API 报错 {res['metrics'].get('api_error_count')} 条，疑似额度/网络问题），"
                  f"已拒绝覆盖 baseline，防止污染基线。")
        else:
            bp = os.path.join(ROOT, "evaluation/reports/baseline.json")
            json.dump(rep, open(bp, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
            print(f"[baseline] 已保存 {bp}")

    print("\n" + "=" * 30)
    print("知数AI Evaluation Report")
    print("=" * 30)
    print(md)
    print(f"\n报告文件：\n  {rp}\n  {mdp}")

    if gate and not gate["passed"]:
        sys.exit(2)
    if reg and not reg["passed"]:
        sys.exit(3)


if __name__ == "__main__":
    main()
