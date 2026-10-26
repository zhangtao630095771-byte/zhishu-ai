# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 10（成功执行 8）
- Tag: after-feedback10  |  Generated: 2026-10-08 11:51:45

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 80.0% | 85.7% | -5.7pp |
| Intent Accuracy | N/A | 89.3% | - |
| Metric Accuracy | 100.0% | 95.5% | +4.5pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 85.7% | 84.6% | +1.1pp |
| SQL Execution Success | 90.0% | 91.4% | -1.4pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.13 | 4.74 | +0.39 |
| P95 Latency | 9.96 | 7.23 | +2.73 |
| Average Latency | 5.34 | 4.73 | +0.61 |
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
- 失败：ok: ok=False 期望=True error=需要澄清：未说明统计哪个业务线的订单量，也未说明"客服调度身份"的判定口径；可选：小板订单量（dwd_tu_order_all，is_service_staff
- error：需要澄清：未说明统计哪个业务线的订单量，也未说明"客服调度身份"的判定口径；可选：小板订单量（dwd_tu_order_all，is_service_staff 区分身份）/ 大板订单量（ods_tu_large_handcart_order_all）/ 代驾订单量（ods_valet_order_all）/ 全部业务
- SQL：`-- 需要澄清：未说明统计哪个业务线的订单量，也未说明"客服调度身份"的判定口径；可选：小板订单量（dwd_tu_order_all，is_service_staff 区分身份）/ 大板订单量（ods_tu_large_handcart_order_all）/ 代驾订单量（ods_valet_order_all）/ 全部业务线合计`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2025-09-08' AND order_time < '2026-10-08'`
