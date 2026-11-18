# P0 核心能力升级 · 实施记录（implementation）

> 记录本次新增/修改的全部代码与数据库迁移。原则：复用现有闸门，不另造执行入口；新增表幂等、只增不删。

---

## 一、新增文件

| 文件 | 职责 |
|---|---|
| `evaluation/result_compare.py` | 业务结果对比引擎：六类判定（exact / within_tolerance / structure_only / column_mismatch / row_count_mismatch / incomparable）+ 逐格差异定位 |
| `golden_cases.py` | 标准问题集管理（P0-1）：CRUD + 版本历史 + 审核发布 + 停用归档 + 标准 SQL 安全验证 |
| `test_result_compare.py` | 结果对比引擎专项测试（22 项断言，unit 层） |
| `test_golden_cases.py` | 标准问题集专项测试（19 项断言，config 层，临时库） |
| `frontend/static/js/golden.js` | 前端：标准问题集管理页 + P0 核心指标看板页 |

## 二、修改文件

| 文件 | 改动 |
|---|---|
| `config_store.py` | ① `init_store()` 末尾调用 `golden_cases.migrate_schema(cur)`（幂等，失败不挡启动）；② 新增 `CONFIRM_REASONS`（8 类 L4 确认分类）；③ `review_record()` 增加 `confirm_reason` 参数；④ 新增 `dashboard_sql_quality()` / `dashboard_feedback()` 看板聚合 |
| `server.py` | ① 新增 10 个 `/api/golden/*` 路由 + 1 个 `/api/dashboard/p0` 路由（默认走 admin 中间件）；② `ReviewBody` 增加 `confirm_reason` 字段并透传；③ 新增 `_pct()` / `_latest_eval_summary()` 辅助 |
| `run_tests.py` | registry 登记 `test_result_compare.py`（unit）、`test_golden_cases.py`（config） |
| `evaluation/experiments/ablation/run_ablation.py` | 补齐消融：B(Semantic Layer) / E(JOIN 基数) / F(SQL 经验) / D(可信排序) |
| `frontend/index.html` | 新增「标准问题」「评测看板」两个页签 + `tab-golden`/`tab-dash` 两个 section + 引入 golden.js |
| `frontend/static/js/core.js` | `switchTab` 增加 golden / dash 两个 loader |
| `frontend/static/app.css` | 追加看板 `.dash-grid/.dash-card/.dash-row` 样式 |

## 三、数据库结构（全部幂等，只增不删）

新增 3 张表 + 1 个列（DDL 在 `golden_cases.SCHEMA`，由 `init_store()` 调用）：

1. `golden_cases` —— 标准问题集（case_id / question / business_line / roles / expected_metrics / expected_entities / time_caliber / expected_dimensions / expected_sql / expected_result / result_check / data_snapshot / snapshot_type / acceptance / review_status / verify_status / status / version / source …）
2. `golden_case_versions` —— 版本历史（改 expected_sql/result 自动 +1 版本留痕）
3. `golden_case_verify_log` —— 标准 SQL 验证记录
4. `query_records.confirm_reason` —— L4 业务确认分类列（8 类）

## 四、关键设计决策

### 4.1 标准 SQL 验证复用现有闸门（不另造旁路）

`golden_cases.validate_expected_sql()` 依次调用 `guard.sql_guard`（只读）→ `guard.check_tables_ex`（表白名单）→ `sql_validator.validate_sql_ex`（五维业务校验）→ `permission.evaluate`（字段级权限，可选）。管理员录入的 expected_sql 与模型生成的 SQL 走**同一套判定**，硬安全失败（只读/表/字段）返回 `hard=True` 不可自愈。

### 4.2 审核强制重验（不能拿旧验证当终身免检）

`set_review(cid, "approved")` 会**重新调用** `validate_expected_sql`——即使有人绕过创建时的校验、直接改库把 expected_sql 换成越权 SQL，审核通过这关也会拦住（`test_review_requires_revalidation` 覆盖）。

### 4.3 结果对比不是「字符串相等」

`result_compare.compare()` 输出六类判定 + `cell_diffs`（row/col/expected/actual/kind）定位差异；数值按 precision 归一、NULL/空串归一、无序逐行比较，避免「GROUP BY 顺序不同」误报。

### 4.4 审核状态与验证状态独立

`review_status`（draft/pending/approved/rejected）与 `verify_status`（unverified/verified）是两个独立字段、两组独立接口，`trust` 由二者联合推导——对齐现有 SQL 经验中心「审核通过 ≠ 结果正确」的铁律。

## 五、未触碰的红线

- `agent._exec_sql` / `agent._pre_execute_check` 零改动；
- 未新增任何绕过统一闸门的执行入口；
- 未改动 guard / sql_validator / permission 的判定逻辑；
- 未修改任何测试的预期值来「制造通过」。
