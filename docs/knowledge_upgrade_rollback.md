# 知数 AI 知识库治理升级 · 回滚方案

> 时间：2026-10-22
> 原则：所有改动均可回滚；迁移只增不删，代码回退后旧列不影响运行。

---

## 1. 备份

| 备份 | 路径 |
|---|---|
| agent_config.db（升级前） | `.workbuddy/backup/agent_config.db.bak-20261022-083938` |
| config.db（升级前，空） | `.workbuddy/backup/config.db.bak-20261022-083938` |

---

## 2. 数据库回滚

本次迁移**只新增表、只新增列，未删除/修改任何数据**：

- 新增表：`knowledge_history`、`index_state`
- 新增列：7 张知识表 `created_at/updated_at/updated_by/version/source_ref`；`feedbacks` 的 `related_knowledge_id/regression_sample_id/root_cause`

**回滚方式（二选一）：**

1. **恢复备份**（最彻底）：
   ```bash
   cp .workbuddy/backup/agent_config.db.bak-20261022-083938 agent_config.db
   ```
   注意：会丢失升级后产生的变更历史与新数据。

2. **保留新列、仅回退代码**（推荐）：
   新列/新表不影响旧代码运行（旧 `SELECT` 用显式列名，不读新列；`INSERT` 不含新列走默认值）。只需回退代码即可。

---

## 3. 代码回滚

| 改动 | 回滚 |
|---|---|
| `agent.py`（Legacy 接入 sql_validator） | 删除 `legacy_ctx`/`legacy_resolved` 构建块 + 循环内的 `v = sql_validator.validate_sql_ex(...)` 块，恢复为原「只读+表权限+字段权限」 |
| `config_store.py`（历史/元数据/反馈闭环） | 删除 `_migrate_knowledge_governance` 调用与 `_row_dict`/`_record_history`/`list_knowledge_history`/`set_index_state`/`get_index_state`；15 个 CRUD 函数恢复为原签名（去掉 `operator` 参数与历史写入）。**新列保留不影响运行** |
| `rag.py`（build_index 拆分） | 恢复为单函数 `build_index`（去掉 `_build_index` + 包装与 `set_index_state` 调用） |
| `run_tests.py` | 移除 `test_legacy_validation`、`test_knowledge_governance` 两行 |

---

## 4. 回滚验证

回滚后执行：

```bash
CONFIG_DB=/tmp/rollback_test.db SQL_REVIEW_ENABLED=false .venv/bin/python run_tests.py
```

预期：unit/config/integration 全绿；`agent_config.db` 知识计数不变（48 metrics / 42 entities / 16 relationships / 9 kb_docs / 19 dicts / 38+149 comments）。

---

## 5. 关键安全约定（回滚后仍需遵守）

1. 所有执行路径统一经过执行前闸门（本次阶段 1 修复，回滚会**重新打开 GAP-1**，请勿回滚 `agent.py` 的安全修复）。
2. 表名解析失败 fail-closed（`TABLE_PARSE_STRICT=1`）。
3. `LEGACY_FALLBACK_MODE` 生产禁止 `always`。
4. 数据库账号保持只读（当前 `CK_USER=<只读账号名>` 建议改为 `ai_reader`）。
