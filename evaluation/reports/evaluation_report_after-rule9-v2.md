# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 10（成功执行 8）
- Tag: after-rule9-v2  |  Generated: 2026-10-08 11:55:00

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
| P50 Latency | 5.85 | 4.74 | +1.11 |
| P95 Latency | 9.68 | 7.23 | +2.45 |
| Average Latency | 5.5 | 4.73 | +0.77 |
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
- 失败：ok: ok=False 期望=True error=需要澄清：未说明统计哪类订单（小板/大板/代驾）及"客服调度身份"的判定口径；可选：小板订单按 is_service_staff 分组 / 大板订单按 larg
- error：需要澄清：未说明统计哪类订单（小板/大板/代驾）及"客服调度身份"的判定口径；可选：小板订单按 is_service_staff 分组 / 大板订单按 large_handcart_staff_id 分组 / 代驾订单按 is_service_staff 分组
- SQL：`-- 需要澄清：未说明统计哪类订单（小板/大板/代驾）及"客服调度身份"的判定口径；可选：小板订单按 is_service_staff 分组 / 大板订单按 large_handcart_staff_id 分组 / 代驾订单按 is_service_staff 分组`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2025-09-08' AND order_time < '2026-10-08'`
