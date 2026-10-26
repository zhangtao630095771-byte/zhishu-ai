# 04 · Agent 工程化能力审查

> 判定原则：**以实际功能为准，不以是否使用 LangChain/LangGraph 等框架为准**（任务书明确要求）。本项目完全自研、零 Agent 框架依赖。

---

## 一、Orchestration（编排）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（确定性流水线，非模型自主循环）** |

- **工作流清晰**：`agent.ask_v2` 是显式 8 步流水线（理解→检索→resolve→生成→校验→执行→结果校验→回答），每步有 `trace` 耗时标记（`agent.py:607-871`）。
- **非模型自主循环**：SQL 生成→校验→自愈是**确定性 for 循环**（`agent.py:714-736`），`MAX_RETRY=3` 上限，模型不决定"下一步做什么"。
- **超时/重试/终止条件**：LLM 可传 `timeout`（`llm.py:38`）；执行重试有上限；`query_understanding` 有 8s 超时降级（`query_understanding.py:75`）。
- **是否需要引入显式状态机**：**暂不需要**。当前单一问答任务的线性编排足够；仅当进入"多轮会话式分析/看板增删改"（见 `06-productization-roadmap.md`）时才需要结构化 Query State。

**源码依据**：`agent.py:601-872`；`agent.py:714-736`（重试循环）；`config.py:165`（`MAX_RETRY=3`）。

---

## 二、Tool Calling（工具调用）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（自研函数调用，非框架 tool）** |

- **工具=固定函数**：系统不向模型暴露任意工具列表；模型只产出 SQL，工具调用（检索/校验/执行）由代码确定性编排，模型**无法选择调用**任何超出范围的工具。这比"模型自由 tool calling"更安全。
- **输入输出 Schema**：SQL 是唯一模型输出，经 `guard.clean_sql` 清洗 + 五维校验 + 表权限校验约束（`agent.py:716`）。
- **参数校验/失败识别**：`sql_validator.validate_sql_ex` 返回结构化 `status`（passed/failed/error/degraded），区分"校验器崩溃"与"没问题"（`sql_validator.py:433-511`），避免"异常当通过"。
- **最小权限**：数据库只读账号 + 表白名单 + 字段级 + 行级四层（`permission.py:11-15` 分工注释）。
- **调用审计**：权限判定审计 `add_permission_audit`（`permission.py:584-610`）+ 取数记录落库。

**待验证**：模型**不可**调用未暴露工具这一结论成立的前提是——系统没有任何"把 LLM 输出解析为函数名再执行"的路径。已确认：`ai_ingest`/`permission.parse_nl_policy` 等 LLM 输出仅用于"生成草稿"，不直接执行（`permission.py:771-854` 注释"只草拟不生效"）。

---

## 三、Grounding 与 Abstention（依据与拒答）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（较完善）** |

- **Grounding（有依据）**：SQL 引用的表必须落在召回表∪授权白名单（`guard.check_tables_ex`）；字段必须在召回表 `column_list` 内（`sql_validator._check_p3`）。评测有 `grounding_accuracy`（基线 100%）。
- **Abstention（拒答）**：多级拒答——
  - 未召回表/指标 → 拒绝（`agent.py:658-662`）
  - 问停用指标 → 拒绝（`agent.py:651-656`）
  - 模型自述无法回答 → 结构判定拒答（`agent.py:78-87` `_refusal_reason`）
  - 拒答/非查询 → `NotExecutable` 唯一出口拦截（`agent.py:114-132`）
- **推测 vs 事实区分**：`nl_answer` 要求"声明歧义/假设、不编造数字"（`nl_answer.py:40-41`）；结果返回 `ambiguity` 字段。
- **数据冲突拒答**：`abstention` 数据集含 `conflicting` 类型（口径冲突应拒答），已有评测覆盖。

**源码依据**：`agent.py:649-662`；`agent.py:78-132`；`nl_answer.py:25-41`。

---

## 四、Evaluation（评测）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（框架完整，但部分维度未实现）** |

- **固定基准集**：✅ 8 类数据集约 94 条（详见 `01` 报告）。
- **多维度评估**：
  - 检索：`table_retrieval_recall`（✅）、指标召回（✅）、`forbidden` 误召回（✅）、**`field_retrieval_recall`（❌ 未实现，恒 None）**
  - SQL：`sql_accuracy`（✅ 子串）、`sql_not_contains`（✅）、`time`（✅）、`format`（✅）
  - 业务结果：**`result_accuracy`（❌ 未实现，缺 expected_result）**
  - 完整任务：`overall_accuracy`（✅ 全维度 AND）、`sql_execution_success_rate`（✅）、`abstention_accuracy`（✅）
  - 可信度：`grounding_accuracy`（✅）、`hallucination_rate`（✅ 保守三规则）
- **回归测试**：✅ `evaluation/gate.py` 有 Regression Gate（对比历史最佳容忍度）。
- **版本对比**：✅ 报告带 baseline 对比 + delta。
- **复现历史失败**：✅ `regression` 数据集 14 条来自线上反馈回流，`reflow.py` 支持持续回流。

**关键缺陷**：当前基线 **overall_accuracy=85.7% < Release Gate 93%**（`evaluation/reports/baseline.json`），`sql_execution_success_rate=91.4% < 98%`。**若现在跑 `--gate` 会判 FAIL**。

**源码依据**：`evaluation/gate.py`；`evaluation/evaluators/checks.py`；`evaluation/reports/baseline.json`。

---

## 五、Observability（可观测性）

| 判定 | 结论 |
|---|---|
| 状态 | **部分实现（有 trace，缺结构化日志与追踪链）** |

| 项 | 状态 | 依据 |
|---|---|---|
| 检索证据记录 | ⚠️ 部分 | `route_info` 记录命中方式，但无结构化检索证据落库 |
| 模型/工具调用记录 | ❌ 缺 | 无统一调用记录 |
| SQL 记录 | ✅ | 取数记录 `query_records` + 权限审计 `final_sql`（但存明文，见 S-01） |
| 执行结果/错误记录 | ✅ 部分 | `query_records` 存结果快照 |
| 模型/Prompt/知识版本 | ⚠️ 部分 | 知识有 `version` 列 + `knowledge_history`；但 Prompt 无版本快照 |
| 耗时记录 | ✅ | `agent.py` trace 逐阶段耗时 |
| 敏感数据泄露风险 | ⚠️ 有 | SQL 明文入审计（S-01） |
| 按日志定位失败 | ⚠️ 弱 | 全程 `print()`，无 trace_id，多请求并发时无法串联 |

**源码依据**：`agent.py:607-613/870`（trace）；`server.py:826`（request_id 缺失）；`config_store.py:850`（index_state）；全程 `print()`（`server.py`/`agent.py` 多处）。

---

## 六、六项能力总评

| 能力 | 判定 | 一句话结论 |
|---|---|---|
| Orchestration | 已实现 | 确定性流水线，暂无需状态机 |
| Tool Calling | 已实现（自研） | 模型只产 SQL，无自由工具调用面，安全 |
| Grounding | 已实现 | 表/字段双重依据校验，基线 100% |
| Abstention | 已实现 | 多级拒答 + 唯一出口 fail-closed |
| Evaluation | 已实现（框架完整） | 但 result_accuracy/field_recall 未实现，基线低于门禁 |
| Observability | 部分实现 | 有 trace，缺结构化日志与 request_id 追踪链 |

---

## 七、特别说明（任务书要求）

- **本项目未用 LangChain/LangGraph**，但 Orchestration/Tool/Evaluation 能力**均已通过自研实现**，不因缺框架而判定能力缺失。
- **是否需要引入 Agent 框架**：见 `07-prioritized-action-plan.md` 技术选型部分——**结论是暂不需要**，当前自研编排更贴合"确定性 SQL 生成 + 强校验"的场景，引入框架反而增加失控面。
