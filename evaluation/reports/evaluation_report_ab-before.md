# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 24）
- Tag: ab-before  |  Generated: 2026-10-08 10:29:43

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 77.1% | 76.5% | +0.7pp |
| Intent Accuracy | 82.1% | 78.6% | +3.6pp |
| Metric Accuracy | 95.5% | 95.2% | +0.2pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 84.6% | 84.6% | +0.0pp |
| SQL Execution Success | 85.7% | 82.3% | +3.4pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 8.92 | 5.39 | +3.53 |
| P95 Latency | 17.78 | 7.64 | +10.14 |
| Average Latency | 9.44 | 4.99 | +4.45 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 5 | 33.3% |
| intent | 5 | 33.3% |
| sql_contains | 2 | 13.3% |
| time | 1 | 6.7% |
| metrics | 1 | 6.7% |
| format | 1 | 6.7% |

## 失败用例明细（8 条）

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g011] 模糊表达-情况
- 问题：最近大板情况怎么样
- 失败：ok: ok=False 期望=True error=无法回答：问题过于宽泛，未明确要统计的指标（如订单量、完单率、金额等）与时间范围，现有信息不足以确定具体口径；intent: got=None 期望=summary
- error：无法回答：问题过于宽泛，未明确要统计的指标（如订单量、完单率、金额等）与时间范围，现有信息不足以确定具体口径
- SQL：`-- 无法回答：问题过于宽泛，未明确要统计的指标（如订单量、完单率、金额等）与时间范围，现有信息不足以确定具体口径`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，ods_tu_large_handcart_order_all 仅有 state=6 完单口径，无妥；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，ods_tu_large_handcart_order_all 仅有 state=6 完单口径，无妥投相关字段）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，ods_tu_large_handcart_order_all 仅有 state=6 完单口径，无妥投相关字段）`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少"调度发单"相关标识字段，无法区分是否为调度发单）；sql_contains: SQL缺谓词=['is_service_staff = 2']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（缺少"调度发单"相关标识字段，无法区分是否为调度发单）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少"调度发单"相关标识字段，无法区分是否为调度发单）`

### [g019] 易混淆字段-通用status=6
- 问题：查询完单金额
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：大板/代驾/小板口径不同，且未给出时间范围与统计维度，无法确定唯一口径）；sql_contains: SQL缺谓词=['status = 6']；intent: got=None 期望=summary
- error：无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：大板/代驾/小板口径不同，且未给出时间范围与统计维度，无法确定唯一口径）
- SQL：`-- 无法回答：现有表结构不足以计算（"完单金额"未指明业务类型：大板/代驾/小板口径不同，且未给出时间范围与统计维度，无法确定唯一口径）`

### [g032] 复合指标-询价后发单率
- 问题：查询2026年9月的询价后发单率
- 失败：ok: ok=False 期望=True error=执行失败（已重试）：Received ClickHouse exception, code: 36, server response: Code: 36. DB
- error：执行失败（已重试）：Received ClickHouse exception, code: 36, server response: Code: 36. DB::Exception: MONTH doesn't look like datepart name in dateTrunc: While processin
- SQL：`WITH new_users AS (SELECT id AS customer, toDate(toDateTime(ctime)) AS reg_date, dateTrunc('MONTH', toDateTime(ctime)) AS reg_month FROM ctc_data_warehouse.ods_tu_customer_all WHERE toDate(toDateTime(`

### [g034] 边界-跨年区间
- 问题：查询2025年12月到2026年1月的订单量
- 失败：intent: got=trend 期望=summary
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE toDateTime(ctime) >= '2025-12-01 00:00:00' AND toDateTime(ctime) < '2026-02-01 00:00:00'`

### [rf-7-20261008100604] 回流-other
- 问题：查询26年每月注册司机数
- 失败：format: 『月份』不匹配 ^\d{4}-\d{2}$，样例=['2026-01-01', '2026-02-01']
- SQL：`SELECT toStartOfMonth(create_data_time) AS `月份`, COUNT(DISTINCT id) AS `注册司机数` FROM ctc_data_warehouse.ods_tu_driver_all WHERE create_data_time >= '2026-01-01 00:00:00' AND create_data_time < '2027-01`
