# 知数AI Evaluation Report

- Dataset: `evaluation/datasets/regression`
- Samples: 12（成功执行 10）
- Tag: named-table-fix  |  Generated: 2026-10-08 15:22:04

## 核心指标

| 指标 | 值 | Baseline | Delta |
|---|---|---|---|
| Overall Accuracy | 75.0% | 85.7% | -10.7pp |
| Intent Accuracy | N/A | 89.3% | - |
| Metric Accuracy | 100.0% | 95.5% | +4.5pp |
| Table Retrieval Recall | 100.0% | 100.0% | +0.0pp |
| Field Retrieval Recall | N/A | N/A | - |
| SQL Accuracy | 83.3% | 84.6% | -1.3pp |
| SQL Execution Success | 83.3% | 91.4% | -8.1pp |
| Time Accuracy | 100.0% | 90.0% | +10.0pp |
| Result Accuracy | N/A | N/A | - |
| Grounding Accuracy | 100.0% | 100.0% | +0.0pp |
| Hallucination Rate | 0.0% | 0.0% | +0.0pp |
| Abstention Accuracy | 100.0% | 100.0% | +0.0pp |
| Consistency Rate | N/A | N/A | - |
| P50 Latency | 5.37 | 4.74 | +0.63 |
| P95 Latency | 7.51 | 7.23 | +0.28 |
| Average Latency | 5.2 | 4.73 | +0.47 |
| Average Token | N/A | N/A | - |
| Average Cost | N/A | N/A | - |

## Error Analysis（失败维度分布）

| 失败维度 | 次数 | 占比 |
|---|---|---|
| ok | 2 | 66.7% |
| sql_contains | 1 | 33.3% |

## 失败用例明细（3 条）

### [6] 客服调度身份（is_service_staff 字典）
- 问题：统计各客服调度身份的订单量
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中仅有发单身份 is_service_staff，无法区分具
- error：无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中仅有发单身份 is_service_staff，无法区分具体客服调度人员）
- SQL：`-- 无法回答：现有表结构不足以计算（缺少客服调度身份与订单的关联字段，dwd_tu_order_all 中仅有发单身份 is_service_staff，无法区分具体客服调度人员）`

### [7] 代驾调度发单（is_service_staff=2）
- 问题：统计代驾调度发单的订单量
- 失败：sql_contains: SQL缺谓词=['is_service_staff = 2']
- SQL：`SELECT COUNT(*) AS "代驾发单订单量" FROM ctc_data_warehouse.ods_valet_order_all WHERE order_time >= '2026-09-08 00:00:00' AND order_time < '2026-10-08 00:00:00'`

### [12] 用户点名「资金流水表」→ 不得被实体过滤误杀
- 问题：查询8月份资金流水表中 缴纳代驾审核激活费未退回的司机数及流水金额
- 失败：ok: ok=False 期望=True error=无法回答：现有表结构不足以计算（ods_tu_record_all 缺少可识别"代驾审核激活费"的流水类型字段，ods_tu_record_type_all 与
- error：无法回答：现有表结构不足以计算（ods_tu_record_all 缺少可识别"代驾审核激活费"的流水类型字段，ods_tu_record_type_all 与 ods_tu_record_all 之间无关联字段，无法确定哪些流水属于代驾审核激活费）
- SQL：`-- 无法回答：现有表结构不足以计算（ods_tu_record_all 缺少可识别"代驾审核激活费"的流水类型字段，ods_tu_record_type_all 与 ods_tu_record_all 之间无关联字段，无法确定哪些流水属于代驾审核激活费）`
