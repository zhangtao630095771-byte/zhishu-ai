# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/golden/golden_semantic_v1.jsonl`
- Samples: 1（成功执行 1）
- Tag: smoke_off  |  Generated: 2026-11-13T15:53:30.344693

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 100.0% | 76.5% | +23.5pp |
| Intent Accuracy | 100.0% | 78.6% | +21.4pp |
| Metric Accuracy | 100.0% | 95.2% | +4.8pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | N/A | 84.6% | - |
| SQL Execution Success | 100.0% | 82.3% | +17.6pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | N/A | 100.0% | - |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 36.88 | 5.39 | +31.49 |
| P95 Latency | 36.88 | 7.64 | +29.24 |
| Average Latency | 36.88 | 4.99 | +31.89 |
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
| SQL Accuracy | >=94.0% | N/A | ❌ |
| SQL Execution Success | >=98.0% | 100.0% | ✅ |
| Grounding Accuracy | >=95.0% | 100.0% | ✅ |
| Hallucination Rate | <=3.0% | 0.0% | ✅ |
| Consistency | >=95.0% | N/A | ❌ |
| P95 Latency | <=8.0s | 36.88s | ❌ |
