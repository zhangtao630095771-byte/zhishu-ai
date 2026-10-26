# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 10（成功执行 9）
- Tag: final-regression  |  Generated: 2026-10-08 12:05:52

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 70.0% | 85.7% | -15.7pp |
| Intent Accuracy | N/A | 89.3% | - |
| Metric Accuracy | 83.3% | 95.5% | -12.1pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 83.3% | 84.6% | -1.3pp |
| SQL Execution Success | 90.0% | 91.4% | -1.4pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 4.69 | 4.74 | -0.05 |
| P95 Latency | 9.01 | 7.23 | +1.78 |
| Average Latency | 5.44 | 4.73 | +0.71 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 1 | 33.3% |
| sql_contains | 1 | 33.3% |
| metrics | 1 | 33.3% |

## 失败用例明细（3 条）

### [6] 客服调度身份（is_service_staff 字典）
- 问题：统计各客服调度身份的订单量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all.is_service_staff 为客户发单身份，非客服调度
- error：无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all.is_service_staff 为客户发单身份，非客服调度身份）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all.is_service_staff 为客户发单身份，非客服调度身份）`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2026-09-08 00:00:00' AND order_time < '2026-10-08 00:00:00'`

### [10] 国庆假期（当年）+ 指标含糊 → 必须按默认口径答出来（不得拒答）
- 问题：查询分析国庆假期期间小板订单的数据
- 失败：metrics: 缺指标=['订单量'] 实际=['询价后发单率']
- SQL：`SELECT COUNT(*) AS "订单量", COALESCE(SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END), 0) AS "完单量", round(COALESCE(SUM(CASE WHEN status = 6 THEN 1 ELSE 0 END) * 1.0 / COUNT(*), 0), 4) AS "完单率", COALESCE(SUM(`
