# 02 · 数据库知识清单审查（企业业务语义知识库建设 · 第一阶段）

> 审查日期：2026-11-10 · 方式：sqlite3 实际查询 `agent_config.db`（非只读建表语句推断）
> 数据库文件：`/Users/zhangtao/Downloads/autosql/zhishu-ai/agent_config.db`（约 7.7 MB）

---

## 1. 数据库文件及用途

| 文件 | 用途 |
|---|---|
| `agent_config.db` | **唯一生产配置库**：业务知识 + Prompt + 权限 + 账号 + 经验 + 审计（38 张表） |
| `demo_shop.db` | SQLite 演示业务库（开发用，非生产） |
| `config.db` | 空文件（0 字节，遗留） |
| `agent_config.db.bak-*` | 6 份历史备份（2026-10-08 ~ 10-29） |

---

## 2. 全部表清单（实测 38 张）

按用途分组（行数为实测）：

### 2.1 知识主表（Semantic Layer P0~P4）

| 表 | 行数 | 层级 | 说明 |
|---|---|---|---|
| `prompts` | 2 | P0 | 系统规则/角色（`sql_rules`、`role`） |
| `metrics` | 57 | P1 | 指标定义（权威口径） |
| `entities` | 52 | P2 | 业务实体 |
| `relationships` | 22 | P2 | 关联/Join 规则 |
| `table_comments` | 46 | P3 | 表业务注释 |
| `column_comments` | 233 | P3 | 字段业务注释 |
| `dict_entries` | 23 | P3 | 数据字典（编码→中文） |
| `kb_docs` | 10 | P4 | 普通业务文档 |
| `metric_constraint` | 17 | P1 派生 | 指标硬约束（派生表，见 §7） |
| `table_suggestions` | 2 | — | AI 草稿表注释（未启用遗留） |
| `table_acl` | 42 | — | 表访问控制白名单 |
| `knowledge_history` | 1263 | — | 知识变更历史（审计） |

### 2.2 经验/案例/反馈

| 表 | 行数 | 说明 |
|---|---|---|
| `sql_experience` | 165 | SQL 经验中心（成功/失败/修正案例） |
| `sql_experience_rules` | **0** | 归纳规则（当前为空） |
| `sql_experience_usage` | 1126 | 经验召回/采用日志 |
| `golden_cases` | 4 | 标准问题/黄金 SQL |
| `golden_case_versions` | 0 | 黄金案例版本 |
| `golden_case_verify_log` | 4 | 黄金案例验证日志 |
| `feedbacks` | 5 | 用户反馈 |

### 2.3 查询记录/上下文

| 表 | 行数 | 说明 |
|---|---|---|
| `query_records` | 325 | 取数记录（含审核状态） |
| `query_result_snapshots` | 146 | 结果快照 |
| `query_context` | 1 | 增量修改锚点 |
| `clarify_sessions` | 24 | 澄清会话 |
| `favorites` | 4 | 收藏 |

### 2.4 权限/账号

| 表 | 行数 | 说明 |
|---|---|---|
| `users` | 2 | 用户 |
| `roles` | 2 | 角色 |
| `user_role_bindings` | 5 | 用户-角色绑定 |
| `permission_policies` | **0** | 字段级权限策略（未启用） |
| `row_permission_rules` | **0** | 行级权限规则（未启用） |
| `sensitive_resources` | 324 | 敏感资源登记 |
| `data_domains` | 0 | 数据域 |
| `permission_audit_logs` | 155 | 权限审计 |
| `permission_approvals` | 0 | 权限审批 |
| `permission_policy_versions` | 0 | 权限策略版本 |
| `ai_policy_drafts` | 0 | AI 权限草稿 |

### 2.5 系统

| 表 | 行数 | 说明 |
|---|---|---|
| `sessions` | 1 | 登录会话 |
| `audit_log` | 496 | 通用审计日志 |
| `index_state` | 1 | RAG 索引状态 |

---

## 3. 知识主表字段/类型/索引（实测）

### 3.1 metrics（P1 指标，主键 `name`）

字段：`name`(TEXT PK)、`keywords`(JSON)、`definition`、`doc`、`tables`(JSON)、`enabled`(INTEGER)、`priority`(INTEGER)、`metric_type`(TEXT)、`sql_template`、`depends_metrics`(JSON)、`conflict_key`、`required_predicates`(JSON)、`required_columns`(JSON)、`forbidden_predicates`(JSON)、`created_at/updated_at/updated_by/version/source_ref`、`stable_key`、`status`(TEXT, 默认 approved)、`owner_id/steward_id/source_system/effective_from/effective_to/content_hash/approved_at/approved_by`、`source`(默认 manual)、`knowledge_level`(默认 1)。

索引：`idx_metrics_stable(stable_key)`、`idx_metrics_status(status)`。

`metric_type` 分布（实测）：count 27 / other 16 / amount 8 / ratio 6。

### 3.2 entities（P2 实体，主键 `name`）

字段：`name`(PK)、`table_name`、`grain`、`comment`、`keywords`(JSON)、`enabled` + 同款治理字段 + `knowledge_level`(默认 2)。

### 3.3 relationships（P2 关系，主键 `id` AUTOINCREMENT）

字段：`id`(PK)、`from_entity`、`to_entity`、`join_sql`、`join_type`(默认 LEFT)、`cardinality`(默认 1:1)、`pre_agg_required`(默认 0)、`rule_note`、`enabled` + 治理字段 + `knowledge_level`(默认 2)。

### 3.4 table_comments（P3 表注释，主键 `table_name`）

字段：`table_name`(PK)、`comment`、`source`(默认 manual) + 治理字段 + `knowledge_level`(默认 3)。

### 3.5 column_comments（P3 字段注释，复合主键 `(table_name, column_name)`）

字段：`table_name`、`column_name`、`comment`、`source`(默认 manual)、`created_at/updated_at/updated_by/version/source_ref`、`stable_key`、`status`(默认 approved)、`owner_id/steward_id/source_system/effective_from/effective_to/content_hash/approved_at/approved_by`、`knowledge_level`(默认 3)，主键 `(table_name, column_name)`。

索引：`idx_column_comments_stable(stable_key)`、`idx_column_comments_status(status)`。

### 3.6 dict_entries（P3 字典，主键 `name`）

字段：`name`(PK)、`dict_type`、`field_hint`、`keywords`(JSON)、`items`(JSON)、`enabled` + 治理字段 + `source` + `knowledge_level`(默认 3)。

### 3.7 kb_docs（P4 文档，主键 `id`）

字段：`id`(PK)、`title`、`content`、`keywords`(JSON)、`priority`(默认 4)、`enabled` + 治理字段 + `knowledge_level`(默认 4)。

---

## 4. 知识状态与审核字段

七张知识主表统一纳入治理（`config_store._migrate_knowledge_governance`，899 行），统一字段：

- `status`：approved / candidate / rejected / draft / reviewing / deprecated / archived
- `source`：manual（人）/ ai（机器起草）
- `approved_at / approved_by`：核准时间/人
- `owner_id / steward_id`：责任人/数据管家
- `source_system / source_ref`：来源系统/引用
- `content_hash`：内容哈希（口径变更检测）
- `stable_key`：稳定业务键（如 `metric:完单量`）
- `version`：版本号
- `knowledge_level`：P1~P4 显式分层

**status 分布实测：**

| 表 | approved | candidate | rejected |
|---|---|---|---|
| metrics | 54 | 3 | 0 |
| entities | 45 | 7 | 0 |
| relationships | 22 | 0 | 0 |
| kb_docs | 9 | 1 | 0 |
| dict_entries | 21 | 2 | 0 |
| table_comments | 44 | 2 | 0 |
| column_comments | 181 | 49 | 3 |

> 注意：严格模式（`GOVERNANCE_STRICT_STATUS=true`，当前默认）下，candidate 不进生产检索；当前仍有约 64 条 candidate 待复核（主要在 column_comments 49 条）。

**source 分布实测：** 各表均存在 `ai` 来源（metrics 4 / entities 10 / kb_docs 1 / dict_entries 2 / table_comments 3 / column_comments 53），AI 生成内容需复核转正。

---

## 5. 知识间关联方式（实测）

| 关联 | 实现 |
|---|---|
| 指标 → 表 | `metrics.tables`（JSON 数组，如 `["ods_tu_order_all"]`） |
| 实体 → 表 | `entities.table_name`（主表） |
| 关系 → 实体 | `relationships.from_entity / to_entity`（用实体名） |
| 字段注释 → 表 | `column_comments.table_name + column_name`（复合） |
| 字典 → 字段 | `dict_entries.field_hint`（如 `app_entity_source`） |
| 指标硬约束 → 指标 | `metric_constraint.metric_id = metrics.stable_key`（派生） |
| 反馈 → 知识 | `feedbacks.related_knowledge_id`（如 `metric:代驾完单量`） |
| 经验 → 源记录 | `sql_experience.source_id / source_type`（反向追溯） |

---

## 6. 重复知识检测

本次未做语义去重（只读审查阶段）；但从结构上发现两处**潜在重复存储**：

1. **指标硬约束双存储**：`metrics.required_predicates/required_columns/forbidden_predicates`（JSON 列，被 `sql_validator._check_p1` 直接消费）与 `metric_constraint` 表（17 行，由 `_migrate_governance.build_metric_constraint` **全量重建的派生表**）。二者非双事实源（metric_constraint 每次从 metrics 重建），但存在一致性窗口，需注意。
2. **表注释双来源**：`table_comments`（进 RAG）与 `table_suggestions`（AI 草稿，遗留未启用，仅 2 行）。

---

## 7. 版本管理能力（实测）

- 已有 `version` 字段 + `knowledge_history`（1263 行，create/update/delete/enable/disable 快照审计）。
- 但 `version` 字段当前**基本恒为 1**（仅 metrics 少数行有 39/77 等值，来源不明，未能确认其递增机制是否真正生效）。
- `golden_cases` 有 `golden_case_versions` 版本表；`sql_experience` 有 `version` + `supersedes`（规则取代）。
- 结论：**有审计历史（append-only），但无成熟的分支/回滚式版本管理。**

---

## 8. 知识与 RAG 索引的同步关系（实测）

- RAG 索引在进程内存中构建：`rag.build_index()` → `semantic_layer.enrich_index()`，**非持久化**。
- 索引状态落库：`index_state` 表（1 行，status=ok，含 tables/metrics/dicts 计数）。
- 知识变更后需**手动重建索引**（`POST /api/index/rebuild`）才生效；无自动增量同步。
- 结果缓存按「知识指纹」失效（`result_cache._knowledge_fp`，48 行），知识变更后 30 秒内缓存自动失效。

---

## 9. 十项能力盘点表

| # | 能力 | 当前实现位置 | 数据存储 | API | 前端页面 | 可复用 | 改造建议 |
|---|---|---|---|---|---|---|---|
| 1 | 指标 | `config_store.get_metrics`(1252 行) / `knowledge.get_metrics` | `metrics` 表 | `/api/metrics` 等 | 知识管理-指标 | ✅ 高度可复用 | 补全 9 条缺 `stable_key` 的指标；核对 `version` 递增机制 |
| 2 | 业务实体 | `config_store.get_entities`(1492 行) | `entities` 表 | `/api/kb/entities` | 知识管理-实体 | ✅ 可复用 | 实体仅登记「主表」，卫星表靠阈值兜底，建议显式登记卫星表 |
| 3 | 表注释 | `config_store.get_table_comments`(2026 行) | `table_comments` | `/api/tables/{name}` | 知识管理-表 | ✅ 可复用 | `table_suggestions` 遗留表可清理 |
| 4 | 字段注释 | `config_store.get_column_comments`(2059 行) | `column_comments` | `/api/kb/column/...` | 知识管理-字段 | ✅ 可复用 | 49 条 candidate 待复核；复合键 `表.字段` 需保留 |
| 5 | 数据字典 | `config_store.get_dict_entries`(1367 行) / `rag._build_dicts` | `dict_entries` | `/api/kb/dicts`、`/api/kb/dict/import` | 知识管理-字典 | ✅ 可复用 | 支持从字典源表一键导入，能力已成熟 |
| 6 | JOIN 规则 | `config_store.get_relationships`(1590 行) | `relationships` | `/api/kb/relationships` | 知识管理-关系 | ✅ 可复用 | `pre_agg_required` 已建模一对多预聚合，成熟 |
| 7 | SQL 案例 | `sql_experience.py`(2105 行) | `sql_experience`(165) + `golden_cases`(4) | `/api/exp/*`、`/api/golden/*` | SQL经验/标准问题 | ✅ 可复用 | 经验库与黄金库**语义重叠**（都存标准 SQL），建议明确分工 |
| 8 | Prompt 规则 | `prompts` 表 + `prompt.py` | `prompts`(2) | `/api/prompts` | Prompt配置 | ⚠️ 部分复用 | `sql_experience_rules`(0 行)与 prompts 规则存在定位重叠 |
| 9 | 知识审核 | `governance.py` + `config_store`(1727-1924) | `status/source` 字段 + `knowledge_history` | `/api/kb/governance/*` | 知识治理 | ✅ 可复用 | 治理框架成熟，仅需清 candidate 存量 |
| 10 | 用户反馈 | `config_store.add_feedback`(2580 行) | `feedbacks`(5) | `/api/feedback`、`/api/suggest` | 反馈处理 | ⚠️ 数据稀疏 | 5 条反馈太少，闭环字段已就位但缺数据积累 |
