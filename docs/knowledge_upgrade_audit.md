# 知数 AI 知识库治理升级 · 阶段 0 源码审计报告

- **审计对象**：`/Users/zhangtao/Downloads/autosql/zhishu-ai` 当前工作区源码
- **审计日期**：2026-10-22
- **审计方式**：只读代码审查 + 实际运行测试基线 + 配置库数据盘点 + 关键缺口核验
- **结论口径**：「已复现」= 当前 `.venv` 环境实际跑出；「静态审查」= 代码路径推导；「数据盘点」= 对 `agent_config.db` 实测计数。

---

## 0. 结论摘要

当前系统已是**较完整的企业级骨架**：V2 分层语义流水线、`sql_validator` 执行前五维校验（含三态 `validate_sql_ex`）、`guard` 表名提取三态（`extract_tables_ex`/`check_tables_ex` fail-closed）、`_exec_sql` 唯一执行出口、`_pre_execute_check` 统一执行前闸门、字段级/行级权限引擎（`permission.py`）、94 条评测集、分层测试 runner。

**任务书 §八「P0 安全」所列的 8 项高风险问题，绝大多数已在既有 stage1/stage2 中修复并落地**（见 §2 复核表）。本次审计重新核验后确认：**无需推倒重写**。

仍存在的真实缺口集中在三块，是本轮知识库升级的重点：

| 编号 | 缺口 | 严重度 | 类型 |
|---|---|---|---|
| GAP-1 | Legacy 兜底路径不跑 `sql_validator` 业务校验（P1/P2/P3） | **P0 残留** | 安全一致性 |
| GAP-2 | 知识记录无版本、无变更历史、无审核状态、无来源/有效期元数据 | **P0（本任务核心）** | 知识治理 |
| GAP-3 | 索引重建无状态追踪：重建失败可能静默沿用旧向量 | P1 | 数据完整性 |
| GAP-4 | Golden Dataset 缺标准 SQL / 期望结果集，`result_accuracy=null` | P1 | 评测闭环 |
| GAP-5 | 8 条 `1:N` 关系 `pre_agg_required=0`，JOIN 粒度风险未显式标注 | P1 | 数据质量 |
| GAP-6 | 反馈（feedbacks）与知识/回归样例之间无结构关联 | P1 | 反馈闭环 |
| GAP-7 | P1 指标硬约束「同族任一满足」未按 `conflict_key` 分组，跨族可能互吞 | P1（待业务确认） | 校验逻辑 |

> **重要事实**：当前工作区**不是 Git 仓库**（无 `.git`）。所有改动缺乏版本控制保护，故本次实施前已做独立备份（见 `docs/knowledge_upgrade_baseline.md` §1.4），实施时所有迁移必须幂等、可回滚。

---

## 1. 当前实现（以源码为准）

### 1.1 模块与真实调用链

| 模块 | 实际职责 | 行数 |
|---|---|---|
| `config_store.py` | 知识存储（SQLite `agent_config.db`）+ 全量 CRUD + 迁移 | 3214 |
| `knowledge.py` | 知识访问入口（接口隔离，读写都转发给 config_store） | 127 |
| `rag.py` | 关键词+向量混合检索、索引构建、表/字典/指标召回 | 725 |
| `semantic_layer.py` | P0~P4 分层、多路检索编排、冲突解决、Prompt 组装 | 565 |
| `query_understanding.py` | LLM 意图/指标/实体/维度/时间/过滤识别 | 124 |
| `agent.py` | 主编排（V2 / Legacy / 自愈 / 唯一执行出口） | 889 |
| `sql_validator.py` | P0/P1/P2/P3/Metric 五维校验 + `validate_sql_ex` 三态 | 460 |
| `result_validator.py` | 执行后结果体检（比率越界/全NULL/全0/行数） | 94 |
| `guard.py` | 清洗、只读护栏、表名提取三态、表权限 fail-closed | 677 |
| `permission.py` | 字段级 ALLOW/MASK/DENY + 行级改写 + 结果脱敏 + 审计 | 854 |
| `server.py` | FastAPI + RBAC + 知识维护接口 + 权限接口 | 2935 |
| `evaluation/` | 评测 runner / gate / report / datasets(94条) | — |

### 1.2 知识存储实际表结构（`config_store.init_store`）

已存在 20+ 张表，知识类核心表及列：

| 表 | 关键列 | 缺口 |
|---|---|---|
| `metrics` | name, keywords, definition, doc, tables, enabled, priority, metric_type, sql_template, depends_metrics, conflict_key, required_predicates, required_columns, forbidden_predicates | **无** version/created_at/updated_at/created_by/status/reviewed_by/valid_from/valid_to/tags/source_ref |
| `entities` | name, table_name, grain, comment, keywords, enabled, source | 同上 |
| `relationships` | id, from_entity, to_entity, join_sql, join_type, cardinality, pre_agg_required, rule_note, enabled | 同上 |
| `kb_docs` | id, title, content, keywords, priority, enabled | 同上 |
| `dict_entries` | name, dict_type, field_hint, keywords, items, enabled | 同上 |
| `table_comments` / `column_comments` | table/column, comment, source(manual/ai) | 无 version/时间戳 |
| `feedbacks` | id, question, sql, reason, detail, tables, metrics, dicts, status, note, created_at, updated_at | 无「关联知识ID/回归样例ID」 |
| `query_records` | id, username, question, sql, ok, ... understanding, review_status, attempts, pipeline, created_at | 无 validation/result_warnings/trace/audit_json |

### 1.3 知识 CRUD 函数（`config_store.py`）

`get_metrics/set_metric_enabled/upsert_metric/delete_metric`、`get_entities/upsert_entity/delete_entity`、`get_relationships/upsert_relationship/delete_relationship`、`get_kb_docs/upsert_kb_doc/delete_kb_doc`、`set_table_comment/set_column_comment`、`add_feedback/update_feedback/delete_feedback` 等。

**关键事实**：`upsert_*` 均为**直接覆盖**（`INSERT ... ON CONFLICT ... DO UPDATE` 或 `DELETE+INSERT`），`delete_*` 均为**物理删除**，**全程不写任何变更历史、不留版本快照**。

---

## 2. 任务书 §八「P0 安全」复核结论

| 任务书列出的风险 | 当前状态 | 证据（源码） |
|---|---|---|
| 执行失败自愈 SQL 未重新校验 | ✅ 已修复 | `agent.py:729-761` 自愈循环每轮调用 `_pre_execute_check` |
| V2 与 Legacy 业务校验不一致 | ⚠️ **部分残留（GAP-1）** | `ask_legacy`(agent.py:274) 只过 `sql_guard`+`check_tables_ex`+`permission.evaluate`，**未调用** `sql_validator` |
| `LEGACY_FALLBACK_MODE=always` 绕过 | ✅ 已修复 | `agent.py:861-863` 生产强制收敛为 whitelist；`test_gate` 有 `test_production_blocks_always_fallback` |
| 表名解析失败/解析器不可用 fail-open | ✅ 已修复 | `guard.py:411-457` `check_tables_ex` 三态 fail-closed；`TABLE_PARSE_STRICT` 默认 1 |
| 校验器内部异常被当通过 | ✅ 已修复 | `sql_validator.py:374-452` `validate_sql_ex` 返回 status=error，逐层 try/except 记录 `checker_error` |
| 默认管理员口令/状态接口/错误信息/行数限制 | ✅ 大部分已修复 | `users.must_change_password` 已加；`/api/status` 权限收紧（见 stage2）；`MAX_RESULT_ROWS` 应用层 LIMIT；`CK_USER` 建议只读但**当前 `.env` 为 `operation` 未验证只读**（遗留建议项） |
| SQL 展示/执行/校验报告一致 | ✅ 已修复 | `agent.py:135-154` `_finalize_sql` + `sql = final_sql`（行级改写后以 final_sql 为准） |

---

## 3. 真实缺陷详述

### GAP-1（P0 残留）Legacy 路径缺少业务校验

**位置**：`agent.py:274-449` `ask_legacy`。
**证据**：`grep sql_validator agent.py` 仅命中 `_pre_execute_check`（line 501，属 ask_v2）。`ask_legacy` 的执行循环（line 371-448）只做 `guard.sql_guard` + `guard.check_tables_ex` + `permission.evaluate`，**P1 指标硬约束（required_predicates/forbidden_predicates）、P2 JOIN 预聚合、P3 列存在性均不生效**。
**风险**：当 V2 因可恢复故障（超时/连接）回退 Legacy 时，Legacy 可能执行一条不满足权威口径的 SQL。
**影响范围**：`agent.ask` 的回退分支；生产 `LEGACY_FALLBACK_MODE=whitelist` 下仅可恢复故障触发，但仍应关闭。

### GAP-2（P0 核心）知识记录无版本/历史/审核/来源/有效期

**位置**：`config_store.py` 全部知识表 + `upsert_*`/`delete_*` 函数。
**证据（数据盘点）**：
- `metrics` 列清单无 `version/created_at/updated_at/created_by/status/reviewed_by/valid_from/valid_to/tags/source_ref`。
- `entities` 有 `source`（29 条 ai / 13 条 manual），但 **29 条 AI 生成实体无复核标记、无审核人、无时间戳**。
- `column_comments` 100 条 ai / 49 条 manual，同样无审核流转。
**风险**：① 修改知识后旧口径无法追溯、无法回滚；② 无法区分「草稿/待审核/生效/停用/归档」；③ 无法确认知识来源与有效期；④ AI 生成的知识与人工确认的知识混在同级，误当权威。

### GAP-3（P1）索引重建无状态追踪

**位置**：`rag.build_index()`（rag.py:324-378）。
**证据**：函数构建完直接返回 dict，**不落库、不记录成功/失败状态、不记录知识计数与时间戳**。若 embedding 模型加载失败/DB 读 schema 失败，调用方拿到异常，但无「上一次可信索引」状态，无法判断「当前服务在用过期向量」。
**风险**：重建失败可能被静默忽略，服务继续用旧索引答数，而运维无感知。

### GAP-4（P1）Golden Dataset 缺标准 SQL 与期望结果

**位置**：`evaluation/datasets/golden/golden_v1.jsonl`（35 条）+ `eval/cases.json`（8 条）。
**证据**：golden 字段集 = `expected_ok/expected_tables/expected_metrics/expected_sql_contains/expected_sql_not_contains/expected_abstain/expected_format/time_range/...`，**无 `expected_sql`（标准全量 SQL）、无 `expected_result`/`result_check`、无 `sql_dialect`、无 `knowledge_ids`、无 `acceptance`**。`runner.aggregate` 中 `result_accuracy` 恒为 `null`。
**风险**：无法区分「SQL 可执行」与「业务结果正确」，不能据此宣称业务准确率。

### GAP-5（P1）8 条 1:N 关系未标注预聚合

**证据（数据盘点）**：`relationships` 中 `cardinality='1:N' AND pre_agg_required=0` 共 **8 条**（id 5/6/7/14/15/17/18/20，含 mall_order→goods、guaranty_order→scheme 等典型明细放大场景）。
**风险**：这些关系在 `sql_validator._check_p2` 只对 `pre_agg_required=1` 报 error；1:N 但未勾选预聚合的，JOIN 后行数膨胀**不会**被拦，属于「SQL 能跑但业务数错」的静默风险。

### GAP-6（P1）反馈未进入知识/评测闭环

**位置**：`config_store.add_feedback/update_feedback`（config_store.py:1728-1793）。
**证据**：`feedbacks` 表有 `status(pending/resolved/ignored)` 与 `note`，但**无「关联知识 ID」「转化出的回归样例 ID」「根因分类联动」**。管理员把反馈标 resolved 后，没有任何机制把它变成一条知识或一条回归样例。
**风险**：同类错误反复出现，无法沉淀，无法统计「修正后回归表现」。

### GAP-7（P1，待业务确认）指标硬约束跨族互吞

**位置**：`sql_validator.py:142` `if declared and all(missing for _n, missing in declared)`。
**证据**：当命中多个指标、其中 A 满足、B 不满足时，`all(missing)` 为 False → **不报 error**。注释说明这是「同族任一满足」策略（完单量 status=6 vs 代驾完单量 status=40 属正常冲突）。但实现**未按 `conflict_key`/族分组**，把「同族」与「跨族」一视同仁。跨族场景（如完单量 + 发单量）可能漏拦。
**风险**：需业务确认族划分后才能安全修复，否则会误拦同族正常冲突。当前标注为「已发现但需业务确认」。

---

## 4. 影响范围与建议修改文件

| 缺口 | 建议修改文件 | 破坏性 |
|---|---|---|
| GAP-1 | `agent.py`（Legacy 接入 `sql_validator`）+ `test_gate.py` | 低（只增校验，不删功能） |
| GAP-2 | `config_store.py`（新增 `knowledge_history` 表 + 元数据列迁移 + CRUD 写历史） | 低（ALTER ADD COLUMN + 新表，幂等） |
| GAP-3 | `config_store.py` + `rag.py`（索引状态表 + build_index 落状态） | 低 |
| GAP-4 | `evaluation/datasets/golden/*.jsonl` + `evaluation/checks.py`/`runner.py`（标准 SQL + result 维度） | 低（只增字段） |
| GAP-5 | 知识库数据（为 1:N 关系补 pre_agg 或显式标注 unknown）+ `sql_validator._check_p2` 对 1:N 给 warning | 中（涉及口径判定，需业务确认） |
| GAP-6 | `config_store.py`（feedbacks 增 `related_knowledge_id`/`regression_sample_id`）+ `server.py` | 低 |
| GAP-7 | `sql_validator.py` `_check_p1` 按 conflict_key 分组 | 中（需业务确认族划分） |

---

## 5. 实施顺序（本次执行计划）

1. **阶段 0**：审计 + 基线（本文档 + baseline）。
2. **阶段 1（安全）**：关闭 GAP-1（Legacy 业务校验一致性），补齐回归测试。
3. **阶段 2（知识治理）**：落地 GAP-2（版本/历史/元数据）+ GAP-3（索引状态）。
4. **阶段 3（评测闭环）**：GAP-4（Golden 标准 SQL + result 维度）+ GAP-6（反馈关联）。
5. **阶段 4（验证交付）**：全量回归 + 迁移验证 + 回滚文档 + 最终报告。

> GAP-5、GAP-7 涉及业务口径判定，本次**只标注、不改动数据/逻辑**，避免未确认就改口径造成误拦。留给业务确认后单独处理。

---

## 6. 关键结论

- 系统骨架健康，测试全绿（见 baseline）。**不推倒重写，不换技术栈，不升级依赖，不换向量模型。**
- 本轮真正的增量价值在于：**知识可追溯（版本/历史/来源/审核）**、**Legacy 安全一致性收口**、**索引与评测的可观测闭环**。
- 所有改动遵循：幂等迁移、只增列不删列、新表不重建、可回滚、测试先行。
