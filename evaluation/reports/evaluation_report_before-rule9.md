# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 10（成功执行 9）
- Tag: before-rule9  |  Generated: 2026-10-08 11:53:25

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 70.0% | 85.7% | -15.7pp |
| Intent Accuracy | N/A | 89.3% | - |
| Metric Accuracy | 100.0% | 95.5% | +4.5pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 71.4% | 84.6% | -13.2pp |
| SQL Execution Success | 80.0% | 91.4% | -11.4pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 0.0% | 100.0% | -100.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 6.01 | 4.74 | +1.27 |
| P95 Latency | 9.74 | 7.23 | +2.51 |
| Average Latency | 5.96 | 4.73 | +1.23 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 2 | 40.0% |
| sql_contains | 2 | 40.0% |
| abstain | 1 | 20.0% |

## 失败用例明细（3 条）

### [6] 客服调度身份（is_service_staff 字典）
- 问题：统计各客服调度身份的订单量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少"客服调度身份"与订单的关联字段；dwd_tu_order_all.is_service_staff 为客户发单身份，非客服
- error：无法回答：现有表结构不足以计算（缺少"客服调度身份"与订单的关联字段；dwd_tu_order_all.is_service_staff 为客户发单身份，非客服调度身份，且无客服调度人员维度表）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少"客服调度身份"与订单的关联字段；dwd_tu_order_all.is_service_staff 为客户发单身份，非客服调度身份，且无客服调度人员维度表）`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE NOT (order_time IS NULL) AND order_time >= toDateTime('2026-09-08 00:00:00') AND order_time < toDateTime('2026-10-08 00:0`

### [10] 国庆假期（当年）+ 指标不明 → 应「需要澄清」而非「表结构不足」
- 问题：查询分析国庆假期期间小板订单的数据
- 失败：ok: ok=True 期望=False error=；abstain: abstain=False 期望=True；sql_contains: SQL缺谓词=['需要澄清']
- SQL：`SELECT toDate(toDateTime(ctime)) AS "统计日期", COUNT(*) AS "发单量", SUM(CASE WHEN is_picked = 1 THEN 1 ELSE 0 END) AS "接单量", SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END) AS "完单量", SUM(CASE WHEN status = 7 T`
