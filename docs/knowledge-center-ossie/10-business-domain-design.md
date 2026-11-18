# 知数AI 业务域与标准化业务语义模型 · 整体设计（第一步~第四步，待确认）

> 日期：2026-11-12 · 阶段：设计阶段（只读审查 + 边界/映射/数据模型/接口/权限/版本方案）  
> 原则：**未确认不实施**（第五步确认后再改库与后端），不删除现有模块、不重复建设 Semantic Layer。



---

## 0. 结论摘要

本次在既有融合式改造基础上，引入 **业务域（Business Domain）** 作为企业业务组织的**上层分类**，形成「业务域 → 业务主题 → Semantic Model」三层体系。核心判断：

| 判断             | 结论                                                                                        |
| -------------- | ----------------------------------------------------------------------------------------- |
| 业务域承载          | **复用 `data_domains`**（已有 `domain_type` 字段，0 行），取值从 `business_topic` 调整为 `business_domain` |
| 业务主题承载         | **新增 `business_subjects` 表**（业务域下的一层，之前误放在 data_domains）                                  |
| Semantic Model | **动态映射**（`semantic_object_builder` 按主题/域实时组装）+ 发布快照（`semantic_models`）                    |
| 知识资产归属         | 多对多关联业务域（不强制转换 OSSIE，Golden SQL/文档/规则等仅关联）                                                |
| 与之前工作的关系       | 之前 `business_topic` 相关代码需调整为 `business_domain` + 新增主题层（见 §7）                              |

---

## 1. 只读审查结果（当前真实状态）

| 对象                        | 现状                                                                                                          |
| ------------------------- | ----------------------------------------------------------------------------------------------------------- |
| `data_domains`            | 字段 name/display_name/description/owner/enabled/created_at/updated_at/**domain_type**；**0 行**                |
| `topic_assets`            | 字段 topic_name/asset_type/asset_key；**0 行**                                                                  |
| 知识表业务域字段                  | metrics/entities/relationships/kb_docs/sql_experience **均无** domain 字段；golden_cases.business_line 存在但**全空** |
| `semantic_object_builder` | 已实现动态映射（build_datasets/metrics/relationships/semantic_model/get_semantic_objects）                           |
| `semantic_models`         | 发布快照表（之前建的「订单主题」candidate 模型，id=4）                                                                          |

> 结论：业务域体系**需新建**（无现成业务域数据），且需把之前 `data_domains.business_topic` 调整为 `business_domain`。

---

## 2. 三个概念边界（明确，不混为一谈）

### 2.1 Business Domain（业务域）

企业业务组织的**上层分类**，如：订单、司机、财务、营销、救援。

| 属性    | 承载                                 |
| ----- | ---------------------------------- |
| 编码    | `data_domains.name`                |
| 名称    | `display_name`                     |
| 描述    | `description`                      |
| 负责人   | `owner`                            |
| 所属部门  | 新增 `department` 字段                 |
| 状态/权限 | `enabled` + 现有权限体系                 |
| 关联    | 业务主题、Semantic Model、知识资产（多对多）、跨域引用 |

### 2.2 Business Subject（业务主题）

业务域下的**具体分析范围**，如订单域下的「订单经营」「订单履约」「订单质量」。

- **不与业务域混为一谈**；初期不过度拆分（建议一个域 1~3 个主题起步）。
- 承载：新增 `business_subjects` 表（name/domain_name/display_name/description/enabled）。

### 2.3 Semantic Model（标准化语义模型）

面向 Agent 的**结构化业务语义表达**，按固定版本 OSSIE Schema 组织 Dataset/Field/Metric/Dimension/Relationship/AI Context。

- **业务域、业务主题是知数AI 内部组织/治理模型**，不强行改变 OSSIE 原生对象定义；
- Semantic Model = `semantic_object_builder.build_semantic_model(subject)` 动态组装 + 发布快照。

### 2.4 三层关系

```
业务域（Business Domain）          1 ──── N  业务主题（Business Subject）  1 ──── N  Semantic Model
   │  data_domains(business_domain)          business_subjects                    动态映射 + 发布快照
   │
   └──── 直接关联（跨主题）────────────────────────────────────────────────────────► Semantic Model
   │
   └──── 多对多关联知识资产（Golden SQL / 文档 / 规则 / 反馈 …，不强制转 OSSIE）
```

---

## 3. 数据模型方案（只增不改，幂等）

### 3.1 data_domains 扩展（复用为业务域）

```sql
-- 已有 domain_type 字段，取值语义调整 + 新增部门字段
ALTER TABLE data_domains ADD COLUMN department TEXT;   -- 所属部门
-- domain_type 取值约定（调整）：
--   data_domain      —— 原「数据域」（权限模块，sensitive_resources.domain_name 语义）
--   business_domain  —— 新「业务域」（本次上层分类）★ 由之前的 business_topic 改名而来
```

### 3.2 业务主题（新表）

```sql
CREATE TABLE IF NOT EXISTS business_subjects (
    name         TEXT PRIMARY KEY,      -- 主题标识（如 order_operation）
    domain_name  TEXT NOT NULL,         -- 关联业务域（data_domains.name，business_domain）
    display_name TEXT,                  -- 主题中文名（如「订单经营」）
    description  TEXT,
    enabled      INTEGER DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_bs_domain ON business_subjects(domain_name);
```

### 3.3 业务域/主题 → 知识资产关联（多对多，引用指针）

```sql
-- 复用现有 topic_assets 表，语义从「主题→资产」扩展为「域/主题→资产」：
--   asset_type 取值扩展：entity / metric / relationship / golden_sql / doc / rule / feedback
-- 关联粒度：topic_assets.topic_name 可指向 business_domain.name 或 business_subjects.name
-- （用 asset_type 区分关联到哪一层，或用 domain_assets 独立表——建议复用 topic_assets 并加 scope 列）
ALTER TABLE topic_assets ADD COLUMN scope TEXT DEFAULT 'subject';  -- domain / subject
```

> 知识资产可跨多个业务域（多对多），`topic_assets` 主键 (topic_name, asset_type, asset_key) 天然支持多域关联同一资产。

### 3.4 语义模型关联

- 一个业务域对应**多个** Semantic Model（通过多个业务主题，或直接关联）；
- Semantic Model 由「业务主题」动态组装，发布后固化到 `semantic_models`（快照）并记录 `model_name` 与 `subject_name`/`domain_name` 的关联。

---

## 4. 接口方案（复用 + 新增）

| 接口                                          | 方法                  | 说明                                                                                 | 状态    |
| ------------------------------------------- | ------------------- | ---------------------------------------------------------------------------------- | ----- |
| `/api/domains`（业务域列表/CRUD）                  | GET/POST/PUT/DELETE | 复用 `config_store.list/upsert/delete_data_domain`，加 `domain_type='business_domain'` | 复用+扩展 |
| `/api/subjects`（业务主题 CRUD）                  | GET/POST/PUT/DELETE | 新增 `business_subjects` 表 CRUD                                                      | 新增    |
| `/api/domains/{name}/assets`（域资产关联）         | GET/POST/DELETE     | 复用 `topic_assets`（scope=domain）                                                    | 复用+扩展 |
| `/api/subjects/{name}/assets`（主题资产关联）       | GET/POST/DELETE     | 复用 `topic_assets`（scope=subject）                                                   | 复用+扩展 |
| `/api/semantic/export`（动态组装导出）              | GET                 | 已有，加 `subject`/`domain` 参数                                                         | 扩展    |
| `/api/semantic/import`（导入写回）                | POST                | 已有                                                                                 | 复用    |
| `/api/semantic/models/{name}/publish`（发布快照） | POST                | 新增：动态组装 → 固化到 semantic_models                                                      | 新增    |
| `/api/semantic/objects`（统一语义读取接口）           | GET                 | 已有 `get_semantic_objects`，加域/主题参数                                                  | 扩展    |

---

## 5. 权限与版本方案

### 5.1 权限

- 业务域级权限：复用现有 RBAC（默认 admin），业务域 `owner`（负责人）字段记录责任，后续可扩展为「域负责人可见」；
- 知识资产权限：复用现有 `permission` 体系，不新建一套。

### 5.2 审核与发布

- 业务域/业务主题：`enabled` + 治理字段（复用 data_domains/business_subjects 的 enabled）；
- Semantic Model 发布：动态映射（编辑态）→ 发布快照（`semantic_models`，`status='published'`），`semantic_model_versions` 保留历史 + 回滚；
- 知识资产审核：复用现有治理（`status`/`source`/`approved_at`）。

### 5.3 版本追溯

- 语义模型：发布快照 + `semantic_model_versions`（append-only，回滚）；
- 知识资产：`knowledge_history`（既有）；
- 变更影响展示：发布时 diff 新旧快照（第四阶段补）。

---

## 6. 知识资产 → 业务域及 OSSIE 对象映射清单

| 知识资产（真实表）                                | 业务域归属      | OSSIE 对象          | 说明                                       |
| ---------------------------------------- | ---------- | ----------------- | ---------------------------------------- |
| 业务实体 `entities`                          | 关联业务域（多对多） | Dataset           | 实体主表→source；grain 扩展属性                   |
| 字段 `column_comments`                     | 随实体归属域     | Field + Dimension | dimension_type（time→is_time，其他→label）    |
| 指标 `metrics`                             | 关联业务域      | Metric            | expression（提取+复核）；required_predicates 扩展 |
| 表关系 `relationships`                      | 关联业务域      | Relationship      | from/to→dataset；cardinality 扩展           |
| 业务术语 `keywords/dict_entries`             | 关联业务域      | AI Context        | synonyms                                 |
| Golden SQL `golden_cases/sql_experience` | 关联业务域（不转换） | 内部扩展              | 业务域 + 语义对象关联                             |
| 业务文档 `kb_docs`                           | 关联业务域（不转换） | 内部文档              | 业务域关联                                    |
| 业务规则 `prompts.sql_rules`                 | 全局/域级（可选）  | 内部扩展              | 域级规则                                     |
| 用户反馈 `feedbacks`                         | 关联业务域（不转换） | 内部扩展              | 反馈关联                                     |

> 结构化语义资产（实体/字段/指标/关系）**必须具备业务域归属**；非结构化资产（Golden SQL/文档/规则/反馈）**支持关联、不强制转换 OSSIE**。

---

## 7. 与之前工作的调整关系

| 之前的做法                                                           | 调整为                                                                   |
| --------------------------------------------------------------- | --------------------------------------------------------------------- |
| `data_domains.domain_type='business_topic'`（业务主题放 data_domains） | `domain_type='business_domain'`（业务域），业务主题下移到 `business_subjects` 表    |
| `config_store.list/upsert_business_topic`（主题 CRUD）              | 改为 `list/upsert_business_domain`（域 CRUD）+ 新增 `business_subjects` CRUD |
| `topic_assets`（主题→资产）                                           | 扩展 `scope` 列，支持「域→资产」「主题→资产」两层                                        |
| `semantic_object_builder.build_semantic_model(topic)`           | 参数从 topic 扩展为 subject/domain                                          |

> 均为**兼容调整**（已有字段/表复用 + 新增），不删除现有模块。

---

## 8. 待确认事项（第五步实施前）

1. **业务域清单**：是否按「订单/司机/财务/营销/救援」等真实业务域初始化？（需你提供或确认业务域编码/名称）
2. **业务主题粒度**：订单域下是否拆「订单经营/订单履约/订单质量」？（建议初期每域 1~3 主题）
3. **domain_type 取值调整**：确认把之前 `business_topic` 改为 `business_domain`？（推荐）
4. **资产多对多关联**：确认知识资产可跨多业务域（不限制单一归属）？
5. **Agent 业务域识别**：第五阶段是先做「关键词/实体→业务域」的规则映射，还是 LLM 识别？（建议先规则、后 LLM）

---

## 9. 实施顺序（确认后）

1. 第五步：数据表迁移（data_domains 加 department + 新建 business_subjects + topic_assets 加 scope）；
2. 后端：业务域/主题 CRUD + 关联 + 发布快照 + 统一 Semantic API；
3. 前端：业务域管理/详情/主题管理 + 语义模型可视化维护 + 关联 + 域级质量检查 + YAML 预览；
4. Agent：业务域识别 → 主题识别 → Semantic Model 选择 → 语义对象获取 → 关联知识检索 → SQL 生成。
