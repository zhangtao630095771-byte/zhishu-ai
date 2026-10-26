# -*- coding: utf-8 -*-
"""
evaluation/gate.py —— Release Gate / Regression Gate

★ 原则（用户要求）：
    只有「Regression PASS + 核心指标达阈值 + 幻觉未恶化 + 成本性能可接受」
    才算优化成功；任一核心能力退化即不得发布。
"""

# 指标 → (比较符, 阈值)。阈值可后续按真实 Benchmark 自动校准。
DEFAULT_THRESHOLDS = {
    "overall_accuracy": (">=", 0.93),
    "metric_accuracy": (">=", 0.95),
    "table_retrieval_recall": (">=", 0.95),
    "field_retrieval_recall": (">=", 0.95),
    "sql_accuracy": (">=", 0.94),
    "sql_execution_success_rate": (">=", 0.98),
    "grounding_accuracy": (">=", 0.95),
    "hallucination_rate": ("<=", 0.03),   # 越低越好
    "consistency_rate": (">=", 0.95),
    "p95_latency": ("<=", 8.0),           # 秒，越低越好
}

NAMES = {
    "overall_accuracy": "Overall Accuracy",
    "metric_accuracy": "Metric Accuracy",
    "table_retrieval_recall": "Table Retrieval",
    "field_retrieval_recall": "Field Retrieval",
    "sql_accuracy": "SQL Accuracy",
    "sql_execution_success_rate": "SQL Execution Success",
    "grounding_accuracy": "Grounding Accuracy",
    "hallucination_rate": "Hallucination Rate",
    "consistency_rate": "Consistency",
    "p95_latency": "P95 Latency",
}

# 回归门禁关注的核心指标（历史能力不得明显退化）
CORE = ["overall_accuracy", "metric_accuracy", "table_retrieval_recall",
        "sql_accuracy", "grounding_accuracy", "sql_execution_success_rate"]


def _fmt(k, v):
    if v is None:
        return "N/A"
    if k == "p95_latency":
        return f"{v}s"
    return f"{v * 100:.1f}%"


def check_release_gate(metrics, thresholds=None):
    """按阈值判定是否允许发布。指标缺失视为不通过（避免"没测就放行"）。"""
    th = thresholds or DEFAULT_THRESHOLDS
    rows = []
    for k, (op, target) in th.items():
        actual = metrics.get(k)
        name = NAMES.get(k, k)
        if actual is None:
            rows.append({"key": k, "name": name, "target": f"{op}{_fmt(k, target)}",
                         "actual": "N/A", "ok": False, "note": "指标缺失"})
            continue
        ok = (actual >= target) if op == ">=" else (actual <= target)
        rows.append({"key": k, "name": name, "target": f"{op}{_fmt(k, target)}",
                     "actual": _fmt(k, actual), "ok": bool(ok), "note": ""})
    return {"passed": all(r["ok"] for r in rows), "rows": rows}


def compare_baseline(metrics, baseline_metrics, tolerance=0.02):
    """
    Regression Gate：核心指标不得低于历史最佳 - tolerance。
    tolerance 允许小幅波动（评测噪声）。幻觉率单独要求不得恶化超过 tolerance。
    """
    rows = []
    passed = True
    for k in CORE:
        cur, base = metrics.get(k), (baseline_metrics or {}).get(k)
        if cur is None or base is None:
            rows.append({"key": k, "name": NAMES.get(k, k),
                         "baseline": _fmt(k, base), "current": _fmt(k, cur),
                         "ok": True, "note": "N/A（不计入）"})
            continue
        ok = cur >= base - tolerance
        passed = passed and ok
        rows.append({"key": k, "name": NAMES.get(k, k),
                     "baseline": _fmt(k, base), "current": _fmt(k, cur),
                     "ok": bool(ok), "note": "" if ok else "退化超过容忍度"})
    # 幻觉不得恶化
    cur_h, base_h = metrics.get("hallucination_rate"), (baseline_metrics or {}).get("hallucination_rate")
    if cur_h is not None and base_h is not None:
        ok = cur_h <= base_h + tolerance
        passed = passed and ok
        rows.append({"key": "hallucination_rate", "name": "Hallucination Rate",
                     "baseline": _fmt("hallucination_rate", base_h),
                     "current": _fmt("hallucination_rate", cur_h),
                     "ok": bool(ok), "note": "" if ok else "幻觉恶化"})
    return {"passed": passed, "rows": rows}
