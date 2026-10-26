# 01 · 项目概况与真实架构盘点

> 审查日期：2026-10-23　|　审查范围：`/Users/zhangtao/Downloads/autosql/zhishu-ai/`（全量源码只读审查）
> 结论基准：以实际源码、配置与评测报告为准，非 README/历史描述。

---

## 一、项目定位

知数AI（Zhishu AI）是一个 **企业级 AI 取数 / Text-to-SQL 平台**：用户用自然语言提问，系统检索元数据与业务口径 → 生成 SQL → 过安全/业务校验闸门 → 在 ClickHouse（生产）/ SQLite（演示）执行 → 结果校验 → 自然语言回答。已从"一句话→一条 SQL"的演示原型，演进为带 Semantic Layer、字段级/行级权限、评测门禁与知识治理的半产品化系统。

---

## 二、真实技术栈与依赖

| 层 | 选型 | 依据 |
|---|---|---|
| 语言/运行时 | Python 3.13（Dockerfile `FROM python:3.13-slim`） | `Dockerfile:9` |
| Web 框架 | FastAPI + Uvicorn（单 worker） | `server.py`；`Dockerfile:40` |
| 大模型 | DeepSeek（`deepseek-chat`，OpenAI 兼容接口） | `config.py:36-37`；`llm.py:14-17` |
| 向量检索 | fastembed + `BAAI/bge-small-zh-v1.5`（512 维，本地缓存） | `config.py:94`；`rag.py:247-318` |
| SQL 解析 | sqlglot（表提取 / AST 权限 / 表名前缀 / 列归属） | `guard.py:221-226` |
| 数据源 | ClickHouse（生产，`clickhouse-connect`）/ SQLite（演示） | `db.py:38-43`；`db_clickhouse.py`；`db_sqlite.py` |
| 知识/账号存储 | SQLite `agent_config.db`（`CONFIG_DB` 可配） | `config_store.py:33` |
| 结果导出 | openpyxl（xlsx）/ 标准库 CSV | `exporter.py`；`requirements.txt:18` |
| 前端 | 单文件原生 JS（`frontend/index.html`，约 317KB） | `frontend/` |

**依赖清单**（`requirements.txt`）共 9 个：`openai、python-dotenv、fastembed、sqlglot、fastapi、uvicorn[standard]、python-multipart、openpyxl、clickhouse-connect`。**全部为裸包名，未锁定版本**（无版本号、无哈希、无 lock 文件）。

---

## 三、模块清单与职责

### 核心取数链路（主流程）

| 文件 | 行数 | 职责 |
|---|---|---|
| `server.py` | 2935 | FastAPI 服务：鉴权中间件、约 60+ 端点、结果加工、落库/审计 |
| `agent.py` | 941 | 核心编排：V2 流水线 + Legacy 兜底 + 唯一执行出口 + fail-closed 降级 |
| `query_understanding.py` | 124 | 查询理解：意图/指标/实体/维度/时间/歧义（LLM 结构化输出） |
| `semantic_layer.py` | 565 | 语义层：P0~P4 分层检索、冲突解决、Prompt 分层组装 |
| `rag.py` | 754 | 检索引擎：建索引、混合检索（关键词+向量）、字典召回、点名表识别 |
| `sql_validator.py` | 519 | SQL 五维校验：P0 规则/P1 口径/P2 JOIN/P3 Schema/Metric 比率 |
| `guard.py` | 677 | 安全护栏：清洗、只读校验、表级权限（sqlglot AST + 正则兜底） |
| `permission.py` | 854 | 字段级 ALLOW/MASK/DENY + 结果脱敏 + 行级权限 AST 改写 + 审计 |
| `result_validator.py` | 94 | 执行后结果合理性体检（比率越界/全 NULL/行数/全 0） |
| `nl_answer.py` | 55 | 自然语言回答（失败降级表格快照） |
| `db.py` / `db_clickhouse.py` / `db_sqlite.py` | 168/425/372 | 数据源门面 + 两种实现（适配器模式） |
| `prompt.py` | 219 | Prompt 组装（system / 修复 / 逻辑自审） |
| `config_store.py` | 3530 | 配置库：30 张表 CRUD + 账号/会话/权限/反馈/快照 |
| `knowledge.py` | 127 | 知识访问入口（接口隔离层，代理 config_store） |
| `calendar_util.py` | 424 | 时间口径 / 节假日解析（`festivals.json` 2020-2030 静态表） |

### 辅助模块（被主流程引用）

| 文件 | 职责 |
|---|---|
| `suggest.py` | 反馈→优化建议；表/实体/字段注释 AI 起草（source=ai，人工可覆盖） |
| `join_suggest.py` | JOIN 关联字段推荐（确定性 + LLM 推断，含 cardinality） |
| `gaps.py` | 知识缺口扫描（枚举未说明/幽灵值/缺注释/缺实体） |
| `ai_ingest.py` | 知识 AI 整合录入（自然语言/问题+SQL → P1~P4 落库） |
| `enum_parse.py` | 字段注释「编码→含义」解析（唯一判据，多模块共享） |
| `colname.py` | 结果计算列中文名 + 同名列消歧 |
| `exporter.py` | 知识库导出（JSON/Markdown，FORMAT_VERSION 版本化） |
| `admin.py` | 命令行管理工具（直接操作 config_store，无鉴权） |
| `ssh_tunnel.py` | 按需拉起 ClickHouse SSH 隧道（仅开发机） |

### 评测与测试

| 文件/目录 | 职责 |
|---|---|
| `evaluation/` | 评测框架：schema/runner/report/gate/reflow/run_consistency/ablation |
| `evaluation/datasets/` | 8 类数据集（golden/regression/abstention/adversarial/boundary/counterfactual/paraphrase/inbox） |
| `evaluate.py` | 评测 CLI 入口（--gate/--regression 门禁） |
| `test_*.py`（12 个）+ `run_tests.py` | 独立脚本测试，覆盖闸门/权限/护栏/ACL/检索/缺口/标签/治理/流水线/legacy/join/导入 |
| `eval/` | 轻量回归集（8 条 cases.json + run_eval.py） |

### 遗留 / 一次性脚本（未被主流程引用）

`check1.py`、`check2.py`、`_fix_record_type_join.py`、`_probe_driver_13.py`、`_seed_biz_entities.py`、`_seed_caliber.py`、`_seed_metric_constraints.py`（半遗留）。工具类：`ck_check.py`、`list_tables.py`、`tools/enum_scan.py`、`check_consistency.py`。

---

## 四、系统架构图（基于真实代码）

```
┌─────────────────────────────────────────────────────────────┐
│                        前端 frontend/index.html                │
│              （原生 JS 单页，登录/取数/知识库/权限/审核）          │
└──────────────────────────────┬──────────────────────────────┘
                               │ HTTP (Bearer token)
┌──────────────────────────────▼──────────────────────────────┐
│                   server.py (FastAPI, 单 worker)              │
│  _auth_middleware(public/user/admin 三级) → 60+ 端点           │
│  api_ask → asyncio.to_thread(agent.ask, index, q, user)      │
└──────────────┬───────────────────────────────┬──────────────┘
               │                                │
   ┌───────────▼───────────┐        ┌───────────▼───────────┐
   │  agent.py（编排大脑）    │        │ config_store.py（配置库）│
   │  ask → ask_v2 → legacy │        │ SQLite agent_config.db  │
   └───────────┬───────────┘        │ prompts/metrics/entities│
               │                    │ relationships/docs/ACL  │
               │                    │ users/sessions/权限/审计 │
               │                    └───────────▲───────────┘
               │                                │ knowledge.py(接口)
   ┌───────────▼────────────────────────────┐   │
   │ ① query_understanding.understand()      │   │
   │ ② semantic_layer.retrieve_semantic()    │   │
   │    P0规则→P1指标→P2实体关系→P3表→P4文档   │   │
   │ ③ resolve() 冲突解决 + Prompt 分层组装    │   │
   │ ④ llm.chat() 生成 SQL                   │   │
   │ ⑤ _pre_execute_check() 统一闸门：         │   │
   │    guard.sql_guard(只读)                  │   │
   │    guard.check_tables_ex(表白名单)         │   │
   │    sql_validator.validate_sql_ex(五维)     │   │
   │    permission.evaluate(字段级AST)          │   │
   │    permission.apply_row_filters(行级改写)   │   │
   │ ⑥ _exec_sql()【唯一执行出口】→ db 门面      │   │
   │ ⑦ result_validator.validate_result()      │   │
   │ ⑧ nl_answer.answer()                      │   │
   └───────────┬────────────────────────────┘   │
               │                                 │
   ┌───────────▼───────────┐        ┌────────────▼──────────┐
   │ db.py（门面/适配器）     │        │ rag.py（检索引擎）        │
   │ db_clickhouse│db_sqlite │        │ _build_index → 向量      │
   └───────────┬───────────┘        │ _hybrid_retrieve(混合)   │
               │                    └────────────────────────┘
   ┌───────────▼───────────┐
   │ ClickHouse(内网172.17) │ / SQLite demo_shop.db
   │ （只读账号 + 超时/LIMIT）│
   └───────────────────────┘
```

---

## 五、关键业务调用链（/api/ask）

1. `server.api_ask`（`server.py:702`）→ 取可信 user（中间件注入，非前端可篡改 role）
2. `agent.ask`（`agent.py:892`）→ `ask_v2`（`agent.py:601`）
3. `query_understanding.understand`（意图+指标+实体识别）
4. `semantic_layer.retrieve_semantic`（P0~P4 分层检索，含实体过滤、点名表豁免、卫星表保留）
5. `semantic_layer.resolve` + `build_prompt_parts`（分层 Prompt，时间口径注入）
6. `llm.chat` 生成 SQL → `_finalize_sql`（清洗 + 补库前缀）
7. 重试循环内 `_pre_execute_check`：只读 → 表白名单 → 五维校验 → 字段级权限 → 行级改写（改写后重新校验）
8. `_exec_sql`（唯一执行出口，拒答/非查询 fail-closed）
9. `result_validator` 结果体检 + `permission.mask_rows` 脱敏 + `nl_answer` 回答
10. `server` 侧：列中文名翻译、SQL 排版、落取数记录、结果快照、权限审计

---

## 六、运行与部署方式

- **开发 CLI**：`.venv/bin/python main.py`（`main.py:8`，跑 8 条演示问题）
- **Web 服务**：`./serve.sh`（前台）或 `./serve.sh -d`（后台），`uvicorn server:app --host 127.0.0.1 --port 8000`（`serve.sh:93/112`）
- **开发机常驻**：`com.zhishuai.server.plist`（launchd，含绝对路径 `/Users/zhangtao/...`）
- **生产 Docker**：`docker build` + `docker run`，`CONFIG_DB=/app/data/agent_config.db` + `-v zhishu-data:/app/data`（`Dockerfile:20-21`；`deploy.md:74-87`）
- **环境变量**：`.env`（真实密钥）/ `.env.example`（模板）。关键项：`DEEPSEEK_API_KEY`、`DB_TYPE`、`CK_HOST/USER/PASSWORD/DATABASE`、`EMBED_LOCAL_ONLY`、`ACL_DEFAULT_VISIBLE`、`APP_ENV`、`CONFIG_DB`、`SSH_*`

---

## 七、数据库 / 数据 / 产物分布

| 类别 | 文件 | 说明 |
|---|---|---|
| 知识+账号库 | `agent_config.db`（999KB）+ 3 个 `.bak-*` | 最值钱资产，含 28 指标/9 实体/2 关系/7 字典/13 ACL/94 字段注释/账号 |
| 演示库 | `demo_shop.db`（24KB） | SQLite 演示数据（含敏感表 `dim_user_contact` 用于验证权限） |
| 空库 | `config.db`（0 字节） | 疑似遗留，未使用 |
| 模型缓存 | `models/`（bge-small-zh-v1.5，约 91MB） | 内网零联网启动用 |
| 打包产物 | `dist/zhishu-ai-src-20261019-0944.zip`、`zhishu-ai-code-bundle.zip/.md/目录` | 历史打包副本，含 `.env.example`（无密钥） |
| 密钥 | `.env` | 含真实 `DEEPSEEK_API_KEY`、`CK_PASSWORD`、`SSH_*` |
| 文档 | `docs/`（21 份）、`DESIGN.md`、`deploy.md`、产品文档、日报 | |

---

## 八、测试 / CI / 日志 / 监控现状

- **自动化测试**：有，12 个 `test_*.py` + `run_tests.py` 分层调度器（unit/config/integration），但**非 pytest 风格**，是独立脚本 + `SystemExit` 返回码。
- **CI**：**无**（无 `.github/`、无任何 workflow）。
- **评测**：有完整框架，8 类数据集共约 94 条（golden 35 + regression 14 + abstention 10 + boundary 13 + paraphrase 9 + adversarial 6 + counterfactual 6 + inbox 1）。**当前基线 overall_accuracy=85.7%**，低于 Release Gate 阈值 93%。
- **日志**：**全程 `print()`，无 `logging` 模块、无日志级别、无结构化日志、无 trace_id**。
- **监控**：耗时 trace 有返回（`agent.py` trace），但无外部监控/告警接入。

---

## 九、未使用代码 / 重复模块 / 职责问题

1. **遗留脚本**（可归档/删除）：`check1.py`、`check2.py`、`_fix_record_type_join.py`、`_probe_driver_13.py`、`_seed_biz_entities.py`、`_seed_caliber.py`、`_seed_metric_constraints.py`、`config.db`（空文件）。
2. **重复逻辑**：噪声表过滤规则在 `knowledge.py:121`（`NOISE_TABLE_MARKERS`）、`check_consistency.py:41`、`list_tables.py:44` **三处重复**，且 `knowledge.py:118` 注释明确要求"这套规则必须只有一份"——已违反自身约定，存在口径漂移风险。
3. **上帝文件**：`config_store.py` 3530 行，同时承担 30 张表 CRUD + 账号/会话/口令 + RBAC/字段级/行级/审计 + 反馈 + 收藏 + 快照 + JSON 工具，权限体系（约 2980-3530 行）应独立。
4. **重复 JSON 工具**：`_to_json`/`_to_json_str`（`config_store.py:318/3495`）、`_from_json`/`_from_json_str`（`323/3499`）功能重复、命名混乱。
5. **无真实循环依赖**：`prompt.py:63` 的延迟导入是预防性的，`rag.py` 顶层不 import prompt。

---

## 十、无法确认事项与验证方法

| 事项 | 无法确认原因 | 验证方法 |
|---|---|---|
| `.env` 内真实密钥的完整清单 | 为遵守"不输出真实密钥"未读取 `.env` 全文 | 入库前由 owner 逐一人工核对 |
| ClickHouse 只读账号实际授权范围 | 需连真实库验证 | `python ck_check.py`；`curl http://<CK_HOST>:8123/ping` |
| 线上真实准确率 | 评测为本地离线跑，非生产流量 | 接入线上 query_records + 反馈回流量化 |
| `models/` 是否与代码同版本 | 无法从文件时间戳确认 | 重建索引 `POST /api/index/rebuild` 后验证注释覆盖率 |
