# 数据权限引擎 · 操作手册与部署 / 回滚方案

> 适用版本：2026-10-21 阶段 2（P0+P1+P2+P3 行级）
> 面向：管理员（权限配置、发布、验证、回滚）

---

## 一、验证环境准备（测试角色 → 敏感字段 → 策略 → 验证拒绝）

下面全部操作在「数据权限」页（左侧导航「系统管理 → 数据权限」）或后端接口完成。建议先用一个**测试库**验证，不要直接在生产库上做破坏性测试。

### 1. 创建测试角色 + 绑定用户

1. 进入「数据权限 → 角色与用户」。
2. 角色名填 `staff`、显示名 `普通员工`，点「新建角色」。
3. 在「用户角色绑定」选择测试用户 `zhangsan`，勾选 `staff`，保存。

等价接口：

```bash
curl -X POST http://127.0.0.1:8000/api/permission/roles \
  -H "Authorization: Bearer <admin_token>" -H "Content-Type: application/json" \
  -d '{"display_name":"普通员工","description":"测试"}'
```

### 2. 登记数据域 + 敏感字段映射

「数据权限 → 数据域与敏感字段」：

1. 数据域标识 `driver`、显示名 `司机信息`，保存。
2. 敏感字段：域 `driver`、表 `ods_tu_driver_all`、字段 `mobile_phone`、等级 `high`，点「登记敏感字段」。
   - **映射状态必须是 `confirmed` 才参与生效判定**；AI 推荐的候选是 `pending`，需管理员确认。

### 3. 配置字段访问策略（发布即生效）

「数据权限 → 字段访问策略」：

| 场景 | 主体 | 表 | 字段 | 动作 | 级别 | 脱敏规则 |
|---|---|---|---|---|---|---|
| 只能看脱敏手机号 | role:staff | ods_tu_driver_all | mobile_phone | 查询 | MASK | phone |
| 禁止导出完整手机号 | role:staff | ods_tu_driver_all | mobile_phone | 导出 | DENY | — |
| 禁止看资金流水明细 | role:staff | ods_tu_record_all | *（整表） | both | DENY | — |

规则优先级内置：**DENY 恒优先于 MASK / ALLOW**；多角色取最小权限；敏感字段无任何授权 → 默认 DENY（fail-closed）。

### 4. 行级权限（可选）

1. 给用户设置已验证属性：`PUT /api/users/zhangsan/attributes` body `{"attributes":{"dept_id":"D001"}}`（仅管理员可设，用户不能自填）。
2. 行级规则：`PUT /api/permission/row-rules` body：
   `{"subject_type":"role","subject_name":"staff","table_name":"ods_tu_driver_all","filter_sql":"dept_id = '{dept_id}'"}`。
3. 效果：该角色用户查询 `ods_tu_driver_all` 时，SQL 被自动改写为
   `FROM (SELECT * FROM ods_tu_driver_all WHERE dept_id = 'D001')`，只能看到本部门数据。

### 5. 验证拒绝结果

用 `staff` 用户登录，直接构造 SQL 问数（例如在「智能取数」问“查司机手机号”）：

- **DENY 字段**：应返回“字段级权限拦截…命中 DENY”，且不执行、不返回任何手机号。
- **MASK 字段**：可执行，但返回 `138****1234` 形式，导出同样只含脱敏值。
- **资金流水明细**：返回“命中 DENY，已拒绝”。
- **行级**：结果只含 `dept_id='D001'` 的数据（观察最终 SQL 已含 `WHERE dept_id = 'D001'`）。
- **历史/导出绕过**：权限收紧后，再访问旧历史记录，DENY 字段的数据不再返回；导出直接 403。

普通用户收到的是通用提示（“当前账号没有查看该类敏感信息的权限…”），不会看到内部表结构 / 字段明细 / 未授权 SQL。

---

## 二、AI 自然语言配置助手

1. 「数据权限 → AI 配置助手」输入：
   “普通员工不允许查看司机和客户的完整手机号，可以查看脱敏手机号；不允许导出完整手机号；不能查看资金流水明细，但可以查看授权的经营汇总指标。”
2. 点「解析为草稿」，系统调 LLM 解析成结构化规则并做**资源映射校验**：
   - 表/字段不在敏感清单、角色不在角色表 → 进入「歧义」列表，**不允许直接发布**。
3. 无歧义时点「确认并发布」，规则才真正写入 `permission_policies` 并生效。
4. 有歧义时必须先补齐敏感字段映射或角色，再发布。

> AI 草稿**永不自动生效**；发布前必须管理员确认。

---

## 三、部署 / 备份 / 回滚 / 权限恢复

### 1. 部署前备份（必做）

```bash
cd /Users/zhangtao/Downloads/autosql/zhishu-ai
cp agent_config.db agent_config.db.bak-$(date +%Y%m%d-%H%M%S)
```

### 2. 数据库迁移

迁移是**幂等**的：服务启动时 `config_store.init_store()` 自动执行 `_migrate_permission_tables`，
只 `CREATE TABLE IF NOT EXISTS` 新增 10 张权限表，**不删除、不重建**现有表（users/sessions/table_acl/query_records 等一律不动）。

也可在隔离副本上先行验证：

```bash
cp agent_config.db /tmp/migrate_test.db
CONFIG_DB=/tmp/migrate_test.db .venv/bin/python -c \
  "import config_store; print(config_store.migrate_permission_schema())"
# 期望 {"ok": true, "tables": {... 全部 true ...}}
```

### 3. 回滚

- 迁移只加表，**不回滚也不影响旧代码**：老版本代码不读这些新表，照常运行。
- 代码回滚：`git revert` 对应提交即可；新增配置默认更安全，无破坏性默认值。
- 数据回滚：恢复 `agent_config.db.bak-*` 备份文件。

### 4. 权限恢复（误配导致锁死时的应急）

1. **管理员被锁**：内置 `admin` 是受审计的超级用户，字段级/行级规则不影响其访问；若账号本身异常，可用备份恢复 `users` 表。
2. **误配 DENY 导致某角色全部不可查**：停用该策略即可（`POST /api/permission/policies/{id}/enabled` body `{"enabled":0}`），策略版本表会记录停用动作，可追溯。
3. **整表 DENY 误伤**：删除/停用对应 `column_name='*'` 的策略。
4. **规则冲突**：系统内置 DENY 优先，不会默认放行；冲突无法确定时对敏感数据拒绝并记录原因（见审计）。

### 5. 审计查看

- 「数据权限 → 权限审计」查看每次判定的用户、角色、涉及表/列、判定结果（allow/deny/mask）、拒绝原因、是否改写、最终执行 SQL（已脱敏字面量）。
- 普通用户**不能**修改或删除审计日志（接口默认 admin）。
- 审计日志**不落敏感原始值**（手机号/银行卡等以 `?` 替换，脱敏列只记规则名）。

---

## 四、关键安全约定（勿破坏）

1. 权限判断全部服务端确定性执行；不靠前端隐藏、不靠 Prompt、不靠表级白名单代替字段级。
2. 解析失败 / 元数据缺失 / 引擎异常 / 策略加载失败 → **拒绝敏感查询**（fail-closed），绝不放行。
3. AI 只产 `pending` 草稿；有歧义禁止发布；`mapping_status=pending` 的敏感字段不参与生效判定。
4. 脱敏规则幂等（phone/id_card/bank_card/name），支持权限收紧后历史重校验再脱敏。
5. 行级过滤条件来自管理员定义的可信模板 + 已验证用户属性，占位符值做注入字符校验，缺失即拒绝。
