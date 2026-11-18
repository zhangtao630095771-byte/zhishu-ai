# -*- coding: utf-8 -*-
"""
evaluation/experiments/ablation/run_ablation.py —— Ablation Runner（消融实验）

原理：把某个模块【临时替换为 no-op】再跑同一数据集，比较指标差值，
      从而量化该模块的真实贡献（而不是"看起来很重要"）。

用法：
    .venv/bin/python evaluation/experiments/ablation/run_ablation.py --dataset golden --limit 20

输出（用户要求格式）：
    module | baseline_accuracy | ablated_accuracy | delta | latency_delta

★ 注意：消融通过 monkeypatch 实现，不修改任何业务代码；运行完自动还原。
"""
import argparse
import contextlib
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ROOT = os.path.dirname(os.path.dirname(os.path.dirname(HERE)))
if ROOT not in sys.path:
    sys.path.insert(0, ROOT)

from evaluation import runner  # noqa: E402


# ── 各模块的「禁用」实现（返回一个 restore 回调）──
@contextlib.contextmanager
def disable_sql_validator():
    import sql_validator
    orig = sql_validator.validate_sql
    sql_validator.validate_sql = lambda *a, **k: []
    try:
        yield
    finally:
        sql_validator.validate_sql = orig


@contextlib.contextmanager
def disable_result_validator():
    import result_validator
    orig = result_validator.validate_result
    result_validator.validate_result = lambda *a, **k: []
    try:
        yield
    finally:
        result_validator.validate_result = orig


@contextlib.contextmanager
def disable_dictionary():
    import rag
    orig = rag.retrieve_dicts
    rag.retrieve_dicts = lambda *a, **k: ([], "ablation: 字典检索已禁用")
    try:
        yield
    finally:
        rag.retrieve_dicts = orig


@contextlib.contextmanager
def disable_calendar():
    import calendar_util
    orig = calendar_util.build_time_context
    calendar_util.build_time_context = lambda *a, **k: ""
    try:
        yield
    finally:
        calendar_util.build_time_context = orig


@contextlib.contextmanager
def disable_entity_filter():
    """禁用「实体级业务线隔离」：把 P2 实体过滤关掉（模拟修复前的跨线误召回）"""
    import semantic_layer
    orig = semantic_layer.retrieve_semantic
    # 简化实现：清空 understanding.entities，使 wanted_tables 为空 → 不过滤
    def patched(index, question, understanding=None):
        u = dict(understanding or {})
        u["entities"] = []
        return orig(index, question, u)
    semantic_layer.retrieve_semantic = patched
    try:
        yield
    finally:
        semantic_layer.retrieve_semantic = orig


@contextlib.contextmanager
def disable_semantic_layer():
    """B：关闭 Semantic Layer —— 把 P0~P4 分层语义检索整体置空，
    让 ctx 空 → 触发零召回兜底拒答，量化语义层本身的贡献。"""
    import semantic_layer
    orig = semantic_layer.retrieve_semantic
    semantic_layer.retrieve_semantic = lambda *a, **k: semantic_layer.SemanticContext()
    try:
        yield
    finally:
        semantic_layer.retrieve_semantic = orig


@contextlib.contextmanager
def disable_join_cardinality():
    """E：关闭 JOIN 基数约束 —— 只停用 P2 一对一/一对多预聚合检查（不碰其它维度）。"""
    import sql_validator
    orig = sql_validator._check_p2
    sql_validator._check_p2 = lambda *a, **k: None
    try:
        yield
    finally:
        sql_validator._check_p2 = orig


@contextlib.contextmanager
def disable_sql_experience():
    """F：关闭 SQL 经验检索 —— retrieve 恒返回 None（无成功案例参考 / 无失败预防）。"""
    import sql_experience
    orig = sql_experience.retrieve
    sql_experience.retrieve = lambda *a, **k: None
    try:
        yield
    finally:
        sql_experience.retrieve = orig


@contextlib.contextmanager
def disable_trust_ranking():
    """D：关闭知识可信等级排序 —— 把 trust/verify 权重归零（仅保留语义/表/指标相似度排序）。"""
    import config
    orig = getattr(config, "EXP_RANK_WEIGHTS", None)
    w = dict(orig or {})
    w["trust"] = 0.0
    w["verify"] = 0.0
    config.EXP_RANK_WEIGHTS = w
    try:
        yield
    finally:
        if orig is None:
            # 原本没有显式权重 → 恢复默认（删除属性回到 getattr 默认 {}）
            try:
                del config.EXP_RANK_WEIGHTS
            except Exception:
                pass
        else:
            config.EXP_RANK_WEIGHTS = orig


MODULES = {
    "sql_validator": disable_sql_validator,
    "result_validator": disable_result_validator,
    "dictionary": disable_dictionary,
    "calendar": disable_calendar,
    "entity_filter": disable_entity_filter,
    # ★ 本次 P0-4 补齐：B/C/E/F/D 对照（对齐任务书 §6.4 的七组消融）
    "semantic_layer": disable_semantic_layer,       # B 关闭 Semantic Layer
    "join_cardinality": disable_join_cardinality,   # E 关闭 JOIN 基数约束
    "sql_experience": disable_sql_experience,       # F 关闭 SQL 经验检索
    "trust_ranking": disable_trust_ranking,         # D 关闭知识可信等级排序
}

# ★ 安全 / 合规模块：永不消融（即使显式指定也拒绝）。
#   消融它们会制造「越权查询 / 编造成功 / 绕过权限」这类危险行为，
#   对产品决策没有价值，只有安全风险。
#   若确需评估其影响，只能在隔离环境（只读副本库）里单独进行，且不得作为删改依据。
NEVER_ABLATE = {
    "guard",                 # 只读校验 + 表级权限
    "table_acl",             # 表白名单
    "disabled_metric_block",  # 停用指标拦截
    "abstain_fallback",      # 兜底拒答（防编造）
}
# ★ 另注：消融是【诊断】手段，不是【裁剪】手段。
#   本工具只改内存中的函数引用（contextmanager + finally 自动还原），不触碰磁盘源码；
#   任何「删除/停用模块」的决定，都必须走 Regression Gate + Release Gate，并附 Before/After。


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--dataset", default=os.path.join(ROOT, "evaluation/datasets/golden"))
    ap.add_argument("--limit", type=int, default=None)
    ap.add_argument("--modules", default=",".join(MODULES.keys()))
    a = ap.parse_args()

    print("① 测全量系统（baseline）…")
    full = runner.run(a.dataset, limit=a.limit, quiet=True)["metrics"]
    base_acc = full.get("overall_accuracy")

    rows = []
    for name in [m.strip() for m in a.modules.split(",") if m.strip()]:
        if name in NEVER_ABLATE:
            print(f"  ⛔ 拒绝消融安全模块：{name}（安全/合规模块永不消融）")
            continue
        ctx = MODULES.get(name)
        if not ctx:
            print(f"  跳过未知模块 {name}")
            continue
        print(f"② 消融：移除 {name} …")
        with ctx():
            ab = runner.run(a.dataset, limit=a.limit, quiet=True)["metrics"]
        rows.append({
            "module": name,
            "baseline_accuracy": base_acc,
            "ablated_accuracy": ab.get("overall_accuracy"),
            "delta": (None if (base_acc is None or ab.get("overall_accuracy") is None)
                      else round(base_acc - ab.get("overall_accuracy"), 4)),
            "latency_delta": (None if (full.get("avg_latency") is None or ab.get("avg_latency") is None)
                              else round(ab["avg_latency"] - full["avg_latency"], 2)),
        })

    out = {"baseline": full, "ablation": rows}
    p = os.path.join(HERE, "ablation_result.json")
    json.dump(out, open(p, "w", encoding="utf-8"), ensure_ascii=False, indent=2, default=str)
    print("\n===== Ablation Result =====")
    print(f"{'module':16s} {'baseline':>9s} {'ablated':>9s} {'delta':>8s} {'latency_delta':>14s}")
    for r in rows:
        print(f"{r['module']:16s} {str(r['baseline_accuracy']):>9s} {str(r['ablated_accuracy']):>9s} "
              f"{str(r['delta']):>8s} {str(r['latency_delta']):>14s}")
    print(f"\n结果已保存：{p}")


if __name__ == "__main__":
    main()
