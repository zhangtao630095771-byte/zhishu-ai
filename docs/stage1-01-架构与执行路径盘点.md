# 阶段 1-01：架构与 SQL 执行路径盘点

> 时间：2026-10-19  
> 范围：`agent.py` / `guard.py` / `sql_validator.py` / `server.py` / `admin.py` / `semantic_layer.py` / `rag.py` / `config.py` / 测试目录  
> 方法：先读真实调用链，再用最小脚本复现；报告结论与代码不一致时，以代码和实测为准。

---

## 1. 结论摘要

1. 用户问数只有 **一个运行入口**：`POST /api/ask → agent.ask → ask_v2（主）/ ask_legacy（兜底） → _exec_sql → db.execute_sql`。
2. 已具备三道闸门：`guard.sql_guard`（只读）、`guard.check_tables`（表权限）、`sql_validator.validate_sql`（业务口径/Schema）。
3. 但存在 **1 条执行绕过 + 2 处 fail-open + 1 处静默跳过**，均已复现：
   - 执行失败后的自愈 SQL **不再过任何校验**，可直接执行未授权表；
   - 表名解析失败时 `check_tables` 返回“通过”（fail-open）；
   - 前导注释让 `validate_sql` 直接返回空报告（全部校验被跳过）。
4. 当前 `.env` 已显式 `ACL_DEFAULT_VISIBLE=false`，本机是按白名单运行；代码默认值 `true` 仍是新部署风险。
5. 基线测试全绿：unit 8/8、config 83/83、integration 51/51（2026-10-19 实测）。

---

## 2. 运行架构

| 层   | 文件                                            | 职责                                                |
| --- | --------------------------------------------- | ------------------------------------------------- |
| 接口  | `server.py`                                   | FastAPI；鉴权中间件、`/api/ask`、知识库 CRUD、导出              |
| 编排  | `agent.py`                                    | `ask()` → `ask_v2()` / `ask_legacy()`；重试、自愈、拒答、降级 |
| 语义层 | `semantic_layer.py`                           | P0~P4 分层检索、实体/关系合并、Prompt 组装                      |
| 检索  | `rag.py`                                      | 表/指标/字典/实体向量 + 关键词混合检索                            |
| 知识  | `config_store.py`                             | 指标、实体、关系、字段注释、字典、文档、ACL、用户                        |
| 安全  | `guard.py`                                    | SQL 清洗、只读校验、表名提取、表权限                              |
| 校验  | `sql_validator.py`                            | P0~P3 + Metric 五维校验                               |
| 执行  | `db.py` → `db_clickhouse.py` / `db_sqlite.py` | 统一数据访问门面                                          |
| 后台  | `gaps.py`                                     | 知识缺口扫描，直接用 `db.execute_sql` 跑系统生成的 SQL            |

数据流：

```
问题 → Query Understanding → P1/P2/P3/P4 检索 → Prompt → LLM 生成 SQL
     → sql_guard → check_tables → validate_sql → _exec_sql → ClickHouse
     → result_validator → 自然语言回答
```

---

## 3. SQL 执行路径全清单

| # | 路径              | 入口/位置                                               | 执行前校验                                   | 实际执行             | 结论                            |
| - | --------------- | --------------------------------------------------- | --------------------------------------- | ---------------- | ----------------------------- |
| 1 | 首次生成（V2）        | `agent.py:514` → `:542`                             | sql_guard + check_tables + validate_sql | `_exec_sql`      | ✅ 完整                          |
| 2 | 校验失败后自愈（V2）     | `agent.py:555-556` → 循环顶                            | 重新走 1 的三道                               | `_exec_sql`      | ✅ 完整                          |
| 3 | **执行失败后自愈（V2）** | `agent.py:596-601`                                  | **无**                                   | `_exec_sql`      | ❌ 可绕过                         |
| 4 | Legacy 首次/修复    | `agent.py:349-411`                                  | sql_guard + check_tables（每次循环）          | `_exec_sql`      | ✅ 安全检查完整；无 validate_sql（历史设计） |
| 5 | 人工编辑后重跑         | —                                                   | —                                       | —                | 无此接口；导出只读快照，不重跑               |
| 6 | 后台知识缺口扫描        | `gaps.py:92/115`                                    | 无（SQL 由系统拼装）                            | `db.execute_sql` | ⚠️ 不在用户路径，需单独约束/审计            |
| 7 | 评测 / CLI        | `evaluation/runner.py`、`eval/run_eval.py`、`main.py` | 走 `agent.ask`                           | 同 1~4            | ✅ 同一路径                        |
| 8 | 运维脚本            | `ck_check.py`、`check1.py`、`admin.py check-acl`      | —                                       | 直连/只读检查          | 非服务运行路径                       |

**关键事实**：`agent.py` 内所有数据库执行都收口到 `_exec_sql`（`test_gate` 有静态检查禁止裸 `db.execute_sql`），但 `_exec_sql` 只做“拒答/非查询”判定，**不做权限与口径校验**；校验分布在调用点，因此“新增一个调用点但忘了校验”仍会发生 —— 本次路径 3 就是实例。

---

## 4. 闸门覆盖矩阵

| 路径       | sql_guard | check_tables | validate_sql | 执行 SQL = 校验 SQL |
| -------- | --------- | ------------ | ------------ | --------------- |
| 1 首次生成   | ✅         | ✅            | ✅            | ✅               |
| 2 校验自愈   | ✅         | ✅            | ✅            | ✅               |
| 3 执行自愈   | ❌         | ❌            | ❌            | ❌               |
| 4 Legacy | ✅         | ✅            | —（设计如此）      | ✅               |

---

## 5. 已复现问题（修改前证据）

### P0-A：执行失败自愈绕过全部校验

复现方式：桩替换 LLM/DB，首次 SQL 合法但执行报错，自愈返回未授权表。

```
LLM calls: 4 | check calls: 1 | exec calls: 2
executed SQL:
  SELECT count(*) FROM ctc_data_warehouse.ods_valet_order_all
  SELECT * FROM ctc_data_warehouse.secret_table
ok: True
```

**结论**：`check_tables` 只被调用 1 次（首次）；自愈 SQL 未经校验即执行，且返回 `ok=True`。

### P0-B：表名解析失败 fail-open

```
A. sqlglot 不可用 + 反引号表名
   SELECT * FROM `secret_table` → (True, [])      ← 放行
   SELECT * FROM secret_table   → (False, [...])  ← 正常拦截

B. 解析器抛错 + 反引号表名（正则抓不到）
   SELECT * FROM `secret_table` → (True, [])      ← 放行
```

**结论**：`extract_tables_detail` 只返回“方式说明”字符串，调用方无法区分“解析成功”与“降级/失败”；正则抓不到反引号标识符时返回空集合，被当作“没有越权表”。

### P0-C：前导注释让整轮校验被跳过

```
sql = "-- 正常取数\nSELECT * FROM `secret_table`"
validate_sql(...) → []
```

**结论**：`validate_sql` 的 `if not sql or sql.startswith("--"): return issues` 是“返回空 = 通过”，导致 P0/P1/P2/P3/Metric 全部不执行。

### P0-D：实体主表未并入 P3（取数准确性）

```
P2: ['司机信息表']
P3: [...6 张费用/流水表...]   ← 无 ods_tu_driver_all
all_table_names: 同上
```

**结论**：`SemanticContext.all_table_names()` 对 `p2_entities` 二次解包（`e.get("raw")`），而 `p2_entities` 存的就是实体本身，`table_name` 在顶层 → 永远取到 `None`。

### P0-E：校验器异常被吞（静态确认）

- `_check_p3` 捕获 `guard.extract_tables` 异常后 `return`，不产生任何 issue；
- `_check_columns` 在 AST 解析失败时 `return`，不记录降级原因；
- 全流程没有 `passed/failed/error/degraded` 状态，调用方只能看到 issues 列表。

---

## 6. 静态风险与待业务确认（未复现为事故）

| 编号  | 事项                                         | 现状                                      | 判定                                |
| --- | ------------------------------------------ | --------------------------------------- | --------------------------------- |
| S-1 | `LEGACY_FALLBACK_MODE=always`              | 任意异常都回退 legacy                          | 静态风险；当前 `.env` 未设置，默认 `whitelist` |
| S-2 | `/api/status` 普通用户可见                       | `allowed_tables` 全量返回                   | 已确认代码路径，属信息暴露                     |
| S-3 | 默认管理员 `admin/admin123`                     | 硬编码、无强制改密                               | 已确认；当前库已有 admin/liuhao 两个账号       |
| S-4 | `ACL_DEFAULT_VISIBLE` 代码默认 `true`          | 当前 `.env` 显式 `false`                    | 新部署风险，非当前环境风险                     |
| S-5 | `sql_guard` 未拦 `INTO OUTFILE` / `OPTIMIZE` | 关键词表缺项                                  | 静态风险；只读账号是外部防线                    |
| S-6 | 字段注释歧义                                     | 司机表 `extra_information_fee` 是否“提升比例”未确认 | 待业务确认，不得擅自认定                      |

---

## 7. 数据契约问题

1. **实体两种形态并存**：`index["entities"]` 是包装对象 `{name, keywords, doc, raw}`，`p2_entities` 是裸实体；两处都用变量名 `e`，无类型约束。
2. **列清单可能缺失**：`_check_columns` 在 `ctx.p3_table_objs` 无 `column_list` 时直接跳过（老索引/异常索引），无告警。
3. **索引缓存**：`index["_semantic"]` 在进程内缓存；知识库修改后若无重建，实体/字段变更不生效（需确认 `/api/index/rebuild` 与写操作的联动）。
4. **校验结果无状态**：只有 issues 列表，无法区分“通过”“失败”“校验器异常”“降级”。

---

## 8. 测试覆盖现状

| 测试                 | 层级          | 覆盖                                    | 缺口                           |
| ------------------ | ----------- | ------------------------------------- | ---------------------------- |
| `test_gate.py`     | unit        | 校验 error 阻断、fail-closed 降级、指标硬约束、静态检查 | 无“自愈 SQL 重校验”、无“实体主表进 P3”    |
| `test_guard.py`    | unit        | 反引号、CTE、多语句、注释、权限                     | 无“解析器不可用/抛错”的 fail-closed 用例 |
| `test_retrieve.py` | unit        | 检索/路由                                 | 无实体→主表合并断言                   |
| `test_acl.py`      | config      | ACL 读写与默认策略                           | 无 `/api/status` 暴露面测试        |
| `test_pipeline.py` | unit        | 流水线接线                                 | 无自愈路径                        |
| `test_gaps.py`     | integration | 缺口扫描                                  | 依赖真实库                        |



---

## 9. 分阶段实施计划

| 阶段           | 内容                                                | 验收                                             |
| ------------ | ------------------------------------------------- | ---------------------------------------------- |
| **S1 安全闸门**  | 统一执行前置校验；自愈必重校验；表名解析 fail-closed；校验器状态化；前导注释修复    | ✅ 已完成，见 `stage1-02-S1整改报告.md`（test_gate 68/68） |
| **S2 降级与配置** | `always` 生产禁用；`/api/status` 收口；ACL 默认值；高危子句；默认管理员 | ✅ 已完成，见 `stage1-03-S2整改报告.md`（test_gate 95/95） |
| **S3 数据契约**  | 统一实体形态；`table_of()` 单点；Schema 校验；实体回归 | ✅ 已完成，见 `stage1-04-S3整改报告.md`（test_gate 103/103） |
| **S4 检索准确性** | 表 keywords 补字段/别名；指标降噪；真实案例对比 | ✅ 已完成，见 `stage1-05-S4整改报告.md`（test_gate 122/122） |
| **S5 口径与评测** | 信息费比例口径、Golden 集、监控指标                             | 口径待确认项标记；回归集可重复                                |

> 本轮先执行 S1；每完成一个阶段单独跑测试并记录，不跨阶段合并验证。
