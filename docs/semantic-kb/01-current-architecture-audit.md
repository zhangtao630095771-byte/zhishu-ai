# 01 · 项目结构审查（企业业务语义知识库建设 · 第一阶段）

> 审查日期：2026-11-10 · 审查方式：只读（Read / Grep / Glob / sqlite3）  
> 项目根目录：`/Users/zhangtao/Downloads/autosql/zhishu-ai`  
> 说明：以下所有模块名、函数名、行数、表名均为本次实测核对结果，非臆测。



---

## 1. 总体定位

知数AI 是一个「自然语言 → SQL」的 AI 取数系统：用户用中文提问，系统检索业务知识、生成 ClickHouse 查询 SQL，经过多层安全/口径校验后执行并返回结果。

**核心事实：双流水线已收敛为单一流水线。** 全仓唯一的业务编排入口是 `agent.ask()` → `agent.ask_v2()`；旧 Legacy 兜底路径已退役（`agent.py:1171-1185` 明确注释「Legacy 兜底路径已退役，不再存在」）。详见《03-agent-call-chain.md》。

---

## 2. 前端技术栈与页面目录

| 维度   | 结论                                                                      |
| ---- | ----------------------------------------------------------------------- |
| 技术栈  | **原生 JS + 原生 CSS**，无 React/Vue 等框架，无构建工具（无 package.json / node_modules） |
| 入口   | `frontend/index.html`（约 84 KB 单页应用）                                     |
| 样式   | `frontend/static/app.css`（60 KB）、`frontend/static/experience.css`       |
| 脚本目录 | `frontend/static/js/` 共 19 个 JS 文件                                      |

前端 JS 文件清单（实测）：

| 文件                                               | 职责（据文件名推断）  |
| ------------------------------------------------ | ----------- |
| `js/boot.js` / `js/core.js` / `js/core/state.js` | 启动、核心状态管理   |
| `js/ask.js`                                      | 智能取数主交互     |
| `js/feedback.js`                                 | 反馈处理        |
| `js/history.js`                                  | 取数历史        |
| `js/experience.js`                               | SQL 经验中心页   |
| `js/golden.js`                                   | 标准问题（黄金案例）页 |
| `js/admin.js`                                    | 账号管理        |
| `js/knowledge/registry.js`                       | 知识管理总入口     |
| `js/knowledge/metrics.js`                        | 指标知识        |
| `js/knowledge/gaps.js`                           | 知识缺口        |
| `js/knowledge/prompt.js`                         | Prompt 配置   |
| `js/knowledge/dict.js`                           | 数据字典        |
| `js/knowledge/kb.js`                             | 知识库（表/字段注释） |

页面导航（`index.html:97-195` 实测）分三组共 15 个 tab：

- **使用**：智能取数(ask)、收藏取数(fav)、取数审核(review)
- **知识运营**：知识管理(kb)、知识治理(gov)、反馈处理(feedback)、SQL经验(exp)、标准问题(golden)、评测看板(dash)
- **系统管理**：Prompt 配置(prompt)、表与权限(tables)、账号管理(users)、数据权限(perm)

---

## 3. 后端技术栈与模块目录

- 语言/框架：**Python 3 + FastAPI**（`server.py`，4107 行）
- LLM：`llm.py`（58 行，封装 `chat()`），默认 DeepSeek（`config.LLM_MODEL="deepseek-flash"`）
- Embedding：`rag.py` 用 `fastembed` + `BAAI/bge-small-zh-v1.5`（离线优先，`config.EMBED_LOCAL_ONLY` 默认 1）
- 包管理：`requirements.txt` / `requirements.lock.txt`

核心模块实测行数与职责：

| 模块                       | 行数   | 职责                                |
| ------------------------ | ---- | --------------------------------- |
| `agent.py`               | 1210 | **编排大脑**：唯一业务流水线 `ask_v2`         |
| `server.py`              | 4107 | FastAPI 路由（约 130 个接口）             |
| `config_store.py`        | 4372 | SQLite 配置库 CRUD + 知识治理 + 账号权限     |
| `rag.py`                 | 724  | 检索引擎（混合检索：关键词+向量）                 |
| `semantic_layer.py`      | 590  | 语义层：P0~P4 分层 + 冲突解决 + 多路检索        |
| `sql_experience.py`      | 2105 | SQL 经验中心（成功案例/失败经验/规则）            |
| `prompt.py`              | 628  | Prompt 组装（角色/口径/字典/硬约束/修复）        |
| `guard.py`               | 720  | SQL 安全护栏（清洗+只读+表权限+SQL 解析）        |
| `sql_validator.py`       | 679  | SQL 生成前五维校验（P0~P3 + 指标）           |
| `requirement_review.py`  | 1088 | 需求一致性审核（第六维：是否答对题）                |
| `permission.py`          | 890  | 字段级权限（ALLOW/MASK/DENY + AST + 脱敏） |
| `suggest.py`             | 773  | 知识缺口建议/自动补注释                      |
| `clarification.py`       | 515  | 多轮澄清（指标口径确认）                      |
| `golden_cases.py`        | 495  | 标准问题/黄金 SQL 库                     |
| `sql_modify.py`          | 273  | 增量修改结构保护（AST diff）                |
| `query_understanding.py` | 131  | 查询理解（意图+指标+实体识别）                  |
| `knowledge.py`           | 127  | 知识访问入口（接口隔离）                      |
| `gaps.py`                | 426  | 知识缺口扫描                            |
| `result_cache.py`        | 226  | 结果缓存                              |
| `result_validator.py`    | —    | 执行后结果体检                           |
| `nl_answer.py`           | —    | 自然语言结论生成                          |
| `ai_ingest.py`           | 258  | 自然语言/问答一键入库                       |
| `calendar_util.py`       | —    | 日历/节假日时间口径                        |
| `exceptions.py`          | —    | LLM 错误分类（4xx/429/超时）              |
| `governance.py`          | 37   | 知识治理状态准入（status/source 过滤）        |
| `config.py`              | 383  | 集中配置（阈值/方言/模型/开关）                 |
| `db.py`                  | 169  | 数据源门面（Facade）                     |
| `db_clickhouse.py`       | 525  | ClickHouse 实现（只读账号）               |
| `db_sqlite.py`           | —    | SQLite 演示实现                       |
| `llm.py`                 | 58   | LLM 调用封装                          |

---

## 4. 数据库与存储方式

### 4.1 SQLite 配置库 `agent_config.db`

- 由 `config_store.py` 管理，**共 38 张表**（实测 `.tables`）。
- 存储：业务知识（指标/实体/关系/文档/字典/表注释/字段注释）、Prompt、权限、账号、反馈、SQL 经验、黄金案例、查询记录、审计等。
- 完整表结构与行数见《02-existing-knowledge-inventory.md》。

### 4.2 ClickHouse 业务库（只读）

- `db_clickhouse.py` 通过 HTTP 接口（默认 8123）连接，账号为只读（`config.CK_USER` 默认 `ai_reader`）。
- 应用层强制 `LIMIT`（`config.MAX_RESULT_ROWS`）、服务端超时、内存上限（`MAX_QUERY_MEMORY` ≈ 11.2 GiB）。
- `db.py` 是门面，`DB_TYPE=clickhouse`（当前 `.env` 已确认）时路由到 `db_clickhouse`。

---

## 5. 各模块真实代码结构核对

### 5.1 Agent 调用入口（`agent.py`）

- 对外入口：`ask(index, question, user, understanding_override, modify_context, want_answer)`（1171 行）
- 实际实现：`ask_v2(...)`（580 行），**全仓唯一业务编排**。
- 关键子函数：`_exec_sql`（119 行，**唯一执行出口 chokepoint**）、`_pre_execute_check`（345 行，统一前置校验）、`_build_system_prompt`（429 行，Prompt 唯一组装点）、`_review_sql`（303 行，分层自审）、`_sql_risk_level`（269 行）。
- 状态链：`chain` 字典记录 generated / gate_passed / requirement_verdict / executed / exec_ok / result_sane / business_verified / requirement_fulfilled 七态。

### 5.2 RAG 检索模块（`rag.py`）

- `build_index()`（420 行）→ `_build_index()`（343 行）：抓 schema → 权限过滤 → 组装知识库 → 算向量。
- `_hybrid_retrieve()`（508 行）：**关键词精确命中优先（长词优先），向量兜底**。
- `retrieve_dicts()`（699 行）：字典第三条检索线。
- `named_tables()` / `field_keyword_tables()`：用户点名表识别。
- `match_disabled_metric()`（441 行）：停用指标问题级拦截。

### 5.3 知识管理模块

- `knowledge.py`（127 行）是**接口隔离层**，实际读写全在 `config_store.py`：
  - `get_metrics` → `config_store.get_metrics(enabled_only=True)`（1252 行）
  - `get_entities` → `config_store.get_entities`（1492 行）
  - `get_relationships` → `config_store.get_relationships`（1590 行）
  - `get_kb_docs` → `config_store.get_kb_docs`（1925 行）
  - `get_dict_entries` → `config_store.get_dict_entries`（1367 行）
  - `get_table_comments` / `get_column_comments` → `config_store`（2026/2059 行）

### 5.4 SQL 安全模块（`guard.py`）

- `clean_sql`（137 行）、`sql_guard`（194 行，只读校验）、`check_tables_ex`（454 行，表白名单）、`extract_tables` / `extract_tables_ex`（AST+正则）、`qualify_tables`（549 行，补库名前缀）、`extract_columns`（689 行）。
- 表名解析严格模式：`config.TABLE_PARSE_STRICT`（生产默认 1，解析失败即拒绝）。

### 5.5 Prompt 管理模块（`prompt.py`）

- 组装函数：`build_hard_constraints_block`（167 行）、`_build_schema_block`（185 行）、`_build_metric_block`（199 行）、`_build_dict_block`（257 行）。
- 修复类 Prompt：`build_repair_prompt`（365 行）、`build_review_prompt`（387 行）、`build_requirement_repair_prompt`（471 行）、`build_requirement_review_prompt`（524 行）。
- 可配置 Prompt 存于 `prompts` 表（2 条：`sql_rules` 7171 字符、`role` 280 字符）。

### 5.6 用户反馈与审核模块

- 反馈：`feedbacks` 表（5 行，含 `related_knowledge_id` / `root_cause` 治理闭环字段），`config_store.add_feedback`（2580 行）等。
- 取数审核：`query_records` 表（325 行，`review_status` none/pending/approved/rejected），`server.py` `/api/records/{rid}/submit`、`/api/review/queue`。
- 知识治理审核：`config_store.list_pending_knowledge`（1727 行）、`set_knowledge_status`（1789 行），`_review_governance.py` 复核工具。

### 5.7 任务调度与后台任务

- 无独立任务调度框架。后台任务以 **asyncio.to_thread** 线程池方式在请求内异步执行（`server.py:867-870`）。
- 自动补表注释后台循环：`server._doc_loop`（1453 行）+ `_queue_auto_doc`（1502 行）。
- 索引重建：`POST /api/index/rebuild`（3754 行）。
- 无 Celery / APScheduler 等；定时报告/批量任务能力**未见**（未能确认是否通过外部 cron 调用）。

### 5.8 部署与配置方式

- `serve.sh` / `restart.sh` 启动脚本；`Dockerfile`；`.env` / `.env.example` 配置；`deploy.md` 部署文档；`com.zhishuai.server.plist`（macOS launchd）。
- `ssh_tunnel.py` + `tunnel.sh`：ClickHouse SSH 隧道连接。
- `requirements.txt`：依赖清单。

---

## 6. 与任务书假设的差异提示

任务书假设核心模块为「agent / rag / knowledge / prompt / guard / config_store / server」七模块。实际代码已扩展出**语义层与经验层**两套更上层的编排模块，且均在真实运行：

- `semantic_layer.py`（P0~P4 分层 + 冲突解决 + 多路检索）—— 任务书未列出，但已是检索主链路。
- `sql_experience.py`（SQL 经验中心，2105 行，全仓第二大体量业务模块）—— 任务书未列出。
- `query_understanding.py`、`requirement_review.py`、`sql_validator.py`、`clarification.py`、`sql_modify.py`、`permission.py` 等均为新增的校验/理解/权限模块。

**结论：任务书「模块划分可能已变化」的提醒属实，实际架构比任务书描述的更完整、分层更多。**

---

## 7. 架构审查小结

| 审查项 | 结论                                          |
| --- | ------------------------------------------- |
| 前端  | 原生 JS 单页应用，15 个功能 tab                       |
| 后端  | Python/FastAPI，单一流水线 `ask_v2`               |
| 存储  | SQLite（38 表，配置+知识+经验+审计）+ ClickHouse（只读业务库） |
| 检索  | 语义层 P0~P4 分层 + 混合检索（关键词优先/向量兜底）+ 字典第三条线     |
| 安全  | 四道防线：只读账号 → ACL 白名单 → guard 表校验 → 字段级权限     |
| 经验  | SQL 经验中心（成功/失败/规则三层）                        |
| 调度  | 无独立调度器，请求内线程池 + 索引重建接口                      |
