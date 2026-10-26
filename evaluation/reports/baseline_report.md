# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 26）
- Tag: ab-after2  |  Generated: 2026-10-08 10:40:35

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 85.7% | 76.5% | +9.2pp |
| Intent Accuracy | 89.3% | 78.6% | +10.7pp |
| Metric Accuracy | 95.5% | 95.2% | +0.2pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 84.6% | 84.6% | +0.0pp |
| SQL Execution Success | 91.4% | 82.3% | +9.1pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 4.74 | 5.39 | -0.65 |
| P95 Latency | 7.23 | 7.64 | -0.41 |
| Average Latency | 4.73 | 4.99 | -0.26 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 3 | 30.0% |
| intent | 3 | 30.0% |
| sql_contains | 2 | 20.0% |
| time | 1 | 10.0% |
| metrics | 1 | 10.0% |

## 失败用例明细（5 条）

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g011] 模糊表达-情况
- 问题：最近大板情况怎么样
- 失败：ok: ok=False 期望=True error=无法回答：问题过于宽泛，未明确需要统计的指标（如订单量、完单率、金额等）及时间范围，现有信息不足以确定具体查询口径；intent: got=None 期望=summary
- error：无法回答：问题过于宽泛，未明确需要统计的指标（如订单量、完单率、金额等）及时间范围，现有信息不足以确定具体查询口径
- SQL：`-- 无法回答：问题过于宽泛，未明确需要统计的指标（如订单量、完单率、金额等）及时间范围，现有信息不足以确定具体查询口径`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE NOT (order_time IS NULL)`

### [g019] 易混淆字段-通用status=6
- 问题：查询完单金额
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：小板/大板/代驾口径不同，且未给出时间范围与统计维度，无法确定唯一口径）；sql_contains: SQL缺谓词=['status = 6']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：小板/大板/代驾口径不同，且未给出时间范围与统计维度，无法确定唯一口径）
- SQL：`-- 无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：小板/大板/代驾口径不同，且未给出时间范围与统计维度，无法确定唯一口径）`
