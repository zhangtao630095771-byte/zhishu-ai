# S1 整改报告：SQL 安全执行闸门

> 时间：2026-10-19
> 阶段：S1（任务 1~3 的核心部分）
> 原则：先复现、再修复、再回归；未运行验证的不标记为完成。

---

## 1. 结论

S1 范围内 3 个已复现的 P0 绕过全部封堵：

| 编号 | 问题 | 修改前 | 修改后 |
|---|---|---|---|
| S1-A | 执行失败自愈的 SQL 不再校验 | 未授权表被执行，返回 `ok=True` | 重新过闸门，拒绝执行，`validation_status=blocked` |
| S1-B | 表名解析失败 fail-open | 反引号表名 + 解析失败 → 放行 | 解析不可信 → 拒绝（默认 strict） |
| S1-C | 前导注释跳过全部校验 / 校验器异常被吞 | 返回空报告 = 通过 | 前导注释照常校验；异常 → `status=error` + error 级 issue |

真实服务已重启验证：`/api/ask` 正常取数返回 `ok=true, pipeline=v2, validation_status=passed`。

---

## 2. 修改清单

| 文件 | 函数/位置 | 改动 |
|---|---|---|
| `guard.py` | `extract_tables_ex`（新增，344） | 返回 `parsed / degraded / failed` 三态与原因 |
| `guard.py` | `check_tables_ex`（新增，392） | 解析不可信时 fail-closed；带 `message` |
| `guard.py` | `extract_tables` / `extract_tables_detail` / `check_tables` | 保留旧签名，内部改走 ex 版本 |
| `sql_validator.py` | `validate_sql_ex`（新增，344） | 返回 `issues/status/degraded/error` 四态 |
| `sql_validator.py` | `_check_p3` | 不再吞 `extract_tables` 异常（上抛，由 ex 记 error） |
| `sql_validator.py` | `_check_columns` | 列清单缺失/AST 解析失败 → 记 warning 降级，不再静默 |
| `sql_validator.py` | `validate_sql` | 兼容旧接口，内部调用 ex，去掉 `startswith("--")` 整体跳过 |
| `agent.py` | `_pre_execute_check`（新增，429） | 只读 + 表权限 + 五维校验的唯一入口 |
| `agent.py` | `ask_v2` 校验循环（569） | 改用统一闸门；`validation_status` 落到结果 |
| `agent.py` | `ask_v2` 执行自愈（639） | **自愈 SQL 重新过完整闸门**（本次核心修复） |
| `agent.py` | `ask_legacy`（原 368） | 改用 `check_tables_ex`，拿到 fail-closed 的具体原因 |
| `config.py` | 152/158 | 新增 `APP_ENV`、`TABLE_PARSE_STRICT`（默认 True） |
| `server.py` | `/api/ask` payload | 透出 `validation_status` |
| `test_gate.py` | 新增 3 组用例 + 修 `_set` 恢复缺陷 | 68 项断言 |

---

## 3. 逐项证据

### S1-A：执行失败自愈绕过校验

**修改前复现**

```
LLM calls: 4 | check calls: 1 | exec calls: 2
executed SQL:
  SELECT count(*) FROM ctc_data_warehouse.ods_valet_order_all
  SELECT * FROM ctc_data_warehouse.secret_table
ok: True
```

**根因**：`agent.py` 执行失败自愈分支只做 `sql = _finalize_sql(...)`，随后直接 `_exec_sql(sql)`，没有重新调用 `sql_guard / check_tables / validate_sql`。

**修改后**

```
LLM calls: 2 | check calls: 2 | exec calls: 1
executed SQL: ['SELECT count(*) FROM ...ods_valet_order_all']
ok: False
error: SQL 引用了未授权的表：['secret_table']
validation_status: blocked
```

**回归用例**：`test_gate.py::test_repair_must_revalidate`（5 项断言）。

---

### S1-B：表名解析 fail-open

**修改前复现**

```
A. sqlglot 不可用 + 反引号
   SELECT * FROM `secret_table` → (True, [])
B. 解析器抛错 + 反引号
   SELECT * FROM `secret_table` → (True, [])
```

**根因**：`extract_tables_detail` 只返回 `(tables, 说明字符串)`；调用方无法区分“SQL 真的没表”和“解析失败没认出来”。正则又不认反引号，空集合被当成“无越权表”。

**修改后**

```
A → ok=False, status=failed
B → ok=False, status=failed
正常 SQL → ok=True, status=parsed
正常解析的越权表 → ok=False, illegal=['secret_table']
```

**回归用例**：`test_gate.py::test_table_parse_fail_closed`（8 项断言）。

**兼容开关**：`TABLE_PARSE_STRICT=0` 可恢复“允许正则兜底”（仅建议本地/测试）。

---

### S1-C：前导注释跳过校验 / 校验器异常被吞

**修改前复现**

```
sql = "-- 正常取数\nSELECT * FROM `secret_table`"
validate_sql(...) → []
```

**根因**：
- `validate_sql` 开头 `if not sql or sql.startswith("--"): return issues` —— 返回空列表即“通过”；
- `_check_p3` 捕获 `extract_tables` 异常后 `return`；
- `_check_columns` 解析失败/无列清单时静默跳过；
- 没有状态字段，调用方无法区分“通过”和“校验器崩了”。

**修改后**

```
前导注释 + 未授权表 → status=failed, issue=table_exist
空 SQL → status=failed
校验器抛异常 → status=error, issue=checker_error, _has_error=True
```

**回归用例**：`test_gate.py::test_validator_status_and_comment`（6 项断言）。

---

## 4. 测试结果（修改前后）

| 测试 | 修改前 | 修改后 |
|---|---|---|
| `test_gate.py` | 49/49 | **68/68** |
| `test_imports.py` | 26/26 | 26/26 |
| `test_pipeline.py` | 11/11 | 11/11 |
| `test_join_suggest.py` | 10/10 | 10/10 |
| `test_guard.py` | 全部通过 | 全部通过 |
| `test_labels.py` | 33/33 | 33/33 |
| `test_retrieve.py` | 22/22 | 22/22 |
| `test_acl.py`（config） | 83/83 | 83/83 |
| `test_gaps.py`（integration） | 51/51 | 51/51 |

运行命令：

```bash
cd /Users/zhangtao/Downloads/autosql/zhishu-ai
.venv/bin/python run_tests.py
```

真实链路（服务重启后）：

```json
{"ok": true, "pipeline": "v2", "validation_status": "passed", "attempts": 1}
```

---

## 5. 兼容性说明

1. `extract_tables()`、`extract_tables_detail()`、`check_tables()`、`validate_sql()` 全部保留原签名，旧调用与测试不受影响。
2. 新增 `*_ex()` 供安全关键路径使用；`check_tables()` 在 fail-closed 时把原因放进返回列表，避免静默。
3. `validate_sql()` 不再对 `--` 开头 SQL 直接返回空报告；空 SQL 现在会返回 error issue（更严格）。
4. `/api/ask` 新增 `validation_status` 字段，前端不依赖它，向后兼容。
5. `validate_sql_ex` 在缺少 `column_list` 时会多一条 warning（不阻断），前端校验详情可能多一行提示。

---

## 6. 已知风险与未完成项

| 项 | 状态 | 说明 |
|---|---|---|
| 解析降级一律拒绝 | ⚠️ 已启用 | 若 sqlglot 对某些 ClickHouse 方言解析失败，合法 SQL 也会被拒；线上需监控 `status=degraded/failed` 比例 |
| `LEGACY_FALLBACK_MODE=always` | ❌ 未修（S2） | 生产环境仍可能因任意异常降级到弱校验路径 |
| `/api/status` 表清单暴露 | ❌ 未修（S2） | 普通用户仍能拿到 `allowed_tables` |
| 默认管理员 `admin123` / 强制改密 | ❌ 未修（S2） | 无 `must_change_password` |
| `ACL_DEFAULT_VISIBLE` 默认 true | ❌ 未修（S2） | 当前 `.env` 已显式 false，本机安全 |
| `INTO OUTFILE` / `OPTIMIZE` 等高危子句 | ❌ 未修（S2） | 只读账号仍是外部防线 |
| 实体主表未并入 P3 | ❌ 未修（S3） | 取数准确性问题，独立回归 |

---

## 7. 下一步（S2）

1. `always` 模式在生产环境强制降级为 `whitelist`；
2. `/api/status` 对非管理员只返回数量；
3. `ACL_DEFAULT_VISIBLE` 默认改 false（配迁移说明）；
4. `sql_guard` 补 `INTO OUTFILE` / `OPTIMIZE` / `SYSTEM` 等高危写法；
5. 默认管理员改为环境变量/随机初始密码 + `must_change_password` + 自助改密接口；
6. 每项配套测试后再进入 S3（实体合并）。
