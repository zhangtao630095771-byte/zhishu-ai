# S2 整改报告：降级策略、状态接口、ACL 默认值与账号安全

> 时间：2026-10-19
> 阶段：S2（任务 4~5）
> 原则：先复现、再修复、再回归；每项都有修改前后证据。

---

## 1. 结论

| 编号 | 事项 | 修改前 | 修改后 |
|---|---|---|---|
| S2-A | 生产环境 `LEGACY_FALLBACK_MODE=always` | 业务异常也回退 legacy，`ok=True` | 生产强制收敛为 whitelist，fail-closed |
| S2-B | `/api/status` 表清单 | 所有登录用户都能拿到 `allowed_tables` | 仅管理员可见，普通用户只给数量 |
| S2-C | `ACL_DEFAULT_VISIBLE` 代码默认 | 默认 `true`（黑名单，新表自动暴露） | 生产默认 `false`，开发/测试默认 `true` |
| S2-D | 高危 SQL 子句 | `INTO OUTFILE` / `SETTINGS` 直接放行 | 全部拦截 |
| S2-E | 默认管理员 | 硬编码 `admin123`，无强制改密 | 环境变量/随机初始密码 + 首次登录强制改密 + 自助改密 |

测试：`test_gate.py` 68 → **95**，全量 unit/config/integration 全绿；真实服务端到端验证通过。

---

## 2. 逐项证据

### S2-A：生产环境禁止 always 降级

**修改前**

```
config.APP_ENV=production, LEGACY_FALLBACK_MODE=always
ask_v2 抛 ValueError（业务逻辑错误）
→ legacy calls: 1, ok: True
→ 日志还错误地写成"V2 可恢复故障"
```

**根因**：`agent.ask` 的 `allow = (mode == "always") or ...` 对任何异常都成立，且日志不区分"可恢复"与"被 always 强制"。

**修改后**

```
production + always + ValueError
→ ⛔ 生产环境禁止 LEGACY_FALLBACK_MODE=always，已按 whitelist 处理
→ legacy calls: 0, ok: False
```

开发环境仍允许 `always`（对照用例验证），并在结果里增加 `degrade_kind=forced`。

**用例**：`test_gate.py::test_production_blocks_always_fallback`（4 项）。

---

### S2-B：`/api/status` 表清单收口

**修改前**：`api_status()` 不接收 `Request`，无条件返回 `allowed_tables`；普通用户可拿到完整表名清单（可探测敏感表）。

**修改后**：

```python
def _status_payload(index, user):
    is_admin = bool(user and user.get("role") == "admin")
    payload = {... "allowed_table_count": len(index["allowed_tables"]) ...}
    if is_admin:
        payload["allowed_tables"] = index["allowed_tables"]
    return payload
```

端到端（临时账号，测完已删除）：

```
login: 200 | must_change: True
before change /api/status: 403 | must_change_password
change password: 200
after change /api/status: 200 | allowed_tables in payload: False
```

管理员仍可见清单：`status keys: True | count: 30`。

**用例**：`test_gate.py::test_status_hides_tables_for_non_admin`（4 项）。

---

### S2-C：ACL 默认策略

**修改前**：`os.getenv("ACL_DEFAULT_VISIBLE", "true")` —— 新部署漏配时默认"全部可见"。

**修改后**：

```python
def _default_acl_visible(app_env):
    return app_env != "production"

ACL_DEFAULT_VISIBLE = _env_bool("ACL_DEFAULT_VISIBLE", _default_acl_visible(APP_ENV))
```

- 生产：默认白名单（拒绝），新增表不会自动暴露；
- 开发/测试：默认黑名单，避免本地 demo 一张表都看不见；
- 当前 `.env` 已显式 `ACL_DEFAULT_VISIBLE=false`，行为不变。

**用例**：`test_gate.py::test_acl_default_by_env`（3 项）。

---

### S2-D：高危 SQL 子句

**修改前**

```
SELECT * FROM t INTO OUTFILE '/tmp/x'      → (True, '通过')
SELECT * FROM t SETTINGS max_threads=1     → (True, '通过')
```

**修改后**：新增 `FORBIDDEN_PATTERNS`，在去注释后的 SQL 上匹配：

| 模式 | 说明 |
|---|---|
| `INTO OUTFILE` / `INTO DUMPFILE` | 防结果落盘 |
| `OPTIMIZE TABLE` | 防重写表 |
| `SYSTEM FLUSH/RELOAD/DROP/SHUTDOWN/KILL/RESTART` | 防管理命令 |
| `RENAME TABLE` / `EXCHANGE TABLES` | 防换表 |
| `SETTINGS` | 执行参数必须由系统统一注入 |

**用例**：`test_gate.py::test_sql_guard_dangerous_clauses`（5 项）。

---

### S2-E：默认管理员与强制改密

**修改前**

```python
DEFAULT_ADMIN = {"username": "admin", "password": "admin123", ...}
# 首次建库直接写入，日志打印明文；无 must_change 字段
```

**修改后**

1. `DEFAULT_ADMIN` 不再含 `password`；初始密码取 `ADMIN_INITIAL_PASSWORD`，未配置则随机生成（仅首次日志打印一次）。
2. `users` 新增 `must_change_password`，老库自动 `ALTER TABLE` 迁移。
3. 新建账号、管理员重置密码、老库 `admin123` 迁移，均标记 `must_change_password=1`。
4. 中间件：未改密时只允许 `/api/auth/me`、`/api/auth/logout`、`/api/me/password`，其余接口 403。
5. 新增 `POST /api/me/password`：校验原密码 → 更新 → 清除标记。
6. 前端新增强制改密页；登录/刷新时检测标记，改密成功后才进入主界面。

**端到端验证**

```
login: 200 | must_change: True
before change /api/ask: 403 {"type":"must_change_password"}
change password: 200
after change /api/status: 200
```

**用例**：`test_gate.py::test_password_change_flow`（11 项，含临时库迁移）。

**⚠️ 当前环境行为变化**：现有 `admin` 仍在使用 `admin123`，服务重启后已被标记为必须改密。
下次登录会直接进入"修改密码"页；改密前 `/api/ask` 返回 403。这是本次安全整改的预期行为。

---

## 3. 修改清单

| 文件 | 改动 |
|---|---|
| `agent.py` | `ask()` 生产环境禁止 always；日志区分 recoverable/forced；新增 `degrade_kind` |
| `server.py` | `_status_payload()`；`/api/status` 接收 Request；`/api/me/password`；中间件强制改密闸门；`PasswordChangeBody` |
| `config.py` | `_env_bool` / `_default_acl_visible`；ACL 默认按环境；`APP_ENV`、`TABLE_PARSE_STRICT` |
| `guard.py` | `FORBIDDEN_PATTERNS`：导出/系统命令/SETTINGS |
| `config_store.py` | `must_change_password` 建表+迁移；初始密码随机/环境变量；`change_own_password`；`flag_default_password_admins`；重置密码标记改密 |
| `frontend/index.html` | 强制改密页、登录/刷新分支、403 拦截、自助改密提交 |
| `test_gate.py` | 新增 S2 五组用例（27 项） |
| `deploy.md`、`docs/zhishu-ai-packaging.md` | 更新默认管理员说明 |

---

## 4. 测试结果

| 测试 | S1 后 | S2 后 |
|---|---|---|
| `test_gate.py` | 68/68 | **95/95** |
| `test_imports.py` | 26/26 | 26/26 |
| `test_pipeline.py` | 11/11 | 11/11 |
| `test_guard.py` | 通过 | 通过 |
| `test_labels.py` | 33/33 | 33/33 |
| `test_retrieve.py` | 22/22 | 22/22 |
| `test_acl.py` | 83/83 | 83/83 |
| `test_gaps.py` | 51/51 | 51/51 |

真实服务：`/api/ping` 200；admin 登录返回 `must_change=True`；`/api/ask` 在改密前 403、改密后 200。

---

## 5. 兼容性与风险

**兼容**
- 旧接口路径不变；`/api/status` 对管理员返回字段不变，普通用户少了 `allowed_tables`。
- 旧库自动补列，不重建表、不丢账号。
- `DEFAULT_ADMIN["password"]` 已移除；代码内无其他引用（已 grep 确认）。

**风险 / 未完成**
| 项 | 状态 | 说明 |
|---|---|---|
| 现有 admin 强制改密 | ✅ 已生效 | 下次登录需改密；可用 `ADMIN_FORCE_CHANGE=0` 关闭（不推荐） |
| ClickHouse 只读账号 | ⚠️ 部署项 | 代码无法验证；需确认 `CK_USER` 不是写账号 |
| 错误/日志敏感信息全量审计 | ⚠️ 未做 | 抽查未见口令/密钥输出，但未逐条审计所有异常分支 |
| 资源上限 | ✅ 已有 | `MAX_QUERY_MEMORY` / `MAX_QUERY_TIMEOUT` + `_ensure_limit` |
| 实体主表合并 | ❌ S3 | 司机表问题仍未修 |
| 知识治理/Golden | ❌ S4/S5 | 未开始 |

---

## 6. 下一步（S3）

1. 统一实体数据契约（`raw` vs 包装对象），提供 `table_of(entity)` 单点；
2. 修 `all_table_names()` 主表合并；
3. 补“实体命中 → 主表进 P3 → 字段进 Prompt”的集成测试；
4. 用司机问题做修复前后对比，确认 `ods_tu_driver_all` 进入上下文。
