# 知数AI 四模块职责梳理与 OSSIE 标准符合性审查（只读）

> 日期：2026-11-13 · 方式：只读审查（代码 + 真实库核对）
> 范围：业务域 / 知识资产 / 知识治理 / 语义模型 四模块职责 + OSSIE 标准对象映射符合性
> 原则：**本阶段只提交审查报告与改造方案，不实施大规模代码及数据库变更。**

---

## 0. 结论摘要

四模块职责**大体清晰、边界合理**，主要问题集中在三处：

| # | 问题 | 结论 |
|---|---|---|
| 1 | 知识资产**未完全具备** OSSIE 标准对象编辑能力 | 缺 Dataset.primary_key、Metric.expression 编辑、Relationship.from/to_columns 显式字段 |
| 2 | 知识治理的「知识录入」实为**资产创建**，与知识资产功能重叠 | 应迁入知识资产 |
| 3 | 语义模型与语义注册表**不重复**（登记 vs 派生视图），但存在旧快照遗留 + 旧服务待废弃 | 清理遗留即可 |

**核心结论：四模块无需大规模重构，只需「补标准字段编辑 + 知识录入迁移 + 业务域加查看跳转」。**

---

## 1. 四模块当前功能清单

### 1.1 业务域（tab-domain，新增）

| 项 | 内容 |
|---|---|
| 功能 | 业务域 CRUD、业务主题 CRUD、发布语义模型、删除 |
| 表 | `data_domains`(business_domain) + `business_subjects` + `topic_assets` |
| 接口 | `/api/domains`、`/api/subjects`、`/api/domains\|subjects/{name}/assets`、`/api/semantic/models/{name}/publish` |
| **缺失** | 查看域内资产清单（跳转知识资产）、Agent 使用情况 |

### 1.2 知识资产（tab-kb）

| 项 | 内容 |
|---|---|
| 功能 | 表结构信息（表/字段注释 + 维度下拉）、指标信息（指标 CRUD）、数据字典、语义注册表（实体/关系/文档） |
| 表 | `table_comments` `column_comments` `metrics` `entities` `relationships` `dict_entries` `kb_docs` |
| 接口 | `/api/kb/*`、`/api/metrics`、`/api/tables` |
| **缺失** | OSSIE 标准字段编辑（primary_key、expression、from/to_columns） |

### 1.3 知识治理（tab-gov）

| 项 | 内容 |
|---|---|
| 功能 | 待复核、知识总览、知识缺口、**知识录入（ai-ingest）**、导出备份 |
| 表 | 治理字段（status/source）+ `knowledge_history` + `semantic_registry` + `metric_constraint` |
| 接口 | `/api/kb/governance/*`、`/api/kb/overview`、`/api/kb/gaps`、`/api/kb/ai-ingest`、`/api/export` |
| **重叠** | 「知识录入」实为资产创建，与知识资产（tab-kb）创建功能重叠 |

### 1.4 语义模型（tab-semantic）

| 项 | 内容 |
|---|---|
| 功能 | 标准配置预览（动态映射 OSSIE YAML）、导入导出 |
| 表 | `semantic_models`（发布快照 2 行）+ `semantic_model_versions` + `ossie_import_log` + `ossie_export_log` |
| 接口 | `/api/semantic/export`、`/api/semantic/import`、`/api/semantic/objects`、`/api/semantic/models/{name}/publish` |

---

## 2. 四个模块的表 / 接口 / 页面对应关系

| 模块 | 数据库表 | 主要接口 | 前端页面 |
|---|---|---|---|
| 业务域 | data_domains、business_subjects、topic_assets | /api/domains、/api/subjects、/api/*/assets、/api/semantic/models/{name}/publish | tab-domain |
| 知识资产 | table_comments、column_comments、metrics、entities、relationships、dict_entries、kb_docs | /api/kb/*、/api/metrics、/api/tables | tab-kb（4 子模块） |
| 知识治理 | 治理字段、knowledge_history、semantic_registry、metric_constraint | /api/kb/governance/*、/api/kb/overview、/api/kb/gaps、/api/kb/ai-ingest、/api/export | tab-gov（5 子模块） |
| 语义模型 | semantic_models、semantic_model_versions、ossie_import_log、ossie_export_log | /api/semantic/* | tab-semantic |

---

## 3. OSSIE 标准对象映射差异表（严格逐项，不凭中文概念相似认定）

| OSSIE 对象 | 标准字段 | 知数AI 映射 | 符合性 | 缺失标准字段 |
|---|---|---|---|---|
| Semantic Model | version/name/datasets/metrics/relationships | business_subjects→name；动态组装 datasets/metrics/relationships | ✅ | 无 |
| Dataset | name/source/primary_key/unique_keys/fields/metrics | entities.name→name、table_name→source、column_comments→fields | ⚠️ 部分 | **primary_key、unique_keys 缺失**（entities 无主键字段） |
| Field | name/expression/dimension/label/datatype/ai_context | column_name→name、列引用→expression、dimension_type→dimension | ⚠️ 部分 | **datatype 未落库**（连库取）；**计算表达式缺失**（只有简单列引用） |
| Metric | name/expression/description/datatype | metrics.name→name、definition→description、提取→expression | ⚠️ 部分 | **expression 无可视化编辑入口**（仅后端提取 + 预览可见）；datatype 缺 |
| Dimension | is_time（Field 子对象） | dimension_type=time→is_time | ⚠️ 部分 | 仅时间维度；geo/status/type/channel 用 label 表达（OSSIE 无专门维度类型字段） |
| Relationship | name/from/to/from_columns/to_columns | from/to_entity→from/to；join_sql 提取→from/to_columns | ⚠️ 部分 | **from_columns/to_columns 无显式字段**（从 join_sql 正则提取，不完美） |
| AI Context | instructions/synonyms/examples | keywords→synonyms、dict_entries→同义词 | ✅ | 无 |

### 3.1 关键符合性结论

- ✅ **可无损映射**：Semantic Model、AI Context
- ⚠️ **部分映射（缺标准字段）**：Dataset（缺 primary_key）、Field（缺 datatype）、Metric（缺 expression 编辑）、Relationship（缺 from/to_columns 显式）
- ➖ **知数AI 内部扩展**（用 nonportable_extensions 承载，符合规范）：grain、cardinality、pre_agg_required、required_predicates、knowledge_level、status

### 3.2 支持性核对

| 检查项 | 结论 |
|---|---|
| 标准字段映射 | 部分（见上表） |
| 标准属性缺失 | primary_key、datatype、from/to_columns |
| 内部扩展字段 | 有（nonportable_extensions，规范允许） |
| 标准格式导出/再导入 | ✅（`/api/semantic/export` + `/api/semantic/import`） |
| 导入导出保持对象关系 | ✅（往返一致性测试通过） |
| 编辑态与发布态分离 | ✅（动态映射=编辑态，semantic_models=发布态） |
| Agent 读取已发布语义资产 | ✅（business_domain_service + get_semantic_objects，读知识表 approved） |

---

## 4. 重复功能和重复数据清单

| # | 重复点 | 现状 | 判断 |
|---|---|---|---|
| 1 | 知识治理「知识录入」vs 知识资产创建 | ai-ingest（一句话/SQL 投喂）在 tab-gov，与 tab-kb 的资产创建重叠 | ⚠️ **功能重叠**（录入是创建，不是治理） |
| 2 | 指标编辑（tab-kb）vs 指标审核（tab-gov） | 编辑与审核分离 | ✅ 合理，不算重复 |
| 3 | semantic_models（发布快照）vs semantic_registry（统一登记） | registry=443 行元数据登记，models=2 行发布快照 | ✅ 不重复 |
| 4 | semantic_model_service（旧）vs business_domain_service（新） | agent.py 已切到 business_domain_service，旧服务未被调用 | ⚠️ **待废弃** |
| 5 | 旧「订单主题」approved 快照（semantic_models id=4） | 融合式改造前遗留，语义已过时 | ⚠️ **待清理** |

---

## 5. 推荐模块职责及导航调整方案

### 5.1 职责划分（明确边界）

| 模块 | 职责 | 不做什么 |
|---|---|---|
| **业务域** | 业务组织入口：域/主题管理、查看域内资产清单/治理状态/模型/Agent 使用，**点击资产跳转知识资产** | 不建资产编辑器 |
| **知识资产** | **统一资产管理中心**：所有资产的创建/编辑入口（含 OSSIE 标准字段编辑） | 不做审核/发布 |
| **知识治理** | **统一治理工作台**：审核、质量、OSSIE 映射完整性校验、版本、发布/下线/回滚、影响分析 | 不建资产编辑器 |
| **语义模型** | 标准配置预览 + 导入导出 + 发布快照（标准化派生视图） | 不建内容编辑器 |

### 5.2 导航调整

| 调整 | 说明 |
|---|---|
| 知识资产 tab-kb 增加「智能录入」子模块 | 从知识治理迁移 ai-ingest（一句话/SQL 投喂是创建，归资产管理中心） |
| 知识资产 tab-kb 补 OSSIE 标准字段编辑 | 指标加 expression 编辑；实体加 primary_key；关系加 from/to_columns |
| 知识治理 tab-gov 移除「知识录入」 | 迁移到知识资产；保留待复核/总览/缺口/导出 |
| 业务域 tab-domain 增加「资产清单 + 跳转」 | 域详情展示资产清单（实体/指标/关系），点击跳转知识资产编辑 |
| 语义模型 tab-semantic 保持不变 | 预览 + 导入导出 + 发布 |

---

## 6. 保留 / 合并 / 迁移 / 废弃清单

| 动作 | 对象 | 说明 |
|---|---|---|
| **保留** | 业务域、知识资产（4 子模块）、知识治理（审核/缺口/导出）、语义模型（预览/导入导出/发布） | 职责清晰，保留 |
| **迁移** | 知识录入（ai-ingest）从 tab-gov → tab-kb「智能录入」 | 录入是创建，归资产 |
| **补充** | 知识资产补 OSSIE 标准字段编辑（primary_key/expression/from_to_columns） | 补缺失标准字段 |
| **废弃** | `semantic_model_service.py`（旧语义注入，已不被 agent 调用） | 避免两套语义读取逻辑 |
| **清理** | 旧「订单主题」approved 快照（semantic_models id=4） | 融合式改造前遗留 |

---

## 7. 数据兼容性、权限及历史资产迁移风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| 补标准字段（primary_key/from_to_columns） | 新增列，历史数据为空 | ALTER ADD COLUMN 幂等，空值=「未登记」，不破坏现有 |
| 知识录入迁移 | 前端导航移动，后端接口不变 | 仅改前端 tab 归属，`/api/kb/ai-ingest` 不变 |
| 废弃 semantic_model_service | 已无调用方（agent.py 已切换） | 删除前 grep 确认无引用 |
| 清理旧快照 | 旧「订单主题」approved 快照删除 | 备份后删除，或标记 deprecated |
| 业务域加「查看跳转」 | 纯前端展示 + 跳转链接 | 不改后端数据 |
| 权限 | 四模块均为 admin（业务域/资产/治理/语义模型） | 保持现状，域级权限后续扩展 |

---

## 8. 待确认事项（实施前）

1. **知识录入迁移**：确认把「知识录入」（一句话/SQL 投喂）从知识治理迁到知识资产？（推荐）
2. **补 OSSIE 标准字段编辑**：确认在知识资产补 primary_key / expression / from_to_columns 可视化编辑？（推荐，这是「知识资产具备 OSSIE 编辑能力」的关键）
3. **废弃 semantic_model_service.py**：确认废弃旧服务？（推荐，避免两套语义读取）
4. **清理旧「订单主题」快照**：确认删除或标记 deprecated？

> 按你的要求，本阶段**未实施**任何代码/数据库变更，仅提交审查报告与改造方案。确认后我再进入实施。
