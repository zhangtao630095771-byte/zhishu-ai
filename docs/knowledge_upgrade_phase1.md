# 知数 AI 知识库治理升级 · 阶段 1 整改报告（安全与数据完整性）

> 时间：2026-10-22
> 目标：关闭 GAP-1（Legacy 兜底路径缺少业务校验），使所有执行路径统一经过完整校验闸门。

---

## 1. 结论

任务书 §八 所列 8 项 P0 安全风险中，**7 项在既有 stage1/stage2 已修复**（详见 `knowledge_upgrade_audit.md` §2 复核表）。本次阶段 1 只补最后一项残留：

- **GAP-1**：`ask_legacy` 原来只做「只读 + 表权限 + 字段权限」，**未调用 `sql_validator`**，导致 P1 指标硬约束 / P2 JOIN / P3 列存在性在 Legacy 路径不生效。

已修复：Legacy 现在用命中的指标/表/关系构建最小 `SemanticContext`，复用与 V2 同一套 `sql_validator.validate_sql_ex`，校验 error 级问题 → 回灌模型自愈 → 用尽重试仍未达标 → 拒绝执行（fail-closed）。

---

## 2. 改动文件与内容

| 文件 | 改动 |
|---|---|
| `agent.py` | `ask_legacy` 增加：① 构建 `legacy_ctx`（p1_metrics / p3_table_objs / p2_relationships）+ `legacy_resolved`（bindings / metric_types）；② 执行循环里在 `check_tables_ex` 之后、字段权限之前接入 `sql_validator.validate_sql_ex`，error 级问题自愈重试，耗尽即拒绝并返回 `validation`/`validation_status` |
| `test_legacy_validation.py`（新） | 4 组回归测试：缺 required_predicates / 含 forbidden_predicates / 引用不存在列 / 满足条件正常执行 |
| `run_tests.py` | 将 `test_legacy_validation.py` 纳入 unit 层 |

---

## 3. 回归测试结果（实际执行）

`test_legacy_validation.py` —— **9/9 通过**：

| 场景 | 结果 |
|---|---|
| Legacy 拒绝缺 `required_predicates` 的 SQL，`db.execute_sql` 调用 0 次 | ✅ |
| Legacy 拒绝含 `forbidden_predicates` 的 SQL，`db.execute_sql` 调用 0 次 | ✅ |
| Legacy 拒绝引用不存在列的 SQL，`db.execute_sql` 调用 0 次 | ✅ |
| Legacy 满足条件时正常执行，`db.execute_sql` 调用 1 次（正向） | ✅ |

全量回归（`run_tests.py`）：unit 全绿（含新增 9 项）、config 84 + 31、integration 51，**全部通过**。

---

## 4. 兼容性

- `ask_legacy` 签名不变（`user` 参数已有）；新增的 `legacy_ctx`/`legacy_resolved` 为内部局部变量。
- `config_store.get_relationships(enabled_only=True)` 调用已用 `try/except` 兜底（配置库未初始化时降级为空，不阻塞）。
- 行为变化：Legacy 现在更严格 —— 会拒绝以前可能「带病执行」的 SQL（这正是修复目标）。

---

## 5. 未关闭项

- **GAP-7**（P1 指标硬约束「同族任一满足」未按 `conflict_key` 分组）：涉及业务族划分，需业务确认后才能安全修复，本阶段**只标注不动**（见 audit §3 GAP-7）。
