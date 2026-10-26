# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 24）
- Tag: after-feedback10-golden  |  Generated: 2026-10-08 11:58:20

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 77.1% | 85.7% | -8.6pp |
| Intent Accuracy | 78.6% | 89.3% | -10.7pp |
| Metric Accuracy | 95.5% | 95.5% | +0.0pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 84.6% | 84.6% | +0.0pp |
| SQL Execution Success | 85.7% | 91.4% | -5.7pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.55 | 4.74 | +0.81 |
| P95 Latency | 9.68 | 7.23 | +2.45 |
| Average Latency | 5.05 | 4.73 | +0.32 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| intent | 6 | 40.0% |
| ok | 5 | 33.3% |
| sql_contains | 2 | 13.3% |
| time | 1 | 6.7% |
| metrics | 1 | 6.7% |

## 失败用例明细（8 条）

### [g001] 简单查询-订单量
- 问题：查询订单量
- 失败：ok: ok=False 期望=True error=需要澄清：未说明要统计哪类业务的订单量；可选：小板订单量（dwd_tu_order_all，order_type=1）/ 大板订单量（ods_tu_large_；intent: got=None 期望=summary
- error：需要澄清：未说明要统计哪类业务的订单量；可选：小板订单量（dwd_tu_order_all，order_type=1）/ 大板订单量（ods_tu_large_handcart_order_all）/ 代驾订单量（ods_valet_order_all）/ 全部订单量
- SQL：`-- 需要澄清：未说明要统计哪类业务的订单量；可选：小板订单量（dwd_tu_order_all，order_type=1）/ 大板订单量（ods_tu_large_handcart_order_all）/ 代驾订单量（ods_valet_order_all）/ 全部订单量`

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g009] 同比-节前对比
- 问题：查询25号前7天同比去年中秋节前7天发单完单率
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少"发单完单率"的明确定义口径；"发单完单率"既非发单率也非完单率，无法唯一确定分子分母）；intent: got=None 期望=compare
- error：无法回答：现有表结构不足以计算（缺少"发单完单率"的明确定义口径；"发单完单率"既非发单率也非完单率，无法唯一确定分子分母）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少"发单完单率"的明确定义口径；"发单完单率"既非发单率也非完单率，无法唯一确定分子分母）`

### [g011] 模糊表达-情况
- 问题：最近大板情况怎么样
- 失败：ok: ok=False 期望=True error=需要澄清：未说明要统计哪个指标与时间范围；可选：大板发单量（近30天）/ 大板完单量（近30天）/ 大板完单率（近30天）/ 大板订单金额（近30天）；intent: got=None 期望=summary
- error：需要澄清：未说明要统计哪个指标与时间范围；可选：大板发单量（近30天）/ 大板完单量（近30天）/ 大板完单率（近30天）/ 大板订单金额（近30天）
- SQL：`-- 需要澄清：未说明要统计哪个指标与时间范围；可选：大板发单量（近30天）/ 大板完单量（近30天）/ 大板完单率（近30天）/ 大板订单金额（近30天）`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=需要澄清：未说明"妥投量"的统计口径；可选：按 state=6 完单量统计 / 按 finish_time 非空的完单量统计 / 按运输完成时间在指定区间统计；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']；intent: got=None 期望=summary
- error：需要澄清：未说明"妥投量"的统计口径；可选：按 state=6 完单量统计 / 按 finish_time 非空的完单量统计 / 按运输完成时间在指定区间统计
- SQL：`-- 需要澄清：未说明"妥投量"的统计口径；可选：按 state=6 完单量统计 / 按 finish_time 非空的完单量统计 / 按运输完成时间在指定区间统计`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2025-09-08' AND order_time < '2026-10-08'`

### [g019] 易混淆字段-通用status=6
- 问题：查询完单金额
- 失败：ok: ok=False 期望=True error=需要澄清：未说明要统计哪类业务的完单金额；可选：小板/拖车完单金额（dwd_tu_order_all，status=6）/ 大板完单金额（ods_tu_larg；sql_contains: SQL缺谓词=['status = 6']；intent: got=None 期望=summary
- error：需要澄清：未说明要统计哪类业务的完单金额；可选：小板/拖车完单金额（dwd_tu_order_all，status=6）/ 大板完单金额（ods_tu_large_handcart_order_all，state=6）/ 代驾完单金额（ods_valet_order_all，status=40）/ 全部业务合计
- SQL：`-- 需要澄清：未说明要统计哪类业务的完单金额；可选：小板/拖车完单金额（dwd_tu_order_all，status=6）/ 大板完单金额（ods_tu_large_handcart_order_all，state=6）/ 代驾完单金额（ods_valet_order_all，status=40）/ 全部业务合计`

### [g034] 边界-跨年区间
- 问题：查询2025年12月到2026年1月的订单量
- 失败：intent: got=trend 期望=summary
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE ctime >= toUnixTimestamp(toDateTime('2025-12-01 00:00:00')) AND ctime < toUnixTimestamp(toDateTime('2026-02-01 00:00:00'))`
