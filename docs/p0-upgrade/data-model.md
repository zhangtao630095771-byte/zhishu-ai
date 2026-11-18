# P0 核心能力升级 · 数据模型与接口设计（data-model）

> 本次新增的数据结构与接口。复用原则：不改现有 36 张表，只新增表；不改 `_pre_execute_check` / `_exec_sql`。

---

## 一、新增数据表

### 1.1 `golden_cases` —— 标准问题集（P0-1）

对齐任务书 §3.1 的 15 个字段，并补齐治理字段。

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | TEXT PK | case_id（人可读，如 `g001` / `GC-20261102-001`） |
| `question` | TEXT | 标准业务问题 |
| `business_line` | TEXT | 业务线 |
| `roles` | TEXT(JSON) | 适用角色列表 |
| `expected_metrics` | TEXT(JSON) | 标准指标 |
| `expected_entities` | TEXT(JSON) | 标准实体 |
| `time_caliber` | TEXT | 标准时间口径 |
| `expected_dimensions` | TEXT(JSON) | 标准维度 |
| `expected_sql` | TEXT | 标准 SQL（必须过闸门验证） |
| `expected_result` | TEXT(JSON) | 标准结果（`{"columns":[...],"rows":[...],"precision":N}`） |
| `result_check` | TEXT(JSON) | 规则化验收（行数/列名/关键单元格），可选 |
| `data_snapshot` | TEXT | 数据快照/测试数据版本标识（区分固定测试集 vs 生产口径） |
| `snapshot_type` | TEXT | `fixed`(固定测试集) / `production`(生产业务案例) / `history`(历史结果追溯) |
| `acceptance` | TEXT | `auto` / `rule` / `manual` |
| `review_status` | TEXT | `draft` / `pending` / `approved` / `rejected` |
| `verify_status` | TEXT | `unverified` / `verified`（独立于审核） |
| `reviewer` / `review_note` / `reviewed_at` | — | 审核人/备注/时间 |
| `status` | TEXT | `active` / `archived` / `disabled` |
| `version` | INTEGER | 版本号 |
| `source` | TEXT | `manual` / `import` / `feedback` / `seed-experience` |
| `created_at` / `updated_at` | TEXT | 时间戳 |

**约束**：`review_status=approved` 前，`expected_sql` 必须已通过 `validate_expected_sql`（见 §2.1）；`acceptance=auto` 时 `expected_result` 不得为空。

### 1.2 `golden_case_versions` —— 版本历史（P0-1 版本管理）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `case_id` | TEXT | 外键 → golden_cases.id |
| `version` | INTEGER | 版本号 |
| `expected_sql` | TEXT | 该版本的标准 SQL |
| `expected_result` | TEXT(JSON) | 该版本的标准结果 |
| `changed_by` / `change_note` | — | 变更人/说明 |
| `created_at` | TEXT | 时间 |

### 1.3 `golden_case_verify_log` —— 标准 SQL 验证记录（P0-1-B）

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | INTEGER PK | 自增 |
| `case_id` | TEXT | 外键 |
| `result` | TEXT | `passed` / `failed` |
| `issues` | TEXT(JSON) | 校验问题列表（含 rule/message） |
| `checked_by` / `created_at` | — | 触发人/时间 |

> 三张表都用 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`（幂等，与现有一致），落在 `config_store.init_store()` 末尾。

---

## 二、核心函数设计

### 2.1 `validate_expected_sql(sql, allowed_tables, user=None)` —— 标准 SQL 安全验证（P0-1-B）

**必须复用现有闸门，不另造执行入口**：

```
① guard.sql_guard(sql)           只读 / 禁 DDL·DML·SETTINGS
② guard.check_tables_ex(sql, allowed)   表白名单
③ sql_validator.validate_sql_ex(sql, ctx, resolved, allowed)   五维业务校验
④ permission.evaluate(sql, user, meta)  字段级权限（user 非空时）
```

返回 `{ok, hard, issues, error, status}`。**管理员录入的 expected_sql 与模型生成的 SQL 走同一套判定**，不因来源而放宽。

### 2.2 `result_compare.py` —— 结果对比引擎（P0-1-C / P0-2-L3）

纯函数，不连库。输入 `(actual_rows, expected_result)`，输出：

```json
{
  "verdict": "exact | within_tolerance | structure_only | column_mismatch | row_count_mismatch | incomparable",
  "matched": true/false,
  "details": {
     "row_count_expected": N, "row_count_actual": M,
     "col_count_expected": N, "col_count_actual": M,
     "cell_diffs": [{"row": i, "col": j, "expected": v, "actual": w, "kind": "numeric|string|null"}],
     "extra_rows": [...], "missing_rows": [...]
  }
}
```

**六类判定（对齐任务书 §3.2-C）**：
- `exact` 完全一致
- `within_tolerance` 数值容差内一致（precision 归一）
- `structure_only` 结果结构一致但数值不一致（行/列对齐但逐格数值不同）
- `column_mismatch` 字段不一致（列名/列数不同）
- `row_count_mismatch` 行数不一致
- `incomparable` 无法比较（无结果/无期望/类型无法解析）

**禁止**用「字符串完全相等」当唯一判据。差异定位：给出第一个差异的 (row, col, expected, actual)。

### 2.3 L4 业务确认分类（P0-2-L4）

取数审核动作新增可选 `confirm_reason` 字段（复用 `FEEDBACK_REASONS` 的语义，收敛为 8 类）：

```
correct / wrong_metric / wrong_time / wrong_scope / join_dup / wrong_enum / result_mismatch / other
```

审核通过且无问题 → `correct`；驳回时必填具体原因类别。落库到 `query_records`（新增列 `confirm_reason`）。

---

## 三、API 设计

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `/api/golden/cases` | 列表（支持 status/review/业务线过滤） |
| POST | `/api/golden/cases` | 新增（自动校验 expected_sql） |
| GET | `/api/golden/cases/{cid}` | 详情（含版本） |
| PUT | `/api/golden/cases/{cid}` | 编辑（改 expected_sql 会重新校验并 +1 版本） |
| POST | `/api/golden/cases/{cid}/review` | 审核（approve 前强制再验一次 expected_sql） |
| POST | `/api/golden/cases/{cid}/verify` | 验证 expected_result（固定测试集上执行） |
| POST | `/api/golden/cases/{cid}/archive` | 停用/归档 |
| POST | `/api/golden/cases/{cid}/compare` | 结果对比（喂入实际结果 → result_compare） |
| POST | `/api/golden/import` | 批量导入（JSONL/JSON） |
| GET | `/api/golden/cases/{cid}/versions` | 版本历史 |
| GET | `/api/dashboard/p0` | P0 核心指标聚合 |

---

## 四、评测指标口径显式化（P0-4）

| 指标 | 分母 | 分子 | 统计范围 |
|---|---|---|---|
| Intent Accuracy | 有 `intent` 标注的用例 | 意图一致 | golden/regression |
| Knowledge Recall@K | 有 `expected_tables/expected_metrics` 的用例 | Top-K 召回命中 | golden/regression |
| SQL Execution Success Rate | 期望可执行的用例 | `ok=true` | golden |
| SQL Valid Pass Rate | 期望可执行的用例 | 闸门 passed（含 warning） | golden |
| Exact Result Match Rate | 有 `expected_result` 的用例 | verdict=exact/within_tolerance | golden-demo/fixed |
| Numeric Result Accuracy | 有 `expected_result` 且可数值化的用例 | 数值归一后一致 | golden-demo |
| **Business Verified Rate** | **经人工审核的用例** | **review_status=approved** | 全量 |
| Golden SQL Reuse Rate | 召回经验案例的查询 | 实际采用(direct_reuse/reference) | 全量 |
| Feedback Resolution Rate | 全部反馈 | status=resolved | 全量 |

> **铁律**：`Business Verified Rate` 的分子**只含人工审核通过的**，`exec_ok=true` 不算；`SQL Execution Success Rate` ≠ `Result Accuracy`。两者在报告与看板上并列、独立展示。

---

## 五、看板聚合逻辑（P0-5）

`/api/dashboard/p0` 一次性返回四组：

- **业务结果**：标准问题总量、已验证案例数、业务结果正确率（Business Verified Rate）、未验证数量、反馈率。
- **SQL 质量**：生成成功率、执行成功率、校验通过率、平均自愈次数、JOIN 相关错误数。
- **知识质量**：Golden SQL 数、复用率、候选知识数、待审核数、冲突数、验证通过率。
- **AI 评测**：当前基线版本、最新评测结果、Regression/Release Gate 结果、消融对比、历史趋势。

数据来源：`golden_cases`、`sql_experience`、`sql_experience_rules`、`sql_experience_usage`、`query_records`、`feedbacks`、`evaluation/reports/`。
