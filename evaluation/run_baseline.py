# -*- coding: utf-8 -*-
"""
evaluation/run_baseline.py —— 取数评测对拍基线（一键入口）

★ 职责：把散落的评测零件串成一条主链路，回答「这次改动 vs 基线，到底变好没」：
      runner.run（跑评测）
      → result_compare 六类对拍（已并入 runner 的 result_verdict）
      → gate.check_release_gate（发布门禁：核心指标达阈值）
      → gate.compare_baseline（回归门禁：核心指标不退化）
      → report 产出 JSON + Markdown
      → 基线文件（供下次 compare_baseline 对比）

★ 402 余额不足等【环境故障】的诚实处理（关键）：
      runner.aggregate 已统计 api_error_count / _valid；
      若 _valid=False（大量用例因 API 报错失败），本次结果不可信，
      【拒绝覆盖基线】，只写 *_invalid_apibalance 后缀报告提醒——
      绝不能让「环境故障」被误写成「系统能力」写进基线。

用法：
    .venv/bin/python evaluation/run_baseline.py --dataset evaluation/datasets/regression
    .venv/bin/python evaluation/run_baseline.py --dataset evaluation/datasets/regression --limit 5
    .venv/bin/python evaluation/run_baseline.py --dataset evaluation/datasets/regression --baseline evaluation/reports/xxx.json
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluation import runner, gate, report  # noqa: E402

# 默认基线文件：只存 metrics，供下次 compare_baseline 对比
BASELINE_PATH = os.path.join(HERE, "reports", "baseline_report.json")


def load_baseline(path):
    """加载基线 JSON，返回 (metrics, raw)。文件不存在/损坏 → (None, None)。"""
    if not path or not os.path.exists(path):
        return None, None
    try:
        data = json.load(open(path, encoding="utf-8"))
    except Exception:
        return None, None
    return data.get("metrics"), data


def _print_gate(title, rows, passed):
    print(f"\n{title}：")
    for row in rows:
        mark = "✅" if row["ok"] else "❌"
        note = f"（{row['note']}）" if row.get("note") else ""
        if "target" in row:
            # Release Gate：阈值 vs 实际
            print(f"   {mark} {row['name']:<24} 阈值{row['target']:<12} 实际{row['actual']}{note}")
        else:
            # Regression Gate：基线 vs 当前
            print(f"   {mark} {row['name']:<24} 基线{row.get('baseline') or 'N/A':<10} 当前{row.get('current') or 'N/A'}{note}")
    print(f"   => {'PASS' if passed else 'FAIL'}")


def main():
    ap = argparse.ArgumentParser(description="取数评测对拍基线（一键）")
    ap.add_argument("--dataset", default=os.path.join(HERE, "datasets/regression"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--tag", default=None)
    ap.add_argument("--baseline", default=None, help="基线 JSON（不传则用默认 baseline_report.json）")
    ap.add_argument("--save-baseline", action="store_true",
                    help="显式确认后覆盖基线（smoke/抽样测试请勿加，避免污染历史可信基线）")
    ap.add_argument("--quiet", action="store_true")
    a = ap.parse_args()

    tag = a.tag or os.path.basename(a.dataset.rstrip("/"))

    print(f"① 跑评测：{a.dataset}")
    result = runner.run(a.dataset, limit=a.limit, quiet=a.quiet)
    metrics = result["metrics"]
    valid = metrics.get("_valid", True)
    api_err = metrics.get("api_error_count", 0)

    # ── 六类对拍分布 ──
    vdist = metrics.get("result_verdict_distribution") or {}
    if vdist:
        print("\n② 六类对拍分布（result_compare）：")
        for k, v in sorted(vdist.items()):
            print(f"   {k:<22} {v}")
        er = metrics.get("result_exact_rate")
        if er is not None:
            print(f"   结果一致率（exact + within_tolerance）= {er * 100:.1f}%")
    else:
        print("\n② 六类对拍：无可比对用例（expected_result 覆盖为 0，需先跑 annotate 补标准答案）")

    # ── 发布门禁 ──
    g = gate.check_release_gate(metrics)
    _print_gate("③ Release Gate（发布门禁）", g["rows"], g["passed"])

    # ── 回归门禁（和基线对比）──
    base_metrics, _ = load_baseline(a.baseline or BASELINE_PATH)
    rg = None
    if base_metrics:
        rg = gate.compare_baseline(metrics, base_metrics)
        _print_gate("④ Regression Gate（回归门禁，vs 基线）", rg["rows"], rg["passed"])
    else:
        print("\n④ 无基线可比（首次跑，本次即基线）")

    # ── 生成报告 ──
    report_payload = {
        "metrics": metrics,
        "results": result["results"],
        "dataset": result["dataset"],
        "tag": tag,
        "generated_at": datetime.datetime.now().isoformat(),
    }

    # ★ 环境故障（如 402 余额不足）：结果不可信，拒绝覆盖基线
    if not valid:
        print(f"\n⚠️ 本次 {api_err} 条用例因 API 环境故障失败，结果不可信，拒绝覆盖基线。")
        report_payload["tag"] = f"{tag}_invalid_apibalance"
        jp = report.save(report_payload)
        mdp = jp.rsplit(".", 1)[0] + ".md"
        open(mdp, "w", encoding="utf-8").write(
            report.to_markdown(report_payload, gate=g, baseline=base_metrics))
        print(f"   已写 invalid 报告：{jp}")
        print(f"   已写 invalid 报告：{mdp}")
        print("   基线未被覆盖（保持上一次可信基线）")
        return 1

    jp = report.save(report_payload)
    mdp = jp.rsplit(".", 1)[0] + ".md"
    open(mdp, "w", encoding="utf-8").write(
        report.to_markdown(report_payload, gate=g, baseline=base_metrics))
    print(f"\n⑤ 报告：{jp}")
    print(f"   报告：{mdp}")

    # 更新基线（供下次 compare_baseline）—— 只有显式 --save-baseline 才覆盖，
    # 防止 smoke / 抽样测试（--limit）污染历史可信基线。
    if a.save_baseline:
        os.makedirs(os.path.dirname(BASELINE_PATH), exist_ok=True)
        json.dump({
            "experiment_id": f"EXP-{datetime.datetime.now().strftime('%Y%m%d-%H%M%S')}",
            "tag": tag,
            "generated_at": report_payload["generated_at"],
            "dataset": result["dataset"],
            "metrics": metrics,
        }, open(BASELINE_PATH, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
        print(f"   基线已更新：{BASELINE_PATH}（--save-baseline 显式确认）")
    else:
        print("   基线未更新（未加 --save-baseline，保持上次可信基线）")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
