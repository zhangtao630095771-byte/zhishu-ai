# 知数AI OSSIE 融合式改造 · 第一阶段审查报告（只读）

> 审查日期：2026-11-12 · 方式：只读（sqlite3 PRAGMA 查真实表结构 + 字段实际取值核对）
> 原则：**映射关系必须依据现有真实数据结构确定，不凭空创建字段、不假设功能已实现。**

---

## 0. 结论摘要（金字塔原理 · 结论先行）

### 0.1 方向确认

本补充要求明确了**融合式改造**：不独立建设 OSSIE 管理模块、不重复存储语义知识，OSSIE 仅作为**现有知识资产的标准化映射规范**。这与之前 P0~P3 的「独立语义模型 + 快照存储」做法**方向不同**，需要按新方向重做映射审查与架构调整。

### 0.2 五个必须正视的真实事实（均实测核对，非假设）

| # | 事实 | 影响 |
|---|---|---|
| 1 | **「业务主题」概念完全缺失**：`data_domains` 表 0 行、`golden_cases.business_line` 全空、`entities` 无主题字段 | Semantic Model 的容器（name + datasets 集合）没有现成存储，需补齐或复用空表 |
| 2 | **「维度」未结构化沉淀**：无 dimension 表，仅 `golden_cases.expected_dimensions`（评测用）零散记录 + `entities.grain` | Dimension 只有「时间维度」可从字段类型推断，地区/客户等维度缺失 |
| 3 | **指标 SQL 非结构化**：`metrics.sql_template` **57 条全空**，SQL 散在 `definition` 文本（36/57 含 SQL 关键字，21/57 纯自然语言口径） | Metric 的 `expression.sql` 需从 definition 提取，且 21 条无法提取 |
| 4 | **Legacy 流水线已彻底退役**：`ask_legacy` 函数已删除，`agent.py:1244` 注释「双流水线已收敛，Legacy 已退役」 | 补充要求 §8「两条流水线继续保留」的前提已不存在 |
| 5 | **之前建的 `semantic_models` 表是独立快照** | 与「不重复存储同一份语义知识」原则冲突，需调整 |

### 0.3 核心结论

- **可映射资产**：数据表（entities）、表关系（relationships）、业务术语（keywords/dict）、字段注释（column_comments）。
- **部分映射（需补字段或提取）**：指标（Metric，SQL 需从 definition 提取）、字段（Field，datatype 需连库取）。
- **缺失（需新建概念）**：业务主题（Semantic Model 容器）、维度（Dimension）。
- **不直接映射（按新要求）**：SQL 案例、业务文档（走内部扩展/文档资产）。

---

## 1. 现有知识资产真实清单（实测）

### 1.1 知识主表（P0~P4）

| 表 | 层级 | 行数 | 关键字段（实测） | 是否已纳入治理 |
|---|---|---|---|---|
| `prompts` | P0 | 2 | key, content | 否（无 status 列） |
| `metrics` | P1 | 57 | name, keywords, definition, doc, tables, metric_type, sql_template(**全空**), required_predicates, required_columns, forbidden_predicates, conflict_key + 治理字段 | ✅ |
| `entities` | P2 | 52 | name, table_name, grain(**全有值**), comment, keywords + 治理字段 | ✅ |
| `relationships` | P2 | 22 | from_entity, to_entity, join_sql, join_type, cardinality, pre_agg_required, rule_note + 治理字段 | ✅ |
| `table_comments` | P3 | 46 | table_name, comment, source + 治理字段 | ✅ |
| `column_comments` | P3 | 233 | table_name, column_name, comment, source + 治理字段 | ✅ |
| `dict_entries` | P3 | 23 | name, dict_type, field_hint, keywords, items + 治理字段 | ✅ |
| `kb_docs` | P4 | 10 | id, title, content, keywords, priority + 治理字段 | ✅ |

> 治理字段（7 张知识表统一）：`status/source/stable_key/content_hash/knowledge_level/version/owner_id/steward_id/source_system/effective_from/effective_to/approved_at/approved_by/created_at/updated_at/updated_by`。

### 1.2 非结构化/经验/评测资产（按新要求不映射为 OSSIE 对象）

| 表 | 行数 | 说明 |
|---|---|---|
| `sql_experience` | 165 | SQL 经验（trust_level 含 golden、golden_published、corrected_sql、root_cause） |
| `golden_cases` | 4 | 标准问题（expected_sql、expected_result、business_line **全空**、review_status、verify_status） |
| `feedbacks` | 5 | 用户反馈（related_knowledge_id、root_cause） |
| `knowledge_history` | 1263 | 知识变更审计 |
| `metric_constraint` | 16 | 指标硬约束派生表 |
| `semantic_registry` | 443 | 统一注册表（派生投影） |

### 1.3 之前改造新增（需重新评估）

| 表/文件 | 之前定位 | 与新要求的关系 |
|---|---|---|
| `semantic_models` | 独立语义模型快照（model_json 存 dataset/metric/relationship 副本） | ⚠️ 与「不重复存储」冲突 |
| `semantic_model_versions` | 模型版本快照 | ⚠️ 同上 |
| `ossie_adapter.py` | 内部模型 dict ⇄ OSSIE dict 转换 | 可复用转换逻辑，但「内部模型」需改为从知识表动态组装 |
| `semantic_model_service.py` | 读 semantic_models 表匹配/注入 | 需改为从知识表动态组装语义对象 |

---

## 2. OSSIE 完整映射表（基于真实数据结构）

| 当前知识资产 | 真实存储（表.字段） | OSSIE 对象 | 映射状态 | 缺失属性 | 内部扩展（nonportable_extensions） |
|---|---|---|---|---|---|
| 业务主题 | **缺失**（data_domains 0 行；golden_cases.business_line 全空；entities 无主题字段） | Semantic Model | ❌ 缺失 | name（业务主题名）、datasets 集合的归属关系 | 无 |
| 数据表 / 逻辑数据集 | `entities.name` + `table_name` + `grain` + `comment` | Dataset | ✅ 可映射 | `primary_key`、`unique_keys`（entities 无主键字段） | `grain`、`knowledge_level`、`status`、`source`、`stable_key`、`version` |
| 字段结构 | `column_comments.table_name+column_name+comment`；真实类型在 ClickHouse | Field | ⚠️ 部分映射 | `datatype`（配置库无类型，需连库取）、计算表达式（无） | `source`、`status`、`knowledge_level` |
| 指标定义 | `metrics.definition`（混合口径）+ `metric_type`（sql_template 全空） | Metric | ⚠️ 部分映射 | `expression.sql`（36/57 可从 definition 提取，21/57 纯文本无法提取） | `required_predicates/columns`、`forbidden_predicates`、`conflict_key`、`metric_type`、`depends_metrics` |
| 维度 | **未结构化**（golden_cases.expected_dimensions 零散；entities.grain；无 dimension 表） | Field.dimension（is_time） | ❌ 缺失 | 地区/客户等维度；仅「时间维度」可从字段类型推断 | 无 |
| 表关系 | `relationships.from/to_entity` + `join_sql` + `cardinality` + `pre_agg_required` | Relationship | ✅ 可映射 | `from_columns`/`to_columns`（需从 join_sql 提取） | `cardinality`、`pre_agg_required`、`join_sql`、`rule_note` |
| 业务术语 | `metrics/entities/dicts/docs.keywords` + `dict_entries.items` | AI Context | ✅ 可映射 | 无 | 数据字典（编码→中文）作为同义词扩展 |
| SQL 案例 | `sql_experience` + `golden_cases` | 内部扩展知识 | ➖ 不直接映射 | 不适用 | Golden SQL 关联（`golden_cases.id`）、信任等级 |
| 业务文档 | `kb_docs` | 内部文档资产 | ➖ 不直接映射 | 不适用 | 文档关联（`kb_docs.id`） |

### 2.1 映射状态图例

- ✅ 可映射：现有字段能直接对应 OSSIE 字段
- ⚠️ 部分映射：能对应，但有关键字段缺失或需转换
- ❌ 缺失：知数AI 没有对应概念的存储
- ➖ 不直接映射：按新要求，保留为内部资产/扩展

---

## 3. 差异分析（现状 vs OSSIE 标准）

| # | 差异 | 现状 | OSSIE 要求 | 改造方向 |
|---|---|---|---|---|
| 1 | 业务主题（Semantic Model 容器） | 无概念 | 需要 name + datasets 集合 | 复用 `data_domains` 空表补业务主题，或新增 `business_topics` 表 |
| 2 | 维度（Dimension） | 未结构化 | Field.dimension.is_time | 时间维度从字段类型推断；地区/客户维度需新增 `dimensions` 表 |
| 3 | 指标 expression | definition 文本（sql_template 空） | 结构化 expression.sql | 从 definition 提取 SQL（36/57），其余作为 AI Context/description |
| 4 | Dataset primary_key | 无 | 主键字段列表 | 新增 `entities.primary_key`（JSON），或从 ClickHouse 主键/唯一键推导 |
| 5 | Field datatype | 配置库无类型 | DataType 枚举 | 连 ClickHouse 取真实类型，映射到 OSSIE DataType |
| 6 | Relationship from/to_columns | 只有 join_sql | 字段名列表 | 从 join_sql 提取（已有 `_extract_join_columns`），或新增显式字段 |
| 7 | 语义模型存储 | 之前建了独立快照表 | 不重复存储 | 废弃快照，改为从知识表动态映射 |

---

## 4. 风险报告

| # | 风险 | 等级 | 说明 | 缓解 |
|---|---|---|---|---|
| 1 | `semantic_models` 快照表与「不重复存储」冲突 | 高 | 之前 P1~P3 建的独立快照，与融合式方向矛盾 | 废弃该表，OSSIE 对象从知识表动态映射（映射结果不落库，或只做派生缓存） |
| 2 | 指标 SQL 提取歧义 | 中 | 36 条 definition 含 SQL，但格式不统一（含"即/★/换行"等杂质），提取可能出错 | 提取 + 人工复核；21 条纯文本口径不强转 SQL |
| 3 | 业务主题、维度需新增概念 | 中 | 引入新表/新字段，属于数据模型变更 | 第二阶段先设计，不急着改前端 |
| 4 | 两条流水线前提不符 | 中 | Legacy 已退役，无「两条流水线」可保留 | 需用户确认：恢复 Legacy，还是按单流水线处理 |
| 5 | Field datatype 依赖连库 | 低 | 配置库无类型，映射时需连 ClickHouse | 映射层缓存类型，库不可达时类型留空（Opaque） |
| 6 | 关系端点「实体名 vs 表名」混用 | 中 | 22 条关系里 3 条端点缺失（前次审查已发现） | 见 `04-relationship-endpoint-audit.md` |

---

## 5. 与之前工作的关系与调整建议

### 5.1 可复用（保留）

| 资产 | 复用方式 |
|---|---|
| `ossie_adapter.py` 的 Schema 校验器、YAML/JSON 解析、扩展属性打包 | ✅ 直接复用（校验/序列化与"从哪里读数据"无关） |
| `ossie_adapter.py` 的对象转换（entity→dataset、metric→metric、relationship→relationship） | ✅ 复用，但「输入」从"semantic_models 快照"改为"从知识表实时读取" |
| `ossie_schema/` 内置 schema | ✅ 保留（版本已固定） |
| `semantic_model_service.py` 的 Prompt 注入渲染 | ✅ 复用渲染逻辑 |
| 前端「语义模型」tab | 部分复用（改造为「标准配置预览」） |

### 5.2 需调整（废弃或重构）

| 资产 | 调整 |
|---|---|
| `semantic_models` / `semantic_model_versions` 表 | **废弃独立快照**：改为「业务主题 → 资产引用」的轻量归属表，或不落库、纯动态映射 |
| `ossie_import_log` / `ossie_export_log` | 保留（日志与是否重复存储无关） |
| `apply_model_to_kb` | 反向（OSSIE 导入 → 写回知识表）保留；正向（知识表 → OSSIE）改为动态映射 |
| `semantic_model_service.match_models` | 改为从知识表动态组装语义对象 |

### 5.3 推荐目标架构（融合式）

```
现有 7 张知识表（唯一事实源，含治理字段）
        │  动态映射（不落库，或仅派生缓存）
        ▼
统一语义对象组装层（semantic_object_builder）
        │
        ├─► OSSIE Adapter（导出 YAML/JSON，经 Schema 校验）
        │        ▲
        │        └── OSSIE 导入（解析校验 → 写回知识表，走审核）
        │
        └─► Agent 统一语义读取接口（Dataset/Field/Metric/Dimension/Relationship/AI Context）
                 └── Multi-RAG 补充文档/案例/非结构化知识
```

**铁律：知识表是唯一事实源，OSSIE 对象是派生视图，绝不重复存储。**

---

## 6. 待确认事项（阻塞第二阶段）

1. **业务主题容器**：复用 `data_domains`（空表）改造成「业务主题」，还是新建 `business_topics` 表？主题如何与实体/指标关联（多对多还是按主表归属）？
2. **维度沉淀**：是否本次就新增 `dimensions` 表（时间/地区/客户等），还是先只映射时间维度、其余维度后续补？
3. **`semantic_models` 快照表处置**：是否同意废弃独立快照，改为「动态映射 + 业务主题引用」？（推荐）
4. **两条流水线**：Legacy 已退役，确认「单流水线 + 统一语义读取接口」即可，还是需要恢复 Legacy？
5. **指标 expression**：是否接受「36/57 从 definition 提取 SQL + 人工复核，21/57 纯文本口径保留为 description/AI Context」？

---

## 7. 附录：关键实测数据

- `metrics.sql_template`：57 条全空；`definition` 36/57 含 SQL 关键字。
- `entities.grain`：52/52 全有值。
- `data_domains`：0 行；`golden_cases.business_line`：4 条全空。
- `entities` 无 `primary_key` / `related_tables` / `business_line` 字段。
- `relationships`：22 条，3 条端点缺失（详见 `04-relationship-endpoint-audit.md`）。
- `ask_legacy` 函数已删除，`agent.py:1244` 注释「Legacy 已退役」。
