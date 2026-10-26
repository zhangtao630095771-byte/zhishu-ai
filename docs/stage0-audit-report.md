# 知数 AI · 阶段 0 源码审查报告

- **审查对象**：`zhishu-ai-src-20261019-0944.zip` 对应的工作区源码
- **审查日期**：2026-10-19
- **审查方式**：只读代码审查 + 现有测试基线 + 临时 monkeypatch 复现（未修改任何项目源码）
- **结论口径**：本报告中的“已复现”均指在当前 `.venv` 环境下实际跑出的结果；“静态审查”指仅通过代码路径推导、尚未单独构造运行时用例。

---

## 0. 结论摘要

### 0.1 总体判断

当前系统已经具备较完整的企业级骨架：V2 分层语义流水线、`sql_validator` 执行前闸门、`_exec_sql` 唯一执行出口、RBAC 中间件、表级 ACL、指标/实体/关系注册表、94 条评测集。**不是推倒重写，而是在现有骨架上补“所有执行路径统一过闸”和“校验失败可区分”两件事。**

但存在 **5 类 P0 级风险**，其中 4 类已用临时脚本稳定复现：

| 编号 | 问题 | 状态 | 风险 |
|---|---|---|---|
| P0-1 | 执行失败自愈后的修复 SQL 不再过执行前校验 | **已复现** | 越权表/违规 SQL 可能被实际执行 |
| P0-2 | Legacy 路径缺少 V2 的业务校验，`always` 模式可静默降级 | 静态审查 | 业务硬约束被绕过 |
| P0-3 | 表名解析失败/解析器缺失时 fail-open | **已复现** | 反引号表名等可绕过表白名单 |
| P0-4 | 校验器内部异常被吞，等同“校验通过” | **已复现** | 关键校验失效时仍执行 |
| P0-5 | 默认管理员口令、状态接口暴露、错误信息、LIMIT 等 | 静态审查 + 配置核对 | 账号/ schema/大结果集风险 |

### 0.2 测试基线（当前 `.venv`）

| 层级 | 结果 | 说明 |
|---|---|---|
| `compileall` | ✅ 通过 | 全项目语法编译无错误 |
| unit | ✅ 8/8 文件通过 | `test_gate` 49 项、`test_guard`、`test_retrieve` 22 项等 |
| config | ✅ 83/83 通过 | 已指向 `/tmp` 副本，未写生产库 |
| integration | ✅ 51/51 通过 | 当前确实连上真实 ClickHouse（`ctc_data_warehouse`） |

> **重要修正**：任务书提到“run_tests 多项失败 / 缺少 openai”，在当前工作区的 `.venv` 下**未复现**。当前依赖齐全（openai/fastapi/sqlglot/clickhouse_connect/fastembed 均可导入），unit/config/integration 全绿。历史失败更可能是**执行环境未用 `.venv`** 所致，不应判定为代码缺陷。

---

## 1. 审查范围与方法

### 1.1 重点文件

| 模块 | 作用 | 本次关注点 |
|---|---|---|
| `agent.py` | 主编排：V2 / Legacy / 自愈 / 执行 | P0-1、P0-2、P1-3、P1-5 |
| `guard.py` | 清洗、只读护栏、表名提取、权限 | P0-3、P0-5 |
| `sql_validator.py` | 生成后五维校验 | P0-4、P1-1、P1-2 |
| `result_validator.py` | 执行后结果体检 | P1-4 |
| `server.py` | FastAPI、RBAC、接口 | P0-5、P1-5 |
| `config_store.py` | 账号/会话/知识/记录 | P0-5、P1-5 |
| `config.py` | 默认配置 | P0-2、P0-5 |
| `prompt.py` | Prompt 与修复提示词 | P1-3 |
| `db_clickhouse.py` | 执行与 LIMIT | P0-5 |
| `semantic_layer.py` | P0~P4 注册表与 Prompt | P1-1、P1-2 |
| `evaluation/`、`evaluate.py`、`run_tests.py` | 评测与测试 | P1-4、P1-5、测试基线 |

### 1.2 方法

1. 按“入口 → 检索 → 生成 → 校验 → 执行 → 结果”读完整调用链。
2. 对每个 P0 点位构造最小复现（monkeypatch `db.execute_sql` / `guard.extract_tables` / `llm.chat`），统计实际执行次数与护栏调用次数。
3. 跑现有测试基线，区分“代码失败 / 环境缺失 / 集成未执行”。
4. 对评测集做规模与字段核查，不伪造基准结果。

### 1.3 复现脚本

临时脚本：`/tmp/stage0_repro.py`（不属于项目源码，可随时删除）。运行方式：

```bash
cd /Users/zhangtao/Downloads/autosql/zhishu-ai
CONFIG_DB=/tmp/stage0_agent_config.db SQL_REVIEW_ENABLED=false \
  .venv/bin/python /tmp/stage0_repro.py
```

---

## 2. 当前主调用链

### 2.1 V2（企业级，默认）

```
/api/ask
  └─ agent.ask(index, question)                         # agent.py:666
       └─ agent.ask_v2(index, question)                 # agent.py:428
            ① query_understanding.understand()          # 意图/指标/实体/维度/时间/过滤
            ② semantic_layer.retrieve_semantic()        # P0~P4 分层检索
            ③ rag.match_disabled_metric()               # 停用指标拦截
            ④ semantic_layer.resolve()                  # 冲突解决 + 权威口径绑定
            ⑤ semantic_layer.build_prompt_parts()       # role → 时间 → P0→P1→P2→P3→P4
            ⑥ llm.chat() → _finalize_sql()               # 生成 + 清洗 + 补库名前缀
            ⑦ for attempt in 1..MAX_RETRY:               # agent.py:528
                 guard.sql_guard()                       #   只读
                 guard.check_tables()                    #   表白名单
                 sql_validator.validate_sql()            #   P0~P3 + Metric
                 if error: llm.chat(修复) → 回到循环     #   修复后重新校验
            ⑧ 用尽重试仍有 error → 拒绝执行              # agent.py:562
            ⑨ _exec_sql(sql)                            # agent.py:589（唯一出口）
            ⑩ 执行失败 → 自愈循环                        # agent.py:596-617（★ 问题点）
            ⑪ result_validator.validate_result()        # 结果 warning
            ⑫ nl_answer.answer()                        # 自然语言回答
```

### 2.2 Legacy（兜底）

```
agent.ask_legacy(index, question)                        # agent.py:256
  ① rag.retrieve() / retrieve_dicts()
  ② 停用指标拦截 / 空表拒绝
  ③ prompt.build_system_prompt()
  ④ llm.chat() → _finalize_sql()
  ⑤ for attempt in 1..MAX_RETRY:                         # agent.py:349
       guard.sql_guard()                                 #   只读
       guard.check_tables()                              #   表白名单
       _exec_sql()
       失败 → llm.chat(build_repair_prompt) → 下一轮（会重新过上面两道）
  ★ 没有 sql_validator：P1 指标硬约束 / P2 JOIN / P3 列存在性全部不生效
```

### 2.3 降级策略

`agent.ask()`（`agent.py:666-698`）：

- `LEGACY_FALLBACK_MODE=whitelist`（默认）：仅 `TimeoutError` / `ConnectionError` / 消息含 timeout、connection refused 等才回退。
- `off`：绝不回退。
- `always`：**任何异常都回退**（`agent.py:681`），会把 V2 的校验/逻辑异常静默降级到弱校验的 Legacy。

---

## 3. P0 问题清单

### P0-1 执行失败自愈绕过执行前校验（已复现）

**位置**

- `agent.py:588-617`，尤其是 `agent.py:596-601`：

```python
for ex_attempt in range(2, config.MAX_RETRY + 1):
    fix_user = prompt.build_repair_prompt(question, sql, err)
    fixed_raw = llm.chat(system_prompt, fix_user)
    sql = _finalize_sql(fixed_raw)
    try:
        columns, rows = _exec_sql(sql)      # ★ 直接执行，没有再跑 sql_guard / check_tables / validate_sql
        break
```

**证据（实际运行输出）**

```
最终 SQL = SELECT * FROM ctc_data_warehouse.secret_table
db.execute_sql 调用序列 = [
  'SELECT * FROM ctc_data_warehouse.allowed_table',
  'SELECT * FROM ctc_data_warehouse.secret_table'
]
guard 调用次数 = {'sql_guard': 1, 'check_tables': 1}
>>> P0-1 判定：❌ 存在绕过（修复 SQL 被执行，未过表权限）
```

说明：首次 SQL 通过校验并执行（抛模拟错误）；模型修复成 `secret_table` 后，`_exec_sql` 直接执行。`guard.check_tables` 只在首次执行前被调用 1 次，修复后 **0 次**。

**风险**

- 修复 SQL 若引用未授权表、写操作、多语句、不存在字段、缺指标必要条件，均可能被执行。
- `_exec_sql` 只做“拒答/非查询语句”检查，不能替代只读、白名单、业务校验。
- 界面展示的 SQL、校验的 SQL、真正执行的 SQL 不一致（展示的是修复后 SQL，`validation` 还是修复前的旧报告）。

**修复建议（阶段 1）**

1. 抽出唯一执行前闸门：

```python
def _pre_execute_gate(sql, ctx, resolved, allowed_tables, *, stage):
    """返回 (ok, sql, issues, error_stage)"""
    sql = _finalize_sql(sql)                 # 清洗 + 库名前缀
    ok, why = guard.sql_guard(sql)            # 只读
    if not ok: return False, sql, [], "readonly"
    ok, illegal = guard.check_tables(sql, allowed_tables)  # 白名单
    if not ok: return False, sql, [], "acl"
    issues = sql_validator.validate_sql(sql, ctx, resolved, allowed_tables)
    if sql_validator._has_error(issues):     # 校验器三态改造后改为 result.status
        return False, sql, issues, "validator"
    return True, sql, issues, None
```

2. 所有路径统一调用：初次生成、V2 校验自愈、**执行失败自愈**、Legacy、人工编辑后执行（如有）。
3. 修复 SQL 必须重新走 `clean_sql` / `qualify_tables` / 只读 / 白名单 / `sql_validator`。
4. 失败即停：`error` 级、无法解析、权限提取不可靠 → 拒绝执行。
5. 结果里保留“实际执行 SQL + 最终一次校验报告 + 执行阶段”，保证三者一致。

**回归测试（阶段 1 必做）**

| # | 场景 | 期望 |
|---|---|---|
| 1 | 首次合法，执行失败后修复成越权表 | `db.execute_sql` 只被调用 1 次（首次），修复 SQL 不执行 |
| 2 | 修复 SQL 含写操作/多语句 | 不执行，返回只读/多语句拦截 |
| 3 | 修复 SQL 引用不存在字段 | 不执行，返回 P3 error |
| 4 | 修复 SQL 缺指标必要条件 | 不执行，返回 P1 error |
| 5 | 修复 SQL 是拒答文本 | 不执行，`NotExecutable` |
| 6 | 修复 SQL 全部通过 | 执行 2 次（首次失败 + 修复成功），`validation` 为修复后报告 |

---

### P0-2 Legacy 校验一致性与 `always` 降级（静态审查）

**位置**

- `agent.py:256-411`：`ask_legacy` 全程没有调用 `sql_validator`。
- `agent.py:666-698`：`allow = (mode == "always") or ...`。
- `config.py:149-154`：默认 `whitelist`，注释已提示 `always` 不推荐，但**无运行时硬限制**。

**证据**

- 静态检索：`sql_validator` 在 `agent.py` 中只出现在 `ask_v2`（`agent.py:542/544/562`），`ask_legacy` 无引用。
- Legacy 每轮只过 `guard.sql_guard` + `guard.check_tables`；修复后下一轮会重新过这两道（这点是好的），但 P1 指标硬约束、P2 JOIN 规则、P3 列存在性都不生效。
- `mode=always` 时，V2 内任何异常（包括校验器代码异常、KeyError、类型错误）都会回退到 Legacy，等于“校验失败 → 不校验地执行”。

**风险**

- 生产环境误设 `LEGACY_FALLBACK_MODE=always`，会把 V2 的 bug/校验异常静默降级。
- V2 因业务校验失败而抛异常时，Legacy 可能执行一条不满足权威口径的 SQL。
- Legacy 无 `trace`、无 `validation`、无 `degraded` 细分阶段，排障困难。

**修复建议**

1. Legacy 复用同一套 `_pre_execute_gate`（至少包含 `sql_validator` 的 P1/P2/P3 硬检查）。
2. `always` 模式：
   - 启动时打印显著风险提示；
   - 生产环境（`DB_TYPE=clickhouse` 且非 debug）建议直接拒绝启动或强制改为 `whitelist`；
   - 或在 `/api/status` 暴露 `legacy_fallback_mode` 并在前端标红。
3. 降级记录补齐：`degrade_stage` 细化为 `understand/retrieve/validate/execute`，并记录触发异常类型、最后一条 SQL、失败阶段。
4. `_is_recoverable` 的字符串匹配收窄：只匹配明确的连接/超时错误，避免把业务异常（消息里恰好含 `timeout`）误判为可恢复。

**回归测试**

- V2 校验器抛异常 + `always` → 断言不会执行未校验 SQL（或 Legacy 也走同一闸门）。
- Legacy 路径缺 `required_predicates` → 断言拒绝执行。
- `whitelist` 下业务异常 → 断言 fail-closed，不回退。
- 可恢复连接异常 → 断言回退且 `degraded=true`、`degrade_stage` 正确。

---

### P0-3 表权限解析失败时 fail-open（已复现）

**位置**

- `guard.py:339-354` `extract_tables_detail`：解析失败 → `return _extract_by_regex(sql), f"正则兜底(...)"`。
- `guard.py:357-379` `check_tables`：只拿 `extract_tables(sql)` 的集合，**丢弃“用了哪种方式/是否可靠”**。
- `guard.py:217` `_TABLE_PATTERN`：只匹配 `[a-zA-Z_]` 开头的表名，反引号/双引号表名不匹配。
- `guard.py:306-325` 正则兜底：只处理 `FROM/JOIN` 后的简单标识符；多语句、表函数、复杂 CTE 可能漏。

**证据（实际运行输出）**

```
extract_tables = set()
check_tables   = (True, [])
>>> P0-3 判定：❌ 空提取被默认放行
```

复现条件：`guard._HAS_SQLGLOT = False`，SQL 为 ``SELECT * FROM `secret_table` ``，白名单为 `["allowed_table"]`。正则不匹配反引号 → `used = ∅` → `illegal = ∅` → 放行。

补充复现（`sql_guard` 的边界）：

```
INTO OUTFILE : (True, '')
OPTIMIZE     : (True, '')
```

说明 `SELECT ... INTO OUTFILE`（ClickHouse 可写文件）与 `SELECT 1; OPTIMIZE TABLE ...` 都能通过 `sql_guard`。前者是真实的文件写风险；后者依赖数据库是否允许多语句。

**风险**

- 解析器缺失/方言不支持/语法异常时，恶意或错误的引号表名可绕过白名单。
- CTE 列清单、`AS MATERIALIZED`、表函数、多语句等边界可能漏提取。
- 正则兜底没有“提取不可靠”的信号，调用方无法选择 fail-closed。

**修复建议**

1. `extract_tables_detail` 返回结构化结果：

```python
{"tables": set(), "method": "parser|regex|none", "reliable": bool, "reason": str}
```

2. `check_tables(sql, allowed, strict=True)`：
   - 解析器不可用或解析失败，且 SQL 含 `FROM/JOIN` 但提取为空 → **拒绝**；
   - 生产链路默认 `strict=True`；单测/演示可显式 `strict=False`。
3. 正则兜底增强：
   - 支持反引号/双引号/方括号标识符；
   - 支持 `WITH name (col) AS (`、`AS MATERIALIZED`；
   - 明确拒绝多语句（或先按分号拆分逐条校验）；
   - 识别表函数（`numbers()`、`remote()` 等）并按策略处理。
4. `sql_guard` 增补 ClickHouse 高危子句：`INTO OUTFILE`、`FORMAT`（写文件场景）、`OPTIMIZE`、`SYSTEM`、`RENAME`、`MERGE`、`KILL`、`SET` 等；同时保留只读账号作为独立防线。
5. 保持 `CK_USER` 只读账号要求，并在启动时探测（见 P0-5）。

**回归测试**

CTE、嵌套子查询、JOIN、库名前缀、注释、字符串字面量、敏感表、多语句、解析失败、解析器缺失、反引号/双引号表名、表函数。

---

### P0-4 校验器异常/提前返回被当成通过（已复现）

**位置**

- `sql_validator.py:279-293` `_check_p3`：

```python
try:
    used = guard.extract_tables(sql)
except Exception:
    return          # ★ 静默跳过 P3，且不执行 _check_columns
```

- `sql_validator.py:235-276` `_check_columns`：`extracted is None` 直接 `return`（AST 解析失败静默跳过）。
- `sql_validator.py:332-350` `validate_sql`：只返回 `issues` 列表，没有“校验器运行失败/降级”状态。
- `sql_validator.py:343-344`：

```python
if not sql or sql.startswith("--"):
    return issues       # ★ 以 -- 开头的 SQL 直接跳过全部校验
```

**证据（实际运行输出）**

```
validate_sql issues = []
>>> P0-4 判定：❌ 异常被吞，返回空 issues（等同通过）
```

复现条件：monkeypatch `guard.extract_tables` 抛 `RuntimeError`，调用 `validate_sql("SELECT * FROM allowed_table", ...)`，返回 `[]`。

**补充风险：前导行注释绕过**

模型输出 `-- 说明\nSELECT ...` 时：

- `_refusal_reason` 剥掉前导注释后以 `SELECT` 开头 → 不算拒答；
- `validate_sql` 看到 `sql.startswith("--")` → 直接返回 `[]`；
- `sql_guard` 去注释后放行；`check_tables` 仍能提取表（sqlglot 能解析注释），所以表权限尚在；
- 但 **P0 规则、P1 指标硬约束、P2 JOIN、P3 列存在性全部被跳过**。

**风险**

- 关键安全校验（表权限）或业务校验（指标必要条件）在“提取失败/解析失败/前导注释”时被静默跳过。
- 失败与通过不可区分，监控指标失真。

**修复建议**

1. 引入三态结果（建议新增，不破坏旧调用）：

```python
@dataclass
class ValidationResult:
    status: str          # "passed" | "failed" | "error" | "degraded"
    issues: list
    degraded_reasons: list
```

2. `validate_sql` 对每个 `_check_*` 单独 `try/except`：
   - 关键检查（P0 只读、P3 表/列）异常 → `status="error"`，调用方 fail-closed；
   - 辅助检查（比率写法、启发式 warning）异常 → `status="degraded"` + 原因，允许继续但必须记录。
3. 去掉 `sql.startswith("--")` 的提前返回；改为先剥前导注释，再判断是否为空/拒答。
4. 保留 `validate_sql` 旧签名返回 issues 以兼容测试；新增 `validate_sql_ex` 给主链路使用。

**回归测试**

分别向 P0/P1/P2/P3/Metric 注入异常，断言：
- 关键检查异常 → 拒绝执行；
- 辅助检查异常 → 继续执行但 `degraded_reasons` 非空；
- 前导注释 SQL 仍执行完整校验；
- 空 SQL/纯注释 → 拒绝。

---

### P0-5 权限、账号、信息暴露与资源上限（静态审查 + 配置核对）

#### P0-5.1 默认管理员口令硬编码

- `config_store.py:1590-1595`：`DEFAULT_ADMIN = {"username": "admin", "password": "admin123", ...}`。
- `config_store.py:1636-1654`：首次无账号时自动创建，并把 `admin / admin123` 打印到日志。
- 无“首次登录强制改密”标记。

**风险**：生产首启若未改密，等于公开默认口令。`deploy.md:163` 已提示改密，但系统未强制。

**建议**：
- 改为 `ADMIN_INIT_PASSWORD` 环境变量；未设置时生成随机口令并只打印一次；
- 增加 `must_change_password` 字段，首登强制改密；
- 生产环境检测到默认口令 → 拒绝启动或显著告警；
- 日志不回显明文口令。

#### P0-5.2 `ACL_DEFAULT_VISIBLE` 代码默认 true

- `config.py:168-169`：`ACL_DEFAULT_VISIBLE = os.getenv("ACL_DEFAULT_VISIBLE", "true")`。
- 当前 `.env` 实际值为 `False`（已核对），所以**本环境是 fail-closed**；但新环境若漏配 `.env`，默认会把所有未登记表设为可见。

**风险**：部署遗漏配置 → 白名单形同虚设。

**建议**：代码默认改为 `false`；开发演示显式设 `ACL_DEFAULT_VISIBLE=true`；启动时打印当前策略。

#### P0-5.3 `/api/status` 向普通用户暴露表清单

- `server.py:2394-2407`：返回 `allowed_tables`（完整白名单）与 `llm_model`。
- `_required_role` 中 `/api/status` 为 `user`（`server.py:96-97`）。

**风险**：普通用户可枚举全部可查表名与模型信息，降低攻击成本。

**建议**：非管理员只返回计数（`tables_in_index` 等）；完整 `allowed_tables` 仅管理员可见（或新增 admin-only 接口）。前端当前只用计数，不会破坏页面。

#### P0-5.4 错误信息可能泄露库内细节

- `agent.py:693`：`error=f"企业级流水线异常，已中止（fail-closed）：{reason}"`，`reason` 含异常类型与消息。
- `server.py:api_ask` 原样回传 `result["error"]`。
- 多个管理接口 `detail=f"...{str(e)[:200]}"`（管理员可见，风险较低）。

**风险**：数据库连接串、表名、字段名、驱动堆栈可能出现在普通用户页面。

**建议**：
- 普通用户只返回“查询失败/知识不足/权限不足”等可理解文案 + 错误码；
- 完整异常写服务端日志（脱敏连接串、口令）；
- 管理端可看详细原因。

#### P0-5.5 外层 LIMIT 判定可能失效

- `db_clickhouse.py:395`：`has_limit = bool(re.search(r"\blimit\b", s, ...))`。
- 若 `LIMIT` 只出现在子查询，外层不会补 LIMIT。

**风险**：`SELECT * FROM big WHERE id IN (SELECT id FROM small LIMIT 10)` 可能返回超大结果集（仍受 `max_result_rows=10000` 兜底，但应用层 1000 行上限失效）。

**建议**：用 AST 判断“最外层是否已有 LIMIT”；或统一包一层 `SELECT * FROM (<sql>) AS _t LIMIT N`（注意列名/排序语义）；保留 `max_result_rows` 作为最后防线。

#### P0-5.6 只读账号未校验

- `config.py:40`：`CK_USER` 默认 `ai_reader`，但代码不验证该账号是否真的只读。
- `db_clickhouse.py:30` 注释要求只读。

**建议**：启动/健康检查时执行 `SELECT currentUser()` 与 `SHOW GRANTS`，检测到写权限则告警或拒绝启动。

#### P0-5.7 `/api/ask` 无频率/并发限制

- `server.py:661-752`：`asyncio.to_thread` 并发执行，无 per-user 限流。
- 已有 `MAX_QUERY_TIMEOUT=120s`、`MAX_RESULT_ROWS=1000`、`CK_MAX_RESULT_ROWS=10000`。

**风险**：单个账号可并发发起大量昂贵查询，拖垮集群/LLM 配额。

**建议**：加一个可配置的 per-user 信号量/令牌桶（如 `ASK_MAX_CONCURRENCY`、`ASK_RATE_PER_MIN`），先检查现有配置，避免重复限制。

---

## 4. P1 问题清单

### P1-1 关键业务口径硬约束存在跨指标漏拦（已复现）

**位置**

- `sql_validator.py:62-145` `_check_p1`。
- 关键判定：`sql_validator.py:139`：

```python
if declared and all(missing for _n, missing in declared):
    issues.append({... "severity": "error" ...})
```

**证据（实际运行输出）**

```
issues = []
>>> P1-1 判定：❌ B 的硬约束缺失但未报 error
```

复现条件：指标 A `required_predicates=["status = 6"]` 已满足；指标 B `required_predicates=["status = 40"]` 未满足；SQL 只有 `status = 6`。`declared = [("A", []), ("B", ["过滤 status = 40"])]`，`all(missing)` 为 False → 不报 error。

**风险**

- “同族任一满足”的策略只适用于同一业务族的指标（如 完单量 status=6 / 代驾完单量 status=40）。跨族指标同时命中时，B 的硬约束会被 A 的满足“带走”。
- `_satisfied` 用字符串包含判断（`sql_validator.py:111-128`），注释/字符串里的 `status = 6` 也能满足；`status IN (6)` 有专门处理，但更复杂的等价写法（`status BETWEEN 6 AND 6`、`status = 0x06`）不识别。
- Legacy 完全不执行这些硬约束。

**修复建议**

1. 按 `conflict_key` / 指标族分组，每组独立判断；只有“同组内任一满足”才放行，跨组必须各自满足。
2. `_satisfied` 改为 AST/结构化判断：剥掉注释与字符串字面量，解析 `WHERE` 条件；至少支持 `=`、`IN`、`BETWEEN`、`AND/OR` 的保守匹配。
3. 保留启发式 `status/fee` 为 warning；`required_predicates/required_columns` 维持 error。
4. Legacy 复用同一检查。

**回归测试**

同族任一满足、跨族各自满足、`IN`、`BETWEEN`、注释/字符串伪造、Legacy 路径。

### P1-2 JOIN 基数保护是文本正则，存在漏判（静态审查）

**位置**

- `sql_validator.py:151-172` `_check_p2`。
- `semantic_layer.py:350-362` 注入 `【JOIN 铁律】`。

**现状**

- 只检查 `pre_agg_required=1` 的关系。
- `direct`：SQL 中任意位置出现 `JOIN right_table`。
- `subq`：任意位置出现 `(SELECT ... FROM right_table ... GROUP BY`。
- 二者同时存在 → 认为已预聚合，不报错。

**漏判场景**

- `SELECT ... FROM (SELECT ... FROM fee GROUP BY order_id) f JOIN fee ON ...`：`direct` 命中第二个直接 JOIN，`subq` 命中第一个预聚合 → 不报错，但第二个直接 JOIN 仍会放大。
- CTE 预聚合：`WITH agg AS (SELECT ... FROM fee GROUP BY order_id) SELECT ... JOIN agg`：`direct` 不命中 `fee`（JOIN 的是 `agg`），不会报错（这点是对的）；但如果同一 SQL 还有一处直接 `JOIN fee`，也会被 `subq` 的 CTE 误判为“已预聚合”。
- 只对 `pre_agg_required=1` 生效；`cardinality='1:N'` 但未勾选 `pre_agg_required` 的关系不检查。
- 不校验 JOIN 键、业务范围条件、预聚合粒度是否与最终粒度一致。

**建议**

- 用 sqlglot 构建 JOIN 图：区分“直接 JOIN 基表”与“JOIN 预聚合子查询/CTE”；
- 对每条 1:N 关系检查右侧是否被预聚合、JOIN 键是否匹配、粒度是否一致；
- `cardinality='1:N'` 至少给 warning，`pre_agg_required` 给 error；
- 保留现有 Prompt 铁律，代码校验作为硬闸门。

### P1-3 自愈后原始需求保持性不足（静态审查）

**位置**

- `prompt.py:150-166` `build_repair_prompt`：只要求“针对错误信息修正”，**没有要求保持指标/时间/维度/过滤/JOIN**。
- `agent.py:406`（Legacy 自愈）、`agent.py:597`（V2 执行失败自愈）都调用它。
- V2 校验自愈（`agent.py:551-554`）反而有“保持原问题统计口径与分组维度不变”，两条路径不一致。

**风险**

- 执行失败自愈时，模型可能把“按月分组”修成“总数”、把时间范围改掉、丢掉过滤条件。
- 系统只检查语法/执行成功，不检查需求是否仍被满足。

**建议**

1. `build_repair_prompt` 增加硬性约束：

```
必须保持：指标口径、时间范围、分组维度、过滤条件、JOIN 关系不变；
只修正导致执行失败的部分；若无法在保持上述条件的前提下修复，输出 `-- 无法修复：原因`。
```

2. 新增 `check_requirement_preserved(understanding, original_sql, repaired_sql)`：
   - 指标集合、时间范围、维度、过滤条件、JOIN 表集合；
   - 用 AST/关键词双重比对；
   - 无法确认 → 拒绝执行或要求澄清，不能仅凭“执行成功”放行。
3. 记录每次修复的 SQL 版本与差异，供审核/回放。

### P1-4 结果校验不能替代业务正确性评测（已核对 baseline）

**位置**

- `result_validator.py:34-94`：只产出 warning（比率越界、全 NULL、全 0、期望多行却 1 行）。
- `evaluation/reports/baseline.json`：

```json
{
  "overall_accuracy": 0.8571,
  "sql_accuracy": 0.8462,
  "sql_execution_success_rate": 0.9143,
  "metric_accuracy": 0.9545,
  "table_retrieval_recall": 1.0,
  "result_accuracy": null,
  "field_retrieval_recall": null,
  "consistency_rate": null,
  "sample_count": 35,
  "executed_count": 26
}
```

- `evaluation/gate.py:11-20` 阈值：`overall_accuracy >= 0.93`、`sql_accuracy >= 0.94`、`sql_execution_success_rate >= 0.98`。

**结论**

- 评测集已有 **94 条**（golden 35 条，超过 30 道要求），覆盖 8 类场景。
- 但 `result_accuracy` 为 `null`：golden 用例大多只有 `expected_tables` / `expected_metrics` / `expected_sql_contains`，**没有参考结果/基准值**。
- 当前 baseline 的 `overall_accuracy=0.8571`、`sql_accuracy=0.8462`、`execution=0.9143` **均低于** gate 阈值；发布门禁会失败。
- 这组数字是历史运行记录，不代表已人工验证的业务准确率；不能据此宣称“业务结果准确率”。

**建议**

- 保留现有 warning，不无限增加“凭结果形态”的硬规则；
- 为关键 golden 用例补 `expected_sql` 或 `expected_result`（基准值/允许误差）；
- 增加 `result_accuracy`、`field_retrieval_recall`、`consistency_rate` 的计算；
- 没有真实数据支持的题目，明确标记 `benchmark: null`，不得伪造。

### P1-5 可解释性与可追踪性缺口（已核对持久化字段）

**位置**

- `config_store.py:1935-1976` `add_query_record` 持久化字段：
  `username, question, sql, ok, tables, metrics, dicts, columns, row_count, result_preview, answer, info, attempts, pipeline, understanding, review_status, created_at`。
- **未持久化**：`validation`、`result_warnings`、`trace`、`degraded`、`degrade_reason`、`degrade_stage`。
- `agent.py:638`：`attempts=attempt` 来自校验循环，执行失败自愈后不更新。
- `agent.py:641`：`validation=last_issues` 是执行前旧报告；执行失败自愈后不重新校验，报告与最终 SQL 不一致。
- `ask_legacy` 无 `trace`。

**风险**

- 事后审核看不到“当时校验发现了什么、是否降级、各阶段耗时、自愈了几次”。
- 自愈后的 SQL 与 validation 不匹配，误导复盘。

**建议**

- `query_records` 增加 `audit_json`（或分列）：`validation`、`result_warnings`、`trace`、`degraded`、`degrade_reason`、`degrade_stage`、`sql_versions`、`gate_stage`；
- `attempts` 统计“校验重试 + 执行重试”；
- 每次自愈记录 `before_sql/after_sql/reason`；
- 普通用户错误信息脱敏（见 P0-5.4）。

---

## 5. P2 观察项（P0/P1 后再处理）

1. **检索阈值是启发式**：`config.py:100-108`（0.45/0.50/0.55）、`SATELLITE_KEEP_THRESHOLD=0.60`、`NAMED_TABLE_MIN_CHARS=4`，注释已标注“样本有限，扩容后需重校准”。
2. **P0~P4 分级注入已存在**：`semantic_layer.build_prompt_parts` 顺序为 role → 时间 → P0→P1→P2→P3→P4；RAG 的 P3/P4 结构上不能覆盖 P0~P2。需要补的是“命中/未命中理由”的可观测性，而不是重建分级。
3. **检索命中说明有限**：`info` 只有 `route_info`、字典原因、时间摘要；缺“为什么没命中某张表/指标”的逐条理由。
4. **`field_retrieval_recall` 未计算**：评测只到表级召回，字段级召回为空。
5. **`test_gaps.py` 分层不一致**：`run_tests.py:36` 标为 `integration`，但文件 docstring 写“不连数据库、不加载模型”；实际运行会通过 `gaps.py` 访问 ClickHouse 注释。
6. **`test_acl.py` 写真实配置库**：`test_acl.py:101-208` 会 `set_table_acl` / `set_metric_enabled`，虽有还原，但 `run_tests.py` 未隔离 `CONFIG_DB`；本次审查手动指向 `/tmp` 副本才避免污染。
7. **`/api/status` 暴露 `allowed_tables`**：见 P0-5.3。
8. **`_ensure_limit` 子查询 LIMIT**：见 P0-5.5。

---

## 6. 已具备能力与历史报告差异

### 6.1 已经实现、应保留、不要重复建设

| 能力 | 位置 | 结论 |
|---|---|---|
| V2 分层语义流水线（P0~P4） | `semantic_layer.py` | 已实现，保留 |
| 执行前五维校验 | `sql_validator.py` | 已实现，需修 P0-4/P1-1/P1-2 |
| 唯一执行出口 + 拒答识别 | `agent.py:96-114`、`_refusal_reason` | 已实现，保留 |
| V2 校验失败 fail-closed | `agent.py:558-572` | 已实现，保留 |
| 可恢复故障白名单降级 | `agent.py:649-663` | 已实现，保留 |
| 表白名单 + CTE 排除 | `guard.py:260-379` | 已实现，需修 P0-3 |
| 强制 LIMIT + 服务端上限 | `db_clickhouse.py:367-425` | 已实现，需修 P0-5.5 |
| RBAC 中间件（默认 admin） | `server.py:91-141` | 已实现，保留 |
| 会话过期/停用即时失效 | `config_store.py:1860-1880` | 已实现，保留 |
| PBKDF2 + 随机盐 | `config_store.py:1597-1610` | 已实现，保留 |
| 账号 CRUD + 登录名重命名 | `config_store.py:update_user` / `server.py` / 前端 | 已实现，保留 |
| 指标硬约束字段 | `config_store.py:377-384`、`733-755` | 已实现，需修判定逻辑 |
| JOIN 预聚合规则 | `config_store.py:583-584`、`semantic_layer.py:350-362` | 已实现，需升级校验 |
| 评测集 94 条 | `evaluation/datasets/` | 已实现，需补基准结果 |
| 测试分层 runner | `run_tests.py` | 已实现，需补计数与隔离 |

### 6.2 与历史报告的差异

1. **“run_tests 多项失败 / 缺 openai”**：在当前 `.venv` 下未复现；unit 8/8、config 83/83、integration 51/51 全绿。更可能是历史执行环境未装依赖，而非代码缺陷。
2. **`test_guard.py` 的 `❌`**：部分是“旧正则 ❌ / 新解析器 ✅”的对比演示，不是失败；`run_tests.py:65-81` 已专门处理这种行中间 `❌`，只有行首 `❌` 才判失败。
3. **`test_gaps.py`**：文件自称纯逻辑，实际会访问 ClickHouse 注释；runner 标为 integration。分类需要修正，但不能据此判定代码缺陷。
4. **`test_acl.py`**：确实写配置库；历史报告若在无备份情况下跑过，可能已改动 ACL/指标状态。本次已先备份 `agent_config.db`，并用 `/tmp` 副本运行。

---

## 7. 测试基线详解

### 7.1 环境

| 项 | 值 |
|---|---|
| Python | `.venv`（3.13） |
| 依赖 | openai / fastapi / uvicorn / sqlglot / clickhouse_connect / fastembed / numpy 均可用 |
| 数据源 | `DB_TYPE=clickhouse`，`CK_DATABASE=ctc_data_warehouse`，本地隧道 `127.0.0.1:8123` |
| ACL 默认 | 当前 `.env`：`ACL_DEFAULT_VISIBLE=False`（fail-closed） |
| 降级模式 | `LEGACY_FALLBACK_MODE=whitelist` |
| 配置库 | 测试时指向 `/tmp/stage0_agent_config.db` 副本；生产库已备份到 `/tmp/agent_config.db.stage0.bak` |

### 7.2 结果

```
compileall: exit 0

unit:
  ✅ test_imports.py              🎉 全部可导入（26/26）
  ✅ test_pipeline.py             🎉 全部通过（11/11）
  ✅ test_gate.py                 🎉 全部通过（49/49）
  ✅ test_join_suggest.py         🎉 全部通过（10/10）
  ✅ test_guard.py                🎉 全部通过
  ✅ test_labels.py               🎉 全部通过（33/33）
  ✅ test_retrieve.py             🎉 全部通过（22/22）
  ✅ evaluation/test_calendar.py  ✅ 全部通过

config:
  ✅ test_acl.py                  🎉 全部通过（83/83）

integration:
  ✅ test_gaps.py                 🎉 全部通过（51/51）
```

### 7.3 测试体系缺口

1. **缺“环境阻塞”独立状态**：`run_tests.py` 只有 PASS/FAIL/SKIP；集成测试失败没有单独归类为“未连接真实库”。
2. **缺聚合计数**：没有统一输出“通过 N / 失败 N / 跳过 N / 环境阻塞 N”。
3. **缺配置库隔离**：`test_acl.py` 默认写 `agent_config.db`。
4. **缺执行调用次数断言**：现有 `test_gate` 主要测“是否拒绝”，未系统断言“违规 SQL 的 `db.execute_sql` 调用次数为 0”（P0-1 的新测试需要补）。
5. **缺自愈需求保持测试**：没有“修复后维度/时间/过滤是否丢失”的自动化用例。

---

## 8. 阶段 1 准备修改的文件清单与兼容性影响

> 本节只是“准备改什么”，阶段 0 不落代码。建议按 P0-1 → P0-3 → P0-4 → P0-2 → P0-5 的顺序小步提交，每步先跑测试再进入下一步。

### 8.1 核心改动

| 文件 | 计划改动 | 兼容性影响 |
|---|---|---|
| `agent.py` | 新增 `_pre_execute_gate`；所有执行路径统一调用；执行失败自愈重新校验；`attempts`/`validation` 修正；Legacy 接入同一闸门；接入需求保持检查 | 内部函数新增；返回字段增加，不删旧字段；行为更严格（可能拒绝以前会执行的 SQL） |
| `guard.py` | `extract_tables_detail` 返回可靠性；`check_tables` 增加 `strict`；正则支持引号/CTE 列清单；`sql_guard` 增补高危子句 | `extract_tables` 签名不变；`check_tables` 默认 `strict=False` 保持旧调用兼容，生产链路显式 `strict=True` |
| `sql_validator.py` | 新增 `ValidationResult` / `validate_sql_ex`；各 check 独立异常处理；修 `_check_p1` 分组逻辑；`_check_p2` 升级 AST；去掉 `startswith("--")` 提前返回 | 保留 `validate_sql` 返回 list 的旧接口；主链路切到 `validate_sql_ex`；测试可渐进迁移 |
| `prompt.py` | `build_repair_prompt` 增加“保持指标/时间/维度/过滤/JOIN”约束 | 仅提示词文本变化；不影响函数签名 |
| `config.py` | `ACL_DEFAULT_VISIBLE` 默认 false；新增 `STRICT_TABLE_EXTRACTION`、`REQUIRE_REPAIR_PRESERVATION`、限流配置 | 默认值变化会影响未配置 `.env` 的新环境（预期为更安全） |
| `config_store.py` | `query_records` 增加 `audit_json`（或分列）；默认管理员初始化改造；迁移逻辑 | SQLite `ALTER TABLE ADD COLUMN` 向后兼容；旧记录字段为空 |
| `server.py` | `/api/status` 按角色裁剪；错误脱敏；持久化 audit；可选限流 | 响应字段减少（普通用户）；前端只用计数字段，预计不破坏 |
| `db_clickhouse.py` | 外层 LIMIT AST 判断；启动只读账号探测 | 查询行为更严格；只读探测失败时告警/拒绝可配置 |
| `run_tests.py` | 增加 config 层临时 DB 隔离；增加 PASS/FAIL/SKIP/ENV_BLOCKED 计数；修正 `test_gaps` 分类 | 仅测试工具变化 |

### 8.2 新增/修改测试

| 测试 | 覆盖 |
|---|---|
| `test_execution_gate.py`（新） | P0-1 六种自愈场景；断言 `db.execute_sql` 调用次数 |
| `test_guard_parse_fail.py`（新/扩展 `test_guard.py`） | P0-3 解析失败、解析器缺失、引号表名、多语句、CTE |
| `test_validator_states.py`（新/扩展 `test_gate.py`） | P0-4 三态、各 check 注入异常、前导注释 |
| `test_requirement_preservation.py`（新） | P1-3 维度/时间/过滤/JOIN 丢失 |
| `test_p1_family.py`（新/扩展 `test_gate.py`） | P1-1 同族/跨族硬约束 |
| `test_acl.py`（改造） | 使用临时 `CONFIG_DB`，不写生产库 |
| `evaluation/datasets/golden`（补充） | 关键题补 `expected_sql` / `expected_result`，计算 `result_accuracy` |

### 8.3 数据库迁移

- `query_records` 新增 `audit_json TEXT`（或 `validation_json` / `trace_json` / `degraded` / `degrade_stage` 分列）。
- 使用 `ALTER TABLE ... ADD COLUMN ...`，旧数据为 NULL；读取端做 `or ""` 兼容。
- 迁移前备份 `agent_config.db`；提供回滚（删除新增列或恢复备份）。
- 不改变 `users` / `sessions` / 知识表结构。

### 8.4 回滚策略

- 每个 P0 单独提交，附对应测试；任一阶段失败可 `git revert` 单个提交。
- 新增配置开关默认“更安全”，但保留旧行为开关（如 `STRICT_TABLE_EXTRACTION=false` 可回退到正则），便于灰度。
- 数据库迁移只加列不删列；回滚代码后旧列不影响运行。

---

## 9. 阶段 1 实施顺序与验收

### 9.1 顺序

1. **P0-1**：统一执行前闸门 + 执行失败自愈重新校验 + 六种回归测试。
2. **P0-3**：表名提取可靠性 + strict fail-closed + 引号/多语句/解析失败测试。
3. **P0-4**：校验器三态 + 异常注入测试；去掉前导注释绕过。
4. **P0-2**：Legacy 接入同一闸门 + `always` 风险限制 + 降级留痕测试。
5. **P0-5**：默认口令/状态接口/错误脱敏/LIMIT/只读账号/限流（按风险从高到低）。

### 9.2 每步验收

- 先跑 `run_tests.py unit` / `config` / `integration`；全绿再提交。
- 新增测试必须包含“违规 SQL 的 `db.execute_sql` 调用次数为 0”。
- 每步汇报：改了什么、测试结果、未解决问题、回滚方式。
- 不在阶段 1 动 P1 业务规则/JOIN/评测集（阶段 2 再做），避免一次大改。

### 9.3 阶段 1 完成标准

- 所有执行路径（V2 初次、V2 校验自愈、V2 执行自愈、Legacy）都经过同一个 `_pre_execute_gate`。
- 修复 SQL 违规时，`db.execute_sql` 不被调用。
- 解析器缺失/解析失败时，生产链路 fail-closed。
- 校验器异常不会被当成通过；关键检查异常必须拒绝。
- Legacy 不会绕过 V2 的核心安全检查。
- 测试报告能区分代码失败/环境缺失/集成未执行，并给出计数。

---

## 10. 限制与未验证项

1. **未做真实业务准确率验证**：当前只有历史 baseline（`overall_accuracy=0.8571`），且 `result_accuracy=null`；不能宣称业务口径准确率。
2. **P1-2 JOIN 漏判为静态推导**：未构造运行时 SQL 用例；阶段 2 需补 AST 测试。
3. **P0-2 `always` 风险为静态审查**：未在真实 V2 异常上跑降级；阶段 1 用 mock 异常补测。
4. **P0-5 只读账号**：未执行 `SHOW GRANTS`；当前仅确认配置默认值与注释要求。
5. **前端 XSS/CSRF**：本次未做前端安全审计；`server.py` 使用 Bearer token，未使用 Cookie，CSRF 风险较低，但存储型 XSS 仍需单独检查。
6. **真实 ClickHouse 集成**：`test_gaps` 已连真实库并通过，但未跑全量业务回归；阶段 2 需在真实库上跑 golden 集。
7. **临时脚本**：`/tmp/stage0_repro.py` 与 `/tmp/stage0_agent_config.db` 为审查用临时文件，不属于项目源码；可删除。
8. **配置库备份**：`/tmp/agent_config.db.stage0.bak` 为审查前备份；生产库未被测试写入（config 层已指向 `/tmp` 副本）。

---

## 附录 A：关键行号索引

| 问题 | 文件:行 |
|---|---|
| P0-1 执行失败自愈 | `agent.py:588-617`（核心 596-601） |
| P0-1 校验自愈（对照） | `agent.py:528-572` |
| P0-2 Legacy 主流程 | `agent.py:256-411` |
| P0-2 `always` 降级 | `agent.py:666-698`、`config.py:149-154` |
| P0-3 提取回退 | `guard.py:339-354` |
| P0-3 `check_tables` | `guard.py:357-379` |
| P0-3 正则表名 | `guard.py:217`、`guard.py:306-325` |
| P0-4 P3 异常吞掉 | `sql_validator.py:279-293` |
| P0-4 列校验跳过 | `sql_validator.py:235-276` |
| P0-4 前导 `--` 跳过 | `sql_validator.py:343-344` |
| P0-5 默认管理员 | `config_store.py:1590-1595`、`1636-1654` |
| P0-5 ACL 默认 | `config.py:168-169` |
| P0-5 状态接口 | `server.py:2394-2407` |
| P0-5 LIMIT 判定 | `db_clickhouse.py:395` |
| P1-1 P1 硬约束 | `sql_validator.py:62-145`（核心 139） |
| P1-2 JOIN 校验 | `sql_validator.py:151-172` |
| P1-3 修复提示词 | `prompt.py:150-166` |
| P1-4 结果校验 | `result_validator.py:34-94` |
| P1-5 记录持久化 | `config_store.py:1935-1976` |
| P1-5 attempts/validation | `agent.py:638-641` |
| 测试分层 | `run_tests.py:26-37` |

## 附录 B：复现结果原文

```text
P0-1 复现：执行失败自愈 → 越权 SQL 是否被执行？
结果 ok = True
最终 SQL = SELECT * FROM ctc_data_warehouse.secret_table
db.execute_sql 调用序列 = [
  'SELECT * FROM ctc_data_warehouse.allowed_table',
  'SELECT * FROM ctc_data_warehouse.secret_table'
]
guard 调用次数 = {'sql_guard': 1, 'check_tables': 1}
>>> P0-1 判定：❌ 存在绕过（修复 SQL 被执行，未过表权限）

P0-3 复现：解析器缺失 + 反引号表名 → 是否 fail-open？
extract_tables = set()
check_tables   = (True, [])
>>> P0-3 判定：❌ 空提取被默认放行

P0-3 补充：sql_guard 对 INTO OUTFILE / OPTIMIZE 的态度
INTO OUTFILE : (True, '')
OPTIMIZE     : (True, '')

P0-4 复现：P3 提取抛异常 → validate_sql 是否返回空 issues？
validate_sql issues = []
>>> P0-4 判定：❌ 异常被吞，返回空 issues（等同通过）

P1-1 复现：A 满足、B 不满足 → 是否报 error？
issues = []
>>> P1-1 判定：❌ B 的硬约束缺失但未报 error
```

---

## 附录 C：阶段 1 开工前需要你确认的事项

1. **是否同意“统一执行前闸门”作为唯一入口**（所有路径，包括 Legacy 和自愈）？
2. **`LEGACY_FALLBACK_MODE=always` 的处理**：生产环境直接拒绝启动，还是只告警并允许临时使用？
3. **`ACL_DEFAULT_VISIBLE` 代码默认值**：是否改为 `false`（开发/演示需显式开启）？
4. **`/api/status` 的 `allowed_tables`**：普通用户是否只保留计数？
5. **默认管理员口令**：改为 `ADMIN_INIT_PASSWORD` 环境变量 + 首登强制改密，是否接受？
6. **阶段 1 是否只做 P0，不动 P1/P2**（推荐），还是希望把 P1-1/P1-3 一并做？

> 按任务书要求，阶段 0 到此为止；未收到上述确认前，不进入阶段 1，不改源码。
