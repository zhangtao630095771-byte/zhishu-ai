# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 28）
- Tag: golden-after-satellite  |  Generated: 2026-10-08 15:02:21

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 88.6% | 85.7% | +2.9pp |
| Intent Accuracy | 96.4% | 89.3% | +7.1pp |
| Metric Accuracy | 95.5% | 95.5% | +0.0pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 92.3% | 84.6% | +7.7pp |
| SQL Execution Success | 97.1% | 91.4% | +5.7pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 6.27 | 4.74 | +1.53 |
| P95 Latency | 18.21 | 7.23 | +10.98 |
| Average Latency | 6.8 | 4.73 | +2.07 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| time | 1 | 20.0% |
| ok | 1 | 20.0% |
| metrics | 1 | 20.0% |
| sql_contains | 1 | 20.0% |
| intent | 1 | 20.0% |

## 失败用例明细（4 条）

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']
- error：无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE create_time >= '2026-09-08' AND create_time < '2026-10-08'`

### [g034] 边界-跨年区间
- 问题：查询2025年12月到2026年1月的订单量
- 失败：intent: got=trend 期望=summary
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE toDateTime(ctime) >= '2025-12-01 00:00:00' AND toDateTime(ctime) < '2026-02-01 00:00:00'`
