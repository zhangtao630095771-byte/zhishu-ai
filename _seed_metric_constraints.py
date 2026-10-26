# -*- coding: utf-8 -*-
"""补种「指标关键口径硬约束」（required_predicates）—— 幂等、非破坏

★ 为什么需要它？
  `config_store.DEFAULT_METRIC_CONSTRAINTS` 只在【首次建库】时随默认指标落库。
  老库升级后（`INSERT OR IGNORE` 不会更新已有行）需要一条显式、可重复执行的补种命令，
  把已确认的必要条件补到对应指标上，让校验器真正能阻断口径混用。

★ 安全性：只补「当前 required_predicates 为空」的指标，不覆盖已有值（人工改过的不动）。

运行： .venv/bin/python _seed_metric_constraints.py
"""
import config_store as cs


def main():
    cs._ensure_ready()
    by = {m["name"]: m for m in cs.get_metrics(enabled_only=False)}
    added, skipped, missing = 0, 0, 0
    for name, preds in cs.DEFAULT_METRIC_CONSTRAINTS.items():
        m = by.get(name)
        if not m:
            print(f"  ‑ 跳过（库中无此指标）：{name}")
            missing += 1
            continue
        if m.get("required_predicates"):
            print(f"  = 已有约束，保持不动：{name} {m['required_predicates']}")
            skipped += 1
            continue
        m = dict(m)                      # 复制原记录，保留 definition/doc/tables/keywords
        m["required_predicates"] = preds
        cs.upsert_metric(m)
        print(f"  ✅ 补种：{name} -> {preds}")
        added += 1
    print(f"\n完成：新增 {added} 个，已有 {skipped} 个，库中缺失 {missing} 个。")
    print("★ 记得重启服务 / 重建索引 后生效。")


if __name__ == "__main__":
    main()
