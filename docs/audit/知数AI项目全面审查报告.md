# 知数AI项目全面审查报告（整合版）

> 审查日期：2026-10-23 ｜ 审查范围：`/Users/zhangtao/Downloads/autosql/zhishu-ai/`（全量源码只读审查）
> 本报告由以下 8 份分报告整合而成，审查过程中未修改任何业务源码、未初始化 Git、未触碰真实密钥明文。

## 目录

1. 项目概况与真实架构盘点
2. 关键问题清单（取数正确性 / 安全 / 稳定性 / 工程质量）
3. 知识库、RAG 与 Semantic Layer 审查
4. Agent 工程化能力审查
5. GitHub 入库安全审查
6. 可复用企业产品化评估
7. 优先级行动清单与技术选型判断
8. 评测计划（测试集设计 / 场景 / 评分 / 回归）

---

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
# 02 · 关键问题清单（取数正确性 / 安全 / 稳定性 / 工程质量）

> 严重程度定义：**P0** 已确认的严重安全/数据泄露/破坏性/核心阻断；**P1** 明显影响取数正确性、权限或核心链路稳定；**P2** 功能缺口、可靠性/维护问题；**P3** 一般优化。
> 结论：**本次审查未发现已确认的 P0 问题**（不强行凑数）。以下按 P1→P3 列出，均附源码证据与可执行验证方法。

---

## 一、P1 级问题（高优先级，建议优先修复）

### C-01　未登记关系的「一对多 JOIN」无笛卡尔放大拦截

| 字段 | 内容 |
|---|---|
| 问题编号 | C-01 |
| 问题描述 | JOIN 防放大校验只对**已在 `relationships` 表登记且 `pre_agg_required=True`** 的关系生效。真实数仓大量表未登记关系，模型直接 `JOIN` 一对多明细表时无任何拦截，聚合指标被静默放大。 |
| 严重程度 | P1 |
| 源码证据 | `sql_validator.py:240-261`（`_check_p2` 仅遍历 `ctx.p2_relationships` 且要求 `rel.get("pre_agg_required")`）；`semantic_layer.py:346-363`（关系仅来自已登记 `relationships` 表）；`config_store.py:627`（`pre_agg_required` 字段存在但依赖人工配置） |
| 触发条件 | 用户问及的业务表对存在 1:N 关系，但管理员尚未在知识库登记该关系或未勾选 `pre_agg_required` |
| 业务影响 | 订单量、金额等聚合指标被放大（如订单×费用明细），返回"看着有数、实际全错"的结果——最危险的静默算错 |
| 复现方式 | 用 `test_legacy_validation.py` 思路：构造一条 `JOIN` 未登记一对多表的 SQL，走 `sql_validator.validate_sql_ex`，观察 P2 不报 error |
| 修复建议 | ① 在 `_check_p2` 增加**基于表名/外键启发式的兜底检测**（如存在 `*_fee`/`*_detail`/`*_record` 明细表被直接 JOIN 且无 GROUP BY 预聚合子查询时告警）；② 推动关系登记覆盖率，未登记时降级为 warning 而非静默 |
| 验收标准 | 未登记一对多表直接 JOIN 时，校验器产出至少 warning 级提示；已登记 `pre_agg_required` 的仍 error 阻断 |
| 回归测试 | 在 `test_legacy_validation.py` / `test_gate.py` 新增"未登记一对多 JOIN 产生告警"用例 |

### C-02　「执行成功但业务结果错误」无标准答案对比机制

| 字段 | 内容 |
|---|---|
| 问题编号 | C-02 |
| 问题描述 | 结果正确性目前只靠启发式 `result_validator`（比率越界/全 NULL/行数/全 0）与"仅诊断不改 SQL"的 LLM 逻辑自审。没有"标准答案对比"（`result_accuracy` 评测维度未实现），无法确定性地发现口径算错。 |
| 严重程度 | P1 |
| 源码证据 | `result_validator.py:34-94`（仅 4 类启发式 warning）；`agent.py:257-271`（`_review_sql` 只诊断不改 SQL）；`evaluation/evaluators/checks.py` 中 `result_accuracy` 恒为 None（`evaluation/reports/baseline.json` 显示 `result_accuracy=None`） |
| 触发条件 | 模型生成"语法正确、执行成功、但口径错误"的 SQL（如该用完成时间却用创建时间、该用 status=6 却用 status=7） |
| 业务影响 | 用户拿到错误数字且无提示，业务决策基于错数 |
| 复现方式 | 查看 `evaluation/reports/baseline.json`：`result_accuracy=None`、`field_retrieval_recall=None`，证明两维度未实现 |
| 修复建议 | ① 短期：把 `golden` 数据集的 `expected_result` 补齐（当前 schema 文档标注均为"未提供"），让 `result_accuracy` 可计算；② 中期：对高频指标配置"合理区间/同比环比"护栏，越界即降级为"待人工复核" |
| 验收标准 | `result_accuracy` 指标有非 None 数值；至少 20 条用例具备 `expected_result` |
| 回归测试 | `evaluate.py --dataset golden` 报告中出现 result_accuracy 分数 |

### S-01　审计日志记录 SQL 明文 + request_id 缺失

| 字段 | 内容 |
|---|---|
| 问题编号 | S-01 |
| 问题描述 | 权限审计与取数记录落库的 SQL **未脱敏**，可能含手机号等字符串字面量；且中间件**未生成 request_id**，审计追踪链断裂。 |
| 严重程度 | P1 |
| 源码证据 | `server.py:835`（`_audit_permission` 传 `payload["sql"]` 明文）；`config_store.py:3365`（`final_sql` 落库）；`permission.py:613-618`（`_safe_sql_repr` 脱敏工具已存在但**未被 server 调用**）；`server.py:826`（引用 `request.state.request_id`，但中间件 `server.py:130-157` 从未设置该字段） |
| 触发条件 | 用户查询含手机号/身份证等字面量的 SQL（如"查询 138**** 的订单"），落库后审计表含明文 |
| 业务影响 | 敏感数据二次泄露面；审计日志无法按 request_id 串联定位 |
| 复现方式 | 提交一条含字符串字面量的取数，检查 `permission_audit_logs.final_sql` 是否含明文字面量 |
| 修复建议 | ① `_audit_permission` 改用 `permission._safe_sql_repr` 存储 SQL 结构（字面量替换为 `?`）；② 中间件生成 `request_id`（`uuid4`）并注入 `request.state` |
| 验收标准 | 审计表 `final_sql` 不再含单/双引号包裹的字面量；`permission_audit_logs.request_id` 非空且可串联 |
| 回归测试 | `test_permission.py` 增加"审计 SQL 脱敏"与"request_id 非空"断言 |

### S-02　登录无速率限制 + 密码强度过弱

| 字段 | 内容 |
|---|---|
| 问题编号 | S-02 |
| 问题描述 | 登录接口无失败次数计数与账户锁定；密码仅校验"≥6 位"，无复杂度要求。 |
| 严重程度 | P1 |
| 源码证据 | `server.py:427`（`api_login` 无重试计数/锁定）；`config_store.py:2310/2428/2498`（`create_user`/`update_user`/`change_own_password` 仅 `len < 6` 校验） |
| 触发条件 | 服务暴露到内网/公网（形态 A/B）后，攻击者可对弱密码账号暴力破解 |
| 业务影响 | 账号接管 → 越权取数/改知识库 |
| 复现方式 | 连续多次错误口令调用 `POST /api/auth/login`，观察无锁定、无退避 |
| 修复建议 | ① 登录失败计数 + 5 次锁定/指数退避；② 密码复杂度（长度≥8 + 至少两类字符）；③ 可选：登录验证码/IP 白名单 |
| 验收标准 | 连续 5 次错密后账号锁定（或触发退避）；弱密码被拒绝 |
| 回归测试 | `test_acl.py` / 新增登录安全测试断言锁定逻辑 |

### S-03　密钥文件与跳板机信息裸露（GitHub 入库前必须处理）

| 字段 | 内容 |
|---|---|
| 问题编号 | S-03 |
| 问题描述 | 项目根目录存在 `.env`（含真实 `DEEPSEEK_API_KEY`、`CK_PASSWORD`、`SSH_PASSWORD` 等）；`tunnel.sh` 硬编码**公网跳板机 IP**（`<跳板机IP>`）与 `SSH_USER`；`deploy.md` 含内网 CK 地址与账号。当前目录**非 git 仓库**，一旦误提交即泄露。 |
| 严重程度 | P1（入库阻断项） |
| 源码证据 | `tunnel.sh:6`（跳板机 IP）、`tunnel.sh:28-29`（从 .env 读 SSH_PASSWORD）、`tunnel.sh:81-103`（expect 明文密码交互）；`.env.example:26`（注释说明"真实密码只放 .env"）；`deploy.md:10/78`（内网 IP/账号） |
| 触发条件 | 将项目纳入 Git 且未排除 `.env`/`tunnel.sh` 中的敏感信息 |
| 业务影响 | 生产 ClickHouse 只读账号、LLM 密钥、跳板机 SSH 凭据泄露 |
| 复现方式 | `cat .env`、`cat tunnel.sh`（本报告不输出内容，由 owner 核对） |
| 修复建议 | 入库前：① `.env` 加入 `.gitignore`（绝不提交）；② `tunnel.sh` 的 IP/账号改为从 `.env` 读取（当前已读 .env，但 IP 仍硬编码）；③ 用 `.env.example` 占位符替代 `deploy.md` 中的真实 IP/账号；④ 若历史已提交，需轮换全部密钥 |
| 验收标准 | `git grep` 无真实 IP/密钥；`.env` 被 `.gitignore` 拦截 |
| 回归测试 | 入库前 `git status` 确认 `.env` 不被跟踪 |

---

## 二、P2 级问题（功能缺口 / 可靠性 / 维护）

### C-03　指标硬约束依赖人工元数据配置完备性

- **描述**：`required_predicates`/`forbidden_predicates` 硬约束（`sql_validator.py:155-234`）只能拦截**已声明**的指标。未声明这些约束的指标，模型仍可静默算错。当前 28 个指标中仅部分配置了约束（`_seed_metric_constraints.py` 为一次性补种）。
- **修复建议**：指标录入时强制要求填写 `required_predicates` 或标注"无硬约束"（显式化），并在知识缺口页提醒未配置约束的指标。
- **验收标准**：所有 enabled 指标均有 `required_predicates` 或显式"无约束"标记。

### C-04　启发式阈值样本有限

- **描述**：`SATELLITE_KEEP_THRESHOLD=0.60`、`NAMED_TABLE_MIN_CHARS=4` 等阈值基于有限样本拍定（`config.py:142/156` 已自注"样本有限"）。
- **修复建议**：随评测集扩充定期重校准（详见 `08-evaluation-plan.md`）；将阈值纳入评测报告对比项。
- **验收标准**：阈值调整有评测数据支撑记录。

### C-05　JOIN 基数建议与校验断层

- **描述**：`join_suggest.py` 的 AI cardinality 建议（`join_suggest.py:136-175`）不自动落库、不联动 `pre_agg_required`，需人工二次确认；确定性候选（同名字段/FK）不产出 cardinality。
- **修复建议**：AI 推断 1:N 时自动回填 `pre_agg_required` 草稿（待审核），减少人工步骤。
- **验收标准**：从 join_suggest 确认一条 1:N 关系后，`relationships.pre_agg_required` 被正确置位。

### R-01　数据库连接无 try/finally（连接泄漏）

- **描述**：`db_clickhouse.py:419-424`（`execute_sql`）、`db_sqlite.py:364-372`、`config_store.py` 全文件均为"手动 close 无 finally"，`client.query` 抛异常时连接不释放。
- **影响**：生产长跑 + 异常频率升高时连接累积。
- **修复建议**：统一改为 `with` / `try-finally` 上下文管理；`config_store` 抽一个 `_connect` 上下文管理器。
- **验收标准**：任一执行抛异常后连接数不增长。

### S-04　初始管理员密码打印 stdout + 默认口令残留

- **描述**：随机初始密码打印到标准输出一次（`config_store.py:2258-2260`）；硬编码公开默认口令 `admin123` 残留于源码（`config_store.py:2285` 迁移检测）。
- **修复建议**：打印改为"仅提示去日志/环境变量查看"；清理残留字符串。
- **验收标准**：进程日志无明文初始密码。

### E-01　`config_store.py` 上帝文件

- **描述**：3530 行承载 30 表 CRUD + 账号 + 权限 + 反馈 + 快照（见 01 报告）。
- **修复建议**：权限体系（约 2980-3530 行）独立为 `permission_store.py`；JSON 工具去重。
- **验收标准**：权限相关函数移至独立模块且测试通过。

### E-02　噪声表过滤规则三处重复

- **描述**：`knowledge.py:121`、`check_consistency.py:41`、`list_tables.py:44` 三份 `NOISE_TABLE_MARKERS` 逻辑重复，`knowledge.py:118` 注释要求"必须只有一份"却已违反。
- **修复建议**：统一收敛到 `knowledge.business_tables()`，其余两处改为调用。
- **验收标准**：全项目仅一处噪声表规则定义。

### E-03　账号改删的孤儿引用

- **描述**：`update_user`（`config_store.py:2404-2418`）改登录名只同步 `sessions`/`query_records`，不同步 `user_role_bindings`/`favorites`/`permission_policies`/`permission_audit_logs`；`delete_user`（`2453-2467`）只删 `users` 表。
- **影响**：改名/删号后权限错位、孤儿数据。
- **修复建议**：联动更新/清理所有含 username 的表，或外键级联 + 统一工具函数。
- **验收标准**：改名后各表 username 一致；删号后无孤儿记录。

### E-04　依赖未锁定 + 无 CI

- **描述**：`requirements.txt` 全裸包名无版本；无 `.github/`、无 CI，回归靠手动 `run_tests.py`。
- **修复建议**：`pip freeze` 锁定版本 + `requirements-dev.txt`；接入 CI 跑 `run_tests.py`（unit/config 两层）。
- **验收标准**：CI 绿钩子，依赖可复现安装。

### E-05　字段注释生成两条路径标准不一致

- **描述**：`suggest.py:579`（`generate_column_comment`）有"覆盖全部取值/防编造/超 8 值拒绝"校验；`ai_ingest.py` 的 column_comment（`_apply` 约 183-198 行）直接写库无同等校验。
- **修复建议**：统一走 `suggest.generate_column_comment`，`ai_ingest` 复用同一校验。
- **验收标准**：两条路径产出的字段注释均通过取值覆盖校验。

---

## 三、P3 级问题（一般优化）

1. **遗留脚本清理**：`check1.py`、`check2.py`、`_fix_record_type_join.py`、`_probe_driver_13.py`、`_seed_*`、空 `config.db` 归档或删除。
2. **日志 `print` 化**：全程 `print()` 无级别/trace_id，建议引入 `logging` + 结构化日志 + request_id。
3. **`admin.py` 无鉴权/审计**：命令行直接改库无留痕，建议加操作日志或仅限本地运维。
4. **`_user_row_to_dict` 硬编码列顺序**（`config_store.py:2202-2216`）：依赖 SELECT 列顺序，建议改 `dict(zip(cur.description, row))` 与 `_record_row_to_dict` 一致。
5. **`upsert_sensitive_resource` 的 `raw_table`/`raw_column` 形参失效**（`config_store.py:3164-3192`）：INSERT 时未使用，需修正或删除形参。

---

## 四、已确认做得好的关键设计（供保留）

1. **唯一执行出口** `_exec_sql`（`agent.py:114-132`）+ 拒答结构判定，从根上杜绝"边拒答边执行"。
2. **自愈后重新过完整闸门**（`agent.py:786-813`），杜绝"修语法时写未授权表"绕过。
3. **fail-closed 贯穿**：表解析失败拒绝（`guard.py:411-457`）、校验器异常阻断（`sql_validator.py:480-491`）、字段级权限解析失败拒绝（`permission.py:278-319`）。
4. **密码安全**：加盐哈希 + `hmac.compare_digest` 恒时比较 + 防账号枚举抹平（`config_store.py:2195/2474/2481`）。
5. **指标级权限**：停用指标"问题级拦截"（`rag.py:413-463` `match_disabled_metric`），防模型用字段硬算。
6. **字段级 AST 权限**：SELECT * 展开、表达式/过滤/分组分位置判定、未登记敏感字段名兜底 MASK（`permission.py:127-205`）。
7. **V2 异常 fail-closed 降级白名单**（`agent.py:875-941`），禁止"校验失败静默降级为不校验执行"。
# 03 · 知识库、RAG 与 Semantic Layer 审查

---

## 一、元数据与 Semantic Layer（任务书 5.1）

### 1.1 已实现的结构化能力

| 维度 | 实现 | 源码依据 |
|---|---|---|
| 表/字段 Schema | 表名+字段名+类型+注释，`column_list` 结构化列清单 | `rag.py:87-175`（`_build_tables`）；`db_clickhouse.py:66-114` |
| 字段注释三级来源 | 人工 > AI(未复核) > 数据库注释 > 表名兜底 | `rag.py:99/130`（优先级明确） |
| 指标定义结构化 | `definition`、`sql_template`、`metric_type`、`depends_metrics`、`conflict_key` | `config_store.py:393-428` |
| 指标硬约束 | `required_predicates`、`required_columns`、`forbidden_predicates` | `config_store.py:426-428`；`sql_validator.py:155-234` |
| 实体关系 | `grain`（粒度）、`cardinality`（1:1/1:N）、`pre_agg_required` | `config_store.py:589-627` |
| 分层优先级 | P0 系统规则 → P1 指标 → P2 实体关系 → P3 Schema → P4 文档，高优先不覆盖 | `semantic_layer.py:30-31` |
| 冲突解决 | 同名指标 `conflict_key` 冲突检测 + 记录 | `semantic_layer.py:489-495`（`resolve`） |

### 1.2 缺口

- **P0/P3 未显式建模**：P0 是 `prompts` 表的文本规则（`config_store.py:41-146`），P3 是 `column_list` + 权限表，没有统一的 `knowledge_level` 字段——分层靠"不同表 + 注释"隐式表达，跨层一致性靠约定而非约束。
- **元数据过期检测不完整**：有 `index_state`（`config_store.py:850`）记录重建状态与知识计数，但**无"表结构变更 vs 索引"的自动 diff 检测**；表结构变了需人工触发 `/api/index/rebuild`。
- **同义指标/口径冲突**：`conflict_key` 字段存在但**未见冲突判定消费逻辑**（`config_store.py:420` 仅存储）。

---

## 二、RAG 检索（任务书 5.2）

### 2.1 检索能力现状

| 能力 | 实现 | 源码依据 |
|---|---|---|
| 混合检索 | 关键词精确命中（长词优先）+ 向量余弦兜底 | `rag.py:480-567`（`_hybrid_retrieve`） |
| 多路检索 | 表/指标/字典/实体/文档五条线，各自阈值+top_k | `semantic_layer.py:244-474`；`rag.py:668-754` |
| 元数据过滤 | 表白名单过滤（`_filter_visible`） | `rag.py:30-59` |
| 关键词优先 | 命中关键词即不走向量，确定性高 | `rag.py:552-554` |
| 字典召回 | 独立检索线，BGE 检索指令特殊处理（`query_prefix=""`） | `rag.py:729-754` |
| 用户点名表 | LCS 最长公共子串 ≥4 字识别，豁免实体过滤 | `rag.py:639-662`（`named_tables`） |
| 字段关键词 | 人工字段注释短语命中表，等同点名 | `rag.py:614-636`（`field_keyword_tables`） |
| 实体过滤 | 摘掉"明确属于别的业务线"的表，卫星表按阈值保留 | `semantic_layer.py:412-451` |
| 指标降噪 | 按问题度量类型（ratio/amount/count）过滤无关指标 | `semantic_layer.py:110-212` |

### 2.2 缺口

- **无重排（Rerank）**：仅"关键词+向量"两路，无精排模型。向量分在"该留的 vs 噪声"上分离度差（源码注释自述 `rag.py:645-650` 实测仅差 0.024），靠"点名表/字段关键词"规则硬补。
- **无过期/冲突/低质量知识识别**：检索侧不判断知识时效性、不判断冲突、不判断质量。
- **检索贡献追踪不完整**：`route_info` 记录了命中方式（`semantic_layer.py:458`），但**无"每条检索结果对最终答案的贡献"量化**，仅日志级说明。

---

## 三、知识分级与权限（任务书 5.3）

### 3.1 分级现状（结论：部分实现，P0/P3 隐式）

| 层级 | 存储 | 强制方式 |
|---|---|---|
| P0 系统规则 | `prompts` 表 `sql_rules`/`role` | Prompt 注入 + `guard`/`sql_validator` 代码硬校验 |
| P1 指标 | `metrics` 表 | Prompt 标注 MUST + `sql_validator._check_p1` 硬约束 |
| P2 实体/关系 | `entities`/`relationships` | Prompt 标注 JOIN 铁律 + `sql_validator._check_p2` |
| P3 Schema | `column_list` | `sql_validator._check_p3` 列存在性 |
| P4 文档 | `kb_docs` | 仅 Prompt 注入"仅供参考" |

### 3.2 权限是"硬约束"还是"软提示"（关键结论）

**硬约束，不是软提示。** 权限在多层强制执行，不依赖模型自觉：
- 表级：`guard.check_tables_ex` 执行前硬校验（`guard.py:411-457`）
- 字段级：`permission.evaluate` AST 判定 DENY/MASK（`permission.py:258-412`）
- 行级：`permission.apply_row_filters` AST 改写注入过滤（`permission.py:685-741`）
- 数据源级：ClickHouse 只读账号 + 表授权（第一道防线）

### 3.3 越权风险点

- **管理员是受审计的超级用户**：字段级不做 ALLOW/MASK/DENY 限制（`permission.py:300-302`），仅记录审计。风险可控（管理员是规则发布者），但需保证管理员账号不泄露（呼应 S-02 弱密码风险）。
- **`admin.py` 无鉴权**：本地命令行直接操作配置库，无留痕（`admin.py` 全文）。
- **导出接口** `/api/export` 依赖中间件默认 admin（`server.py:119`），无额外校验——依赖中间件默认 deny 策略正确配置。

---

## 四、黄金 SQL 与反馈闭环（任务书 5.4）

### 4.1 现状（结论：黄金 SQL 能力**基本缺失**，反馈闭环**部分实现**）

| 能力 | 状态 | 依据 |
|---|---|---|
| 保存经审核 SQL | ❌ 缺失 | `config_store.py` **无 golden_sql 表**；黄金 SQL 仅以 JSONL 文件存在于 `evaluation/datasets/golden/` |
| 记录业务问题/适用条件/口径/依赖表/版本 | ❌ 缺失 | `docs/golden_dataset_schema.md` 中 `expected_sql/expected_result/version/review_status` 均标注"未提供" |
| 用户反馈审核（不直接进生产） | ✅ 部分 | `feedbacks` 表 + `submit_for_review`/`review_record` 审核状态机（`config_store.py:2724/2771`） |
| 已审核案例用于检索/评测/回归 | ⚠️ 部分 | `evaluation/reflow.py` 支持反馈回流到 golden/regression，但**需人工补期望值**，且与运行时检索无联动 |
| 识别重复/过期/冲突黄金 SQL | ❌ 缺失 | 无相关逻辑 |

### 4.2 反馈闭环已实现链路

`feedbacks`（含 `related_knowledge_id`/`regression_sample_id`/`root_cause`，`config_store.py:840-846`）→ `suggest.generate` 生成优化建议 → 人工 `apply_suggestion` → `reflow` 回流评测集。**"人工审核后才生效"的铁律已落实**：`docs/golden_dataset_schema.md:55` 明确"禁止把历史 SQL 自动认定为 Golden SQL"。

---

## 五、汇总

### 已具备能力
- 分层知识（P0~P4）+ 高优先不覆盖 + 冲突记录
- 五路混合检索 + 关键词优先 + 点名表/字段关键词豁免实体过滤
- 指标硬约束（required/forbidden predicates）+ 负向约束防"发单≠完单"幻觉
- 字段级/行级权限硬执行 + 结果脱敏 + 审计
- 反馈审核状态机 + 回流评测集

### 已具备但不完整
- 知识分级（P0/P3 未显式建模）
- 黄金 SQL（有数据集无运行时闭环）
- 元数据过期检测（有状态无自动 diff）
- 反馈闭环（回流需人工补期望值）

### 关键缺口
1. **黄金 SQL 运行时闭环缺失**（保存→检索→复用→去重→过期识别全无）
2. **无 Rerank**（向量分离度差，靠规则硬补）
3. **无知识时效/冲突/质量识别**
4. **元数据变更无自动检测**

### 最小改进方案（按性价比排序）
1. **P1：黄金 SQL 落库闭环**——新增 `golden_sql` 表（字段对齐 `docs/golden_dataset_schema.md`），审核通过的历史记录一键入库，检索时作为 P2 补充上下文，检索贡献可追溯。
2. **P2：元数据变更检测**——`fetch_schema` 时对比 `index_state` 记录的表/字段指纹，变更自动告警 + 提示重建。
3. **P3：指标约束显式化**——所有 enabled 指标强制声明 `required_predicates` 或"无约束"。
4. **P4：Rerank 实验**——先离线用 cross-encoder 验证对"该留 vs 噪声"的分离度提升，再决定是否上线。

### 对取数正确率的预期影响
- 黄金 SQL 闭环：预计可修复"同义改写/易混淆指标"类错误（golden 集 g013/g019 等），预估 +3~5pp。
- 元数据变更检测：避免"表结构变了 SQL 还按旧字段跑"的静默错误，预估减少线上错误率。
- 指标约束显式化：直接拦截口径错误，预估 +2~4pp。
- **验证方式**：每项改造后跑 `evaluate.py --dataset golden --regression`，对比 `overall_accuracy` 与各维度 delta。
# 04 · Agent 工程化能力审查

> 判定原则：**以实际功能为准，不以是否使用 LangChain/LangGraph 等框架为准**（任务书明确要求）。本项目完全自研、零 Agent 框架依赖。

---

## 一、Orchestration（编排）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（确定性流水线，非模型自主循环）** |

- **工作流清晰**：`agent.ask_v2` 是显式 8 步流水线（理解→检索→resolve→生成→校验→执行→结果校验→回答），每步有 `trace` 耗时标记（`agent.py:607-871`）。
- **非模型自主循环**：SQL 生成→校验→自愈是**确定性 for 循环**（`agent.py:714-736`），`MAX_RETRY=3` 上限，模型不决定"下一步做什么"。
- **超时/重试/终止条件**：LLM 可传 `timeout`（`llm.py:38`）；执行重试有上限；`query_understanding` 有 8s 超时降级（`query_understanding.py:75`）。
- **是否需要引入显式状态机**：**暂不需要**。当前单一问答任务的线性编排足够；仅当进入"多轮会话式分析/看板增删改"（见 `06-productization-roadmap.md`）时才需要结构化 Query State。

**源码依据**：`agent.py:601-872`；`agent.py:714-736`（重试循环）；`config.py:165`（`MAX_RETRY=3`）。

---

## 二、Tool Calling（工具调用）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（自研函数调用，非框架 tool）** |

- **工具=固定函数**：系统不向模型暴露任意工具列表；模型只产出 SQL，工具调用（检索/校验/执行）由代码确定性编排，模型**无法选择调用**任何超出范围的工具。这比"模型自由 tool calling"更安全。
- **输入输出 Schema**：SQL 是唯一模型输出，经 `guard.clean_sql` 清洗 + 五维校验 + 表权限校验约束（`agent.py:716`）。
- **参数校验/失败识别**：`sql_validator.validate_sql_ex` 返回结构化 `status`（passed/failed/error/degraded），区分"校验器崩溃"与"没问题"（`sql_validator.py:433-511`），避免"异常当通过"。
- **最小权限**：数据库只读账号 + 表白名单 + 字段级 + 行级四层（`permission.py:11-15` 分工注释）。
- **调用审计**：权限判定审计 `add_permission_audit`（`permission.py:584-610`）+ 取数记录落库。

**待验证**：模型**不可**调用未暴露工具这一结论成立的前提是——系统没有任何"把 LLM 输出解析为函数名再执行"的路径。已确认：`ai_ingest`/`permission.parse_nl_policy` 等 LLM 输出仅用于"生成草稿"，不直接执行（`permission.py:771-854` 注释"只草拟不生效"）。

---

## 三、Grounding 与 Abstention（依据与拒答）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（较完善）** |

- **Grounding（有依据）**：SQL 引用的表必须落在召回表∪授权白名单（`guard.check_tables_ex`）；字段必须在召回表 `column_list` 内（`sql_validator._check_p3`）。评测有 `grounding_accuracy`（基线 100%）。
- **Abstention（拒答）**：多级拒答——
  - 未召回表/指标 → 拒绝（`agent.py:658-662`）
  - 问停用指标 → 拒绝（`agent.py:651-656`）
  - 模型自述无法回答 → 结构判定拒答（`agent.py:78-87` `_refusal_reason`）
  - 拒答/非查询 → `NotExecutable` 唯一出口拦截（`agent.py:114-132`）
- **推测 vs 事实区分**：`nl_answer` 要求"声明歧义/假设、不编造数字"（`nl_answer.py:40-41`）；结果返回 `ambiguity` 字段。
- **数据冲突拒答**：`abstention` 数据集含 `conflicting` 类型（口径冲突应拒答），已有评测覆盖。

**源码依据**：`agent.py:649-662`；`agent.py:78-132`；`nl_answer.py:25-41`。

---

## 四、Evaluation（评测）

| 判定 | 结论 |
|---|---|
| 状态 | **已实现（框架完整，但部分维度未实现）** |

- **固定基准集**：✅ 8 类数据集约 94 条（详见 `01` 报告）。
- **多维度评估**：
  - 检索：`table_retrieval_recall`（✅）、指标召回（✅）、`forbidden` 误召回（✅）、**`field_retrieval_recall`（❌ 未实现，恒 None）**
  - SQL：`sql_accuracy`（✅ 子串）、`sql_not_contains`（✅）、`time`（✅）、`format`（✅）
  - 业务结果：**`result_accuracy`（❌ 未实现，缺 expected_result）**
  - 完整任务：`overall_accuracy`（✅ 全维度 AND）、`sql_execution_success_rate`（✅）、`abstention_accuracy`（✅）
  - 可信度：`grounding_accuracy`（✅）、`hallucination_rate`（✅ 保守三规则）
- **回归测试**：✅ `evaluation/gate.py` 有 Regression Gate（对比历史最佳容忍度）。
- **版本对比**：✅ 报告带 baseline 对比 + delta。
- **复现历史失败**：✅ `regression` 数据集 14 条来自线上反馈回流，`reflow.py` 支持持续回流。

**关键缺陷**：当前基线 **overall_accuracy=85.7% < Release Gate 93%**（`evaluation/reports/baseline.json`），`sql_execution_success_rate=91.4% < 98%`。**若现在跑 `--gate` 会判 FAIL**。

**源码依据**：`evaluation/gate.py`；`evaluation/evaluators/checks.py`；`evaluation/reports/baseline.json`。

---

## 五、Observability（可观测性）

| 判定 | 结论 |
|---|---|
| 状态 | **部分实现（有 trace，缺结构化日志与追踪链）** |

| 项 | 状态 | 依据 |
|---|---|---|
| 检索证据记录 | ⚠️ 部分 | `route_info` 记录命中方式，但无结构化检索证据落库 |
| 模型/工具调用记录 | ❌ 缺 | 无统一调用记录 |
| SQL 记录 | ✅ | 取数记录 `query_records` + 权限审计 `final_sql`（但存明文，见 S-01） |
| 执行结果/错误记录 | ✅ 部分 | `query_records` 存结果快照 |
| 模型/Prompt/知识版本 | ⚠️ 部分 | 知识有 `version` 列 + `knowledge_history`；但 Prompt 无版本快照 |
| 耗时记录 | ✅ | `agent.py` trace 逐阶段耗时 |
| 敏感数据泄露风险 | ⚠️ 有 | SQL 明文入审计（S-01） |
| 按日志定位失败 | ⚠️ 弱 | 全程 `print()`，无 trace_id，多请求并发时无法串联 |

**源码依据**：`agent.py:607-613/870`（trace）；`server.py:826`（request_id 缺失）；`config_store.py:850`（index_state）；全程 `print()`（`server.py`/`agent.py` 多处）。

---

## 六、六项能力总评

| 能力 | 判定 | 一句话结论 |
|---|---|---|
| Orchestration | 已实现 | 确定性流水线，暂无需状态机 |
| Tool Calling | 已实现（自研） | 模型只产 SQL，无自由工具调用面，安全 |
| Grounding | 已实现 | 表/字段双重依据校验，基线 100% |
| Abstention | 已实现 | 多级拒答 + 唯一出口 fail-closed |
| Evaluation | 已实现（框架完整） | 但 result_accuracy/field_recall 未实现，基线低于门禁 |
| Observability | 部分实现 | 有 trace，缺结构化日志与 request_id 追踪链 |

---

## 七、特别说明（任务书要求）

- **本项目未用 LangChain/LangGraph**，但 Orchestration/Tool/Evaluation 能力**均已通过自研实现**，不因缺框架而判定能力缺失。
- **是否需要引入 Agent 框架**：见 `07-prioritized-action-plan.md` 技术选型部分——**结论是暂不需要**，当前自研编排更贴合"确定性 SQL 生成 + 强校验"的场景，引入框架反而增加失控面。
# 05 · GitHub 入库安全审查

> 本阶段仅盘点与提出建议，**不执行**任何 git init/add/commit/push 操作（任务书禁止）。
> 当前状态：项目目录**未初始化 Git**（无 `.git`），**无 `.gitignore`**，无历史提交风险。

---

## 一、现状盘点

| 检查项 | 结果 |
|---|---|
| 是否已初始化 Git | ❌ 否（无 `.git` 目录） |
| 现有分支/未提交修改/历史版本 | 无（非 git 仓库） |
| 敏感信息是否已进历史版本 | 无（从未提交），但需防**首次误提交** |
| 有无 `.gitignore` | ❌ 无（需新建） |
| 有无 `.env.example` | ✅ 有（`/Users/zhangtao/Downloads/autosql/zhishu-ai/.env.example`） |
| 有无 README | ⚠️ 无独立 README（有 `DESIGN.md`、`deploy.md` 可充当） |
| 有无依赖锁定 | ❌ 无（`requirements.txt` 裸版本） |

---

## 二、三个文件清单

### A. 建议入库文件（源码与文档）

| 文件/目录 | 原因 |
|---|---|
| 所有 `*.py`（除遗留脚本，见 C） | 核心业务源码 |
| `requirements.txt`、`Dockerfile`、`.dockerignore`、`.env.example` | 依赖/构建/模板 |
| `serve.sh` | 启动脚本（含个人无关逻辑） |
| `DESIGN.md`、`deploy.md`、`docs/*.md` | 设计/部署/文档（**deploy.md 需先脱敏**，见 B） |
| `frontend/`（index.html、logo） | 前端源码 |
| `ck_demo_setup.sql` | 演示库授权脚本（需确认不含真实账号） |
| `festivals.json`、`metrics.json` | 静态配置数据 |
| `evaluation/`、`eval/` | 评测框架 + 数据集 |
| `test_*.py`、`run_tests.py` | 测试 |
| `.workbuddy/memory/` | **视仓库性质**：公司内网私有仓 ✅ 可入（项目开发约定与记忆，利于协作）；**公共 GitHub ❌ 不入**（含公司业务口径、产品决策、表名，见 B） |

### B. 需脱敏 / 人工确认 / 单独处理

| 文件 | 风险 | 处理 |
|---|---|---|
| `deploy.md` | 含内网 CK 地址 `<内网CK_HOST>`、账号 `operation`、跳板机信息 | 用占位符 `<CK_HOST>`/`<CK_USER>` 替换后入库 |
| `tunnel.sh` | 硬编码公网跳板机 IP `<跳板机IP>` | IP 改为从 `.env` 读，或脱敏为占位符 |
| `com.zhishuai.server.plist` | 含个人绝对路径 `/Users/zhangtao/...` | 改相对路径/占位符，或移出仓库到 `deploy/` 示例 |
| `.workbuddy/memory/` | 含公司业务口径、DataEase 评估、产品构想、表名 | 私有仓可入；公共仓排除 |
| `产品文档_知数AI.md/.docx`、`架构升级方案_知数AI.md`、`日报_*.md` | 公司内部产品资料 | 人工确认是否公开 |
| `zhishu-ai-code-bundle/`、`dist/*.zip`、`zhishu-ai-code-bundle.zip/.md` | 历史打包副本，含旧源码 | 不入库（可由 git 历史替代），建议归档删除 |

### C. 必须排除的文件（`.gitignore`）

| 文件/模式 | 原因 |
|---|---|
| `.env` | **真实密钥**（`DEEPSEEK_API_KEY`/`CK_PASSWORD`/`SSH_PASSWORD`） |
| `*.db`、`*.db-*`、`*.db-journal/wal/shm` | 知识库+账号（含密码哈希）+ 演示数据 + 备份 |
| `.venv/`、`__pycache__/`、`*.pyc`、`*.pyo` | 虚拟环境与缓存 |
| `models/` | embedding 模型缓存（约 91MB，可由 requirements 重下） |
| `.idea/`、`.DS_Store`、`.vscode/` | IDE/系统文件 |
| `*.log` | 日志 |
| `dist/`、`zhishu-ai-code-bundle*` | 打包产物 |
| `agent_config.db.bak-*` | 配置库备份（含账号哈希） |
| 遗留脚本 `check1.py`、`check2.py`、`_*.py` | 一次性脚本（可先归档至 `archive/` 或直接排除） |

---

## 三、重点文件逐项核查（任务书要求）

- **`*.db`/`*.sqlite`/数据库备份**：`agent_config.db`（含 2 账号、知识、反馈）+ 3 个 `.bak-*` + `demo_shop.db` + 空 `config.db` → **全部排除**。迁移靠 `deploy.md` 的 `sqlite3 .backup` 流程，不入库。
- **`.env`/配置/部署文档**：`.env` 排除；`.env.example` 入库；`deploy.md` 脱敏后入库。
- **日志/缓存/上传文件**：无用户上传目录；`*.log` 排除。
- **`.workbuddy/memory/`**：经检查，内容为**项目开发约定 + 业务口径 + 产品决策**（文案约定、DataEase 评估、会话式分析构想、表名与指标名），**不含密钥**。判定：**私有公司仓库可入、公共仓库不可入**。
- **README/DESIGN/docs**：`DESIGN.md`、`docs/` 建议入库（`docs/` 中历史整改报告含表名/口径，私有仓可入）。
- **依赖清单/Dockerfile/启动脚本/测试数据**：`requirements.txt`（裸版本，需锁定）、`Dockerfile`、`serve.sh`、`ck_demo_setup.sql`（需确认无真实账号）入库。

---

## 四、安全入库操作方案（本次不执行）

> 以下为方案，供 owner 在确认后手动或授权执行。

1. **准备 `.gitignore`**：按上面 C 清单 + B 清单写入（`.env`、`*.db*`、`.venv/`、`models/`、`__pycache__/`、`.idea/`、`dist/`、`*code-bundle*`、`*.log`、`.workbuddy/memory/`[公共仓]）。
2. **脱敏**：`deploy.md`、`tunnel.sh`、`com.zhishuai.server.plist` 按 B 清单处理。
3. **锁定依赖**：`pip freeze > requirements.lock.txt`（或 `==` 固定版本）。
4. **补 README**：从 `DESIGN.md` 提炼项目简介、启动方式、架构图、安全须知。
5. **初始化**：`git init` → `git add`（严格按 .gitignore）→ **提交前审查**：`git status` + `git diff --cached` 人工确认无 `.env`/真实 IP/密钥。
6. **密钥泄露自查**：`git grep -nE 'sk-[A-Za-z0-9]|124\.221\.151\.187|172\.17\.0\.120|SSH_PASSWORD'` 应无命中。
7. **关联远程**：公司私有 Git（工蜂/GitLab）优先；若必须 GitHub，用 **Private 仓库** + 移除 `.workbuddy/memory/` 与产品文档。
8. **首次提交**：单次原子提交，附清晰 commit message。
9. **推送**：`git push -u origin main`。
10. **回滚策略**：若误提交密钥 → ① 立即轮换所有暴露的密钥（DeepSeek key、CK 密码、SSH 密码）；② 用 `git filter-repo` 或删除仓库重建（因无历史，重建成本最低）；③ 不要仅靠"删除提交"——密钥一旦 push 即视为泄露。

---

## 五、许可证与合规提示

- **本项目无第三方代码内嵌**（依赖均为 pip 包，不入库），无许可证冲突。
- **embedding 模型 `BAAI/bge-small-zh-v1.5`**：MIT 许可（models/ 不入库，运行时按需下载），无合规风险。
- **公司内部数据**：表名、指标口径、业务文档属公司资产，**公共仓库必须排除**（B 清单）。
- **SQLite `agent_config.db` 含账号密码哈希**：虽为加盐哈希，仍按敏感数据对待，不入库、备份控权限。

---

## 六、入库前必须处理的 TOP 风险（一句话）

1. `.env` 绝不提交（P0 级阻断项）。
2. `tunnel.sh` 跳板机 IP + `deploy.md` 内网地址脱敏。
3. `agent_config.db` + 备份排除。
4. 确认仓库为**私有**，或移除 `.workbuddy/memory/` 与产品文档。
5. 首次提交前 `git diff --cached` 人工审查。
# 06 · 可复用企业产品化评估

> 目标：评估从"单企业定制"走向"可复用产品"的可行路径，**不重构、不改现有代码**。

---

## 一、四层产品架构现状分析

### A. 通用产品内核（可复用性：高）

| 能力 | 复用度 | 说明 |
|---|---|---|
| Agent 编排 | ✅ 高 | `agent.py` 完全与业务解耦，只调模块 |
| SQL 生成与校验 | ✅ 高 | `guard`/`sql_validator` 无业务硬编码（阈值在 `config.py`） |
| 检索 | ✅ 高 | `rag.py` 是引擎，业务知识在 `knowledge`/`config_store` |
| 评测 | ✅ 高 | `evaluation/` 框架通用 |
| 权限引擎 | ✅ 高 | `permission.py` 与 AI 生成逻辑完全解耦 |
| 日志/反馈 | ⚠️ 中 | 反馈闭环通用，但日志 `print` 化非产品级 |

**结论**：通用内核已相当干净，`config.py` 集中了绝大多数可配置项（阈值/方言/模型/超时），是产品化的良好基础。

### B. 企业适配层（可复用性：中，需抽取）

| 项 | 现状 | 问题 |
|---|---|---|
| 数据库连接器 | 已抽象（`db.py` 门面 + 双实现） | ✅ 好，扩展 MySQL/PG 只需加实现 |
| 表结构映射 | `fetch_schema` 统一 `(表,字段,类型)` | ✅ 好 |
| 字段注释 | 人工/数据库双来源 | ✅ 好 |
| 数据目录/指标/实体 | 存 `config_store`（SQLite） | ⚠️ 单库无企业隔离 |
| SQL 方言 | `config.SQL_DIALECT` 单值 | ⚠️ 只支持 ClickHouse/SQLite 二选一，无多方言并存 |

### C. 企业专属知识层（可复用性：低，本是企业数据）

`prompts`/`metrics`/`entities`/`relationships`/`kb_docs`/`dict_entries` 全在 `agent_config.db`。这是**企业资产**，产品化的关键是"可导入/导出/版本化"，而非复用。

### D. 业务应用层（可复用性：低，需新增）

当前只有"自然语言取数"（`/api/ask`）。自助分析、异常发现、原因分析、经营诊断均未实现，属产品化新增。

---

## 二、重点问题回答（任务书 6.6 十问）

1. **哪些能力可直接复用**：Agent 编排、SQL 生成/校验、RAG 检索、权限引擎、评测框架、反馈闭环——六者全部可复用。
2. **哪些写死在代码里**：
   - 业务指标/实体/关系 → 在 `agent_config.db`（非代码，但**单企业实例**）；
   - SQL 方言 → `config.py:84` 单值（`ClickHouse`/`SQLite` 二选一）；
   - 数据库连接 → 仅 CK/SQLite 两种实现；
   - 跳板机 IP → `tunnel.sh`（应入配置）；
   - 演示数据 → `db_sqlite.py`（属 demo，无碍）。
3. **如何分离通用逻辑与企业配置**：**已基本分离**（`config.py` 环境变量 + `config_store` 数据库）。进一步：① 方言/连接器做成插件注册；② 知识导入导出做成标准格式（`exporter` 已有导出，缺导入）。
4. **如何支持不同数据库**：`db.py` 门面模式已支持扩展（加 `db_mysql.py`/`db_postgres.py` 实现同名函数即可）。**注意**：`sql_validator`/`guard` 大量依赖 ClickHouse 方言（`toStartOfMonth`、`SETTINGS`、`countIf`），换库需抽象方言层。
5. **如何实现企业/用户/知识/数据权限隔离**：当前**无多租户**（`config_store.py` 无 tenant 维度）。需引入 `tenant_id` 贯穿所有知识表 + `CONFIG_DB` 按租户分库，或单库加租户列。**数据隔离**依赖 CK 只读账号按库授权（物理隔离最可靠）。
6. **如何导入/导出/版本化企业知识**：导出已有（`exporter.collect`，JSON/Markdown，带 `FORMAT_VERSION`）；**缺导入**（`deploy.md:126` 已自述"只有导出、没有导入接口"）。版本化有 `version` 列 + `knowledge_history`，但缺"知识包快照/回滚"。
7. **如何让每家企业建立自己的 Evaluation 测试集**：`evaluation/` 框架已支持自定义数据集（`--dataset` 传路径），`reflow.py` 支持从反馈回流。产品化只需加"每租户独立的 datasets 目录/存储 + 基线隔离"。
8. **哪些模块应保持简单，不过早抽象**：
   - `rag.py`（混合检索）——保持现状，不要引入向量数据库/Rerank 直到评测证明必要；
   - `sql_validator`（规则校验）——保持确定性正则+AST，不引入可学习校验器；
   - `db.py` 门面——保持"同名函数转发"，不引入 ORM。
9. **当前最小可行产品化路径**（详见 `07` 报告）：先做**多租户隔离 + 知识导入导出 + 方言层**三件"不破坏现有功能"的改造，再谈 SaaS。
10. **产品化改造破坏现有功能的风险**：最大风险在**多租户改造**（给所有知识表加 tenant_id 会触及全链路 SQL 与索引构建）；其次是**方言抽象**（`guard`/`sql_validator` 的 CK 特有逻辑需回归测试）。**必须先补测试覆盖再动**（现有 `test_gate`/`test_permission` 是安全网）。

---

## 三、目标架构建议（不直接改代码）

```
┌──────────────────────────────────────────────────┐
│  通用产品内核（知数AI Core，多租户共享）              │
│  agent.py + guard + sql_validator + permission +   │
│  rag + evaluation + feedback                       │
└───────────────────────┬──────────────────────────┘
                        │ 通过 配置/插件 注入
┌───────────────────────▼──────────────────────────┐
│  企业适配层（每租户一份）                             │
│  db.py 门面 + 连接器(dialect 插件) + 表结构映射       │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│  企业专属知识层（每租户隔离）                          │
│  prompts/metrics/entities/relationships/docs/dicts │
│  + golden_sql + 评测集（导入/导出/版本化）             │
└───────────────────────┬──────────────────────────┘
                        │
┌───────────────────────▼──────────────────────────┐
│  业务应用层（新增）                                  │
│  自然语言取数(已) → 自助分析 → 异常发现 → 经营诊断     │
└──────────────────────────────────────────────────┘
```

**分层原则**：内核零业务硬编码；适配层靠插件；知识层靠租户隔离 + 标准格式；应用层按需扩展。
# 07 · 优先级行动清单与技术选型判断

> 任务编号与 `02-critical-issues.md` 对应。工作量按"单人日"粗略估计，用于排期参考。

---

## 一、优先级任务清单

### P1（建议 1~2 周内，安全与正确性）

| # | 任务 | 对应问题 | 工作量 | 依赖 | 验收指标 | 回滚方案 |
|---|---|---|---|---|---|---|
| 1 | 审计 SQL 脱敏 + request_id 注入 | S-01 | 0.5 天 | 无 | 审计表无字面量；request_id 非空 | 单文件改动，git revert |
| 2 | 登录限流 + 密码复杂度 | S-02 | 1 天 | 无 | 5 次锁定/退避；弱密码拒绝 | 配置开关回退 |
| 3 | 未登记一对多 JOIN 启发式告警 | C-01 | 1.5 天 | 无 | 未登记明细表直接 JOIN 产 warning | 阈值开关回退 |
| 4 | 补齐 golden `expected_result`（≥20 条）→ 启用 result_accuracy | C-02 | 2 天 | 人工审核 | result_accuracy 有数值 | 数据集版本化 |
| 5 | 密钥/敏感信息脱敏 + `.gitignore` + 依赖锁定 + README | S-03/E-04 | 1 天 | owner 确认 | git 无真实密钥/IP；依赖可复现 | 不入库无历史，重建仓库 |

### P2（建议 1 个月内，工程质量与可靠性）

| # | 任务 | 对应问题 | 工作量 | 依赖 | 验收指标 |
|---|---|---|---|---|---|
| 6 | DB 连接 try/finally 化 | R-01 | 1 天 | 无 | 异常后连接不泄漏 |
| 7 | 指标约束显式化（强制 required_predicates 或"无约束"） | C-03 | 1 天 | 无 | 全部 enabled 指标有约束标记 |
| 8 | 噪声表规则收敛为单份 | E-02 | 0.5 天 | 无 | 全项目单处定义 |
| 9 | 账号改删联动清理 | E-03 | 1 天 | 无 | 改名/删号无孤儿引用 |
| 10 | config_store 拆分权限模块 + JSON 工具去重 | E-01 | 2 天 | #6 | 权限独立模块，测试通过 |
| 11 | 字段注释两条路径统一校验 | E-05 | 0.5 天 | 无 | 两路径同校验 |
| 12 | JOIN 基数建议回填 pre_agg_required 草稿 | C-05 | 1 天 | 无 | 确认 1:N 后自动置位 |
| 13 | 元数据变更自动检测 | （03 报告） | 1.5 天 | 无 | schema 变更自动告警 |
| 14 | 引入 logging + trace_id | （04 报告） | 1.5 天 | #1 | 结构化日志可串联 |

### P3（可选，随需求排）

| # | 任务 | 工作量 |
|---|---|---|
| 15 | 遗留脚本清理归档 | 0.5 天 |
| 16 | 初始密码不打印 stdout + 默认口令残留清理 | 0.5 天 |
| 17 | admin.py 加操作审计 | 1 天 |
| 18 | `_user_row_to_dict` 列顺序健壮化 | 0.5 天 |

---

## 二、产品化路线（与 06 报告对应，分阶段，不推倒重建）

| 阶段 | 目标 | 关键改造 | 前置 | 风险 |
|---|---|---|---|---|
| M1（近期） | 单企业稳定上线 | 上述 P1 全清 + P2 关键项 | 无 | 低 |
| M2（1~2 月） | 多租户隔离 | 知识表加 `tenant_id`；`CONFIG_DB` 按租户分库或单库加列；CK 按库物理隔离 | M1 | **高**：全链路 SQL 改造，须测试覆盖先行 |
| M3（2~3 月） | 知识导入导出闭环 | 补 `importer`；知识包快照/回滚 | M2 | 中 |
| M4（3 月+） | 多数据库/多方言 | `db.py` 连接器插件 + 方言抽象层 | M2 | **高**：`guard`/`sql_validator` CK 逻辑需抽象+回归 |
| M5（远期） | 业务应用层扩展 | 自助分析/异常发现/诊断 | M2-M4 | 中 |

**最小可行产品化路径**：**M1 → M2（多租户）**。多租户是"复用产品"的第一门槛，其余能力可在单租户下先行交付。

---

## 三、技术选型判断（任务书第七部分）

| 选型议题 | 结论 | 理由 |
|---|---|---|
| 是否引入 LangChain/LangGraph | **不引入** | 当前自研编排已覆盖 Orchestration/Tool/Evaluation，且"确定性 SQL 生成 + 强校验"场景下，框架的模型自主循环反而增加失控面；引入成本高、迁移风险大 |
| 是否引入独立工作流编排层 | **暂不需要** | 单问答任务线性编排足够；仅多轮会话式分析（M5）时才评估结构化 Query State |
| 是否引入独立 SQL Validator | **已有**（`sql_validator`），保持现状 | 确定性规则校验足够；不引入"可学习校验器"（训练数据难、误判风险） |
| 是否引入更完整评测/追踪 | **评测已完整**；追踪需补 logging/trace_id（#14） | 不需引入 LangSmith 等，先补结构化日志 |
| 是否引入额外向量数据库 | **不引入** | 当前内存向量 + 余弦检索对"几十张表+几十指标"规模足够；`models/` 本地缓存已解决内网；等知识量级上千才评估 Qdrant/Milvus |
| 是否引入消息队列 | **不引入** | 后台任务用 daemon 线程 + 串行队列（`server.py:979-1079`）已够；多实例共享状态时才评估 Redis/Celery |
| 是否引入 Rerank 模型 | **先实验后决定** | 向量分离度差（源码自述仅差 0.024）是真实痛点，但需离线用 cross-encoder 验证提升后再上线 |

### 选型实验设计（任务书要求"通过什么实验决定"）

| 实验 | 方法 | 判定标准 |
|---|---|---|
| Rerank 是否值得 | 用 `bge-reranker` 对 golden 集 top_k 重排，对比 `table_retrieval_recall` | 召回提升 ≥2pp 且 p95 延迟 ≤10s 才上线 |
| 向量数据库是否值得 | 构造 1000+ 表规模压测内存检索 vs 外置 | 内存检索 p95 >200ms 才引入 |
| 方言抽象是否值得 | 先让 `guard._dialects_to_try` 覆盖目标库，跑全测试 | 测试全绿且无新增 CK 耦合 |

---

## 四、回滚总策略

1. **所有改造前**：跑 `run_tests.py`（unit+config 两层）记录基线，`evaluate.py --dataset golden --save-baseline` 保存评测基线。
2. **每项改造**：单文件/单模块小步提交，保留 git revert 能力。
3. **多租户/方言大改造**：单独分支 + feature flag（`APP_ENV`/环境变量）灰度，失败即切回。
4. **数据库结构变更**：`config_store` 迁移已幂等（`_migrate_*`），改造前先 `sqlite3 .backup`。
# 08 · 评测计划（测试集设计 / 场景 / 评分 / 回归）

---

## 一、现状与目标

- **现状**：已有 8 类数据集约 94 条（golden 35 + regression 14 + abstention 10 + boundary 13 + paraphrase 9 + adversarial 6 + counterfactual 6 + inbox 1），基线 `overall_accuracy=85.7%`，但 `result_accuracy`/`field_retrieval_recall` 未实现。
- **目标**：建立**第一版 30~50 条真实业务测试集**（对齐任务书第九问），补齐标准答案对比，把基线推过 Release Gate 阈值（`overall_accuracy≥93%`）。

---

## 二、第一版真实业务测试集设计（30~50 条）

### 2.1 来源（真实业务，非合成）

| 来源 | 采集方式 | 目标条数 |
|---|---|---|
| 线上 `query_records` 审核通过的高频取数 | 从 `query_records` 筛选 `review_status=approved` 且被收藏/复用的 | 15~20 |
| 业务方真实提数需求 | 与业务对齐，记录标准 SQL + 期望结果 | 10~15 |
| 线上反馈（错例） | `feedbacks` 表 `root_cause` 明确的错例，回流后转回归集 | 5~10 |

### 2.2 场景覆盖矩阵（每条用例须标注）

| 维度 | 覆盖要求 |
|---|---|
| 指标类型 | 计数/金额/比率（完单量、信息费、完单率、客单价） |
| 时间口径 | 单日/区间/同比环比/节假日/近 N 天/月底月初 |
| 实体线 | 大板/小板/代驾/司机/客户（跨业务线区分） |
| 复杂度 | 单表单指标 → 多表 JOIN → 一对多预聚合 → 多指标联合 |
| 口径陷阱 | 发单≠完单、status 枚举、费用字段（information_fee vs service_fee）、创建时间 vs 完成时间 |
| 易混淆 | 同义词（妥投量）、同名字段（city）、同名表 |
| 拒答 | 信息不足、跨业务线、口径冲突、无关问题 |
| 数据字典 | 编码取值（车拖车/钻石会员/资金流水类型） |

### 2.3 用例 schema（必须字段，对齐 `evaluation/schema.py`）

```json
{
  "id": "biz-001",
  "question": "查询2026年9月代驾完单量",
  "category": "metric_count",
  "intent": "summary",
  "difficulty": "easy",
  "expected_ok": true,
  "expected_metrics": ["代驾完单量"],
  "expected_tables": ["dwd_tu_order_all"],
  "expected_sql_contains": ["status = 40"],
  "expected_sql_not_contains": ["status = 6"],
  "expected_result": {"完单量": 12345},   // ★ 补齐后 result_accuracy 才可计算
  "expected_sql": "SELECT ...",            // ★ 经人工审核的标准 SQL
  "time_range": "2026-09-01~2026-09-30",
  "review_status": "approved",             // ★ 必须人工审核
  "source": "query_record#123",
  "version": 1
}
```

**关键约束**（继承 `docs/golden_dataset_schema.md`）：`expected_sql`/`expected_result` 必须经**人工审核**后写入，禁止把历史 SQL 自动认定为标准答案。

---

## 三、评分方法

### 3.1 维度（继承现有 checks.py + 补齐缺口）

| 维度 | 指标 | 当前 | 目标 |
|---|---|---|---|
| 检索 | `table_retrieval_recall` | ✅ 100% | 保持 |
| 检索 | `metric_accuracy` | ✅ 95.5% | ≥95% |
| 检索 | `field_retrieval_recall` | ❌ None | 实现（依赖 Schema 生效） |
| SQL | `sql_accuracy` | ✅ 84.6% | ≥94% |
| SQL | `sql_execution_success_rate` | ✅ 91.4% | ≥98% |
| 结果 | `result_accuracy` | ❌ None | 实现（数值集合包含） |
| 完整 | `overall_accuracy` | ✅ 85.7% | ≥93% |
| 可信 | `hallucination_rate` | ✅ 0% | ≤3% |
| 拒答 | `abstention_accuracy` | ✅ 100% | 保持 |
| 延迟 | `p95_latency` | 待测 | ≤8s |

### 3.2 判分原则（继承现有）

- 只做"可判定的弱断言"（子串/正则/集合包含/数值包含），避免误报；
- **无 LLM 判分**（现有已确定，保持——LLM 判分引入二次幻觉与成本）；
- 用例未提供某维度期望 → 该维度记 None 跳过，不计入分母。

---

## 四、质量指标与门禁

- **Release Gate 阈值**（`evaluation/gate.py`，已存在）：`overall_accuracy≥0.93`、`metric_accuracy≥0.95`、`table_retrieval_recall≥0.95`、`sql_accuracy≥0.94`、`sql_execution_success_rate≥0.98`、`grounding_accuracy≥0.95`、`hallucination_rate≤0.03`、`p95_latency≤8.0s`。
- **Regression Gate**：核心指标不得低于历史最佳 −0.02，幻觉率不得恶化。
- **门禁落地**：CI 中跑 `evaluate.py --gate`，不通过阻断合并（当前无 CI，需补）。

---

## 五、回归方案

1. **基线管理**：`evaluate.py --save-baseline`（有 API 健康保护，防止余额不足污染基线）。
2. **每次改造回归**：`run_tests.py`（unit/config 两层，秒级）+ `evaluate.py --dataset golden --regression`。
3. **线上反馈回流**：`evaluation/reflow.py --collect`（feedback → inbox 草稿 → 人工补期望 → promote 到 golden/regression），形成"上线→反馈→回流→回归"闭环。
4. **消融实验**：`experiments/ablation/run_ablation.py` 按模块消融，量化每模块贡献（安全/合规模块列 NEVER_ABLATE）。
5. **一致性评测**：`run_consistency.py` 同题跑 N 次，监控 SQL/结果稳定性（temperature=0 应高度一致）。

---

## 六、验证方式与验收标准

| 里程碑 | 验收标准 |
|---|---|
| 测试集建成 | 30~50 条真实业务用例，`review_status=approved`，`expected_result` 覆盖 ≥80% |
| result_accuracy 启用 | 评测报告出现非 None 的 `result_accuracy` 数值 |
| 门禁达标 | `evaluate.py --gate` 通过（overall≥93%） |
| CI 接入 | 合并请求自动跑测试 + 门禁，失败阻断 |

---

## 七、不编造声明

- 本报告**未执行**任何评测（本次为只读审查），上述基线数字均引自 `evaluation/reports/baseline.json` 与历史报告，非本次新跑。
- 所有"目标值"为计划阈值，非已达成结果；达成情况以实际跑 `evaluate.py` 输出为准。
