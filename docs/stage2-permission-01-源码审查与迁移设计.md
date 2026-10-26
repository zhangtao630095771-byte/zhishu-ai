# 阶段 2：企业数据权限引擎 · 源码审查 / P0 修复计划 / 数据库迁移设计

> 时间：2026-10-21
> 范围：`server.py` / `agent.py` / `guard.py` / `sql_validator.py` / `db.py` / `db_clickhouse.py` / `db_sqlite.py` / `config_store.py` / `rag.py` / `semantic_layer.py` / `gaps.py` / `exporter.py` / `frontend/index.html`
> 方法：逐文件读真实源码核对，不假设历史文档（`stage0`/`stage1-*`）与代码一致；所有结论以当前工作区代码为准。

---

## 0. 结论摘要

1. 上一轮（S1–S4）的 P0 修复**已真实落地**，代码与 `stage1-*` 报告一致：
   - `guard.check_tables_ex()` / `extract_tables_ex()` 三态（parsed/degraded/failed）+ 生产 fail-closed；
   - `sql_validator.validate_sql_ex()` 三态（passed/failed/error/degraded）+ 关键检查异常阻断；
   - `agent._pre_execute_check()` 统一执行前闸门；执行失败自愈 SQL **重新过完整闸门**；
   - Legacy 复用 `check_tables_ex`；生产环境禁止 `LEGACY_FALLBACK_MODE=always`；默认管理员随机化 + 强制改密；`/api/status` 表清单仅管理员可见。
2. 现状是**表级白名单 + 只读 + 列存在性校验**，缺的是本次要建的**字段级 / 角色级 / 导出级 / 行级**权限能力。
3. 用户问数的**唯一执行出口**是 `agent._exec_sql`（`agent.py:113`），`test_gate.py` 有静态契约守住「agent.py 内不得裸调 `db.execute_sql`」——这是本次权限引擎可挂载的最佳单点。
4. 需要新增的能力缺口清单（详见第 4 节）。

---

## 1. 改造前的实际架构与权限执行链路

### 1.1 运行时架构（现状核实）

| 层 | 文件 | 现状职责 |
|---|---|---|
| 接口 | `server.py` | FastAPI；Bearer 会话中间件；60+ 接口；`/api/ask`、`/api/export`、`/api/history`、`/api/records/*` |
| 鉴权 | `server.py:96-156` | `_required_role(method,path)` 三级（public/user/admin，默认 admin）；身份从 `sessions` 表查，**不信任前端 role**；强制改密闸门 |
| 编排 | `agent.py` | `ask()` → `ask_v2()`（主）/ `ask_legacy()`（兜底）；`_pre_execute_check()` 统一闸门；`_exec_sql()` 唯一执行出口 |
| 安全 | `guard.py` | `clean_sql` / `sql_guard`（只读+高危子句）/ `check_tables_ex`（表级白名单，fail-closed） |
| 校验 | `sql_validator.py` | P0 规则 / P1 指标口径 / P2 JOIN / P3 Schema（表+列存在性）/ Metric 比率，五维 + 三态 |
| 执行 | `db.py` → `db_clickhouse.py` / `db_sqlite.py` | 门面分发；ClickHouse 侧 `_ensure_limit`（强制 LIMIT + SETTINGS 资源上限） |
| 知识 | `config_store.py` | SQLite 配置库；账号/会话/指标/实体/关系/ACL/取数历史/结果快照 |
| 检索 | `rag.py` / `semantic_layer.py` | 建索引；`tables[].column_list`（结构化列清单）、`allowed_tables`（可见表清单） |
| 后台 | `gaps.py` | 知识缺口扫描，**直接调 `db.execute_sql`**（系统拼装的聚合 SQL，admin-only 接口） |

### 1.2 主调用链（现状核实，与 `stage1` 报告一致）

```
POST /api/ask  → 中间件(取可信用户) → agent.ask(index, question)
  → ask_v2
    ① understand / retrieve_semantic / resolve / build_prompt
    ② llm.chat → _finalize_sql（clean + 补库名前缀）
    ③ for attempt: _pre_execute_check(sql, ctx, resolved, allowed_tables)
          └─ guard.sql_guard（只读）
          └─ guard.check_tables_ex（表级白名单，fail-closed）
          └─ sql_validator.validate_sql_ex（P0/P1/P2/P3/Metric）
    ④ _exec_sql(sql)  ← 唯一执行出口（内部只做"是否查询语句"判定，调 db.execute_sql）
    ⑤ 执行失败 → 自愈循环（每次自愈 SQL 重新走 _pre_execute_check）
    ⑥ result_validator + nl_answer → 返回
  → 异常时按 LEGACY_FALLBACK_MODE 决定是否回退 ask_legacy（Legacy 同样过 sql_guard + check_tables_ex）
```

### 1.3 现有 RBAC / 表级 ACL 事实

- **角色**：`users.role` 仅 `admin` / `user` 两种（`config_store.VALID_ROLES`），**无自定义角色**。
- **用户-角色**：一对一（`users.role` 字段），无绑定表。
- **表级 ACL**：`table_acl` 表（`table_name, visible, note`）+ `ACL_DEFAULT_VISIBLE`（生产默认 false = 白名单）。`rag.build_index` 据此生成 `allowed_tables`，`guard.check_tables_ex` 在执行前硬校验。
- **字段级 / 导出级 / 行级**：**不存在**。`sql_validator._check_columns` 只做"列是否存在"（防猜错字段），不做"是否有权读/是否需脱敏"。

### 1.4 查询结果 / 历史 / 导出现状（关键缺口）

| 数据出口 | 位置 | 现状控制 | 缺口 |
|---|---|---|---|
| 查询结果 | `server.api_ask`（701-802） | 无字段级控制；原样返回 `columns/rows` | 无 MASK/DENY；敏感值可进响应/快照 |
| 结果快照 | `save_result_snapshot` | 仅成功取数落库 | 快照存**明文**完整结果 |
| 历史回看 | `api_get_record`（2302） | `_can_view`（本人或管理员） | 权限收回后仍可读完整历史 |
| 结果导出 | `api_export_record`（2381） | `_can_view` + 读快照 | 无导出权限校验、无字段级导出控制、**不重校验当前权限** |
| 知识库导出 | `api_export`（2007） | admin-only（中间件默认） | 与权限引擎无关 |

### 1.5 其余 `db.execute_sql` 调用点（统一闸门审计）

- `agent.py:131` `_exec_sql` 内 —— 唯一用户路径执行出口 ✅
- `gaps.py:92/115` —— 后台知识缺口扫描（系统拼装 SQL，admin-only）⚠️ 属系统路径，非用户可控 SQL，本次标注但不纳入字段级闸门
- `check1.py` / `ck_check.py` —— CLI/运维脚本，非服务运行路径
- `zhishu-ai-code-bundle/` —— 打包快照旧副本，非运行代码，忽略

---

## 2. 改造前权限能力缺口总表（本次要补的）

| # | 能力 | 现状 | 本次落点 |
|---|---|---|---|
| 1 | 自定义角色 | 无 | `roles` + `user_role_bindings` |
| 2 | 数据域 | 无 | `data_domains` |
| 3 | 敏感字段映射 | 无（只有字段注释） | `sensitive_resources` |
| 4 | 字段访问策略 ALLOW/MASK/DENY | 无 | `permission_policies` + AST 引擎 |
| 5 | 行级权限（预留） | 无 | `row_permission_rules`（P3 实现数据模型，P1 预留） |
| 6 | 导出独立鉴权 | 无 | 导出走同一引擎 + 导出动作维度 |
| 7 | 历史重校验 | 无 | 回看/导出时按当前权限重新校验 |
| 8 | 权限审计 | 无 | `permission_audit_logs` |
| 9 | AI 自然语言配置 | 无 | `ai_policy_drafts` + 解析/预览/审批 |
| 10 | 字段级 AST 拦截 | 无（仅表级） | `permission.py` |

---

## 3. P0 风险修复计划（本次阶段 2 的 P0）

> 说明：S1–S4 已封堵的 5 类 P0（自愈绕过 / Legacy 弱校验 / 表名 fail-open / 校验器吞异常 / 账号与状态接口）已核实落地，不重复修。下面是**本次权限引擎引入前必须再确认/补齐**的 P0 项，以及新引入的 fail-closed 要求。

| 编号 | 风险 | 现状核实 | 修复计划 | 验收测试 |
|---|---|---|---|---|
| P0-6 | 字段级权限引入后，任何"字段解析失败/表元数据缺失"不得 fail-open | 现 `_check_columns` 缺失列清单仅降级 warning | 权限引擎对 `SELECT *` 展开、字段归属解析失败时**拒绝执行**（fail-closed） | test B-12/B-13 |
| P0-7 | 结果脱敏必须在执行后、任何响应/快照/流式之前确定性完成 | 现无脱敏 | 新增 `permission.mask_rows`，在 `api_ask` 组装 payload 前、`save_result_snapshot` 前调用 | test A-3 |
| P0-8 | 导出必须独立鉴权且重校验当前权限 | 现仅 `_can_view` | `api_export_record` 走权限引擎导出动作校验 + 按当前策略重校验列 | test D-20/21/22 |
| P0-9 | 历史回看不得因"曾有权"绕过当前权限 | 现仅 `_can_view` | 回看/导出按当前权限重校验，DENY 列剔除、MASK 列脱敏 | test D-22 |
| P0-10 | 权限配置库/引擎异常必须 fail-closed | 新增引擎 | 引擎加载策略失败 → 敏感查询拒绝；健康检查暴露状态 | test B-13 |
| P0-11 | `gaps.py` 后台路径不经统一闸门 | 系统拼装 SQL（admin-only） | 标注为系统路径；表名来自知识库可见表；不接用户输入，风险可控，不改动 | 记录说明 |

**P0 执行顺序**：P0-6（引擎 fail-closed）→ P0-7（脱敏）→ P0-8/P0-9（导出/历史）→ P0-10（引擎异常）→ P0-11（记录）。

---

## 4. 数据库迁移设计（增量，兼容现有 SQLite）

### 4.1 迁移原则（沿用现有 `config_store.init_store` 风格）

1. **只增表、不删不重建**现有表（`users`/`sessions`/`table_acl`/`query_records` 等一律不动）。
2. 复用现有 `CREATE TABLE IF NOT EXISTS` + `PRAGMA table_info` + `ALTER TABLE ADD COLUMN` 的幂等迁移范式。
3. 迁移前**备份** `agent_config.db`（`cp` 带时间戳），迁移后校验表存在。
4. 迁移失败不破坏已有数据：所有新增表用独立 `CREATE TABLE IF NOT EXISTS`，异常时逐表 try/except 并记录，不整体回滚账号/知识数据。

### 4.2 新增表

```sql
-- ① 角色（内置 admin/user + 自定义角色）
CREATE TABLE IF NOT EXISTS roles (
    name         TEXT PRIMARY KEY,       -- 角色标识（admin/user/自定义英文名）
    display_name TEXT,                   -- 中文显示名
    description  TEXT,
    builtin      INTEGER DEFAULT 0,      -- 1=内置(admin/user，不可删)；0=自定义
    created_at   TEXT,
    updated_at   TEXT
);

-- ② 用户-角色绑定（多角色）
CREATE TABLE IF NOT EXISTS user_role_bindings (
    username   TEXT NOT NULL,
    role_name  TEXT NOT NULL,
    created_by TEXT,
    created_at TEXT,
    PRIMARY KEY (username, role_name)
);
CREATE INDEX IF NOT EXISTS idx_urb_role ON user_role_bindings(role_name);

-- ③ 数据域
CREATE TABLE IF NOT EXISTS data_domains (
    name         TEXT PRIMARY KEY,       -- 域标识（如 driver / customer / fund_flow / order / summary）
    display_name TEXT,                   -- 司机信息 / 客户信息 / 公司资金流水 / 订单信息 / 经营汇总
    description  TEXT,
    owner        TEXT,                   -- 负责人
    enabled      INTEGER DEFAULT 1,
    created_at   TEXT,
    updated_at   TEXT
);

-- ④ 敏感资源映射（域 → 表 → 字段）
CREATE TABLE IF NOT EXISTS sensitive_resources (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    domain_name   TEXT NOT NULL,
    table_name    TEXT NOT NULL,         -- 归一化后（小写、去库前缀）
    raw_table     TEXT,                  -- 原始写法（含库前缀，供溯源）
    column_name   TEXT NOT NULL,         -- 归一化后
    raw_column    TEXT,
    sensitivity   TEXT DEFAULT 'high',   -- high / medium / low
    aliases       TEXT,                  -- JSON：同义词/别名
    mapping_status TEXT DEFAULT 'confirmed', -- confirmed 已确认 / pending 待确认（AI 推荐候选）
    note          TEXT,
    owner         TEXT,
    created_at    TEXT,
    updated_at    TEXT,
    UNIQUE(table_name, column_name)
);
CREATE INDEX IF NOT EXISTS idx_sr_domain ON sensitive_resources(domain_name);
CREATE INDEX IF NOT EXISTS idx_sr_table ON sensitive_resources(table_name);

-- ⑤ 权限策略（字段级 ALLOW/MASK/DENY）
CREATE TABLE IF NOT EXISTS permission_policies (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL,          -- role / user
    subject_name TEXT NOT NULL,          -- 角色名 或 用户名
    domain_name  TEXT,                   -- 可空（不绑定域）
    table_name   TEXT NOT NULL,          -- '*' 表示任意表
    column_name  TEXT NOT NULL,          -- '*' 表示整表
    action       TEXT DEFAULT 'query',   -- query / export / both
    access       TEXT NOT NULL,          -- ALLOW / MASK / DENY
    mask_rule    TEXT,                   -- 脱敏规则名（phone / id_card / name / bank_card / custom）
    priority     INTEGER DEFAULT 0,      -- 越大越优先；DENY 恒优先于 ALLOW/MASK
    enabled      INTEGER DEFAULT 1,
    version      INTEGER DEFAULT 1,
    created_by   TEXT,
    created_at   TEXT,
    updated_at   TEXT
);
CREATE INDEX IF NOT EXISTS idx_pp_subject ON permission_policies(subject_type, subject_name);
CREATE INDEX IF NOT EXISTS idx_pp_table ON permission_policies(table_name);

-- ⑥ 策略版本 / 发布记录
CREATE TABLE IF NOT EXISTS permission_policy_versions (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    policy_id    INTEGER,
    version      INTEGER,
    payload      TEXT,                   -- 该版本完整快照 JSON
    status       TEXT DEFAULT 'draft',   -- draft / published / disabled
    change_note  TEXT,
    before_json  TEXT,                   -- 变更前快照（回滚/审计）
    after_json   TEXT,
    operator     TEXT,                   -- 操作者
    published_at TEXT
);

-- ⑦ 审批 / 发布记录
CREATE TABLE IF NOT EXISTS permission_approvals (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    draft_id     INTEGER,                -- ai_policy_drafts.id 或 policy 版本
    target_type  TEXT,                   -- policy / draft
    requested_by TEXT,
    approved_by  TEXT,
    status       TEXT DEFAULT 'pending', -- pending / approved / rejected
    reason       TEXT,
    created_at   TEXT,
    decided_at   TEXT
);

-- ⑧ 权限判定 / 变更审计
CREATE TABLE IF NOT EXISTS permission_audit_logs (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    username    TEXT,
    roles       TEXT,                    -- JSON：判定时生效的角色
    request_id  TEXT,
    ts          TEXT,
    question    TEXT,                    -- 业务问题（脱敏后）
    domain      TEXT,
    tables      TEXT,                    -- JSON
    columns     TEXT,                    -- JSON
    action      TEXT,                    -- query / export
    decision    TEXT,                    -- allow / deny / mask
    deny_reason TEXT,
    rule_ids    TEXT,                    -- JSON
    policy_version INTEGER,
    rewritten   INTEGER DEFAULT 0,       -- 是否发生 SQL 改写
    final_sql   TEXT,                    -- 最终执行 SQL 的安全表示（去敏感字面量）
    returned_columns TEXT,               -- JSON
    exported_columns TEXT,               -- JSON
    ok          INTEGER,
    created_at  TEXT
);
CREATE INDEX IF NOT EXISTS idx_pal_user ON permission_audit_logs(username);
CREATE INDEX IF NOT EXISTS idx_pal_ts ON permission_audit_logs(ts);

-- ⑨ AI 自然语言草稿（P2）
CREATE TABLE IF NOT EXISTS ai_policy_drafts (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    source_text  TEXT,                   -- 管理员自然语言原文
    parsed_json  TEXT,                   -- 结构化规则 JSON
    ambiguities  TEXT,                   -- JSON：歧义/需确认项
    impact_json  TEXT,                   -- JSON：影响分析
    status       TEXT DEFAULT 'pending', -- pending / approved / applied / rejected
    created_by   TEXT,
    created_at   TEXT,
    reviewed_by  TEXT,
    reviewed_at  TEXT
);

-- ⑩ 行级权限（P3 数据模型，P1 预留）
CREATE TABLE IF NOT EXISTS row_permission_rules (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    subject_type TEXT NOT NULL,          -- role / user
    subject_name TEXT NOT NULL,
    table_name  TEXT NOT NULL,
    filter_sql  TEXT NOT NULL,           -- 管理员定义的可信过滤片段（如 dept_id = :dept）
    description TEXT,
    enabled     INTEGER DEFAULT 1,
    created_by  TEXT,
    created_at  TEXT,
    updated_at  TEXT
);
```

### 4.3 迁移函数设计

- 在 `config_store.init_store()` 末尾追加 `_migrate_permission_tables(cur)`（幂等：`CREATE TABLE IF NOT EXISTS` + 逐表 `PRAGMA table_info` 校验）。
- 新增一个独立 `def migrate_permission_schema()`：供**迁移前备份后**单独调用，返回每张表的创建结果，失败的表记入返回值（不抛全局异常）。
- 内置角色种子：`INSERT OR IGNORE INTO roles` 写入 `admin`/`user`（builtin=1），并回填现有 `users.role` 到 `user_role_bindings`（老用户绑定到对应内置角色），保证升级后行为与旧版一致。

### 4.4 归一化 / 大小写 / 前缀处理

- `permission.normalize_table(name)`：去库前缀（`db.table` → `table`）、小写、去反引号/双引号。
- `permission.normalize_column(name)`：小写、去引号。
- 敏感字段映射统一以归一化后的 `(table, column)` 为主键；原始写法存 `raw_*` 供溯源。
- 禁止仅凭字段名猜测敏感字段自动发布：AI 只产出 `mapping_status='pending'` 候选，管理员确认后才 `confirmed` 并参与生效判定。

---

## 5. 权限引擎与 AST 拦截设计（P1 核心）

新增模块 `permission.py`，与 AI 生成逻辑完全解耦，服务端确定性执行：

```
permission.effective_policies(user)      # 用户全部角色 → 合并生效策略（DENY 恒优先，多角色取最小权限）
permission.evaluate(sql, user, meta)     # AST 字段级判定 → PermissionDecision{allowed, deny_reasons, mask_map, output_columns}
permission.mask_rows(columns, rows, mask_map)  # 服务端确定性脱敏
permission.audit(...)                    # 写审计
```

判定规则：
1. `SELECT *` / `table.*` 用 `meta`（列清单）展开；无法展开 → **拒绝**（fail-closed）。
2. 表别名、CTE、子查询、UNION、JOIN、函数/CASE WHEN 表达式里的字段引用全部解析归属。
3. `DENY` 字段：查询与结果返回一律禁止；不得通过别名/表达式/子查询间接获取；输出列含 DENY → 拒绝。
4. `MASK` 字段：仅允许作为输出列（展示脱敏）；禁止用于 WHERE/GROUP BY/ORDER BY/JOIN/聚合/表达式（保守策略，v1）。
5. 解析失败 / 字段解析不完整 / 表元数据无法确认 / 权限服务异常 → **拒绝敏感数据查询**（fail-closed）。
6. 改写（如结果列剔除、脱敏）后，实际返回内容与最终校验报告一致，且导出前重新校验当前权限。

**执行闸门挂载点**：`agent._pre_execute_check` 在表级校验与 `sql_validator` 之后，追加 `permission.evaluate`；`api_ask` 返回前、`save_result_snapshot` 前调用 `permission.mask_rows`；`api_get_record` / `api_export_record` 按当前权限重校验。

---

## 6. 交付顺序（本次执行）

| 阶段 | 内容 | 验收 |
|---|---|---|
| P0 | 引擎 fail-closed + 脱敏 + 导出/历史重校验 + 引擎异常阻断 | 测试 B-12/B-13、D-20/21/22 |
| P1 | 角色/数据域/敏感字段/字段策略 + AST 拦截 + 脱敏 + 导出 + 审计 | 测试 A 组、B 组、D 组 |
| P2 | AI 自然语言规则解析/草拟/影响分析/审批/冲突检查 | 测试 E 组 |
| P3 | 行级权限 + 高风险导出审批 + 用户级例外 + 使用分析 | 预留 + 数据模型 |

---

## 7. 未验证项与风险声明

1. 本报告对 `stage1` 已封堵 P0 的核实为**静态代码核对 + 现有测试基线**，未重新逐条构造运行时复现（`stage1` 报告已含运行时证据）。
2. 真实 ClickHouse 集成回归未在本次全量重跑（`test_gaps` 依赖真实库 + 隧道）。
3. 前端 291KB 单文件，新增"数据权限"模块为增量 HTML/JS，不引入构建工具。
