# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 27）
- Tag: final-golden  |  Generated: 2026-10-08 12:04:54

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 85.7% | 85.7% | +0.0pp |
| Intent Accuracy | 89.3% | 89.3% | +0.0pp |
| Metric Accuracy | 95.5% | 95.5% | +0.0pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 92.3% | 84.6% | +7.7pp |
| SQL Execution Success | 94.3% | 91.4% | +2.9pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.58 | 4.74 | +0.84 |
| P95 Latency | 7.32 | 7.23 | +0.09 |
| Average Latency | 5.13 | 4.73 | +0.40 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| intent | 3 | 37.5% |
| ok | 2 | 25.0% |
| time | 1 | 12.5% |
| metrics | 1 | 12.5% |
| sql_contains | 1 | 12.5% |

## 失败用例明细（5 条）

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g009] 同比-节前对比
- 问题：查询25号前7天同比去年中秋节前7天发单完单率
- 失败：ok: ok=False 期望=True error=无法回答：所需数据表不在已授权范围内；intent: got=None 期望=compare
- error：无法回答：所需数据表不在已授权范围内
- SQL：`-- 无法回答：所需数据表不在已授权范围内`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，仅有 state=6 完单口径）`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2026-09-08 00:00:00' AND order_time < '2026-10-08 00:00:00' AND deleted_status = 0`

### [g034] 边界-跨年区间
- 问题：查询2025年12月到2026年1月的订单量
- 失败：intent: got=trend 期望=summary
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE toDateTime(ctime) >= '2025-12-01 00:00:00' AND toDateTime(ctime) < '2026-02-01 00:00:00'`
