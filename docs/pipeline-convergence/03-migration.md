# 双流水线收敛专项 · Legacy 能力迁移清单（03-migration）

> 阶段三迁移计划（**本阶段只产出计划，尚未执行**）。
> 执行前提：阶段四「双流水线对照测试」通过 + 人工确认兜底策略（见 02 §三）。

---

## 一、迁移总原则（对齐任务书 §5）

1. Legacy 有、V2 没有的能力 → 经审计：**不存在**。故「迁移」本质是「以 V2 为准 + 清理重复 + 决策兜底」。
2. 重复实现 → 保留「更可靠、可测试、维护成本更低」的：即 V2 侧（`semantic_layer.retrieve_semantic`、`_build_system_prompt`、V2 自愈循环）。
3. 逻辑不同 → 需业务样例确认（见阶段四对照测试）。
4. 迁移期间 Legacy 暂留、不删除、不做无关大规模重构、不绕过安全控制、每项迁移配测试。

## 二、逐项迁移清单（依据 01 差异矩阵）

| # | 能力 | 处置 | 依赖清理 | 测试 |
|---|---|---|---|---|
| M1 | 意图理解 | 统一到 V2（Legacy 无此能力，直接以 V2 为准） | — | 现有 `test_retrieve`/`test_requirement_review` |
| M2 | P0-P4 检索 | 统一到 `semantic_layer.retrieve_semantic`；`rag.retrieve` 标记待下线 | `check2.py`（工具脚本）改接或归档 | `test_retrieve.py` |
| M3 | Prompt 组装 | 统一到 `_build_system_prompt`；`prompt.build_system_prompt` 待下线 | `check2.py`（`eval_modify_live.py` 的同名函数是本地函数，无关） | `test_prompt_assembly.py` |
| M4 | SQL 经验检索 | 统一到 V2（Legacy 无） | — | `test_sql_experience.py` |
| M5 | 需求契约 | 统一到 V2（Legacy 无） | — | `test_requirement_review.py` / `test_gate_req.py` |
| M6 | SQL 自愈 | 统一到 V2 自愈循环（含「自愈后重过完整闸门」）；废弃 Legacy 自愈（字段级校验不在循环内、无行级） | — | `test_gate.py`（`test_repair_requeue` 类） |
| M7 | 行级权限改写 | 统一到 V2（Legacy 缺失，属缺口，无需迁移、随 Legacy 下线） | — | `test_permission.py` |
| M8 | 结果体检 | 统一到 V2（Legacy 无） | — | `test_result_check.py` |
| M9 | 自然语言回答 | 统一到 V2（Legacy 无） | — | 现有 `nl_answer` 相关 |
| M10 | 状态链 | 统一到 V2 7 态（Legacy 无，随下线只剩一套） | — | `test_status_chain.py` |
| M11 | 兜底策略 | **产品决策**：默认方案 A「fail-closed 不再降级」，删除 `ask_legacy` 调用分支 | — | 新增 `test_pipeline_convergence.py`（分流 + 异常不降级） |

## 三、实施步骤（阶段四通过后）

### 步骤 1：补齐可观测性（先于任何删除）
- `query_records` 幂等 ALTER 补 `degraded`/`degrade_reason`/`trace_json` 列（`config_store.init_store`）。
- 让灰度能识别「实际走的流程版本」。

### 步骤 2：建立对照测试（阶段四，详见 04）
- 相同输入跑 V2 与 Legacy，输出对照报告，确认「不丢 Legacy 能力 + 结果正确率不退化 + 安全不退化」。

### 步骤 3：切换默认到统一主流程（阶段五）
- 测试环境 `LEGACY_FALLBACK_MODE=never`（新枚举）或直接移除降级分支 → 内部回放 → 小范围灰度 → 扩大 → 确认 0 次 Legacy 调用。

### 步骤 4：下线 Legacy（阶段六）
- 删除 `agent.ask_legacy`、`agent.ask` 内的降级分支、`rag.retrieve`（若无其它调用方）、`prompt.build_system_prompt`（若无其它调用方）。
- 处理 `check2.py`、`eval_modify_live.py` 两个依赖脚本。
- 清理 `LEGACY_FALLBACK_MODE` 相关配置与文档。

## 四、回滚方案

| 阶段 | 回滚手段 |
|---|---|
| 步骤 1（补列） | 纯新增列，对旧代码无影响，无需回滚 |
| 步骤 3（切默认） | `LEGACY_FALLBACK_MODE` 切回 `whitelist`（若尚未删代码），或 `git revert` 单次提交 |
| 步骤 4（删代码） | `git revert`；删除前打 tag 保留 Legacy 历史版本（`zhishu-ai-code-bundle` 已是历史快照，可作最后兜底） |

## 五、迁移前的阻塞项（须先确认，否则不进入步骤 3/4）

1. **兜底策略决策**（方案 A / B）——需业务/生产负责人确认。
2. **对照测试数据**：需在隔离测试环境跑通 V2 与 Legacy 的对照（阶段四），当前 `golden-demo` 有 26 条带标准结果，可作基础；生产口径用例缺 `expected_result`（见 P0 专项 known-issues），补齐后再做结果级对照。
3. **日志采集**：确认有无 `degraded`/`trace` 历史数据可统计，否则按审计 §四如实标注「无法统计」。

---

## 结论

**已具备进入迁移（阶段三执行）的条件**，但建议顺序：先补可观测性（步骤 1）→ 跑对照测试（阶段四）→ 人工确认兜底策略 → 再逐步切换与下线。是否现在执行，等待指示。
