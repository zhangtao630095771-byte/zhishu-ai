# -*- coding: utf-8 -*-
"""
evaluation/report.py —— 评测报告生成（JSON + Markdown）

产出用户要求的报告格式：
    evaluation_report.json / evaluation_report.md
"""
import collections
import json
import os

# (指标键, 展示名, 类型)  类型：ratio=比率(乘100)、sec=秒、num=数值
METRIC_ROWS = [
    ("overall_accuracy", "Overall Accuracy", "ratio"),
    ("intent_accuracy", "Intent Accuracy", "ratio"),
    ("metric_accuracy", "Metric Accuracy", "ratio"),
    ("table_retrieval_recall", "Table Retrieval Recall", "ratio"),
    ("field_retrieval_recall", "Field Retrieval Recall", "ratio"),
    ("sql_accuracy", "SQL Accuracy", "ratio"),
    ("sql_execution_success_rate", "SQL Execution Success", "ratio"),
    ("time_accuracy", "Time Accuracy", "ratio"),
    ("result_accuracy", "Result Accuracy", "ratio"),
    ("grounding_accuracy", "Grounding Accuracy", "ratio"),
    ("hallucination_rate", "Hallucination Rate", "ratio"),
    ("abstention_accuracy", "Abstention Accuracy", "ratio"),
    ("consistency_rate", "Consistency Rate", "ratio"),
    ("p50_latency", "P50 Latency", "sec"),
    ("p95_latency", "P95 Latency", "sec"),
    ("avg_latency", "Average Latency", "sec"),
    ("average_token", "Average Token", "num"),
    ("average_cost", "Average Cost", "num"),
]


def fmt(v, kind):
    if v is None:
        return "N/A"
    if kind == "ratio":
        return f"{v * 100:.1f}%"
    return f"{v}"


def error_distribution(results):
    """错误分布：按失败维度统计（Error Analysis 雏形）"""
    cnt = collections.Counter()
    for r in results:
        for dim, s in r["scores"].items():
            if s["status"] is False:
                cnt[dim] += 1
    total = sum(cnt.values()) or 1
    return [(k, v, round(v / total, 4)) for k, v in cnt.most_common()]


def to_markdown(report, gate=None, baseline=None):
    m = report["metrics"]
    lines = []
    lines.append("# 知数AI Evaluation Report\n")
    lines.append(f"- Dataset: `{report.get('dataset')}`")
    lines.append(f"- Samples: {m.get('sample_count')}（成功执行 {m.get('executed_count')}）")
    lines.append(f"- Tag: {report.get('tag', '-')}  |  Generated: {report.get('generated_at', '-')}")
    lines.append("")
    lines.append("## 核心指标\n")
    lines.append("| 指标 | 值 |" + (" Baseline | Delta |" if baseline else ""))
    lines.append("|---|---|" + ("---|---|" if baseline else ""))
    for key, label, kind in METRIC_ROWS:
        cur = fmt(m.get(key), kind)
        if baseline:
            base = fmt((baseline or {}).get(key), kind)
            lines.append(f"| {label} | {cur} | {base} | {_delta(m.get(key), (baseline or {}).get(key))} |")
        else:
            lines.append(f"| {label} | {cur} |")
    lines.append("")
    # 错误分布
    dist = error_distribution(report["results"])
    if dist:
        lines.append("## Error Analysis（失败维度分布）\n")
        lines.append("| 失败维度 | 次数 | 占比 |")
        lines.append("|---|---|---|")
        for k, v, p in dist:
            lines.append(f"| {k} | {v} | {p * 100:.1f}% |")
        lines.append("")
    # 失败明细
    fails = [r for r in report["results"]
             if any(s["status"] is False for s in r["scores"].values())]
    if fails:
        lines.append(f"## 失败用例明细（{len(fails)} 条）\n")
        for r in fails[:50]:
            bad = [f"{dim}: {s['detail']}" for dim, s in r["scores"].items() if s["status"] is False]
            lines.append(f"### [{r['id']}] {r['name'] or r['question']}")
            lines.append(f"- 问题：{r['question']}")
            lines.append(f"- 失败：{'；'.join(bad) if bad else '（见 error）'}")
            if r.get("error"):
                lines.append(f"- error：{str(r['error'])[:160]}")
            if r.get("sql"):
                lines.append(f"- SQL：`{str(r['sql'])[:200]}`")
            lines.append("")
    # 门禁
    if gate:
        lines.append("## Release Gate\n")
        lines.append(f"**{'PASS' if gate['passed'] else 'FAIL'}**\n")
        lines.append("| 指标 | 阈值 | 实际 | 结果 |")
        lines.append("|---|---|---|---|")
        for row in gate["rows"]:
            lines.append(f"| {row['name']} | {row['target']} | {row['actual']} | {'✅' if row['ok'] else '❌'} |")
        lines.append("")
    return "\n".join(lines)


def _delta(cur, base):
    if cur is None or base is None:
        return "-"
    try:
        d = cur - base
    except Exception:
        return "-"
    sign = "+" if d >= 0 else ""
    if abs(cur) <= 1 and abs(base) <= 1:
        return f"{sign}{d * 100:.1f}pp"
    return f"{sign}{d:.2f}"


def save(report, outdir=None):
    here = os.path.dirname(os.path.abspath(__file__))
    outdir = outdir or os.path.join(here, "reports")
    os.makedirs(outdir, exist_ok=True)
    tag = report.get("tag") or "latest"
    jp = os.path.join(outdir, f"evaluation_report_{tag}.json")
    json.dump(report, open(jp, "w", encoding="utf-8"), ensure_ascii=False, indent=2,
              default=str)   # DB 结果可能含 date / Decimal，统一转字符串
    return jp
