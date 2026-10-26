# 知数 AI 知识库治理升级 · 阶段 0 改造前基线

- **基线日期**：2026-10-22
- **用途**：作为本轮升级「优化前后对比」与「回归判定」的改造前基线；所有后续指标变化均以此为参照。

---

## 1. 环境基线

| 项 | 值 |
|---|---|
| Python | `.venv`（3.13.12） |
| 数据源 | `DB_TYPE=clickhouse`，`CK_HOST=127.0.0.1:8123`（SSH 隧道），`CK_DATABASE=ctc_data_warehouse`，`CK_USER=<只读账号名>` |
| Embedding | `BAAI/bge-small-zh-v1.5`（512 维），`EMBED_LOCAL_ONLY=1`，缓存 `models/` |
| ACL | `ACL_DEFAULT_VISIBLE=false`（白名单，fail-closed） |
| 降级 | `LEGACY_FALLBACK_MODE` 默认 `whitelist`；`APP_ENV` 默认 `production` |
| 配置库 | `agent_config.db`（946 KB）；`config.db` 为空（未使用） |
| 版本控制 | **无 Git 仓库**（`.git` 不存在） |

### 1.1 依赖可用性

`requirements.txt` 声明的 openai / python-dotenv / fastembed / sqlglot / fastapi / uvicorn / python-multipart / openpyxl / clickhouse-connect 均在 `.venv` 下可导入（`test_imports.py` 26/26 通过）。

### 1.2 生产数据源连通性

`curl http://127.0.0.1:8123/ping` → `Ok.`，ClickHouse 可达；`integration` 层测试（`test_gaps`）实测通过。

### 1.3 配置库隔离

本次所有会写配置库的测试，一律用 `CONFIG_DB=/tmp/zhishu_test_config.db`（`agent_config.db` 的副本）隔离，**不写生产库**。

### 1.4 备份

| 备份 | 路径 | 说明 |
|---|---|---|
| agent_config.db | `.workbuddy/backup/agent_config.db.bak-20261022-083938` | 审计前完整备份（946 KB） |
| config.db | `.workbuddy/backup/config.db.bak-20261022-083938` | 空库备份 |

---

## 2. 测试基线（实际执行结果）

运行命令：

```bash
cd /Users/zhangtao/Downloads/autosql/zhishu-ai
CONFIG_DB=/tmp/zhishu_test_config.db SQL_REVIEW_ENABLED=false .venv/bin/python run_tests.py
```

结果（2026-10-22 实测，耗时约 37s）：

| 层级 | 测试文件 | 结果 | 计数 |
|---|---|---|---|
| unit | test_imports.py | ✅ | 26/26 |
| unit | test_pipeline.py | ✅ | 11/11 |
| unit | test_gate.py | ✅ | 122/122 |
| unit | test_join_suggest.py | ✅ | 10/10 |
| unit | test_guard.py | ✅ | 通过 |
| unit | test_labels.py | ✅ | 33/33 |
| unit | test_retrieve.py | ✅ | 22/22 |
| unit | test_permission.py | ✅ | 60（权限专项） |
| unit | evaluation/test_calendar.py | ✅ | 通过 |
| config | test_acl.py | ✅ | 84/84 |
| integration | test_gaps.py | ✅ | 51/51（真实 ClickHouse） |

- **通过：11 个测试文件全绿；失败：0；跳过：0。**
- 说明：`test_permission.py` 覆盖任务书 §八 30 项权限验收；`test_gaps.py` 覆盖真实库集成。

---

## 3. 评测集基线（94 条）

| 数据集 | 条数 |
|---|---|
| golden | 35 |
| regression | 14 |
| boundary | 13 |
| abstention | 10 |
| paraphrase | 9 |
| counterfactual | 6 |
| adversarial | 6 |
| inbox | 1 |
| **合计** | **94** |

另：`eval/cases.json` 8 条端到端用例（含 `expected_sql_contains`）。

**历史 baseline（`evaluation/reports/baseline.json`）**：`overall_accuracy=0.8571`、`sql_accuracy=0.8462`、`sql_execution_success_rate=0.9143`、`metric_accuracy=0.9545`、`table_retrieval_recall=1.0`、`result_accuracy=null`、`field_retrieval_recall=null`、`consistency_rate=null`、`sample_count=35`、`executed_count=26`。

> ⚠️ 该组数字是历史运行记录，`result_accuracy=null` 说明**没有期望结果集**可判「业务正确性」。本轮不以此宣称业务准确率。gate 阈值见 `evaluation/gate.py`（overall≥0.93、sql_accuracy≥0.94、execution≥0.98），当前历史值低于阈值。

---

## 4. 知识库数据盘点基线（agent_config.db 实测）

| 表 | 记录数 | 备注 |
|---|---|---|
| metrics | 48 | 全部 enabled=1 |
| entities | 42 | **29 条 source=ai（未复核）**、13 条 manual |
| relationships | 16 | 1:1×4、1:N(pre_agg=0)×8、1:N(pre_agg=1)×2、N:1×2 |
| kb_docs | 9 | — |
| dict_entries | 19 | — |
| table_comments | 38 | — |
| column_comments | 149 | **100 条 source=ai（未复核）**、49 条 manual |
| table_acl | 35 | — |
| feedbacks | 4 | pending×1、resolved×3 |
| users | 2 | — |
| query_records | 110 | — |
| sensitive_resources | 324 | — |
| permission_policies | 0 | 未配置字段策略（默认无策略=放行，靠表级 ACL） |
| row_permission_rules | 0 | 未配置行级 |
| permission_audit_logs | 2 | — |

### 4.1 治理缺口快照

- **无版本/历史**：所有知识表无 `version/created_at/updated_at/created_by/status/reviewed_by/valid_from/valid_to`。
- **未复核 AI 知识**：29 个实体 + 100 条字段注释为 AI 生成，无审核流转、无审核人、无时间戳。
- **1:N 未预聚合关系 8 条**：id 5/6/7/14/15/17/18/20。
- **反馈无关联**：feedbacks 无「关联知识 ID / 回归样例 ID」。

---

## 5. 已知待办（改造前即存在，本轮处理项）

| 缺口 | 状态 | 本轮动作 |
|---|---|---|
| GAP-1 Legacy 无业务校验 | 待修 | 阶段 1 关闭 |
| GAP-2 知识无版本/历史/元数据 | 待修 | 阶段 2 落地 |
| GAP-3 索引重建无状态 | 待修 | 阶段 2 落地 |
| GAP-4 Golden 无标准 SQL/结果集 | 待修 | 阶段 3 补结构 |
| GAP-5 1:N 未标注预聚合 | 待业务确认 | 仅标注，不改数据 |
| GAP-6 反馈未进闭环 | 待修 | 阶段 3 补关联 |
| GAP-7 P1 硬约束跨族 | 待业务确认 | 仅标注 |

---

## 6. 基线结论

系统在改造前已具备：分层语义流水线、执行前五维校验、表/字段/行级权限、唯一执行出口、94 条评测集、分层测试 runner，且**测试全绿、ClickHouse 可达**。

本轮升级的增量目标是：**在不动摇上述安全与兼容基线的前提下，补上知识可追溯性、Legacy 安全一致性、索引与评测的可观测闭环**。改造后必须保持 11 个测试文件全绿，且 `agent_config.db` 中知识记录数不因迁移而减少。
