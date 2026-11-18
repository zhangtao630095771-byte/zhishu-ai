# 知数AI 四模块职责梳理 · 实施交付报告（补标准字段 + 录入迁移 + 废弃清理）

> 日期：2026-11-13 · 阶段：审查确认后的实施（4 项推荐全部落地）

---

## 1. 完成的 4 项改造

| # | 改造 | 说明 |
|---|---|---|
| 1 | 补 OSSIE 标准字段 | `entities.primary_key`、`relationships.from_columns/to_columns`、`metrics.expression`（后端迁移 + CRUD + 映射 + 前端编辑） |
| 2 | 知识录入迁移 | 「知识录入」从 tab-gov 移到 tab-kb「智能录入」 |
| 3 | 废弃旧服务 | 删除 `semantic_model_service.py`，`apply_model_to_kb` 迁移到 `semantic_object_builder` |
| 4 | 清理旧快照 | 删除旧「订单主题」approved 快照（保留 published 发布快照） |

---

## 2. 修改 / 新增文件清单

| 文件 | 改动 |
|---|---|
| `config_store.py` | `_migrate_ossie_std_fields`（primary_key/from_columns/to_columns）；upsert_entity/upsert_relationship/get_entities/get_relationships/upsert_metric/get_metrics 支持标准字段 |
| `semantic_object_builder.py` | build_dataset_object 用 primary_key；build_relationships 优先显式 from/to_columns；重构 import_ossie_to_kb 为参数化 `_sync_internal_to_kb` + 新增 `apply_approved_model_to_kb` |
| `server.py` | EntityBody/RelationshipBody/MetricBody 加标准字段；`/api/kb/entities`、`/api/kb/relationships`、`/api/metrics` 支持；删除 `import semantic_model_service`，apply 接口改用 semantic_object_builder |
| `agent.py` | 删除残留 `import semantic_model_service` |
| `frontend/index.html` | 指标表单加「标准表达式」、实体表单加「主键」、关系表单加「from/to 字段」；知识录入 HTML 从 tab-gov 移到 tab-kb；tab-kb subtabs 加「智能录入」，tab-gov subtabs 移除「知识录入」 |
| `frontend/static/js/knowledge/metrics.js` | 指标 expression 读写 |
| `frontend/static/js/knowledge/registry.js` | 实体 primary_key、关系 from/to_columns 读写 |
| `run_tests.py` | 移除 test_semantic_model_service 注册 |
| 删除 | `semantic_model_service.py`、`test_semantic_model_service.py` |

---

## 3. 关键设计

### 3.1 标准字段映射（补全后）

| OSSIE 对象 | 标准字段 | 映射 |
|---|---|---|
| Dataset | primary_key | `entities.primary_key`（JSON 数组，前端可视化编辑） |
| Relationship | from_columns/to_columns | `relationships.from_columns/to_columns`（显式登记，优先于 join_sql 正则提取） |
| Metric | expression | `metrics.expression`（可视化编辑，审核后作权威口径） |

### 3.2 消除重复（废弃旧服务）

`semantic_object_builder._sync_internal_to_kb(internal, source, operator)` 统一写回逻辑：
- `import_ossie_to_kb` → source='ai'（未审核）
- `apply_approved_model_to_kb` → source='manual'（已审核）

原 `semantic_model_service.apply_model_to_kb`（重复实现）已删除。

---

## 4. 测试结果

- `run_tests.py unit`：26 文件全绿（含 ossie_adapter 23）
- `run_tests.py config`：9 文件全绿
- `test_frontend_structure.py`：23 项通过
- server import OK（无残留引用）；JS 语法 OK

---

## 5. 数据兼容性与回滚

| 项 | 说明 |
|---|---|
| 标准字段迁移 | ALTER ADD COLUMN 幂等，历史数据为空（= 未登记，不影响现有） |
| 知识录入迁移 | 纯前端 HTML 移动，后端接口不变 |
| 废弃旧服务 | 已确认无引用（grep 全仓仅剩注释）；apply 逻辑迁移到 builder |
| 旧快照清理 | 备份 `agent_config.db.bak-cleanup-*` |
| 回滚 | `git checkout` 相关文件；恢复备份 |

---

## 6. 审查报告中的 5 个目标达成

| 审查目标 | 结论 |
|---|---|
| 1. 知识资产具备 OSSIE 编辑能力 | ✅ 已补 primary_key / expression / from_to_columns 可视化编辑 |
| 2. 资产与 OSSIE 映射完整 | ✅ 补全 Dataset/Relationship/Metric 缺的标准字段 |
| 3. 业务域不承担资产编辑 | ✅ 业务域保持组织视图，不建编辑器 |
| 4. 知识资产与治理不重复 | ✅ 知识录入迁移到资产，治理只保留审核/缺口/导出 |
| 5. 语义模型与注册表不重复 | ✅ 废弃旧服务，统一到 semantic_object_builder |
