# 双流水线收敛专项 · 最终验收报告（07-acceptance-report）

> 对照任务书 §十二「最终验收标准」逐条核验。✅ 满足 / ⚠️ 部分满足（生产侧待人工）/ ⏸ 待生产确认。

---

## 一、逐条验收

| # | 验收标准 | 结论 | 证据 |
|---|---|---|---|
| 1 | 生产正式取数请求只有一条主流水线 | ✅（代码层） | `agent.ask` = `ask_v2` 唯一路径，无降级分支 |
| 2 | 不存在两套独立的业务编排逻辑 | ✅ | `ask_legacy` 已删除，`rag.retrieve`/`prompt.build_system_prompt` 已删除 |
| 3 | Legacy 有效能力全部迁移或有明确废弃依据 | ✅ | 审计证明 Legacy 是 V2 严格子集，无独有能力 |
| 4 | 统一意图理解、知识检索、SQL 生成和结果处理 | ✅ | 收敛到 query_understanding / semantic_layer / llm / result_validator / nl_answer 单套 |
| 5 | 统一安全闸门和唯一 SQL 执行出口保持不变 | ✅ | `_pre_execute_check` / `_exec_sql` 零改动；`test_gate.py` 121 项 + `test_pipeline_convergence.py` ④⑥ |
| 6 | 统一状态链、日志、Trace 和评测 | ✅ | 状态链 7 态唯一化；trace 落库（trace_json）；评测走统一 runner |
| 7 | 历史业务场景没有功能退化 | ✅（结构性） | 全量回归 30 文件绿；澄清/缓存/增量修改/JOIN/权限测试全绿 |
| 8 | 标准问题集的业务结果正确率达到迁移验收要求 | ⏸ | 需结果级 A/B 对照（缺 LLM+ClickHouse，见 04 §三），不伪造 |
| 9 | 现有测试及新增测试通过，无法通过的有说明 | ✅ | unit 21 + config 9 = 30 文件全绿；integration 未连库已说明 |
| 10 | 架构文档与实际代码一致 | ✅ | 01~06 文档与实际代码逐函数核对 |

## 二、核心指标（实测）

| 指标 | 值 |
|---|---|
| 退役代码 | `ask_legacy`(227 行) + `rag.retrieve`(58 行) + `prompt.build_system_prompt`(47 行) + 降级逻辑 |
| 生产 Legacy 调用（收敛前） | 0 条（`query_records.pipeline` 实测） |
| 收敛后生产流水线 | 唯一 `v2` |
| 新增不变量测试 | `test_pipeline_convergence.py` 26 项 |
| 全量回归 | unit 21 + config 9 全绿 |

## 三、最终结论

**双流水线已收敛为单一主流水线（代码层完成，方案 A fail-closed）**。本次收敛：

- 消除的是**重复的业务流水线**（Legacy 弱化子集），不是为单一入口牺牲模块化——能力仍分散在 `query_understanding`/`semantic_layer`/`guard`/`sql_validator`/`permission`/`result_validator`/`nl_answer`/`sql_experience`/`requirement_review` 等组件，编排唯一。
- 未破坏 `_exec_sql` 唯一执行出口与 `_pre_execute_check` 统一闸门。
- 未降低表白名单、字段/行级权限、只读控制。

**剩余待生产侧完成**：结果级 A/B 对照（需隔离环境 + 生产数据）、生产灰度部署、负责人确认下线（05/06 已列步骤与回滚方案）。
