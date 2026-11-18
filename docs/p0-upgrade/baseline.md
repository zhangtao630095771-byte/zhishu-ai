# P0 核心能力升级 · 现状审计报告（baseline）

> 生成时间：2026-11-02　口径：**以当前代码为准**（逐文件核对，不采信历史文档的结论）。
> 标注规则：✅ 已完整实现　⚠️ 部分实现　🟡 只有设计/雏形　❌ 尚未实现。
> 原则：已实现的能力**不重复开发**，只补测试与缺口。

---

## 一、总览（规模实测）

| 维度 | 现状 |
|---|---|
| 后端 | Python 3.13 + FastAPI，`config_store.py`(196KB) / `server.py`(180KB) / `agent.py`(78KB) 三大核心 |
| 评测目录 | `evaluation/`（8 类数据集 + 判分器 + Runner + Gate + 回流 + 消融）+ 根目录 `evaluate.py` / `eval/` |
| 数据集 | golden 35 / regression 14 / boundary 13 / abstention 10 / paraphrase 9 / counterfactual 6 / adversarial 6 / **golden-demo 26** / inbox 1 |
| 配置库 | SQLite `agent_config.db` 36+ 张表 |
| 测试 | `run_tests.py` 分 unit/config/integration 三层，registry 共 **29 个测试文件** |
| 测试基线 | unit 层 **20/20 全绿**（2026-11-02 实测） |

---

## 二、逐项能力审计

### P0-1　Expected SQL 与 Expected Result 标准评测体系

| 子项 | 状态 | 证据 |
|---|---|---|
| 数据集字段 `expected_sql` / `expected_result` | ✅ | `evaluation/evaluators/checks.py:check_result` 消费；golden-demo 26 条已带两者 |
| 标准结果结构化比较（行数/列数/无序/精度/NULL） | ✅ | `checks.py:_check_result_structured`，`test_result_check.py` 16 项断言全绿 |
| ground truth 标注工具 | ✅ | `evaluation/annotate_expected.py`（`status`/`annotate`/`seed-from-experience`），`test_annotate_expected.py` 17 项 |
| 经验→评测闭环（seed-from-experience） | ✅ | 同文件 `match_seed`，只搬「已验证成功」案例 |
| 字段覆盖审计工具 | ✅ | `evaluation/schema.py` |
| 三类数据区分（固定测试集/生产业务案例/历史结果） | 🟡 | 有 `golden-demo`(固定 demo 种子) vs `golden`(生产口径) vs `inbox/回流`，但未显式建模「数据快照版本」字段 |
| **标准问题集管理（管理员 CRUD/导入/审核发布/版本/停用归档）** | ❌ | 标准问题仅以 **JSONL 文件**形式存在，无 DB 表、无管理 API、无版本历史、无停用/归档；`version` 只是样例内的一个数字字段 |
| **标准 SQL 的安全验证（录入时必须过闸门）** | ❌ | `annotate_expected.py` 只做人工确认，**不校验** expected_sql 的只读/表白名单/字段权限/Validator；管理员录错也会写入 |
| 标准结果差异定位 | ⚠️ | `check_result` 返回「不匹配」与首个差异行，但无逐格 diff 明细、无「字段不一致/行数不一致/无法比较」六类分类输出 |
| 版本追踪 | ⚠️ | 样例有 `version` 字段，无版本历史表、无 diff 记录 |

### P0-2　业务结果正确性验证体系

| 子项 | 状态 | 证据 |
|---|---|---|
| L1 SQL 结构（只读/表白名单/字段+行级权限） | ✅ | `guard.sql_guard` + `guard.check_tables_ex` + `permission.evaluate` + `permission.apply_row_filters` |
| L2 业务口径（指标/时间/过滤/JOIN 基数/粒度/枚举） | ✅ | `sql_validator.validate_sql_ex`（P0~P3 + 比率），`test_join_cardinality.py` 15 项 |
| L3 结果正确性（Expected Result / 数值差异 / 行数 / NULL / 重复） | ✅ | `check_result` + `result_validator.validate_result`（比率越界/整列 NULL/漏 GROUP BY/全 0） |
| L4 业务人员确认 | ✅ | `query_records.review_status`（approved/rejected）+ `/api/records/{rid}/submit` + 取数审核页 |
| 结果状态链（generated→…→business_verified→requirement_fulfilled） | ✅ | `agent.ask_v2` 的 `chain` 字典 + `config_store._chain_from_record`，`test_status_chain.py` 全绿 |
| **exec_ok 与 business_verified 严格分离** | ✅ | `business_verified` 仅在 review_status=approved 回填；`_chain_from_record` 用三态（True/False/None） |
| 未验证结果显示为「未验证」 | ✅ | `REQ_VERDICT_VIEW` 五态 + skip/bad 分离（前端），有测试守着 |
| **L4 确认分类（指标口径错/时间错/范围错/JOIN 重复/枚举错/不符认知/其他）** | ⚠️ | 已有 `FEEDBACK_REASONS` 枚举（含 wrong_metric/wrong_time/join_dup/result_mismatch 等 16 类），但**取数审核**仅 approved/rejected 二态，未在审核动作上挂这 8 类确认分类 |
| JOIN 重复统计专项测试（8 场景） | ✅ | `test_join_cardinality.py`：1:1/1:N/N:1/N:N/状态表重复/COUNT 错误/预聚合后再 JOIN/DISTINCT 误用 |

### P0-3　Golden SQL 自动进化体系

| 子项 | 状态 | 证据 |
|---|---|---|
| 五类案例区分（Golden/Successful/Corrected/Failed/Template） | ✅ | `sql_experience.py`：`kind ∈ {success,failed,corrected}` + `trust_level` + `sql_experience_rules`(sql_template) |
| Golden 晋升流程（记录→执行→确认→分析→去重→固定数据验证→审批→发布） | ✅ | `upsert_case/update_case/set_case_review/set_case_verify/promote_case/compute_trust` + `dup_of` 去重 + `verify_by_reexec` |
| 未验证不得晋升（硬规则） | ✅ | `compute_trust` 四条硬规则（kind≠success 永不 golden、exec_ok=0 永不 golden、unverified/verify_failed 永不 golden、必须显式 golden_published） |
| 审核状态与验证状态独立 | ✅ | `review_status` 与 `verify_status` 独立，`trust_level` 联合推导 |
| AI 知识提炼（candidate→人工审核→P0~P4） | ✅ | `mine_rules`（校验 source_case_nos 存在，防幻觉）+ `publish_rule`（`conflict_note` 非空需 force） |
| 知识冲突进入待处理队列 | ✅ | `_conflict_of` + `conflict_note` + 不 force 不发布 |
| 版本与回归（版本/修改历史/影响面/使用效果/知识变更触发回归） | ✅ | `version`/`supersedes`/`knowledge_history`/`affected_cases`/`mark_stale`/`sql_experience_usage` |
| 重复案例识别 | ✅ | `dup_of` + 相似度去重 |
| 复用成功率/使用次数 | ✅ | `sql_experience_usage` + `reuse_count/reuse_success/reuse_fail` |

### P0-4　统一 AI 评测体系

| 子项 | 状态 | 证据 |
|---|---|---|
| 六类核心评测（意图/召回/SQL 生成/SQL 安全/结果正确/业务完成） | ⚠️ | intent/metrics/tables/sql_contains/grounding/hallucination/result 已有；「业务任务完成」维度缺显式判定（无 `requirement_fulfilled` 维） |
| 核心指标（Intent/Recall@K/Exec/Valid/Exact Match/…） | ⚠️ | `runner.aggregate` 产出 20+ 指标；缺 Recall@K 精确实现、Context Precision、Exact Result Match 的分母口径需补 `expected_result` 覆盖 |
| Release Gate / Regression Gate | ✅ | `gate.py:check_release_gate/compare_baseline` + `evaluate.py --gate/--regression` + `test_gate.py` 126 项 |
| 版本对比报告 | ✅ | `report.py` 支持 baseline 列 + delta |
| 消融实验（A~G 七组对照） | ⚠️ | `run_ablation.py` 现有 5 组（sql_validator/result_validator/dictionary/calendar/entity_filter）；**缺** B(Semantic Layer)、C(RAG 通道)、D(知识可信排序)、F(SQL 经验检索)；安全模块 NEVER_ABLATE 正确 |
| 安全实验隔离 | ✅ | monkeypatch + finally 还原；NEVER_ABLATE 拒绝消融安全模块 |
| 反馈回流 | ✅ | `reflow.py`（collect/promote，防「反馈即答案」三道铁律） |
| 独立数据集/配置/结果记录 | ⚠️ | 消融结果写 `ablation_result.json`，但无「实验元数据表」持久化多组实验历史 |

### P0-5　P0 核心指标看板

| 子项 | 状态 | 证据 |
|---|---|---|
| P0 核心指标看板（业务结果/SQL 质量/知识质量/AI 评测） | ❌ | 前端 11 个页签，无「看板/大盘」页；后端无 `/api/dashboard/*` 聚合接口 |

---

## 三、安全红线核查（必须不破坏）

| 检查项 | 结论 |
|---|---|
| 唯一执行出口 `_exec_sql` | ✅ 未改动；`test_gate.py:test_no_bare_exec_anywhere` 静态守着（0 处裸执行） |
| 唯一闸门 `_pre_execute_check` | ✅ 六道闸门顺序不可交换；执行 SQL == 校验 SQL |
| 自愈 SQL 重过闸门 | ✅ |
| 审核≠结果正确 | ✅ `review_status` 与 `verify_status` 独立 |

---

## 四、结论：真正需要新增的（缺口清单）

1. **标准问题集管理模块**（DB 表 + CRUD + 导入 + 审核发布 + 版本 + 停用归档 + expected_sql 安全验证）——P0-1 的核心缺口。
2. **结果差异定位 + 六类结果分类**（完全一致/数值容差一致/结构一致数值不一致/字段不一致/行数不一致/无法比较）——P0-1-C、P0-2-L3。
3. **L4 确认分类挂到取数审核动作**（8 类）——P0-2-L4。
4. **P0 核心指标看板**（后端聚合 API + 前端页）——P0-5。
5. **消融实验补全**（Semantic Layer / RAG 通道 / 知识可信排序 / SQL 经验检索）——P0-4。
6. **评测指标口径补全**（Recall@K / Context Precision / Exact Result Match 的分母与统计范围显式化）——P0-4。

> 以上 6 项即为本次实施范围。其余（Golden 晋升、状态链、JOIN 专项、Gates、回流）已实现，只补测试与文档证据，不重建。
