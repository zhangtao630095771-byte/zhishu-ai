# -*- coding: utf-8 -*-
"""
evaluation/runner.py —— 统一评测执行器

★ 职责（对齐 Evaluation Pipeline）：
    加载 Dataset → 调用知数AI（agent.ask）→ 记录完整 Trace
    → 多维判分 → 聚合指标 → 产出结构化结果（供 report/gate 使用）

★ 设计约束（用户要求）：
    不重写任何业务代码；只【调用】现有 agent.ask 并观测其结果。
    执行失败（如 DB 不可达）不中断评测，作为可观测项记录。
"""
import json
import os
import statistics
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(HERE)
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluation.evaluators import checks  # noqa: E402


# ═══════════════════════════════════════════════════════════
# 数据集加载
# ═══════════════════════════════════════════════════════════
def load_dataset(path):
    """支持 .jsonl / .json / 目录（递归合并）"""
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


# ═══════════════════════════════════════════════════════════
# 单用例执行 + 判分
# ═══════════════════════════════════════════════════════════
def run_one(case, index, allowed_tables):
    q = case.get("question") or ""
    t0 = time.time()
    try:
        import agent
        r = agent.ask(index, q)
    except Exception as e:  # 评测不能因单条异常中断
        r = {"ok": False, "error": f"exception: {e}", "sql": "", "tables": [], "metrics": []}
    latency = time.time() - t0

    scores = {}
    for name, (fn, _lower_better) in checks.DIMENSIONS.items():
        st, detail = fn(r, case)
        scores[name] = {"status": st, "detail": detail}
    g, gd = checks.check_grounding(r, case, allowed_tables)
    scores["grounding"] = {"status": g, "detail": gd}
    h, hd = checks.check_hallucination(r, case, allowed_tables)
    scores["hallucination"] = {"status": h, "detail": hd}

    # 业务语义：意图一致性（用例提供 intent 时）
    #   ★ 拒答类用例不判 intent：系统拒答走兜底逻辑，intent 值无判别意义
    und = r.get("understanding") or {}
    if case.get("intent") and not case.get("expected_abstain"):
        same = (und.get("intent") == case["intent"])
        scores["intent"] = {"status": same, "detail": f"got={und.get('intent')} 期望={case['intent']}"}
    # 业务域一致性（用例提供 business_domain 时，用召回表是否落在该域主表判定）
    if case.get("expected_tables"):
        pass  # 由 tables 维度覆盖

    return {
        "id": case.get("id"),
        "name": case.get("name"),
        "category": case.get("category"),
        "difficulty": case.get("difficulty"),
        "question": q,
        "ok": r.get("ok"),
        "error": r.get("error"),
        "sql": r.get("sql"),
        "tables": r.get("tables"),
        "metrics": r.get("metrics"),
        "understanding": und,
        "answer": r.get("answer"),
        "validation": r.get("validation"),
        "rows_preview": (r.get("rows") or [])[:3],
        "latency": round(latency, 2),
        "trace": r.get("trace"),
        "scores": scores,
    }


# ═══════════════════════════════════════════════════════════
# 聚合
# ═══════════════════════════════════════════════════════════
def _rate(results, dim):
    vals = [x["scores"][dim]["status"] for x in results
            if x["scores"].get(dim) and x["scores"][dim]["status"] is not None]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 4)


def _pct(vals, p):
    vals = sorted(v for v in vals if v is not None)
    if not vals:
        return None
    k = max(0, min(len(vals) - 1, int(round((p / 100.0) * (len(vals) - 1)))))
    return round(vals[k], 2)


def _overall(item):
    sts = [s["status"] for s in item["scores"].values() if s["status"] is not None]
    return all(sts) if sts else None


def _overall_rate(results):
    vals = [_overall(x) for x in results]
    vals = [v for v in vals if v is not None]
    if not vals:
        return None
    return round(sum(1 for v in vals if v) / len(vals), 4)


def aggregate(results):
    lat = [x["latency"] for x in results]
    metrics = {
        # 综合
        "overall_accuracy": _overall_rate(results),
        # 业务语义
        "intent_accuracy": _rate(results, "intent"),
        "metric_accuracy": _rate(results, "metrics"),
        "time_accuracy": _rate(results, "time"),
        "sql_accuracy": _rate(results, "sql_contains"),
        # 召回
        "table_retrieval_recall": _rate(results, "tables"),
        "field_retrieval_recall": None,   # 需 Schema 生效，见 roadmap
        # SQL 生成/执行
        "sql_execution_success_rate": _rate(results, "ok"),
        "result_accuracy": _rate(results, "result"),
        # 安全与可信
        "grounding_accuracy": _rate(results, "grounding"),
        "hallucination_rate": (None if _rate(results, "hallucination") is None
                               else round(1 - _rate(results, "hallucination"), 4)),
        "abstention_accuracy": _rate(results, "abstain"),
        # 性能
        "p50_latency": _pct(lat, 50),
        "p95_latency": _pct(lat, 95),
        "avg_latency": round(statistics.mean(lat), 2) if lat else None,
        # 待接入（需要 LLM 用量统计）
        "average_token": None,
        "average_cost": None,
        "consistency_rate": None,
    }
    # 计数
    n_total = len(results)
    n_exec = sum(1 for x in results if x["ok"])
    metrics["sample_count"] = n_total
    metrics["executed_count"] = n_exec
    # ★ API 健康检查：若大量用例因 API 报错（如 402 余额不足）失败，本次结果不可信，
    #   必须拒绝覆盖 baseline —— 否则会把"环境故障"误当成"系统能力"写进基线。
    api_err = sum(1 for x in results
                  if x.get("error") and ("Insufficient Balance" in str(x["error"]) or "402" in str(x["error"])))
    metrics["api_error_count"] = api_err
    metrics["_valid"] = (api_err < max(1, int(n_total * 0.2)))
    return metrics


# ═══════════════════════════════════════════════════════════
# 主流程
# ═══════════════════════════════════════════════════════════
def run(dataset_path, limit=None, quiet=False):
    import rag
    cases = load_dataset(dataset_path)
    if limit:
        cases = cases[:limit]
    if not quiet:
        print(f"加载 {len(cases)} 条用例：{os.path.relpath(dataset_path, ROOT)}")
        print("正在构建语义索引（首次较慢）…")
    index = rag.build_index()
    allowed = index.get("allowed_tables") or []
    results = []
    for i, c in enumerate(cases, 1):
        item = run_one(c, index, allowed)
        results.append(item)
        if not quiet:
            o = _overall(item)
            mark = "✅" if o else ("—" if o is None else "❌")
            bad = [k for k, v in item["scores"].items() if v["status"] is False]
            print(f"  {mark} [{item['id']}] {item['name'] or item['question'][:20]}"
                  + (f"  失败维度={bad}" if bad else "")
                  + f"  ({item['latency']}s)")
    metrics = aggregate(results)
    return {"metrics": metrics, "results": results, "dataset": os.path.relpath(dataset_path, ROOT)}
