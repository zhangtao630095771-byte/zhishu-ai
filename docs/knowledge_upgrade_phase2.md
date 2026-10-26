# 知数 AI 知识库治理升级 · 阶段 2 整改报告（知识治理与语义关联）

> 时间：2026-10-22
> 目标：落地 GAP-2（知识版本/历史/元数据）、GAP-3（索引重建状态），补齐知识可追溯性。

---

## 1. 结论

补齐了知识治理的核心缺口：**变更历史（版本快照 + 审计）、元数据列、索引重建状态**。迁移幂等、只增不删、零数据丢失（生产库迁移前后知识计数完全一致）。

---

## 2. 数据结构变化（新增）

### 2.1 新表 `knowledge_history`（变更历史 / 版本快照）

| 列 | 用途 |
|---|---|
| `entity_type` | metric / entity / relationship / kb_doc / dict / table_comment / column_comment |
| `entity_id` | 主键（metric 用 name；relationship/kb_doc 用 id） |
| `action` | create / update / delete / enable / disable |
| `before_json` / `after_json` | 变更前/后完整快照（供追溯与回滚） |
| `operator` | 操作人（账号体系可用时） |
| `created_at` | 时间戳 |

### 2.2 新表 `index_state`（索引重建状态）

| 列 | 用途 |
|---|---|
| `status` | ok / failed |
| `counts_json` | 重建时知识计数（tables/metrics/dicts/...） |
| `error` | 失败原因摘要 |
| `built_at` | 重建时间 |

### 2.3 元数据列（7 张知识表，幂等 ALTER）

`metrics` / `entities` / `relationships` / `kb_docs` / `dict_entries` / `table_comments` / `column_comments` 统一新增：

- `created_at` / `updated_at`（时间戳）
- `updated_by`（操作人）
- `version`（版本号，update 时自增）
- `source_ref`（来源标识）

> 历史知识缺的元数据一律留 NULL（诚实标记「未知」），**不凭空补造来源/审核人/权威等级**。

---

## 3. 代码改动

| 文件 | 改动 |
|---|---|
| `config_store.py` | 新增 `_migrate_knowledge_governance()`（幂等迁移）；`_row_dict` / `_record_history` 工具；`list_knowledge_history()` / `set_index_state()` / `get_index_state()`；改造 `upsert_metric`/`delete_metric`/`set_metric_enabled`/`upsert_entity`/`delete_entity`/`upsert_relationship`/`delete_relationship`/`upsert_kb_doc`/`delete_kb_doc`/`upsert_dict_entry`/`delete_dict_entry`/`set_dict_enabled`/`set_table_comment`/`set_column_comment`/`delete_column_comment` 共 15 个函数，全部写入变更历史 + 维护 version/时间戳 |
| `rag.py` | `build_index` 拆为 `_build_index`（成功时写 index_state ok）+ 薄包装（失败时写 failed 再抛出） |
| `test_knowledge_governance.py`（新） | 6 组回归测试（迁移幂等 / 指标版本历史 / 实体关系 / 启停 / 注释 / 索引状态） |
| `run_tests.py` | 纳入 config 层 |

---

## 4. 迁移前后数据校验（生产库 `agent_config.db` 实测）

| 表 | 迁移前 | 迁移后 | 差异 |
|---|---|---|---|
| metrics | 48 | 48 | 0 |
| entities | 42 | 42 | 0 |
| relationships | 16 | 16 | 0 |
| kb_docs | 9 | 9 | 0 |
| dict_entries | 19 | 19 | 0 |
| table_comments | 38 | 38 | 0 |
| column_comments | 149 | 149 | 0 |
| feedbacks | 4 | 4 | 0 |
| users | 2 | 2 | 0 |
| query_records | 110 | 110 | 0 |

**结论：迁移前后知识记录零丢失。** 迁移为 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`（幂等），可重复执行。

---

## 5. 测试结果（实际执行）

`test_knowledge_governance.py` —— **31/31 通过**：

- 迁移幂等：`knowledge_history`/`index_state` 表存在，7 张知识表有元数据列；
- `upsert_metric`：create（version=1，before=None）→ update（version=2，before/after 快照正确）→ delete（记录删除前快照）；
- `upsert_entity`/`upsert_relationship`：create/delete 历史正确；
- `set_metric_enabled`/`set_dict_enabled`：enable/disable 历史正确；
- `set_table_comment`/`set_column_comment`/`delete_column_comment`：历史正确；
- `set_index_state`/`get_index_state`：ok/failed 状态与计数正确。

全量回归全绿（unit 含 122+9、config 84+31、integration 51）。

---

## 6. 尚未完成（阶段 3 及以后）

- **知识审核状态机**（draft/pending/approved）与前端界面：本次只加了元数据列与历史，未加审核流转 UI。`source=ai` 的 29 个实体、100 条字段注释仍「未复核」，需后续在管理页加「复核 → 转 manual」入口。
- **GAP-5**（8 条 1:N 关系未标预聚合）：涉及业务口径判定，待业务确认，本次只标注。
