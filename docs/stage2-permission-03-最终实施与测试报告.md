# 阶段 2：企业数据权限引擎 · 最终实施与测试报告

> 时间：2026-10-21
> 前置文档：`stage2-permission-01-源码审查与迁移设计.md`（审查 + P0 计划 + 迁移设计）、`stage2-permission-02-操作手册.md`（操作 + 部署/回滚）

---

## 1. 改造前后对比

### 1.1 改造前（只读代码核实）

| 维度 | 现状 |
|---|---|
| 角色 | 仅 `admin`/`user` 两种（`users.role` 字段），无自定义角色、无绑定表 |
| 表级 | `table_acl` 表白名单 + `guard.check_tables_ex`（S1-S4 已 fail-closed） |
| 字段级 | **无**（只有 `sql_validator._check_columns` 做"列是否存在"，无 ALLOW/MASK/DENY） |
| 导出 | 仅 `_can_view`（本人/管理员），无导出权限、无字段级控制、不重校验 |
| 历史 | 仅 `_can_view`，权限收回后仍可读完整历史 |
| 行级 | **无** |
| 审计 | **无** |
| AI 配置 | **无** |

### 1.2 改造后

新增独立权限引擎 `permission.py`，覆盖：角色/数据域/敏感资源/字段策略（ALLOW/MASK/DENY）/行级改写/结果脱敏/导出鉴权/历史重校验/审计/AI 草拟，全部由服务端确定性执行。

---

## 2. 新增 / 修改文件清单

| 文件 | 改动 | 原因 |
|---|---|---|
| `permission.py`（新） | 统一权限引擎：归一化 / 脱敏 / 策略合并 / AST 字段级拦截 / 行级改写 / 结果脱敏 / 审计 / AI NL 解析 | 与 AI 生成逻辑解耦，唯一字段级权限权威来源 |
| `config_store.py` | 新增 10 张表 + CRUD；`users` 增 `user_attributes`；`metrics` 增 `forbidden_predicates` | 权限体系持久化 + 行级属性 + 负向约束 |
| `agent.py` | `ask/ask_v2/ask_legacy` 增 `user` 参数；`_pre_execute_check` 增字段级闸门 + 行级改写重校验；执行后 `mask_rows` 脱敏 | 所有执行路径统一过闸，执行 SQL == 校验 SQL |
| `server.py` | `/api/ask` 传可信用户 + 审计；历史/导出按当前权限重校验；新增 `/api/permission/*` 20 个接口 | 服务端鉴权 + 管理界面后端 |
| `sql_validator.py` | `_check_p1` 增负向约束 `forbidden_predicates` | 拦"发单量≠完单量"这类口径幻觉 |
| `ssh_tunnel.py` | `_ck_ping` + 假死自动重建 + 15s 节流 | 隧道假死自动恢复（真实事故） |
| `frontend/index.html` | 新增「数据权限」导航 + Tab7（AI 助手/角色/数据域/敏感字段/策略/行级/审计） | 管理员配置界面 |
| `serve.sh` | 支持 `--reload`（默认关闭） | 开发热重载 |
| `test_permission.py`（新） | 60 项权限专项测试 | 覆盖任务书 30 项 |
| `run_tests.py` | 纳入 `test_permission` | 分层回归 |

---

## 3. 新增数据表（10 张，增量迁移）

`roles` / `user_role_bindings` / `data_domains` / `sensitive_resources` / `permission_policies` / `permission_policy_versions` / `permission_approvals` / `permission_audit_logs` / `ai_policy_drafts` / `row_permission_rules`

迁移方式：`CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` + `ALTER TABLE ADD COLUMN`（幂等、只增不删、内置角色回填、老用户角色回填）。

---

## 4. 权限判定流程 + SQL 拦截流程

```
POST /api/ask
  → 中间件：从 sessions 取可信用户（不信任前端 role）
  → agent.ask(index, question, user)
  → ask_v2 / ask_legacy（Legacy 同闸门）
  → _pre_execute_check（唯一执行前闸门）
      ① guard.sql_guard        只读 + 高危子句
      ② guard.check_tables_ex  表级白名单（fail-closed）
      ③ sql_validator          P0/P1/P2/P3/Metric + forbidden 负向约束
      ④ permission.evaluate    字段级 AST：SELECT* 展开、别名/CTE/子查询/UNION/表达式
                                DENY → 拒绝；MASK 输出列 → 脱敏；MASK 在 WHERE/JOIN/表达式 → 拒绝
      ⑤ permission.apply_row_filters  行级 AST 改写（基表包成过滤子查询）
         └ 改写后 → 重新过 ① ② ④（执行 SQL == 校验 SQL）
  → _exec_sql（唯一执行出口）
  → permission.mask_rows（执行后脱敏，返回/快照/导出前）
  → 审计（不落敏感原始值）
```

---

## 5. 30 项测试执行结果（对照任务书）

| # | 场景 | 结果 | 测试定位 |
|---|---|---|---|
| 1 | 管理员配置普通用户不能查看司机完整手机号 | ✅ | E（upsert DENY 策略）|
| 2 | 普通用户查询司机列表，完整手机号不出现在响应 | ✅ | A2 + 真实库实测 |
| 3 | 允许脱敏手机号，响应与导出只含脱敏值 | ✅ | A3（MASK+mask_rows）+ 真实库 `156****8565` |
| 4 | 普通用户不能查询资金流水明细 | ✅ | A4（整表 DENY）|
| 5 | 被授权经营汇总指标仍可查询 | ✅ | A5（ALLOW）|
| 6 | 无关业务查询不受影响 | ✅ | A6 |
| 7 | `SELECT *` 含未授权敏感字段 | ✅ | B7 |
| 8 | 别名/表别名/CTE/子查询绕过 | ✅ | B8a-d |
| 9 | substring/concat/CASE WHEN 绕过 | ✅ | B9a-d |
| 10 | WHERE/GROUP BY/ORDER BY/JOIN/聚合间接访问 | ✅ | B10a-c（WHERE 拒；GROUP/ORDER 展示维度脱敏放行）|
| 11 | UNION 绕过 | ✅ | B11 |
| 12 | 解析失败不得执行 | ✅ | B12 |
| 13 | 权限配置库/引擎异常不得放行 | ✅ | B13（monkeypatch 抛异常 → 不静默放行）|
| 14 | 伪造前端 JS/请求参数不得提权 | ✅ | B14（user_roles 只取可信来源）+ server sessions 鉴权 |
| 15 | 自愈为越权 SQL 不得执行 | ✅ | test_gate `test_repair_must_revalidate` + C15 |
| 16 | Legacy 路径同样校验 | ✅ | C16（ask_legacy 字段级 DENY 拦截）|
| 17 | `always` 降级不能绕过 | ✅ | test_gate `test_production_blocks_always_fallback` |
| 18 | 所有执行调用受统一闸门保护 | ✅ | test_gate 静态检查（`db.execute_sql` 只在 `_exec_sql` 内）|
| 19 | 改写后重新校验，报告与实际 SQL 一致 | ✅ | C19（行级改写 final_sql 含过滤 + rewritten=True）|
| 20 | 无导出权限不能导出 | ✅ | D20 |
| 21 | 直接请求导出不能绕过 | ✅ | D21（query 允许 ≠ export 允许）|
| 22 | 撤销权限后历史不返回原始值 | ✅ | D22 |
| 23 | 权限变更后立即采用新规则 | ✅ | D23 |
| 24 | 审计不含敏感原始值 | ✅ | D24（`_safe_sql_repr` 去字面量）|
| 25 | AI 生成规则不自动生效 | ✅ | E25 |
| 26 | 未经管理员确认不能发布 | ✅ | E25 + server apply 接口（ambiguity 时 400）|
| 27 | 模糊字段映射必须要求确认 | ✅ | E27 |
| 28 | 规则冲突不能默认放行 | ✅ | E28（DENY 优先；敏感无授权 fail-closed）|
| 29 | 规则修改/停用/回滚有审计 | ✅ | E29（版本表 before/after 快照）|
| 30 | 原有账号/知识/指标/SQL 校验/历史测试继续通过 | ✅ | 全量回归 |

**权限专项测试总计 60 项（A~F 组 + P3），全部通过。**

---

## 6. 原有测试回归结果

| 层 | 结果 |
|---|---|
| unit | `test_gate` **122/122**、`test_imports` 26、`test_pipeline` 11、`test_join_suggest` 10、`test_guard`、`test_labels` 33、`test_retrieve` 22、`test_calendar`、`test_permission` **60** |
| config | `test_acl` **84/84**（生产库副本）|
| integration | `test_gaps` **51/51**（真实 ClickHouse）|

真实库验证：`dwd_driver_base_profile_all.phone` DENY 拒绝 / SELECT * 拒绝 / MASK 脱敏 `156****8565` / 别名绕过拒绝。

---

## 7. 剩余风险与说明

1. **`CK_USER=<只读账号名>` 非只读账号**：代码层 `sql_guard` 已拦截写操作作为兜底，但纵深防御建议生产改只读账号 `ai_reader`（代码无法替你验证数据库账号权限）。
2. **MASK 位置策略是"保守+实用"的折中**：GROUP BY/ORDER BY 作为展示维度放行脱敏，WHERE/JOIN/表达式拒绝。若业务要求"数据库端不返回明文"，需进一步做 SQL 改写（`substring` 掩码），风险更高，当前未做。
3. **敏感字段名兜底是启发式**：`phone`/`mobile`/`id_card`/`bank_card` 后缀匹配 + 数字后缀，豁免 `phone_brand`/`phone_model` 等设备属性。极少数特殊命名可能漏判/误判，需靠管理员显式登记敏感资源覆盖。
4. **AI 助手解析依赖 LLM**：`parse_nl_policy` 已做资源映射/角色校验 + 歧义拦截，但解析质量建议在真实环境补 Golden 校准。
5. **launchd 在当前环境 bootstrap 报错**（`Input/output error`，原始 plist 也复现），属环境层限制；plist 已配 `--reload`，下次登录/重启电脑自动生效。当前服务由会话托管运行。
6. **行级权限为 v1**：基表包成 `FROM (SELECT * FROM t WHERE filter) AS alias` 的 AST 改写，已重新校验；但极其复杂的 SQL（多表子查询嵌套 + 行级）建议灰度观察。

---

## 8. 关键安全约定（勿破坏）

1. 权限判断全部服务端确定性执行，不靠前端隐藏 / Prompt / 表级白名单代替字段级。
2. 解析失败 / 元数据缺失 / 引擎异常 / 策略加载失败 → 拒绝敏感查询（fail-closed）。
3. AI 只产 `pending` 草稿；有歧义禁止发布；`mapping_status=pending` 不参与生效判定。
4. 脱敏规则幂等，支持权限收紧后历史重校验再脱敏。
5. 行级过滤条件来自管理员可信模板 + 已验证用户属性，占位符值做注入字符校验，缺失即拒绝。
6. **改完 Python 代码必须重启服务**（现已开 `--reload` 自动重载）。
