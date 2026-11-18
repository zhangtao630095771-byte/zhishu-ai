# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden`
- Samples: 3（成功执行 3）
- Tag: golden  |  Generated: 2026-11-16T11:51:00.689519

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 100.0% | 76.5% | +23.5pp |
| Intent Accuracy | 100.0% | 78.6% | +21.4pp |
| Metric Accuracy | 100.0% | 95.2% | +4.8pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 100.0% | 84.6% | +15.4pp |
| SQL Execution Success | 100.0% | 82.3% | +17.6pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | N/A | 100.0% | - |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 23.59 | 5.39 | +18.20 |
| P95 Latency | 29.57 | 7.64 | +21.93 |
| Average Latency | 22.12 | 4.99 | +17.13 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Release Gate

**FAIL**

| 指标 | 阈值 | 实际 | 结果 |
|---|---|---|---|
| Overall Accuracy | >=93.0% | 100.0% | ✅ |
| Metric Accuracy | >=95.0% | 100.0% | ✅ |
| Table Retrieval | >=95.0% | 100.0% | ✅ |
| Field Retrieval | >=95.0% | N/A | ❌ |
| SQL Accuracy | >=94.0% | 100.0% | ✅ |
| SQL Execution Success | >=98.0% | 100.0% | ✅ |
| Grounding Accuracy | >=95.0% | 100.0% | ✅ |
| Hallucination Rate | <=3.0% | 0.0% | ✅ |
| Consistency | >=95.0% | N/A | ❌ |
| P95 Latency | <=8.0s | 29.57s | ❌ |
