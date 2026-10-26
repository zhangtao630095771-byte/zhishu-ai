# 知数AI Evaluation Report

- Dataset: `../../../../../tmp/focus`
- Samples: 7（成功执行 7）
- Tag: focus-v3  |  Generated: 2026-10-08 12:00:38

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 85.7% | 85.7% | +0.0pp |
| Intent Accuracy | 100.0% | 89.3% | +10.7pp |
| Metric Accuracy | 100.0% | 95.5% | +4.5pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 66.7% | 84.6% | -17.9pp |
| SQL Execution Success | 85.7% | 91.4% | -5.7pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 0.0% | 100.0% | -100.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 7.62 | 4.74 | +2.88 |
| P95 Latency | 8.01 | 7.23 | +0.78 |
| Average Latency | 6.98 | 4.73 | +2.25 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 1 | 33.3% |
| abstain | 1 | 33.3% |
| sql_contains | 1 | 33.3% |

## 失败用例明细（1 条）

### [10] 国庆假期（当年）+ 指标不明 → 应「需要澄清」而非「表结构不足」
- 问题：查询分析国庆假期期间小板订单的数据
- 失败：ok: ok=True 期望=False error=；abstain: abstain=False 期望=True；sql_contains: SQL缺谓词=['需要澄清']
- SQL：`SELECT COUNT(*) AS "订单量", COALESCE(SUM(CASE WHEN o.status = 6 THEN 1 ELSE 0 END), 0) AS "完单量", round(COALESCE(SUM(CASE WHEN o.status = 6 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 0), 4) AS "完单率", COALESCE(`
