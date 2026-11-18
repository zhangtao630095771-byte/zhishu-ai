# -*- coding: utf-8 -*-
"""
evaluation/experiments/experience/run_experience_eval.py —— SQL 经验中心 对照评测

四组对照（同模型 / 同问题集 / 同数据快照 / 同权限条件，只改经验开关）：

    A 组：基线（EXP_ENABLED=0）                      —— 等价于接入前
    B 组：+ 成功 SQL 优先检索（golden 作为 Prompt 参考）
    C 组：+ 失败经验辅助校验（生成前提醒 + 生成后检查 + 命中后修一轮）
    D 组：+ AI 归纳规则（已发布的经验规则/模板）

用法：
    .venv/bin/python evaluation/experiments/experience/run_experience_eval.py --limit 8
    .venv/bin/python evaluation/experiments/experience/run_experience_eval.py --dataset golden --groups A,B

★ 诚实原则：
   · 某指标若现有数据不足以计算（如业务结果正确率需要 expected_result），报告里写「暂不可测」，
     不给 0、也不给估算值；
   · 每组都记录样本数与耗时，样本太小会显式标注「仅供通路验证，不具统计意义」。
"""
import argparse
import json
import os
import sys
from datetime import datetime

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

import config                       # noqa: E402
from evaluation import runner       # noqa: E402

# 组名 → 开关组合
GROUPS = {
    "A": {"EXP_ENABLED": False},
    "B": {"EXP_ENABLED": True, "EXP_SUCCESS_RECALL": True, "EXP_FAIL_PREVENT": False,
          "EXP_FAIL_REPAIR": False, "EXP_RULES": False, "EXP_EXACT_REUSE": False},
    "C": {"EXP_ENABLED": True, "EXP_SUCCESS_RECALL": True, "EXP_FAIL_PREVENT": True,
          "EXP_FAIL_REPAIR": True, "EXP_RULES": False, "EXP_EXACT_REUSE": False},
    "D": {"EXP_ENABLED": True, "EXP_SUCCESS_RECALL": True, "EXP_FAIL_PREVENT": True,
          "EXP_FAIL_REPAIR": True, "EXP_RULES": True, "EXP_EXACT_REUSE": False},
}

# 报告要看的指标（顺序即展示顺序）
METRIC_ROWS = [
    ("overall_accuracy", "综合通过率（全部判定维度均通过）"),
    ("sql_execution_success_rate", "SQL 执行成功率（≠业务正确）"),
    ("sql_accuracy", "SQL 片段命中率（口径/过滤/JOIN 关键片段）"),
    ("table_retrieval_recall", "表召回率"),
    ("metric_accuracy", "指标识别准确率"),
    ("time_accuracy", "时间口径准确率"),
    ("abstention_accuracy", "应拒答时的拒答率"),
    ("hallucination_rate", "幻觉率（越低越好）"),
    ("exp_case_hit_rate", "★ 黄金 SQL 命中率（召回到高可信案例的题目占比）"),
    ("exp_failure_hit_rate", "★ 失败经验覆盖率"),
    ("exp_warning_total", "★ 生成后被判「疑似重犯历史错误」条数"),
    ("avg_latency", "平均耗时（秒）"),
    ("p95_latency", "P95 耗时（秒）"),
]

# 现有数据不足以计算的指标 —— 必须显式写出来，不能装作 0
NOT_MEASURABLE = [
    ("result_accuracy", "最终业务结果准确率", "94 条用例尚无 expected_sql / expected_result，无法自动比对"),
    ("review_first_pass_rate", "审核一次通过率", "离线评测不产生人工审核动作"),
    ("repeat_error_rate", "错误复发率", "需要「同一问题在改造前后的错误集合」配对，当前样本不足"),
    ("misrecall_rate", "经验误召回率", "需要人工逐条判定召回是否适用；当前高可信案例仅个位数"),
    ("reuse_success_rate", "经验复用后的成功率", "复用次数样本极少（<10），统计无意义"),
]


def apply_flags(flags):
    """直接改 config 模块属性（agent 每次运行时 getattr 读取，因此即时生效）"""
    origin = {}
    for k, v in flags.items():
        origin[k] = getattr(config, k, None)
        setattr(config, k, v)
    return origin


def restore(origin):
    for k, v in origin.items():
        setattr(config, k, v)


def _fmt(v):
    if v is None:
        return "暂不可测"
    if isinstance(v, float):
        return f"{v:.4f}"
    return str(v)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(ROOT, "evaluation/datasets/golden"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--groups", default="A,B,C,D")
    a = ap.parse_args()

    groups = [g.strip().upper() for g in a.groups.split(",") if g.strip()]
    for g in groups:
        if g not in GROUPS:
            print(f"❌ 未知分组：{g}（可选 A/B/C/D）")
            return 2

    out = {"dataset": os.path.relpath(a.dataset, ROOT), "limit": a.limit,
           "started_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
           "weights": dict(config.EXP_RANK_WEIGHTS), "groups": {}}

    for g in groups:
        print(f"\n===== 组 {g} =====")
        print("  开关：", GROUPS[g])
        origin = apply_flags(GROUPS[g])
        try:
            res = runner.run(a.dataset, limit=a.limit, quiet=True)
        finally:
            restore(origin)
        out["groups"][g] = {"metrics": res["metrics"], "flags": GROUPS[g]}
        m = res["metrics"]
        print(f"  样本 {m.get('sample_count')} 条，执行成功 {m.get('executed_count')} 条，"
              f"综合通过率 {_fmt(m.get('overall_accuracy'))}，"
              f"黄金命中 {_fmt(m.get('exp_case_hit_rate'))}")

    # ── 生成 markdown 报告 ──
    lines = ["# SQL 经验中心 · 四组对照评测", "",
             f"- 数据集：`{out['dataset']}`　样本上限：{a.limit or '全部'}",
             f"- 时间：{out['started_at']}",
             f"- 排序权重：`{json.dumps(out['weights'], ensure_ascii=False)}`", ""]
    cols = [g for g in groups]
    lines.append("| 指标 | " + " | ".join(f"{g} 组" for g in cols) + " |")
    lines.append("|---|" + "---|" * len(cols))
    for key, label in METRIC_ROWS:
        vals = [_fmt((out["groups"][g]["metrics"] or {}).get(key)) for g in cols]
        lines.append(f"| {label} | " + " | ".join(vals) + " |")
    lines.append("")
    n = (out["groups"][cols[0]]["metrics"] or {}).get("sample_count") if cols else 0
    if n and n < 20:
        lines.append(f"> ⚠️ 本次每组仅 {n} 条样本，**只用于验证评测通路与机制接线，不具统计意义**。"
                     "结论需在全量数据集（94 条）上重跑。")
        lines.append("")
    # ★ 归因约束：任何跨组差异在样本不足时都不能直接算到「经验机制」头上。
    #   这条必须写在报告里，否则读者会把 LLM 随机波动当成机制收益。
    lines.append("## ⚠️ 归因约束（读数前必看）")
    lines.append("")
    lines.append("1. **同题不同解**：生成阶段调用大模型，温度非 0，同一道题每次生成的 SQL 不完全相同。"
                 "因此综合通过率 / SQL 片段命中率的跨组差异**包含随机波动**，单轮不能作为机制收益的证据。")
    lines.append("2. **召回也带随机性**：适用性硬校验依赖模型识别出的「表集合 / 指标集合」，"
                 "识别结果本身有波动，会连带影响黄金命中率（同一道题在不同组可能命中不同数量）。")
    lines.append("3. **只有开关级指标可强归因**：`exp_case_hit_rate` 从 0 变为非 0、`exp_failure_hit_rate` "
                 "随 `EXP_FAIL_PREVENT` 由 0 变为非 0 —— 这类「0 → 非 0」的跳变才是接线成功的可靠证据。")
    lines.append("4. **D 组需要单独核实**：若规则表为空（`sql_experience_rules` 无已发布规则），"
                 "D 组优于 B/C 组**不得归因于经验规则**，只能视为随机波动。")
    lines.append("")
    lines.append("## 暂不可测的指标（不为凑数编造）")
    lines.append("")
    lines.append("| 指标 | 说明 | 原因 |")
    lines.append("|---|---|---|")
    for key, label, why in NOT_MEASURABLE:
        lines.append(f"| {label}（`{key}`） | — | {why} |")

    md = "\n".join(lines)
    p_md = os.path.join(HERE, "experience_eval_report.md")
    p_json = os.path.join(HERE, "experience_eval_result.json")
    with open(p_md, "w", encoding="utf-8") as f:
        f.write(md + "\n")
    with open(p_json, "w", encoding="utf-8") as f:
        json.dump(out, f, ensure_ascii=False, indent=2, default=str)
    print("\n" + md)
    print(f"\n已保存：{p_md}\n已保存：{p_json}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
