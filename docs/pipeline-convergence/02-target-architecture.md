# 双流水线收敛专项 · 统一主流水线目标架构（02-target-architecture）

> 阶段二设计。核心结论：**V2 已经是事实上的统一主流水线**，本次收敛不是「重写一套」，
> 而是「把 V2 确立为唯一主流程 + 决策兜底策略 + 清理 Legacy 重复实现 + 补齐可观测性」。
> 遵循任务书铁律：消除的是重复的**业务流水线**，不是为单一代码入口牺牲模块化、稳定性和业务正确性。

---

## 一、目标形态

```
用户问题
  → 统一入口（/api/ask，server 层：澄清/增量修改/直行决策）
  → 统一意图理解（query_understanding.understand）
  → 统一知识检索（semantic_layer.retrieve_semantic：P0~P4 分层）
  → 统一上下文组装（agent._build_system_prompt：权威性排序）
  → 统一 SQL 生成（llm.chat，含经验精确复用分支）
  → 统一需求一致性校验（requirement_review，仅判定不改写）
  → 统一安全闸门（agent._pre_execute_check：guard→表→validator→字段→行级改写后重校验）
  → 统一 SQL 执行（agent._exec_sql，全仓唯一出口）
  → 统一结果验证（result_validator + 结果脱敏 mask_rows）
  → 统一自然语言回答（nl_answer + _review_sql 尾部并行）
  → 统一状态链（7 态）+ 统一 trace + 统一审计落库
```

- 单流水线 ≠ 单函数：编排在 `ask_v2`（唯一编排点），能力仍分散在 `query_understanding` / `semantic_layer` / `rag` / `prompt` / `sql_experience` / `requirement_review` / `guard` / `sql_validator` / `permission` / `result_validator` / `nl_answer` 等模块化组件中。

## 二、必须保留的既有能力（收敛红线，逐条对齐任务书 §4.1）

| 能力 | 保留方式 |
|---|---|
| 缓存（权限隔离） | `result_cache` 在 server 层，命中时仍写权限审计，不动 |
| `base_query_id` 增量修改 | `_modify_query` + `modify_context` 注入，不动 |
| `clarify` 只能澄清不执行 SQL | `_run_start` 的 ACTION_CLARIFY 分支，不动 |
| `unknown` 进知识治理/拒答，不猜 | `_run_start` 的 STATUS_NEEDS_KNOWLEDGE，不动 |
| 知识审核机制 | `config_store` 治理准入，不动 |
| 状态追踪 | `status_chain` 7 态，保留并统一（Legacy 废弃后仅剩一套） |

## 三、兜底策略决策（本次收敛的**唯一实质性产品决策**）

现状：`ask()` 遇 V2 异常时按 `LEGACY_FALLBACK_MODE` 降级到 Legacy。

审计结论：Legacy 无独立业务价值、且校验不对称（无行级改写、自愈不重过字段/行级闸门）。因此降级到 Legacy **不是安全的降级目标**。

**推荐方案（分级，供决策）：**

- **方案 A（推荐）· fail-closed 不再降级**：删除 Legacy 降级，V2 异常统一 fail-closed 如实返回。理由：① 真实生产 Legacy 调用为 0，降级几乎从未发生；② 瞬时故障（超时/连接）本应由 `ssh_tunnel` 自愈 + `_exec_sql` 重试 + 连接层重试消化，不该靠「换一条更弱的流水线」掩盖。
- **方案 B · 保留轻量只读兜底但重构**：把 Legacy 改造成「纯只读、复用 V2 全部校验组件（含行级改写 + 自愈重过闸门）」的**子集降级路径**，消除安全不对称。成本高、收益低（因为 A 已够用）。
- **方案 C（不推荐）· 维持现状**：继续保留两套，违背本专项目标。

> 本设计**默认按方案 A** 推进；若业务方确认「必须有瞬时故障降级」再切换方案 B。切换属产品/生产决策，需人工确认。

## 四、公共组件抽取清单（避免过度抽象）

审计已确认这些是「真共享」组件，**保持现状不动**：

`guard`、`sql_validator`、`permission`、`_exec_sql`、`_review_sql`、`llm.chat`、`nl_answer.answer`、`result_validator.validate_result`、`rag.retrieve_dicts`、`rag.match_disabled_metric`、`rag.named_tables`、`semantic_layer.*`、`sql_experience.*`、`requirement_review.*`、`calendar_util.*`。

**不建议**新增「Pipeline 抽象基类 / 策略模式」这类封装——当前只有一条主流程，抽象基类是过度设计。

## 五、统一状态链（不变）

`generated → gate_passed → requirement_verdict → executed → exec_ok → result_sane → business_verified → requirement_fulfilled`

铁律重申：`exec_ok=true` ≠ `result_sane=true` ≠ `business_verified=true`。SQL 执行成功绝不自动认定为业务正确。

## 六、可观测性补强（支撑灰度切换，任务书 §7 要求）

当前 `query_records` 未落库 `pipeline`/`degraded`/`trace` 的降级细节。收敛后需补：

1. `query_records` 补 `degraded`（0/1）、`degrade_reason`、`trace_json` 列（幂等 ALTER）。
2. 灰度期通过 `pipeline` 字段识别「实际走的流程版本」，确认 100% 走统一主流程。

## 七、目标文件结构

| 文件 | 目标职责 |
|---|---|
| `agent.py` | 仅保留 `ask`(=ask_v2 唯一编排) + `ask_v2` + 公共助手；**删除 `ask_legacy`** |
| `rag.py` | 保留 `build_index` / `retrieve_dicts` / `match_disabled_metric` / `named_tables`；`retrieve` 若无其它调用方可下线 |
| `prompt.py` | 保留 `build_hard_constraints_block` / `_build_dict_block` / `build_repair_prompt` / `build_requirement_*`；`build_system_prompt`（Legacy 版）下线 |
| `semantic_layer.py` | 唯一检索编排（P0~P4），不动 |
| 其余 | 不动 |

> 注：`zhishu-ai-code-bundle/` 是历史打包快照，**不是当前代码**，清理时一并归档或删除，不作为重构对象。
