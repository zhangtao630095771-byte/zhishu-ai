# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 1（成功执行 1）
- Tag: smoke2  |  Generated: 2026-11-06T17:07:25.872780

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 100.0% | 76.5% | +23.5pp |
| Intent Accuracy | N/A | 78.6% | - |
| Metric Accuracy | 100.0% | 95.2% | +4.8pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 100.0% | 84.6% | +15.4pp |
| SQL Execution Success | 100.0% | 82.3% | +17.6pp |
| Time Accuracy | N/A | 90.0% | - |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | N/A | 100.0% | - |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 31.52 | 5.39 | +26.13 |
| P95 Latency | 31.52 | 7.64 | +23.88 |
| Average Latency | 31.52 | 4.99 | +26.53 |
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
| P95 Latency | <=8.0s | 31.52s | ❌ |
