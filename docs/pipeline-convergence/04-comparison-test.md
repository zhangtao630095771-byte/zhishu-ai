# 双流水线收敛专项 · 对照测试报告（04-comparison-test）

> 阶段四。原则：对照测试是「关键环节」，不得以 SQL 执行成功率冒充业务正确率。
> 本环境无真实 LLM 调用与 ClickHouse，无法做「同一输入双路端到端跑数」的线上 A/B；因此本报告
> **如实区分「已在本环境完成的结构性对照」与「需生产/隔离环境补做的结果级对照」**，不伪造结果。

---

## 一、对照策略说明

对照的核心问题不是「Legacy 和 V2 谁的 SQL 字符串一样」，而是「收敛后统一主流程是否**不丢 Legacy 原有能力 + 业务正确率不退化 + 安全不退化**」。

审计（01）已经证明：**Legacy 是 V2 的严格功能子集**（无 Query Understanding、无 P0~P4 分层检索、无行级权限改写、无结果体检、无状态链、无经验沉淀），且安全/校验/执行/脱敏**已共享同一套实现**。因此「以 V2 为准」不构成任何能力丢失——这是对照测试的第一层结论。

## 二、本环境已完成的结构性对照（有证据）

| 维度 | 对照结论 | 证据 |
|---|---|---|
| 安全组件一致性 | 两条流水线曾共享 guard/sql_validator/permission/_exec_sql；收敛后唯一主流程继续调用它们 | `test_pipeline_convergence.py` ⑥（6 项断言） |
| 唯一执行出口 | 收敛后仍只有 `agent._exec_sql` 一处 `db.execute_sql` | `test_gate.py:test_no_bare_exec_anywhere` + `test_pipeline_convergence.py` ④ |
| 行级权限/字段权限不退化 | 唯一主流程保留行级改写后重校验 + 字段级 DENY 阻断 | `test_permission.py` 59/59、`test_pipeline_convergence.py` ⑥ |
| JOIN 基数/指标口径不退化 | sql_validator 五维校验未被削弱 | `test_join_cardinality.py` 15/15、`test_gate.py` 121/121 |
| 状态链/结果体检/自然语言回答 | 收敛后只剩 V2 路径，这些能力天然保留 | `test_status_chain.py` 46 项、`test_result_check.py` 16/16 |
| 澄清/缓存/增量修改 | 均在 server 层，走 `agent.ask`（=ask_v2），未改 | `test_clarification.py`、`test_modify_query.py` 49/49、`test_result_cache.py` 23 项 |
| 异常处理 | fail-closed：任何异常（含超时）→ ok=False、不降级、不执行 | `test_pipeline_convergence.py` ③（4 项）、`test_gate.py` ② |

## 三、需生产/隔离环境补做的结果级对照（未做，如实标注）

任务书 §6.3 要求对具备 `expected_sql`/`expected_result` 的案例做**结果级比较**。本环境：

- **无 LLM 调用**（无法真实生成 SQL）、**无 ClickHouse**（无法执行取数）。
- `golden-demo` 26 条已带 `expected_result`（demo_shop.db 固定种子），可用于后续隔离环境做确定性回归；
  生产口径用例（golden 35 / regression 14）**缺 `expected_result`**（见 P0 专项 known-issues）。

**结论**：结果级 A/B 对照是「切换上线前」的必要动作，但属于**隔离环境 + 生产数据权限**的验证，不是本地代码能替代的。已在本报告与 05-cutover 中列为上线前阻塞项，绝不宣称「已通过结果级对照」。

## 四、对照测试用例清单（已落地为可复用资产）

`test_pipeline_convergence.py` 用**打桩**（stub 掉 llm/检索/执行）覆盖任务书 §6.1 的核心场景的安全侧断言：

- 无权限字段 → 字段级 DENY 阻断（`test_permission.py` C15）
- 无法回答的问题 → 拒答不执行（`test_gate.py:test_refusal_blocks_execution`）
- 复杂 JOIN / 1:N 状态表 / 重复状态记录 → JOIN 基数校验（`test_join_cardinality.py`）
- 用户修改原始问题 / 连续澄清 → `test_modify_query.py` / `test_clarification.py`

---

## 结论

**结构性对照已通过（全量回归绿）**；**结果级对照因缺 LLM+ClickHouse 无法在本环境完成**，已明确标记，不伪造正确率。满足「切换」的结构性前置条件，但结果级对照须在上线前的隔离环境补做。
