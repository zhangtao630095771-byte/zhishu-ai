# -*- coding: utf-8 -*-
"""
_fix_metric_predicates.py —— 修正「指标公式过滤条件作用域」语义混淆（幂等）

★ 背景（多轮澄清机制专项修复 / 任务第四节）：
  现有指标知识把「查询级全局过滤」与「指标公式内部的条件聚合（分子/分母）」，
  都塞进 required_predicates 这一列，导致：
    · 完单率 required_predicates=["status = 6"] —— 错。status=6 是【分子】条件
      （SUM(CASE WHEN status=6...)），分母是全部订单，绝不能变成全局 WHERE。
    · 接单量 required_predicates=["is_picked = 1"] —— 错。is_picked=1 是条件聚合
      （SUM(CASE WHEN is_picked=1...)），不是全局过滤。
    · 接单率 required_predicates=["is_picked = 1"] —— 错。is_picked=1 是【分子】条件，
      分母是总订单量。

★ 修复原则（任务第四节「必须统一实现的原则」）：
  required_predicates 只表达【查询级全局过滤】（如 完单量 = COUNT(*) WHERE status=6，
  这里的 status=6 确实是全局行过滤）。
  指标公式内部的分子/分母条件，一律由 definition 里的公式表达
  （完单率/接单率的 definition 已写清「分母为全部订单/总订单量」）。

★ 幂等：只把「确实被误标」的指标 required_predicates 清空；其它指标（如 完单量 的
  status=6 是正确全局过滤）保持不动。
"""
import config_store

# 需要清空 required_predicates 的指标（其条件是公式内部条件聚合，非全局过滤）
#   name -> 原因说明
FIX = {
    "完单率": "status=6 是分子条件（SUM(CASE WHEN status=6 ...)/COUNT(*)），分母为全部订单，不是全局过滤",
    "接单量": "is_picked=1 是条件聚合（SUM(CASE WHEN is_picked=1 ...)），不是全局 WHERE",
    "接单率": "is_picked=1 是分子条件（SUM(CASE WHEN is_picked=1 ...)/COUNT(*)），分母为总订单量，不是全局过滤",
}


def main():
    config_store.init_store()
    ms = config_store.get_metrics_with_governance(enabled_only=False)
    by_name = {m["name"]: m for m in ms}

    changed = 0
    for name, why in FIX.items():
        m = by_name.get(name)
        if not m:
            print(f"  ⚠ 未找到指标「{name}」，跳过")
            continue
        cur = m.get("required_predicates") or []
        if cur == []:
            print(f"  = 「{name}」required_predicates 已为空，跳过")
            continue
        # 用 upsert 覆盖（带则写；source=manual 保留 approved 状态），并写变更历史
        m["required_predicates"] = []
        ok, msg = config_store.upsert_metric(m, operator="fix_metric_predicates", source="manual")
        print(f"  ✓ 「{name}」required_predicates {cur} → []  {msg}（{why}）")
        changed += 1

    print(f"\n完成：修正 {changed} 个指标的过滤条件作用域语义。")


if __name__ == "__main__":
    main()
