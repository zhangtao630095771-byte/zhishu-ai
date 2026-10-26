# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 35（成功执行 28）
- Tag: golden-after-named  |  Generated: 2026-10-08 15:25:02

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 85.7% | 85.7% | +0.0pp |
| Intent Accuracy | 100.0% | 89.3% | +10.7pp |
| Metric Accuracy | 95.5% | 95.5% | +0.0pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 92.3% | 84.6% | +7.7pp |
| SQL Execution Success | 91.4% | 91.4% | +0.0pp |
| Time Accuracy | 90.0% | 90.0% | +0.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 83.3% | 100.0% | -16.7pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.25 | 4.74 | +0.51 |
| P95 Latency | 8.17 | 7.23 | +0.94 |
| Average Latency | 5.01 | 4.73 | +0.28 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 3 | 42.9% |
| time | 1 | 14.3% |
| metrics | 1 | 14.3% |
| sql_contains | 1 | 14.3% |
| abstain | 1 | 14.3% |

## 失败用例明细（5 条）

### [g006] 多表Join-注册与发单客户
- 问题：查询2025年全年的注册客户数，注册司机数，发单客户数
- 失败：time: SQL缺时间边界=['2025-01-01', '2025-12-31']
- SQL：`SELECT (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_customer_all WHERE toYear(create_data_time) = 2025) AS "注册客户数", (SELECT COUNT(DISTINCT id) FROM ctc_data_warehouse.ods_tu_driver_all WH`

### [g009] 同比-节前对比
- 问题：查询25号前7天同比去年中秋节前7天发单完单率
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 仅有订单创建/发单时间 ctime，无法区分"发单"与"完单"的独立统
- error：无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 仅有订单创建/发单时间 ctime，无法区分"发单"与"完单"的独立统计口径，且代驾/大板订单主表与通用订单表口径不一致，无法统一计算发单完单率）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少"发单量"口径字段；dwd_tu_order_all 仅有订单创建/发单时间 ctime，无法区分"发单"与"完单"的独立统计口径，且代驾/大板订单主表与通用订单表口径不一致，无法统一计算发单完单率）`

### [g013] 同义词-妥投量
- 问题：统计大板的妥投量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）；metrics: 缺指标=['大板完单量'] 实际=['大板完单率']
- error：无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少妥投状态/妥投时间字段，state 仅有 6=完单，无妥投口径）`

### [g015] 业务术语-代驾调度发单
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2026-09-08 00:00:00' AND order_time < '2026-10-08 00:00:00'`

### [g027] 权限不足-未开放指标
- 问题：查询资金流水明细
- 失败：ok: ok=True 期望=False error=；abstain: abstain=False 期望=True
- SQL：`SELECT r.id AS "流水ID", r.orderno AS "订单编号", r.customer AS "客户ID", r.driver AS "司机ID", r.money AS "金额", r.current_money AS "当前余额", r.type AS "流水类型", t.title AS "类型名称", t.revenue_expenditure AS "收支类型", `
