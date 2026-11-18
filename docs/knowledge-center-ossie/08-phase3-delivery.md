# 知数AI OSSIE 融合式改造 · 第三阶段交付报告（后端标准映射 + Schema 校验 + 导入导出）

> 日期：2026-11-12 · 阶段：第三阶段（后端标准映射、Schema 校验、导入导出；暂不改前端）

---

## 1. 本阶段完成内容

| # | 内容 | 状态 |
|---|---|---|
| 1 | 表结构迁移（4 项 ALTER + topic_assets 新表） | ✅ |
| 2 | `semantic_object_builder` 补全（完整模型组装 + 主题聚合 + 维度整合 + 导入写回） | ✅ |
| 3 | 动态导出 / 导入写回 API（`/api/semantic/export`、`/api/semantic/import`） | ✅ |
| 4 | Schema 校验 + 引用完整性校验接入 | ✅ |

---

## 2. 修改 / 新增文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `config_store.py` | 修改 | 新增 `_migrate_fusion_schema`（4 项迁移 + topic_assets 表）+ 业务主题/主题资产 CRUD（list/upsert/delete_business_topic、list/add/remove_topic_asset） |
| `semantic_object_builder.py` | 修改 | 补全 `build_datasets`/`build_metrics`/`build_relationships`/`build_semantic_model`/`get_semantic_objects`/`import_ossie_to_kb` |
| `server.py` | 修改 | import semantic_object_builder；新增 `GET /api/semantic/export`、`POST /api/semantic/import` |

---

## 3. 表结构迁移（幂等，只增不改）

| 迁移 | 说明 |
|---|---|
| `data_domains` + `domain_type`（默认 `data_domain`） | 复用空表作业务主题容器（`business_topic`），兼容原数据域 |
| `topic_assets` 新表（topic_name, asset_type, asset_key） | 业务主题 → 实体/指标 引用指针（**非副本**） |
| `column_comments` + `dimension_type`（可空） | 字段维度属性（time/geo/status/type/channel），空=自动识别 |
| `metrics` + `expression` + `expression_status` | 指标标准 SQL 表达式 + 审核状态 |

> 迁移失败不挡启动（try/except）；PRAGMA 检查列存在，幂等可重入。

---

## 4. 统一语义对象组装层（`semantic_object_builder`，只读知识表）

| 函数 | 能力 |
|---|---|
| `build_datasets(topic)` | entities → Dataset（含字段维度识别，人工 `dimension_type` 优先） |
| `build_metrics(topic)` | metrics → Metric（含 expression 提取；纯文本指标不映射，记入 un_mapped） |
| `build_relationships(topic)` | relationships → Relationship（端点实体缺失跳过，引用完整） |
| `build_semantic_model(topic)` | 完整 Semantic Model（动态映射，可直接 Schema 校验 + 序列化） |
| `get_semantic_objects(topic)` | **统一语义读取接口**（Agent 优先 approved 口径） |
| `import_ossie_to_kb(doc)` | OSSIE 文档 → 写回知识表（source='ai'，走审核，只新增不覆盖） |

### 4.1 实测结果（真实库动态组装）

| 项 | 值 |
|---|---|
| datasets | 46（全部启用实体） |
| metrics | 33（16 提取 + 17 待补充；24 纯文本不映射） |
| relationships | 2（端点实体完整） |
| 维度字段 | 168（time/geo/status/type/channel 识别） |
| Schema 校验 | ✅ 通过 |
| 引用完整性 | ✅ 通过 |
| 未映射指标 | 24（记入 nonportable_extensions，决策 5：不编造 SQL） |

---

## 5. 动态导出 / 导入写回（融合式核心）

### 5.1 导出（不落库，实时组装）

`GET /api/semantic/export?topic=&format=yaml|json`
- 从知识表**实时动态组装** OSSIE 模型（不读快照、不落库）；
- 空实体 → 400 友好提示；
- 组装结果过 Schema 校验后下载。

### 5.2 导入（写回知识表，走审核）

`POST /api/semantic/import`（body: content/format）
- 解析 → 版本校验 → Schema 校验 → 引用完整性校验 → 写回知识表；
- **source='ai'、status='candidate'**（导入对象未审核，人工复核后才进检索）；
- 只新增、绝不覆盖已存在对象，全程走 `config_store.upsert_*` 审计。

---

## 6. 测试结果

- `run_tests.py unit`：27 文件全绿（含 ossie_adapter 23、semantic_model_service 10）
- `run_tests.py config`：9 文件全绿
- 集成冒烟（临时库）：动态导出 200、导入写回 candidate/ai、迁移字段全部生效

---

## 7. 兼容性与回滚

| 项 | 说明 |
|---|---|
| 表迁移 | 只 ALTER ADD COLUMN + CREATE TABLE IF NOT EXISTS，幂等可重入，不删列不改旧表 |
| data_domains 语义 | domain_type 字段隔离（默认 data_domain），原数据域功能零影响 |
| 导入写回 | source='ai' 走治理，不覆盖人工（复用 upsert 护栏） |
| 回滚 | 迁移列 `ALTER TABLE ... DROP COLUMN` 或恢复备份；`semantic_object_builder.py` 删除即无副作用 |

---

## 8. 遗留（第四阶段 + 后续）

1. **前端改造**（第四阶段）：现有知识资产页面融合 OSSIE 属性 + 标准配置预览区 + 可视化编辑（本阶段未改前端）。
2. **Agent 接入统一语义读取接口**（第五阶段）：`get_semantic_objects` 已就绪，需接入 `ask_v2`（替换之前 `semantic_model_service` 快照读取）+ 真实业务评估。
3. **指标 expression 人工复核**：16 个提取结果需人工在治理工作台确认（`expression_status` 标记），17 个待补充、24 个保留 description。
4. **发布快照机制**（决策 3）：`semantic_models` 表改为「发布快照」语义的落地（编辑态动态映射已就绪，发布态固化待第四阶段实现）。
5. **`semantic_model_service`（读快照）与 `semantic_object_builder`（动态映射）并存**：第五阶段 Agent 接入时统一到后者，避免两套语义读取逻辑。

---

## 9. 关键原则回顾

**知识表是唯一事实源，OSSIE 对象是派生视图，绝不重复存储。**
- 导出 = 实时动态映射，不落库；
- 导入 = 写回知识表，走审核（candidate），不覆盖；
- 统一语义读取接口 = `get_semantic_objects`（唯一实现）。
