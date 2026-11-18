# 双流水线收敛专项 · Legacy 退出与代码清理报告（06-legacy-retirement）

> 阶段六。记录退役的代码、修改的文件、清理后的依赖关系。

---

## 一、退役条件核验（对齐任务书 §8）

| # | 条件 | 核验结果 |
|---|---|---|
| 1 | Legacy 不再承载独有业务能力 | ✅ 审计（01）证明 Legacy 是 V2 严格子集 |
| 2 | 仍被使用的功能已迁移并通过测试 | ✅ 无「Legacy 独有」功能；共享组件保留 |
| 3 | 统一主流程对照测试通过 | ⚠️ 结构性对照通过；结果级对照待隔离环境补做（04） |
| 4 | 灰度验证通过 | ⏸ 待生产部署（05） |
| 5 | 权限/安全/审计未退化 | ✅ 全量回归绿 + `test_pipeline_convergence.py` ⑥ |
| 6 | 无未处理的 Legacy 依赖调用 | ✅ grep 确认 0 处残留（除 docs 注释） |
| 7 | 回滚方案明确 | ✅ 05 §四（git revert） |
| 8 | 生产负责人确认下线 | ⏸ **待人工确认** |

> 说明：条件 3/4/8 属「生产侧」，本环境无法完成。但代码层退役已完成、可安全合并，生产是否上线由负责人按 05 的步骤确认后决定。

## 二、退役的代码

| 代码 | 位置 | 处置 |
|---|---|---|
| `ask_legacy()` | `agent.py` | 删除（227 行） |
| `rag.retrieve()` | `rag.py` | 删除（单路检索，已被 `semantic_layer.retrieve_semantic` 取代） |
| `prompt.build_system_prompt()` | `prompt.py` | 删除（旧 Prompt 组装，已被 `agent._build_system_prompt` 取代） |
| `_is_recoverable()` / `_RECOVERABLE_ERRORS` | `agent.py` | 删除（降级白名单，随 fail-closed 废弃） |
| `LEGACY_FALLBACK_MODE` | `config.py` | 删除（分流配置） |
| `check2.py` | 根目录 | 删除（引用退役代码的废弃验证脚本） |
| `test_legacy_validation.py` | 根目录 | 删除（专门测 ask_legacy，已失效） |

## 三、保留的共享组件（未动）

`guard`、`sql_validator`、`permission`、`_exec_sql`、`_review_sql`、`llm.chat`、`nl_answer.answer`、`result_validator.validate_result`、`rag.retrieve_dicts`、`rag.match_disabled_metric`、`rag.named_tables`、`rag._hybrid_retrieve`、`prompt.build_hard_constraints_block`、`prompt._build_dict_block`、`prompt._build_schema_block`、`prompt._build_metric_block`、`prompt.build_repair_prompt`、`semantic_layer.*`、`sql_experience.*`、`requirement_review.*`、`calendar_util.*`。

> 其中 `rag._hybrid_retrieve`、`prompt._build_*` 等原本被 Legacy 与 V2 共用，V2 仍在用，故保留。

## 四、修改的测试

| 文件 | 改动 |
|---|---|
| `test_gate.py` | 合并降级测试为 `test_fail_closed_always`；删除 `test_production_blocks_always_fallback` |
| `test_permission.py` | 删除 C16「Legacy 路径字段级 DENY」块（Legacy 已无） |
| `test_prompt_constraints.py` | `test_system_prompt_end`→`test_hard_constraints_block_end`（直接测共享硬约束块）；`test_wiring` 去掉 legacy 断言 |
| `test_pipeline.py` | 删除 `test_legacy_prompt_includes_dict` |
| `run_tests.py` | 移除 `test_legacy_validation.py`；登记 `test_pipeline_convergence.py` |
| 新增 `test_pipeline_convergence.py` | 26 项收敛不变量 |

## 五、迁移记录与历史版本

- 历史打包快照 `zhishu-ai-code-bundle/` 仍保留（含旧版 Legacy 实现），可作为最终兜底参考，非当前代码。
- git 历史保留本次全部改动，回滚用 `git revert`。
