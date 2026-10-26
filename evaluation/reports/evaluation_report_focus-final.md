# 知数AI Evaluation Report

- Dataset: `../../../../../tmp/focus`
- Samples: 7（成功执行 6）
- Tag: focus-final  |  Generated: 2026-10-08 12:01:50

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 57.1% | 85.7% | -28.6pp |
| Intent Accuracy | 60.0% | 89.3% | -29.3pp |
| Metric Accuracy | 80.0% | 95.5% | -15.4pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 100.0% | 84.6% | +15.4pp |
| SQL Execution Success | 85.7% | 91.4% | -5.7pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.75 | 4.74 | +1.01 |
| P95 Latency | 7.57 | 7.23 | +0.34 |
| Average Latency | 5.67 | 4.73 | +0.94 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| intent | 2 | 50.0% |
| ok | 1 | 25.0% |
| metrics | 1 | 25.0% |

## 失败用例明细（3 条）

### [g009] 同比-节前对比
- 问题：查询25号前7天同比去年中秋节前7天发单完单率
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 无发单状态字段，无法区分发单与完单）；intent: got=None 期望=compare
- error：无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 无发单状态字段，无法区分发单与完单）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 无发单状态字段，无法区分发单与完单）`

### [g034] 边界-跨年区间
- 问题：查询2025年12月到2026年1月的订单量
- 失败：intent: got=trend 期望=summary
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE toDateTime(ctime) >= '2025-12-01 00:00:00' AND toDateTime(ctime) < '2026-02-01 00:00:00'`

### [10] 国庆假期（当年）+ 指标含糊 → 必须按默认口径答出来（不得拒答）
- 问题：查询分析国庆假期期间小板订单的数据
- 失败：metrics: 缺指标=['订单量'] 实际=['询价后发单率']
- SQL：`SELECT COUNT(*) AS "订单量", COALESCE(SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END), 0) AS "完单量", round(COALESCE(SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 0), 4) AS "完单率", COALESCE(SUM(`
