# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 2（成功执行 2）
- Tag: smoke  |  Generated: 2026-10-08 09:19:02

## 核心指标

| 指标 | 值 |
|---|---|
| Overall Accuracy | 0.0% |
| Intent Accuracy | 0.0% |
| Metric Accuracy | 100.0% |
| Table Retrieval Recall | 100.0% |
| Field Retrieval Recall | N/A |
| SQL Accuracy | 100.0% |
| SQL Execution Success | 100.0% |
| Time Accuracy | N/A |
| Result Accuracy | N/A |
| Grounding Accuracy | 100.0% |
| Hallucination Rate | 0.0% |
| Abstention Accuracy | N/A |
| Consistency Rate | N/A |
| P50 Latency | 4.9 |
| P95 Latency | 6.01 |
| Average Latency | 5.46 |
| Average Token | N/A |
| Average Cost | N/A |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| intent | 2 | 100.0% |

## 失败用例明细（2 条）

### [g001] 简单查询-订单量
- 问题：查询订单量
- 失败：intent: got=summary 期望=data_query
- SQL：`SELECT COUNT(*) AS "订单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE ctime >= toUnixTimestamp(now() - INTERVAL '30' DAY) AND ctime < toUnixTimestamp(now())`

### [g002] 简单查询-完单量
- 问题：统计完单量
- 失败：intent: got=summary 期望=data_query
- SQL：`SELECT COUNT(*) AS "完单量" FROM ctc_data_warehouse.dwd_tu_order_all WHERE status = 6 AND ctime >= toUnixTimestamp(now() - INTERVAL '30' DAY) AND ctime < toUnixTimestamp(now())`
