# P0 核心能力升级 · 最终验收报告（acceptance-report）

> 对照任务书 §十「最终验收条件」逐条核验。结论：✅ 满足 / ⚠️ 部分满足 / ⏸ 待验收（环境/数据依赖）。

---

## 一、逐条验收（15 条）

| # | 验收条件 | 结论 | 证据 |
|---|---|---|---|
| 1 | 能够建立标准业务问题集 | ✅ | `golden_cases` 表 + `/api/golden/cases` CRUD + 前端「标准问题」页 |
| 2 | 能够保存 Expected SQL | ✅ | `expected_sql` 字段，写入前过 `validate_expected_sql` 安全验证 |
| 3 | 能够保存 Expected Result | ✅ | `expected_result` 字段（`{"columns","rows","precision"}`），`acceptance=auto` 强制必填 |
| 4 | 能够在固定测试数据上完成结果对比 | ✅ | `result_compare.compare()` 六类判定；`golden-demo` 26 条固定种子已带标准结果 |
| 5 | 能够识别执行成功但结果错误 | ✅ | `status_chain` 七态分离 + `check_result`/`result_compare` 独立于 `exec_ok` |
| 6 | 能够识别典型 JOIN 重复统计问题 | ✅ | `test_join_cardinality.py` 15 项（1:1/1:N/N:1/N:N/状态表重复/COUNT 错/预聚合/DISTINCT） |
| 7 | 区分执行成功、结果合理、业务验证通过 | ✅ | `generated→gate_passed→executed→exec_ok→result_sane→business_verified→requirement_fulfilled` 七态，前端五态徽章 |
| 8 | Golden SQL 不能未经验证自动晋升 | ✅ | `compute_trust` 四条硬规则（`test_sql_experience.py` 62 项覆盖红线） |
| 9 | 能够从已验证 SQL 生成知识草稿 | ✅ | `sql_experience.mine_rules` → `candidate`，`source_case_nos` 防幻觉 |
| 10 | 能够追踪知识版本和 Golden SQL 版本 | ✅ | `knowledge_history` + `sql_experience.version/supersedes` + `golden_case_versions` |
| 11 | 能够运行六类核心评测 | ⚠️ | 五类可自动跑；第 6 类「业务任务完成」依赖人工审核回填（见 known-issues） |
| 12 | 能够生成版本对比和回归测试报告 | ✅ | `report.py` baseline 列 + `evaluate.py --regression/--gate` |
| 13 | 现有权限、安全闸门、唯一执行出口未被破坏 | ✅ | `test_gate.py` 126 项全绿；`_exec_sql`/`_pre_execute_check` 零改动 |
| 14 | 28 个测试文件 + 新增全部通过 | ✅ | 30 个测试文件全绿（unit 21 + config 9；integration 1 未连库如实标注） |
| 15 | 所有新增功能都有代码、测试、文档证据 | ✅ | implementation.md + test-report.md + 本报告 |

## 二、业务结果准确率（当前值）

**当前无法给出真实的业务结果准确率数字**，原因（诚实说明，不伪造）：

- 生产口径评测集 `golden`（35 条）中 13 条有 `expected_sql` 但 **0 条有 `expected_result`**，`regression`（14 条）8 条有 SQL、0 条有结果；`result_accuracy` 因此长期为 `null`（这是本次升级要解决的根因）。
- 唯一带标准结果的是 `golden-demo`（26 条，基于 `demo_shop.db` 固定种子），可作**确定性回归**，但不等同于生产业务正确率。
- 生产业务正确率需要：① 在真实 ClickHouse 上跑通标准 SQL 取得基准快照；② 业务人员逐条确认口径。这两步都需要**生产数据权限 + 业务确认**，本次环境不具备。

**已为「有结果即可量化」铺好路**：`result_compare` 六类判定 + `golden_cases` 管理 + 看板 `business_verified_rate`（分子只含人工审核通过）。补齐数据后即可得出真实准确率。

## 三、是否可进入 P1

**可以**，但建议先完成两个数据补齐动作（非代码，是业务动作）：

1. 对 `golden`/`regression` 中口径已确认的用例，用 `evaluation/annotate_expected.py` 或 `seed-from-experience` 补齐 `expected_sql` + `expected_result`，让 `result_accuracy` 首次可计算。
2. 组织一轮业务确认，把审核通过的 `query_records` 回填 `confirm_reason`，喂给「业务结果正确率」看板。

> P1 方向（来自基线审计）：业务任务完成度自动判分、Recall@K/Context Precision 精确化、评测指标的历史趋势存储、SSE 流式与并发队列（非评测域，但影响真实体验）。
