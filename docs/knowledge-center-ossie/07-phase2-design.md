# 知数AI OSSIE 融合式改造 · 第二阶段设计报告（标准映射层 + 内部数据模型）

> 日期：2026-11-12 · 阶段：第二阶段（设计 + 后端映射层，暂不大规模改前端）
> 前置：`06-fusion-review.md`（第一阶段审查）+ 用户 5 项决策确认

---

## 0. 决策落地摘要

| # | 决策 | 落地 |
|---|---|---|
| 1 | 业务主题复用 data_domains | 核实：表空、但有前端 CRUD 接口、`sensitive_resources.domain_name` 存 "pii" 分类标签 → 采用「加 `domain_type` 区分」兼容方案 |
| 2 | 维度采用 Field 维度属性模型 | 不新增表；`column_comments` 加 `dimension_type`，启发式识别 time/geo/status/type/channel |
| 3 | 动态映射 + 发布快照 | 编辑态动态映射（不落库），发布态版本固化（semantic_models 改造为发布快照） |
| 4 | 单流水线 + 统一语义读取接口 | 核实：Legacy 彻底清理（config/server/agent 均无 legacy/fallback 残留）→ 单流水线 + `semantic_object_builder` 统一读取 |
| 5 | 指标提取 + 人工复核 | 已实现并跑出：extracted 16 / ambiguous 17 / missing 24 |

---

## 1. 最终映射关系（细化，基于真实字段）

| 当前知识资产（表.字段） | OSSIE 对象 | 映射规则 | 内部扩展（nonportable_extensions） |
|---|---|---|---|
| 业务主题（data_domains.domain_type='business_topic'） | Semantic Model | name=主题标识；datasets=关联实体集 | 无 |
| 数据表/逻辑数据集（entities.name/table_name/grain/comment） | Dataset | name→name；source→table_name；description→comment；primary_key 缺（见 §2） | grain、knowledge_level、status、source |
| 字段（column_comments + ClickHouse 类型） | Field | name→column_name；expression.sql=列名；description→comment；datatype 连库取 | source、status、knowledge_level |
| 维度（column_comments.dimension_type 新增） | Field.dimension（is_time）/ label | time→is_time=true；geo/status/type/channel→label | 无（维度类型本身可作扩展） |
| 指标（metrics.definition + expression 新增） | Metric | name→name；expression.sql=提取/复核的 SQL；description=definition 原文 | required_predicates/columns、forbidden_predicates、conflict_key、metric_type |
| 表关系（relationships.from/to_entity/join_sql/cardinality） | Relationship | from/to→from/to；from_columns/to_columns 从 join_sql 提取 | cardinality、pre_agg_required、join_sql、rule_note |
| 业务术语（keywords + dict_entries） | AI Context | keywords→synonyms；dict_entries→同义词扩展 | 无 |
| SQL 案例（sql_experience/golden_cases） | 内部扩展知识（不直接映射） | 通过 Golden SQL 关联 | Golden SQL id、信任等级 |
| 业务文档（kb_docs） | 内部文档资产（不直接映射） | 文档关联 | kb_docs.id |

---

## 2. 内部数据模型及表结构调整方案（只增不改，幂等）

### 2.1 data_domains 复用为业务主题（决策 1 兼容方案）

```sql
-- 现有 data_domains 增加一列，区分「数据域」与「业务主题」
ALTER TABLE data_domains ADD COLUMN domain_type TEXT DEFAULT 'data_domain';
-- 取值：data_domain（原数据域，权限模块用）/ business_topic（新业务主题，语义模型用）
```

- **兼容**：原「数据域」功能（`/api/permission/domains`、`sensitive_resources.domain_name`）完全不受影响，默认 domain_type='data_domain'。
- **业务主题**：domain_type='business_topic' 的记录，`display_name`=主题中文名，`description`=主题描述。

### 2.2 业务主题 → 资产关联（新增引用表，非副本）

```sql
CREATE TABLE IF NOT EXISTS topic_assets (
    topic_name  TEXT NOT NULL,          -- data_domains.name（business_topic）
    asset_type  TEXT NOT NULL,          -- entity / metric
    asset_key   TEXT NOT NULL,          -- entities.name / metrics.name
    created_at  TEXT,
    PRIMARY KEY (topic_name, asset_type, asset_key)
);
```

- 这是「引用指针」，不存数据副本，符合「不重复存储」。

### 2.3 column_comments 增加维度属性（决策 2）

```sql
ALTER TABLE column_comments ADD COLUMN dimension_type TEXT;  -- 可空
-- 取值：time/geo/status/type/channel/''（空=自动识别）
```

- 空值 = 用 `semantic_object_builder.detect_dimension` 自动识别；
- 人工可显式指定/覆盖（前端可视化编辑，第四阶段）。

### 2.4 metrics 增加标准表达式 + 审核状态（决策 5）

```sql
ALTER TABLE metrics ADD COLUMN expression TEXT;          -- 人工复核后的标准 SQL（可空）
ALTER TABLE metrics ADD COLUMN expression_status TEXT;   -- extracted/ambiguous/missing/approved
```

- `definition` 保持原始口径**不改**（事实源）；
- `expression` 是提取 + 人工复核后的标准 SQL，与 definition 并存；
- `expression_status='approved'` 的指标，其 expression 才作为 Agent 权威计算口径；
- 未 approved 的指标，Agent 只用 description/自然语言口径，不用未审核的 SQL。

---

## 3. 动态映射、发布快照和版本管理方案（决策 3）

### 3.1 三层机制

```
编辑态（实时）        预览态（按需）          发布态（固化）
─────────────        ─────────────        ─────────────
semantic_object_builder 动态组装         生成不可变版本快照
  读 7 张知识表          │                    │
  （唯一事实源）          ▼                    ▼
                     OSSIE YAML/JSON     semantic_models.model_json
                     校验 + 预览          status='published'
                                             │
                                             ▼
                                      semantic_model_versions
                                      （历史版本 + 回滚）
```

### 3.2 各态职责

| 态 | 数据来源 | 落库 | 用途 |
|---|---|---|---|
| 编辑态 | `semantic_object_builder` 实时读知识表 | 否 | 用户在现有知识中心编辑，OSSIE 对象实时反映 |
| 预览态 | 同上 + `ossie_adapter.validate_document` | 否 | 校验 + YAML/JSON 预览（前端「标准配置预览区」） |
| 发布态 | 预览通过后序列化 | 是（`semantic_models`） | Agent 读取审核通过的已发布版本；更新时生成新版本 |

### 3.3 semantic_models 表语义调整

之前 P1~P3 把 `semantic_models` 当「编辑态独立存储」，现调整为「**发布快照**」：
- 编辑/预览阶段：**不再写** semantic_models（动态映射即可）；
- 发布阶段：把动态映射结果固化到 `model_json`，`status='published'`；
- `semantic_model_versions` 保留版本追溯与回滚能力。

> 现有「订单主题」candidate 模型按此语义迁移：要么删除（改为动态映射），要么作为「发布候选快照」保留。建议第四阶段统一处理。

---

## 4. OSSIE Schema 固定版本及兼容策略

- **固定版本**：commit `e6ad94ee87a53fd8f8b6dbf0de33fab59e406c0c`（0.3.0.dev0），schema 内置 `ossie_schema/`，不追 main。
- **兼容策略**：
  1. 导入时识别 `version` 字段，非支持版本 → 明确报错（不静默降级）；
  2. 未来升级：重新下载新 commit 的 schema 覆盖 `ossie_schema/`，更新 `ossie_adapter` 的 `OSSIE_VERSION`/`SCHEMA_COMMIT` 常量；
  3. 升级转换：在 `ossie_adapter.parse_document` 加版本转换层（旧版本字段 → 新版本字段），跑往返一致性测试回归。
- **扩展属性兼容**：`nonportable_extensions`（vendor_name='zhishu-ai'）承载内部治理属性，导入导出不丢。

---

## 5. 统一语义读取接口设计（决策 4 + 第五步）

### 5.1 核实结论

Legacy 流水线**彻底清理**（实测）：`config.py` 无 LEGACY/FALLBACK、`server.py` 无 legacy 路由/参数、`agent.py` 无 degraded/回退逻辑、全仓无 `ask_legacy`/`LEGACY_FALLBACK` 引用。→ **单流水线**，无需保留双流水线兼容代码。

### 5.2 统一接口

`semantic_object_builder` 是唯一语义读取入口，Agent 不再分散读取：

```python
# 统一语义对象读取接口（只读知识表，实时组装）
def get_semantic_objects(topic=None, status="approved"):
    return {
        "datasets":      [...],   # entities → Dataset
        "fields":        {...},   # column_comments → Field（含维度）
        "metrics":       [...],   # metrics → Metric（含 expression + 审核状态）
        "dimensions":    [...],   # 字段维度识别结果
        "relationships": [...],   # relationships → Relationship
        "ai_context":    {...},   # keywords / dict_entries → 同义词
    }
```

- Agent（ask_v2）在 `resolve` 后调用此接口，优先取 `status='approved'` 的对象；
- 之前 P2 的 `semantic_model_service`（读 semantic_models 快照）**替换**为走 `semantic_object_builder`（动态映射 + 发布快照）；
- Multi-RAG 继续补充业务文档、SQL 案例等非结构化知识，不替代结构化语义。

### 5.3 接入原则

1. 优先审核通过的 Dataset/Field/Metric/Dimension/Relationship/AI Context；
2. 指标计算用 `expression_status='approved'` 的 expression；
3. Join 参考审核通过的 Relationship + 基数；
4. SQL 仍过六维闸门，不绕过校验。

---

## 6. 57 个指标的 expression 提取与审核结果（决策 5，已产出）

| 分类 | 数量 | 处理 | 审核状态 |
|---|---|---|---|
| extracted（可提取） | **16** | expression 已提取，进入人工复核 | `candidate` |
| ambiguous（含片段需补） | **17** | 含 SQL 片段但无法干净提取，标记待补充 | `supplement` |
| missing（纯文本） | **24** | 保留 description，不生成 expression | `text_only` |

**16 个可提取指标**（示例）：GMV、代驾司机保证金缴纳金额/数/司机数/转出金额/转出人数、发单量、订单量、完单量、完单金额、取消率、完单率、客单价、接单量、发单率、询价后发单率（后两者为完整 CTE SQL）。

**17 个待补充**（示例）：司机登录天数、司机线下单次数、客户好评/差评单数、小板完单量、特殊地区/时段完单单量、近30天完单单量、接单率、长途取消率过高等。

**24 个纯文本**（示例）：代驾/大板的信息费、完单率、完单量、完单金额、订单量、订单金额、线上订单、调度单、总积分、发单客户数、注册司机/客户数等。

> 提取逻辑见 `semantic_object_builder.extract_metric_expression`；提取结果**不视为已审核口径**，原始 definition 与提取来源始终保留。

---

## 7. 数据迁移方案、兼容风险和回滚方案

### 7.1 迁移清单（幂等，只增不改）

| 迁移 | 语句 | 风险 |
|---|---|---|
| data_domains 加 domain_type | `ALTER TABLE data_domains ADD COLUMN domain_type TEXT DEFAULT 'data_domain'` | 低（新增列有默认值） |
| 新增 topic_assets | `CREATE TABLE IF NOT EXISTS ...` | 无 |
| column_comments 加 dimension_type | `ALTER TABLE column_comments ADD COLUMN dimension_type TEXT` | 低（可空） |
| metrics 加 expression / expression_status | `ALTER TABLE metrics ADD COLUMN ...`（2 列） | 低（可空） |

### 7.2 兼容风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| data_domains 语义冲突 | 数据域 vs 业务主题 | domain_type 字段隔离，默认数据域，零影响 |
| 指标 expression 提取歧义 | 16 个提取需复核，17 个待补充 | 审核状态隔离，未 approved 不作权威口径 |
| 发布快照与动态映射不同步 | 知识修改后旧快照过期 | 发布快照不可变；知识修改后需「重新发布」生成新版本 |
| semantic_models 旧语义 | 之前当编辑态独立存储 | 迁移为发布快照语义，第四阶段处理存量 |

### 7.3 回滚方案

| 项 | 回滚 |
|---|---|
| 表结构 | `ALTER TABLE ... DROP COLUMN`（或恢复备份 `agent_config.db`） |
| 新表 topic_assets | `DROP TABLE topic_assets` |
| 代码 | `git checkout` 相关文件；`semantic_object_builder.py` 删除即无副作用 |
| 指标 expression | 是动态提取 + 人工复核，未写死；回滚即删 expression/expression_status 列 |

---

## 8. 本阶段已产出的代码与验证

| 文件 | 说明 | 验证 |
|---|---|---|
| `semantic_object_builder.py`（新增） | 统一语义对象组装层：`extract_metric_expression`（指标提取）、`detect_dimension`（维度识别）、`build_metrics_with_expression` 等 | 57 指标提取分类已跑出；12 个典型字段维度识别全对 |

> 本阶段**未改前端**、**未改现有知识表结构**（迁移方案见 §7，待第三/四阶段执行）。`semantic_object_builder.py` 只读知识表，零副作用。

---

## 9. 下一阶段（第三阶段）预告

1. 执行 §7 表结构迁移（4 项 ALTER + 1 张新表）；
2. 后端标准映射：`semantic_object_builder` 完整实现（组装完整 Semantic Model + 主题聚合）；
3. Schema 校验 + 导入导出接入 `ossie_adapter`（从"快照式"改为"动态映射式"）；
4. 第四阶段再改前端（现有知识资产页面融合 OSSIE 属性 + 标准配置预览区）。
