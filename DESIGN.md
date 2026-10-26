# 公司级 AI 取数 Agent 设计方案（ClickHouse 版）

> 目标：在现有 `ai-data-agent` 基础上，做成一个**带前端页面、可配置、连 ClickHouse** 的
> 公司级 AI 取数系统。
>
> 页面三大功能：
> ① 问题输入（自然语言 → SQL → 结果）
> ② SQL 展示与执行
> ③ Prompt 在线编辑区
> ④ RAG 知识配置区

---

## 一、总体架构

```
┌─────────────────────────────────────────────────────────────┐
│  浏览器（前端页面）                                            │
│  ┌───────────┬───────────┬──────────────┬───────────────┐   │
│  │ 问答区     │ SQL/结果区 │ Prompt 编辑区 │ 知识库配置区   │   │
│  └───────────┴───────────┴──────────────┴───────────────┘   │
└─────────────────────────────────────────────────────────────┘
                            │  HTTP / SSE（流式）
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  后端 API 层（FastAPI）                                       │
│    POST /api/ask              提问 → 取数                     │
│    GET  /api/prompts          读 Prompt 配置                  │
│    PUT  /api/prompts/{key}    改 Prompt 配置                  │
│    GET/POST/PUT/DELETE /api/metrics   指标知识 CRUD            │
│    GET/PUT /api/tables        表业务注释 CRUD                  │
│    POST /api/index/rebuild    重建 RAG 索引（异步）             │
│    POST /api/eval/run         跑评测                           │
│    GET  /api/logs             查询审计日志                      │
└─────────────────────────────────────────────────────────────┘
                            │
                            ▼
┌─────────────────────────────────────────────────────────────┐
│  Agent 服务（★ 复用现有 ai-data-agent 的模块，几乎不用改）      │
│    agent.py（编排）/ rag.py（检索）/ prompt.py（组装）          │
│    guard.py（护栏）/ llm.py（大模型）/ db.py（数据库）           │
└─────────────────────────────────────────────────────────────┘
         │                                    │
         ▼                                    ▼
┌──────────────────────┐        ┌──────────────────────────┐
│  ClickHouse（业务数据）│        │  配置库（MySQL / SQLite）  │
│  只读账号              │        │  prompts / metrics        │
│  查询超时 + 强制 LIMIT │        │  table_comments / logs    │
└──────────────────────┘        └──────────────────────────┘
```

---

## 二、前端设计（三大区域）

### 页面布局（推荐 Tab 式，避免信息过载）

```
┌──────────────────────────────────────────────────────────────┐
│  AI 取数助手                              [取数] [配置] [评测] │
├──────────────────────────────────────────────────────────────┤
│                                                              │
│  【Tab 1：取数】                                              │
│  ┌────────────────────────────────────────────────────────┐  │
│  │  请输入你的问题…                          [ 提 交 ]     │  │
│  │  例：统计2026年7月各城市完单率和取消率                    │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 处理过程 ─────────────────────────────────────────────┐  │
│  │ ✅ 检索到表：dwd_order_all                              │  │
│  │ ✅ 检索到指标：完单率、取消率（关键词命中）               │  │
│  │ ✅ 生成 SQL（1.2s）                                     │  │
│  │ ✅ 安全检查通过                                          │  │
│  │ ✅ 执行完成（0.3s，共 12 行）                            │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ SQL ──────────────────────────────────────────────────┐  │
│  │ SELECT city,                                          │  │
│  │        SUM(CASE WHEN status=6 THEN 1 ELSE 0 END)*1.0  │  │
│  │        / COUNT(*) AS finish_rate                       │  │
│  │ FROM dwd_order_all                                     │  │
│  │ WHERE create_time >= '2026-07-01'                      │  │
│  │   AND create_time <  '2026-08-01'                      │  │
│  │ GROUP BY city                                          │  │
│  │                              [复制] [重新执行] [编辑执行]│  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
│  ┌─ 结果 ─────────────────────────────────────────────────┐  │
│  │ city      │ finish_rate │ cancel_rate                  │  │
│  │ 北京      │    0.8000   │   0.1000                     │  │
│  │ 上海      │    0.6667   │   0.0000                     │  │
│  │ …                                     [导出 CSV]       │  │
│  └────────────────────────────────────────────────────────┘  │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  【Tab 2：配置】                                              │
│    ┌─ Prompt ─────────┬─ 指标知识 ────┬─ 表注释 ──────┐      │
│    │ sql_rules  (642字) │ 订单量         │ dwd_order_all  │      │
│    │ role       (42字)  │ 完单量         │ dim_user       │      │
│    │                   │ 取消率         │ dim_driver     │      │
│    │ [编辑] [重置默认]   │ [新增][编辑]   │ [编辑]         │      │
│    └───────────────────┴───────────────┴───────────────┘      │
│    ⚠️ 修改后需点击 [重建索引] 才会对检索生效                     │
│                                                              │
├──────────────────────────────────────────────────────────────┤
│  【Tab 3：评测】                                              │
│    [ 开始评测 ]   总准确率 100.0% (16/16)                     │
│    分类统计：基础 3/3 | 跨表 3/3 | 指标 4/4 | 口语 2/2 …       │
│    失败详情：…                                                │
└──────────────────────────────────────────────────────────────┘
```

### 前端技术选型（分两步走，别一上来就上重框架）

| 阶段 | 方案 | 优点 | 适合 |
|---|---|---|---|
| **第一步** | 单个 `index.html`（原生 JS + fetch） | 零构建、零依赖、改完刷新就生效 | 先跑通 |
| **第二步** | Vue3 + Vite + Element Plus | 组件化、好维护、生态成熟 | 正式产品 |

**建议先做第一步。** 理由：前端框架的坑（构建、依赖、跨域）会把你刚建立的信心打散。
先用一个 HTML 文件把"后端能跑通、页面能显示"验证掉，再上框架。

---

## 三、后端 API 设计

| 方法 | 路径 | 作用 | 请求 | 返回 |
|---|---|---|---|---|
| POST | `/api/ask` | 提问取数 | `{question}` | `{ok, sql, columns, rows, tables, metrics, attempts, elapsed}` |
| POST | `/api/ask/stream` | 流式提问（SSE） | `{question}` | 事件流：检索→生成→校验→执行→结果 |
| GET | `/api/prompts` | 列出 Prompt 配置 | — | `[{key, content}]` |
| PUT | `/api/prompts/{key}` | 修改 Prompt | `{content}` | `{ok}` |
| POST | `/api/prompts/{key}/reset` | 恢复默认 | — | `{ok}` |
| GET | `/api/metrics` | 列出指标 | — | `[{name, keywords, definition, doc, tables}]` |
| PUT | `/api/metrics/{name}` | 新增/修改指标 | 指标对象 | `{ok}` |
| DELETE | `/api/metrics/{name}` | 删除指标 | — | `{ok}` |
| GET | `/api/tables` | 列出表的业务注释 | — | `[{table, comment}]` |
| PUT | `/api/tables/{name}` | 修改表注释 | `{comment}` | `{ok}` |
| POST | `/api/index/rebuild` | 重建 RAG 索引 | — | `{ok, tables, metrics}` |
| POST | `/api/eval/run` | 跑评测 | — | `{accuracy, details}` |
| GET | `/api/logs` | 审计日志 | `?limit=100` | `[{time, user, question, sql, ok, elapsed}]` |

### 为什么要有 `/api/ask/stream`（流式）

```
非流式：用户点提交 → 干等 3~5 秒（检索+生成+执行）→ 突然出结果
        → 用户会怀疑"是不是卡了"、"是不是没点上"

流式  ：用户点提交 → 立刻看到"正在检索…" → "正在生成 SQL…"
        → "安全检查通过" → "执行完成" → 出结果
        → 同样的 3~5 秒，感受完全不同
```

**这是一个纯体验优化，但对企业系统的采纳率影响巨大。** 用 SSE（Server-Sent Events）实现最简单。

---

## 四、数据模型（配置库）

现在用 SQLite，生产建议换 MySQL / PostgreSQL（支持并发写、多人协作）。

```sql
-- ① Prompt 配置（已有）
CREATE TABLE prompts (
    key     VARCHAR(64) PRIMARY KEY,
    content TEXT,
    updated_at TIMESTAMP,
    updated_by VARCHAR(64)
);

-- ② 指标知识（已有，建议加审计字段）
CREATE TABLE metrics (
    name       VARCHAR(128) PRIMARY KEY,
    keywords   TEXT,      -- JSON 数组
    definition TEXT,
    doc        TEXT,
    tables     TEXT,      -- JSON 数组
    updated_at TIMESTAMP,
    updated_by VARCHAR(64)
);

-- ③ 表业务注释（已有）
CREATE TABLE table_comments (
    table_name VARCHAR(128) PRIMARY KEY,
    comment    TEXT,
    updated_at TIMESTAMP
);

-- ④ ★ 新增：查询审计日志（企业级必备）
CREATE TABLE query_logs (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    ts          TIMESTAMP,
    user_name   VARCHAR(64),      -- 谁问的
    question    TEXT,             -- 问了什么
    tables_used TEXT,             -- 命中了哪些表
    metrics_used TEXT,            -- 命中了哪些指标
    sql_text    TEXT,             -- 实际执行的 SQL
    ok          BOOLEAN,
    error       TEXT,
    row_count   INT,
    elapsed_ms  INT,
    tokens      INT               -- 成本核算用
);

-- ⑤ ★ 新增：配置修改历史（可回滚）
CREATE TABLE config_history (
    id          BIGINT AUTO_INCREMENT PRIMARY KEY,
    ts          TIMESTAMP,
    config_type VARCHAR(32),      -- prompt / metric / table_comment
    config_key  VARCHAR(128),
    old_value   TEXT,
    new_value   TEXT,
    operator    VARCHAR(64)
);

-- ⑥ ★ 新增：评测用例（把 eval_cases.py 从代码搬进库）
CREATE TABLE eval_cases (
    id           BIGINT AUTO_INCREMENT PRIMARY KEY,
    question     TEXT,
    expected_sql TEXT,            -- NULL = 期望被拒绝
    category     VARCHAR(32),
    enabled      BOOLEAN DEFAULT TRUE
);
```

**为什么要 `config_history`？**
```
业务同学改了指标口径 → 一周后发现数据不对 → 想回滚
        ↓
没有历史记录 → 只能靠记忆 + 手动改回
有历史记录 → 一键回滚，还能看到"谁在什么时候改的"
```

---

## 四之二、★★ 数据权限设计（表级访问控制）

> 企业红线：**不是所有表都该给 AI 看见。**
> 财务表、用户手机号、身份证、薪酬、日志等敏感数据，
> 绝不能因为一句自然语言就被查出来。

### 五层防线（已实现前四层）

```
第一层  数据库账号层        只读账号 + 表级授权（改不了）
        ↓
第二层  表发现层（RAG）     只抓"可见表"，AI 根本看不见其他表
        ↓                   ★ 看不见 = 不可能查（最有效）
第三层  Prompt 约束         规则9：只能查上面列出的表
        ↓                   ★ 软约束，靠模型自觉
第四层  SQL 校验（guard）   提取 SQL 表名 → 不在白名单就拒绝
        ↓                   ★ 硬约束，代码说了算
第五层  审计日志            谁在什么时候查了什么表
```

### 为什么第二层最有效？

```
我们修"幻觉"时得到的经验：
    只给模型看 1 张表  →  它不可能引用第 2 张表
        ↓
权限控制同理：
    不给它看见的表，它连"编"都编不出来（因为它不知道这张表存在）
```

### 为什么还需要第四层？

```
Prompt 是软约束，模型可能不遵守；
RAG 过滤只影响"它看见什么"，
但模型可能从训练数据里知道一张敏感表的名字（比如 user_phones），
凭记忆写出来。
        ↓
所以必须在【执行前】做一次硬校验。
```

### 实现位置

| 层 | 代码位置 | 实现方式 |
|---|---|---|
| 第二层 | `rag.py::_filter_visible()` | 从 `fetch_schema()` 结果里剔除 `visible=0` 的表 |
| 第二层 | `config.py::ACL_DEFAULT_VISIBLE` | 黑名单模式（默认可见）/ 白名单模式（默认拒绝） |
| 第三层 | `config_store.py` 的 `sql_rules` 规则9 | Prompt 里明确禁止查未列出的表 |
| 第四层 | `guard.py::extract_tables() / check_tables()` | **SQL 解析器（sqlglot）提取真实表名**，比对白名单；解析失败时回退正则 |
| 配置 | `config_store.py::table_acl` 表 | `table_name / visible / note` |
| 管理 | `admin.py set-acl / del-acl / list-acl / check-acl` | 命令行管理 |
| **指标层** | `config_store.py::metrics.enabled` | 指标开放开关（1/0） |
| **指标层** | `rag.py::match_disabled_metric()` | 问题问到停用指标 → 入口拦截 |
| **指标层** | `admin.py set-metric <名> on\|off` | 命令行管理 |

### 两种 ACL 策略

```
黑名单模式（ACL_DEFAULT_VISIBLE = True）
    表没登记 → 默认【可见】
    → 适合演示库（表少、都知道是什么）
    ⚠️ 真实库【不要用】这个——几百张表会全部可见

白名单模式（ACL_DEFAULT_VISIBLE = False）
    表没登记 → 默认【不可见】
    → ★ 真实库用这个（默认拒绝，逐个授权）
```

### ★★ 指标级权限（和表级对称的另一半）

**限制"能查哪些表"还不够，还要限制"能回答哪些指标"。**

```
问题：管理者说"只开放订单量、完单率、取消率三个指标"
        ↓
如果只做表级权限：AI 仍能用订单表算出客单价、GMV……
        ↓
而且它算出的口径是【它自己拼的】，不是公司的口径 → 静默算错
```

#### 实现机制（两道）

| 道 | 位置 | 作用 |
|---|---|---|
| ① 不进知识库 | `knowledge.get_metrics()` 只返回 `enabled=1` 的 | AI 检索不到该指标的口径，不知道它是什么 |
| ② **入口拦截** | `rag.match_disabled_metric()` + `agent.ask()` | 问题问到停用指标 → 直接拒绝，不给模型"自己凑口径"的机会 |

**为什么必须有第二道？**

```
停用客单价 → 知识库里没有它的口径
        ↓
但模型看到 dwd_order_all 表里有 amount 字段
        ↓
它自己拼一个 SUM(amount)/COUNT(*) 出来
        ↓
SQL 能跑、不报错、但口径是错的 → 静默算错（企业里最贵的错误）
        ↓
所以必须在入口处拦掉，宁可说"该指标未开放"
```

#### ★ 判定规则：最长关键词优先

停用一个指标时，它的关键词里可能有短词，会误伤别的指标：

```
场景：停用「完单量」（关键词：完单量 / 完单 / 完成订单）
     开放「完单率」（关键词：完单率 / 完成率）

问题"查询完单率" → "完单"（长度 2）也命中了 → 简单写法会把合法的完单率误拦 ❌
        ↓
规则：让【命中的最长关键词】说了算
      "完单率"(3) > "完单"(2) → 判定为在问完单率 → 放行 ✅
```

**这条规则和 `_hybrid_retrieve` 里解决"子串陷阱"用的是同一个思路**——短词是长词的子串时，长词更具体，应该由长词决定。

> 这是本项目第三次遇到"短词误伤长词"这个问题（前两次在检索层）。
> 说明它不是某个函数的偶然缺陷，而是**所有基于关键词匹配的系统都会遇到的固有矛盾**。
> 记住这个模式，你以后设计任何"关键词匹配"功能时都会提前想到它。

#### 管理方式

```bash
python admin.py list-metrics              # 看全部指标 + 开放状态
python admin.py set-metric 客单价 off      # 停用
python admin.py set-metric 客单价 on       # 开放
```
页面上：「指标知识」Tab → 选中指标 → 右侧「开放状态」区块 → 点切换。
**改完要点「重建索引」。**

#### 专项测试

```bash
python test_acl.py
```
覆盖：表级两种模式、指标级拦截、**短关键词误伤边界**、指标名大小写、以及"停用指标不进索引"。
共 21 条用例，不调大模型、不联网，几秒钟跑完。

★ 企业实践：先黑名单快速跑通，稳定后切白名单。
  切换只需改 1 行配置 + 给允许的表逐个授权。
```

### ClickHouse 只读账号（第一层，必须在数据库侧做）

```sql
-- ① 建一个专用只读账号（千万别用 default 账号）
CREATE USER ai_reader IDENTIFIED BY '强密码';

-- ② 只授予【白名单表】的 SELECT 权限
GRANT SELECT ON mydb.dwd_order_all TO ai_reader;
GRANT SELECT ON mydb.dim_user      TO ai_reader;
GRANT SELECT ON mydb.dim_driver    TO ai_reader;
GRANT SELECT ON mydb.dwd_refund    TO ai_reader;

-- ③ 绝对不要这样（全库授权 = 前功尽弃）
-- GRANT SELECT ON mydb.* TO ai_reader;

-- ④ 顺手加上查询超时保护（防止大查询拖垮集群）
ALTER USER ai_reader SETTINGS max_execution_time = 30;
```

**为什么数据库层必须做了，代码层也要做？**

```
数据库权限拦的是"代码被绕过 / 有 bug / 有人直连"
代码护栏拦的是"正常使用时的模型失误"
        ↓
两者防的不是同一种风险，缺一不可
```

### 表名提取：为什么用 SQL 解析器而不是正则

**旧实现的问题**（已修复）：正则只看到"FROM 后面那串字符"，它不理解 SQL 语法结构。

| 场景 | 旧正则结果 | 后果 |
|---|---|---|
| `WITH t AS (...) SELECT * FROM t` | 提取出 `t` | **误拦**合法 SQL（`t` 只是别名） |
| `SELECT * FROM a, b` | 只提取 `a` | **漏判** `b` 的权限 |
| `SELECT 1; DROP TABLE x` | 提取到空 | **漏判**（只看第一段） |
| `FROM x -- FROM secret` | 提取出 `secret` | **误拦**（注释里的不算引用） |
| 反引号 `` FROM `t` `` | 提取到空 | **漏判** |

**现实现**：`guard.extract_tables()` 用 `sqlglot` 解析成语法树后提取。

```python
# 关键三步
statements = sqlglot.parse(sql, read=dialect)      # ① parse 而非 parse_one：遍历全部语句
cte_names  = {c.alias_or_name for c in stmt.find_all(exp.CTE)}   # ② 先收集 CTE 名
tables     = {t.name for t in stmt.find_all(exp.Table)} - cte_names  # ③ 表名 - CTE 名
```

**兜底设计**：解析器可能因方言差异失败，此时按顺序试 `sqlite → clickhouse → mysql → postgres → duckdb`，
全部失败才回退正则（回退版仍会排除 CTE 名，比旧版强）。
`extract_tables_detail()` 会返回用的是哪种方式，便于运维发现"长期在走兜底"这种隐患。

**专项测试**：`test_guard.py`（15 条提取用例 + 9 条权限用例 + 13 条护栏用例）。
护栏是安全功能，必须独立测试——业务错了用户会反馈，**安全错了没有任何症状**。

原则不变：宁可漏判（后面还有数据库权限兜底），也不要让明显越权的放过去。

---

## 五、★ 从现有代码到新架构（复用映射）

**好消息：你已经写好的模块，90% 不用改。**

| 现有文件 | 新架构里的位置 | 要改什么 |
|---|---|---|
| `agent.py` | Agent 服务核心 | **不改** |
| `rag.py` | Agent 服务核心 | **不改** |
| `prompt.py` | Agent 服务核心 | **不改** |
| `guard.py` | Agent 服务核心 | **不改** |
| `llm.py` | Agent 服务核心 | 不改（除非换模型） |
| `config_store.py` | 配置库 | SQLite → MySQL（改连接字符串 + 少量 SQL 方言） |
| `db.py` | ClickHouse 接入 | **★ 只需重写 `fetch_schema()` 和 `execute_sql()`** |
| `knowledge.py` | 知识访问层 | **不改** |
| `admin.py` | 被 API 替代 | 保留作为运维工具 |
| `eval_cases.py` | 评测用例 | 迁移到数据库 |
| `evaluate.py` | 评测服务 | 改成 API 可调用 |
| `config.py` | 配置 | 加 ClickHouse 连接参数 |
| `main.py` | — | 被 FastAPI 入口替代 |

### `db.py` 的 ClickHouse 版本（核心改动）

```python
import clickhouse_connect
import config

def _client():
    """建立 ClickHouse 连接（只读账号）"""
    return clickhouse_connect.get_client(
        host=config.CK_HOST, port=config.CK_PORT,
        username=config.CK_USER, password=config.CK_PASSWORD,
        database=config.CK_DATABASE,
        # ★ 服务端强制超时，防止大查询拖垮集群
        settings={"max_execution_time": config.CK_TIMEOUT_SEC},
    )

def fetch_schema():
    """★ 抓表结构（唯一需要按数据库定制的地方）"""
    client = _client()
    # ① 先列出库里的表
    tables = client.query(
        "SELECT name FROM system.tables WHERE database = %(db)s",
        parameters={"db": config.CK_DATABASE},
    ).result_rows
    # ② 再逐表取字段
    rows = []
    for (t,) in tables:
        cols = client.query(
            "SELECT name, type FROM system.columns "
            "WHERE database = %(db)s AND table = %(tb)s ORDER BY position",
            parameters={"db": config.CK_DATABASE, "tb": t},
        ).result_rows
        for name, dtype in cols:
            rows.append((t, name, dtype))
    return rows          # ← 返回格式和 SQLite 版本完全一致

def execute_sql(sql):
    """执行 SQL，返回 (列名, 行数据)"""
    client = _client()
    # ★ 强制加行数上限，防止 SELECT * 拉全表
    if " limit " not in sql.lower():
        sql = f"{sql} LIMIT {config.MAX_RESULT_ROWS}"
    res = client.query(sql)
    return res.column_names, res.result_rows
```

**看到重点了吗？** 只要 `fetch_schema()` 返回 `[(表名, 字段名, 类型), ...]` 这个统一格式，
**上面所有的 RAG、Prompt、Agent 代码一行都不用改。** 这就是当初做接口抽象的回报。

---

## 六、ClickHouse 接入要点（踩坑清单）

| # | 坑 | 现象 | 对策 |
|---|---|---|---|
| 1 | 系统表不一样 | 查不到表结构 | 用 `system.tables` / `system.columns` |
| 2 | 函数方言不同 | `countIf` vs `CASE WHEN`、`toDate` vs `DATE` | prompt 里的 `{dialect}` 必须写明 ClickHouse |
| 3 | **大表全扫** | `SELECT * FROM 十亿行大表` → 集群卡死 | 强制 LIMIT + 服务端 `max_execution_time` |
| 4 | 分区键没用上 | 查询扫全表，很慢 | prompt 里要求必须带时间过滤条件 |
| 5 | 结果集过大 | 一次返回几百万行，前端崩 | 服务端 `max_result_rows` + 前端分页 |
| 6 | 写入风险 | 模型误生成 `ALTER TABLE` | guard 护栏 + **只读账号** |
| 7 | 时区差异 | 时间统计差 8 小时 | 明确 `toDateTime(x, 'Asia/Shanghai')` |
| 8 | 并发查询打满 | 多人同时问，CK 被压垮 | 限流 + 查询队列 |

**其中 #3 #6 是企业级必须做的。** #3 靠"代码强制 LIMIT + 服务端超时"双保险，#6 靠"护栏 + 只读账号"双保险。

### ★ 已在沙箱验证通过的结论（真实 ClickHouse 26.9）

用本地起的真实 ClickHouse 把整条链路跑通了，以下是**实测**而非推测：

| 验证项 | 结果 |
|---|---|
| 换数据源要改多少代码 | **只改 `db_clickhouse.py` 的两个函数**，rag / prompt / guard / agent 一行未动 |
| 19 条评测用例 | **19/19 = 100%**（和 SQLite 上完全一致） |
| 表/字段注释自动抓取 | ✅ 从 `system.tables.comment` / `system.columns.comment` 抓到 5 条表注释 + 25 条字段注释 |
| 只读账号能否限制可见表 | ✅ **`system.tables` 会自动按 GRANT 过滤**，未授权的表根本不出现 |
| 写操作是否被拦 | ✅ `ALTER TABLE ... DELETE` → `ACCESS_DENIED` |
| 客单价计算结果 | 387.5（与 SQLite 完全一致） |

#### ★★ 最重要的一条发现：数据库授权即 RAG 白名单

```
给 AI 账号 GRANT SELECT ON demo.dwd_order_all       ← 只授 4 张表
        ↓
fetch_schema() 查 system.tables
        ↓
ClickHouse 自动只返回这 4 张（dim_user_contact 根本不出现）
        ↓
RAG 知识库里压根没有敏感表 → AI 连编都编不出来
```

**这意味着不用在代码和数据库两边维护白名单**——数据库授权是唯一事实来源。
代码层的 `table_acl` 退化成"补充手段"（用于"授权了但不想让 AI 查"这种更细的场景）。

> 这是"把权限做在正确的层"的典型例子：**能在数据库做的，就别在应用层做。**
> 应用层会漏、会有 bug、会有新同事不知道绕过；数据库授权绕不过去。

### 接入真实 ClickHouse 的操作步骤（含"只开放几张表 + 几个指标"）

```bash
# ─────────────────────────────────────────────────────────
# 第 0 步：在 ClickHouse 侧限制（最硬的一层，必须由 DBA 或你自己做）
# ─────────────────────────────────────────────────────────
# 建只读账号，只 GRANT 那几张要开放的表：
#   CREATE USER ai_reader IDENTIFIED BY '强密码';
#   GRANT SELECT ON 你的库.表A TO ai_reader;
#   GRANT SELECT ON 你的库.表B TO ai_reader;
#   ALTER USER ai_reader SETTINGS max_execution_time = 30;
# ★ 不要写 GRANT SELECT ON 库.*
# ★ 做完之后，ClickHouse 的 system.tables 会自动【只返回这几张表】
#   → Agent 连其他表的名字都不知道 → 这是最彻底的隔离

# ─────────────────────────────────────────────────────────
# 第 1 步：配连接信息
# ─────────────────────────────────────────────────────────
cp .env.example .env
# 编辑 .env：
#   DB_TYPE=clickhouse
#   CK_HOST / CK_PORT / CK_USER / CK_PASSWORD / CK_DATABASE
#   ACL_DEFAULT_VISIBLE=false     ← ★ 真实库务必改成白名单模式

# ─────────────────────────────────────────────────────────
# 第 2 步：★ 体检（别跳过，能省掉大量盲猜）
# ─────────────────────────────────────────────────────────
.venv/bin/python ck_check.py
#   看四项：① 连接通不通  ② 账号是不是只读
#           ③ 抓到了几张表（应该只等于你授权的那几张）
#           ④ 注释覆盖率（决定知识库质量）

# ─────────────────────────────────────────────────────────
# 第 3 步：限制到具体几张表（双保险，防 DBA 给别人开了全库权限）
# ─────────────────────────────────────────────────────────
.venv/bin/python admin.py set-acl 你的表A allow "订单表"
.venv/bin/python admin.py set-acl 你的表B allow "用户表"
#   ★ 白名单模式下，没登记的表默认不可见 —— 所以只加你要开放的就行

# ─────────────────────────────────────────────────────────
# 第 4 步：限制到具体几个指标
# ─────────────────────────────────────────────────────────
.venv/bin/python admin.py set-metric 客单价 off     # 停用不开放的指标
.venv/bin/python admin.py list-metrics               # 确认开放状态
#   ★ 指标定义需要你自己建（用 export-metrics → 编辑 JSON → import-metrics）
#     真实公司最多的工作量在这一步：把业务口径写成"能照着执行的 SQL 公式"

# ─────────────────────────────────────────────────────────
# 第 5 步：验证限制真的生效（这一步很关键）
# ─────────────────────────────────────────────────────────
.venv/bin/python test_acl.py            # 权限专项测试
.venv/bin/python -c "
import db, rag, agent
index = rag.build_index()
print('可见表:', index['allowed_tables'])
print('开放指标:', [m['name'] for m in index['metrics']])
print('停用指标:', [m['name'] for m in index['disabled_metrics']])
"
# 然后问几个问题试试：
#   问一个已开放的表 → 应该正常出数
#   问一个被屏蔽的表 → 应该被拒
#   问一个被停用的指标 → 应该被拒

# ─────────────────────────────────────────────────────────
# 第 6 步：启动
# ─────────────────────────────────────────────────────────
.venv/bin/python -m uvicorn server:app --reload --port 8000
```

### 表变多之后会怎样（提前打个预防针）

demo 库只有 4 张表，阈值 0.45 的安全区间很宽。真实库几百张表时：

```
候选多了 → 总有一个"碰巧有点像"的冒出来
        → 错误候选的最高分会往上爬
        → 原来的阈值开始放进无关表
        ⇒ 必须重跑评测、重看分数分布、重新校准阈值
```

**这就是为什么评测集必须在接真实库之前就建好**——否则你无法判断"变准了还是变糟了"。

### 内网部署要提前准备的（我们踩过的坑）

| 问题 | 现象 | 解决 |
|---|---|---|
| 模型下不下来 | 启动时卡在 `httpx.ProxyError` / 连接超时 | 在能联网的机器上下好 `models/`，整个目录拷进内网 |
| 临时目录被清理 | 模型突然"不见了"，程序去联网重下 → 失败 | `EMBED_CACHE_DIR=models`（已默认），别用系统临时目录 |
| HF 新传输协议被拦 | 大文件报 `401 Unauthorized ... xethub.hf.co` | `export HF_HUB_DISABLE_XET=1` |
| 镜像 | huggingface.co 完全不通 | `export HF_ENDPOINT=https://hf-mirror.com` |

以上都已在 `config.py` / `.env.example` / `rag.py` 的报错提示里体现：
**加载模型采用"离线优先、联网兜底"，失败时给出能照着做的提示，而不是抛一堆堆栈。**

---

## 七、关键技术决策（含取舍说明）

### 决策 1：SQL 先展示还是直接执行？

| 方案 | 说明 | 取舍 |
|---|---|---|
| **A. 生成即执行** | 用户直接看结果 | 体验流畅，但用户看不到 SQL，出错了不知道为什么 |
| **B. 先展示后执行** | 展示 SQL，用户点确认再跑 | 安全，但多一步操作 |
| **C（推荐）** | **默认自动执行 + SQL 完整展示 + 可编辑重跑** | 兼顾体验与可控 |

### 决策 2：配置改了，索引怎么重建？

```
问题：改了指标定义 → 需要重新算 embedding → 几秒钟
        ↓
如果同步重建：用户点"保存"要等 5 秒
如果异步重建：用户点完立刻返回，后台慢慢建
        ↓
推荐：异步 + 显示状态（"索引重建中…" / "已生效"）
     期间用旧索引继续服务（降级但不中断）
```

### 决策 3：前端要不要做对话历史？

```
第一期：不做。一次一问，刷新即清空。
        → 简单，够验证价值
第二期：做会话记忆（多轮）。支持"那上海呢？"这类追问。
```

### 决策 4：权限怎么做？

```
第一期：不做。所有人看所有数据。
第二期：行级权限 —— 登录用户带角色，自动往 SQL 里注入 WHERE 条件。
        例：销售只能看自己城市
            → 在 guard 层自动加 city = '上海'
        ★ 注意：这必须在【服务端】做，绝不能信前端传上来的条件
```

---

## 八、目录结构（目标形态）

```
ai-data-agent/
├── backend/
│   ├── main.py              FastAPI 入口
│   ├── api/
│   │   ├── ask.py           提问接口（含 SSE）
│   │   ├── configs.py       Prompt/指标/表注释 CRUD
│   │   ├── index.py         索引重建
│   │   ├── evals.py         评测接口
│   │   └── logs.py          审计日志
│   ├── core/                ★ 直接搬现有模块
│   │   ├── agent.py
│   │   ├── rag.py
│   │   ├── prompt.py
│   │   ├── guard.py
│   │   ├── llm.py
│   │   ├── knowledge.py
│   │   └── config_store.py
│   └── db/
│       ├── clickhouse.py    ★ 新的 CK 实现
│       └── config_db.py     配置库
├── frontend/
│   ├── index.html           （第一期：单文件）
│   └── src/                 （第二期：Vue3）
├── eval/
│   ├── cases.py
│   └── runner.py
├── requirements.txt
└── DESIGN.md
```

---

## 九、分阶段实施路线

### 第一期：能跑的 MVP（建议先做这个）

```
目标：浏览器打开一个页面，输入问题，看到 SQL 和结果
        ↓
① db.py 增加 ClickHouse 实现（fetch_schema + execute_sql）
② 加一个 FastAPI 后端，暴露 /api/ask 和 /api/prompts
③ 写一个 index.html（输入框 + 结果展示 + Prompt 编辑框）
④ 本地跑通：uvicorn 启动，浏览器访问
        ↓
产出：一个能演示的完整系统
```

**为什么先做这个**：它能让你**第一次看到产品形态**。而且三个模块（CK 接入、API、页面）
都是薄薄一层，加起来不到 300 行。

### 第二期：配置完善

```
⑤ 指标知识 / 表注释的可视化管理（增删改查 + 索引重建按钮）
⑥ 评测页面（点一下跑评测，看准确率）
⑦ 查询审计日志
⑧ 配置修改历史 + 回滚
```

### 第三期：企业级

```
⑨ 行级权限（按角色自动注入 WHERE）
⑩ 多轮对话（会话记忆）
⑪ 限流、监控、成本统计
⑫ Docker 部署
⑬ 前端上 Vue3 + Element Plus
```

---

## 十、风险与对策

| 风险 | 后果 | 对策 |
|---|---|---|
| **大查询拖垮 CK** | 生产事故 | 强制 LIMIT + `max_execution_time` + 只读账号 + 限流 |
| **口径算错** | 决策失误 | 指标 RAG + 评测集 + 审计日志 |
| **配置改错** | 全站取数出错 | 配置历史 + 回滚 + 上线前跑评测（做成卡点） |
| **成本失控** | 账单暴涨 | token 统计 + 高频问题缓存 |
| **幻觉** | 得到错数据 | 四道防线（上下文/规则/校验/评测） |
| **过度设计** | 做不完 | 严格按分阶段路线走，每期都要能独立演示 |

---

## 附：现有项目资产清单（可直接复用）

```
✅ 已实现且验证通过（16/16 评测）
   agent.py       编排（检索→生成→清洗→护栏→执行→自愈）
   rag.py         混合检索（关键词长词优先 + 向量语义）
   prompt.py      System Prompt 组装
   guard.py       清洗 + 只读护栏
   llm.py         大模型调用
   db.py          数据库（SQLite 实现，换 CK 只改 2 个函数）
   knowledge.py   知识访问层
   config_store.py 配置存储（prompts/metrics/table_comments）
   admin.py       配置管理 CLI
   eval_cases.py  16 条评测用例
   evaluate.py    执行结果比对评测

🆕 需要新增
   后端 API（FastAPI）
   前端页面
   ClickHouse 接入
   审计日志 / 配置历史 / 权限
```
