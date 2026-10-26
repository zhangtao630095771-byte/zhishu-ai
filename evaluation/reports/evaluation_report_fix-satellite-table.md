# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 11（成功执行 10）
- Tag: fix-satellite-table  |  Generated: 2026-10-08 14:58:11

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 81.8% | 85.7% | -3.9pp |
| Intent Accuracy | N/A | 89.3% | - |
| Metric Accuracy | 100.0% | 95.5% | +4.5pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 83.3% | 84.6% | -1.3pp |
| SQL Execution Success | 90.9% | 91.4% | -0.5pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.66 | 4.74 | +0.92 |
| P95 Latency | 7.69 | 7.23 | +0.46 |
| Average Latency | 5.81 | 4.73 | +1.08 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 1 | 50.0% |
| sql_contains | 1 | 50.0% |

## 失败用例明细（2 条）

### [6] 客服调度身份（is_service_staff 字典）
- 问题：统计各客服调度身份的订单量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中 is_service_staff 为客户发单身份，非客服
- error：无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中 is_service_staff 为客户发单身份，非客服调度身份）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中 is_service_staff 为客户发单身份，非客服调度身份）`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2026-09-08 00:00:00' AND order_time < '2026-10-08 00:00:00'`
