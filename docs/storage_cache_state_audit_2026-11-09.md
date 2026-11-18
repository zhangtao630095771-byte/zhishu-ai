# 知数AI · 数据库、缓存与状态管理审查报告

> 审查类型：**只读分析，未修改任何代码、配置或数据库**
> 审查日期：2026-11-09 ｜ 审查范围：`/Users/zhangtao/Downloads/autosql/zhishu-ai/` 全量源码 + 现有 `agent_config.db`
> 口径：**以真实代码与库内实际数据为准**；标注「未确认」的项为无法从代码/库直接证实的内容。
> 说明：审查中涉及账号凭据的字段区段被安全策略拦截，本报告**未读取 `users` 表的密码哈希字段**，仅依据表结构与调用入口描述。

---

## 摘要（结论先行）

1. **存储组件只有两套**：SQLite 配置/知识/状态库（`agent_config.db`）+ ClickHouse 业务数据源（生产）/ SQLite 演示库（`demo_shop.db`）。**没有 Redis、MySQL、PostgreSQL、Milvus/Qdrant/FAISS、MongoDB，也没有消息队列。**
2. **业务状态几乎全部持久化**在 `agent_config.db`：账号、会话、取数记录、澄清会话、查询上下文、结果快照、反馈、审核、权限审计、知识、经验、标准问题。**服务重启不丢这些数据。**
3. **只有"派生数据"和"进行中任务进度"在内存**：RAG 向量索引、取数结果缓存、知识缺口缓存、自动注释/批量补全任务进度、ClickHouse 线程本地连接。**重启后索引与缓存可重建，后台任务进度会丢。**
4. **缓存是派生层，不是事实源**：`result_cache` 为进程内字典，TTL 120s、上限 200 条，key 含用户+角色+可见表指纹+知识指纹，**单实例下不会跨用户串号**；但多 worker/多实例不共享。
5. **最需要关注的三个风险**：SQLite 未启用 WAL（单写者并发写会排队/可能锁等待）、知识改动后索引需**手动重建**才生效、缓存命中路径**没有写 `query_context`**（可能导致"当前窗口增量修改"无锚点）。

---

## A. 当前数据存储架构图

```
┌────────────────────────── 浏览器（原生 JS 单页） ──────────────────────────┐
│  localStorage：登录 token（core.js）                                       │
│  JS 内存变量：CURRENT_USER / lastAnswer / lastQueryContextId / currentClarify │
└───────────────────────────────┬────────────────────────────────────────────┘
                                │ HTTP + Authorization: Bearer <token>
┌───────────────────────────────▼────────────────────────────────────────────┐
│ server.py（FastAPI，单 worker；阻塞调用用 asyncio.to_thread 包住）             │
│ 进程内存态：                                                                 │
│   _INDEX            RAG 向量索引（懒加载，首请求构建）                        │
│   result_cache      _STORE 结果缓存 + _KFP 知识指纹缓存                       │
│   _GAPS_CACHE       知识缺口扫描缓存                                          │
│   _PREP_STATE/_DOC_QUEUE  自动表注释任务进度                                  │
│   _FILL_TASKS       批量字段注释补全任务进度                                   │
│   db_clickhouse._LOCAL  线程本地 ClickHouse 长连接                            │
└───────┬─────────────────────────┬─────────────────────────┬────────────────┘
        │                         │                         │
        ▼                         ▼                         ▼
┌──────────────────┐   ┌──────────────────────┐   ┌──────────────────────────┐
│ 配置/知识/状态库  │   │ 业务数据源（只读）     │   │ 本地文件系统              │
│ SQLite           │   │ db.py 门面            │   │ .env（密钥，gitignore）   │
│ agent_config.db  │   │  ├ ClickHouse（生产） │   │ models/（embedding 模型） │
│ 约 36 张表        │   │  └ SQLite demo_shop.db│   │ festivals.json（节假日）  │
│ CONFIG_DB 可配    │   │                      │   │ logs/prompt/（可选）      │
└──────────────────┘   └──────────────────────┘   │ evaluation/reports/ docs/ │
                                                   │ dist/ 代码包副本           │
                                                   └──────────────────────────┘
```

**关键点**：SQLite 既是"知识库"也是"状态库"，是唯一的**事实源**；ClickHouse 是被查询的**外部只读数据源**，系统不拥有其数据；向量索引与缓存都是从上述两者派生出来的**可重建副本**。

---

## B. 数据归属清单

### B.1 配置 / 知识库（SQLite `agent_config.db`，`config_store.py`）

| 数据名称 | 存储位置 | 表 | 写入入口 | 读取入口 | 持久化 | 可重建 | 更新方式 |
|---|---|---|---|---|---|---|---|
| 账号 | agent_config.db | `users` | `config_store.create_user` / 改密 | 登录、账号管理 | 是 | 否（仅备份/种子） | 增删改 |
| 会话 token | agent_config.db | `sessions` | `create_session()` | `get_session_user()` | 是（12h TTL） | 否（重登） | `delete_session` / `purge_expired_sessions` |
| 系统 Prompt 规则 | agent_config.db | `prompts` | 管理页 / `admin.py` | 索引构建、Prompt 组装 | 是 | 否（人工资产） | 在线编辑 |
| 指标口径 | agent_config.db | `metrics` | 知识管理 / AI 起草 / `_seed_*` | `knowledge.get_metrics` → `rag` | 是 | 否（人工资产） | CRUD + 治理状态 |
| 业务实体 | agent_config.db | `entities` | 知识管理 | `rag` / 语义层 | 是 | 否 | CRUD |
| 关联规则 | agent_config.db | `relationships` | 知识管理 | 语义层 P2 | 是 | 否 | CRUD |
| 数据字典 | agent_config.db | `dict_entries` | 知识管理 / 从库导入 | `rag` 字典召回 | 是 | 否 | CRUD |
| 表注释 | agent_config.db | `table_comments` | 知识管理 / AI 起草 | 索引构建 | 是 | 否 | CRUD |
| 字段注释 | agent_config.db | `column_comments` | 知识管理 / 批量补全 | 索引构建 | 是 | 否 | CRUD |
| 业务文档 | agent_config.db | `kb_docs` | 知识管理 | `rag` P4 | 是 | 否 | CRUD |
| 表权限 ACL | agent_config.db | `table_acl` | 表与权限页 | `rag._filter_visible` / `guard` | 是 | 否 | 逐表授权 |
| 知识变更历史 | agent_config.db | `knowledge_history` | 各知识写入口 | 知识治理页 | 是 | 否 | 追加写 |
| 索引重建状态 | agent_config.db | `index_state` | `set_index_state()` | `get_index_state()` | 是（仅状态/计数） | 索引本体可重建 | upsert |

### B.2 运行状态 / 业务记录（SQLite `agent_config.db`）

| 数据名称 | 存储位置 | 表 | 写入入口 | 读取入口 | 持久化 | 可重建 | 更新方式 |
|---|---|---|---|---|---|---|---|
| 取数记录 | agent_config.db | `query_records` | `add_query_record`（`server._record_ask`） | 历史/审核/看板 | 是 | 否 | 追加 + 审核字段更新 |
| 多轮澄清会话 | agent_config.db | `clarify_sessions` | `clarify_save` | `clarify_get` | 是 | 否 | 状态机更新 |
| 查询上下文（增量修改锚点） | agent_config.db | `query_context` | `save_query_context` | `load_query_context` | 是 | 否 | 追加（按 id） |
| 结果快照 | agent_config.db | `query_result_snapshots` | `save_result_snapshot` | `get_result_snapshot` | 是（≤2000 行） | 重跑可能变化 | `INSERT OR REPLACE` |
| 收藏 | agent_config.db | `favorites` | 收藏接口 | 收藏页 | 是 | 否 | 增删 |
| 用户反馈 | agent_config.db | `feedbacks` | `add_feedback` | 反馈处理页 | 是 | 否 | 状态更新 |
| 权限审计 | agent_config.db | `permission_audit_logs` | `permission.audit` | 权限审计页 | 是 | 否 | 追加写 |
| 权限策略 | agent_config.db | `roles` / `user_role_bindings` / `data_domains` / `sensitive_resources` / `permission_policies` / `permission_policy_versions` / `row_permission_rules` / `permission_approvals` / `ai_policy_drafts` | `permission.py` / 权限页 | `permission.evaluate` | 是 | 否 | CRUD / 版本化 / 审批 |
| SQL 经验案例 | agent_config.db | `sql_experience` | `sql_experience` 写入 | 经验检索 / 经验页 | 是 | 否 | CRUD + 审核/验证 |
| 经验规则 | agent_config.db | `sql_experience_rules` | 规则归纳/发布 | 经验检索 | 是 | 否 | 状态流转 |
| 经验使用日志 | agent_config.db | `sql_experience_usage` | `log_usage` | 归因分析 | 是 | 否 | 追加写 |
| 标准问题集 | agent_config.db | `golden_cases` / `golden_case_versions` / `golden_case_verify_log` | `golden_cases.py` | 评测看板 | 是 | 否 | CRUD + 版本/验证 |

### B.3 业务数据源（外部，只读）

| 数据名称 | 存储位置 | 表/集合 | 写入入口 | 读取入口 | 持久化 | 可重建 | 更新方式 |
|---|---|---|---|---|---|---|---|
| 数仓业务表 | ClickHouse（`ctc_data_warehouse`） | 约 779 张表 / 18,523 字段 | 无（系统只读；上游 ETL 写入） | `db_clickhouse._run` → `db.execute_sql` → `agent._exec_sql` | 是（外部系统） | 不适用（非本系统所有） | 上游同步 |
| 演示数据 | SQLite `demo_shop.db` | 演示表 | `db_sqlite.create_demo_db` | `db.execute_sql` | 是 | **是**（可重建） | 重新生成 |

### B.4 内存态（进程内，重启即失）

| 数据名称 | 存储位置 | 结构 | 写入入口 | 读取入口 | 持久化 | 可重建 | 更新方式 |
|---|---|---|---|---|---|---|---|
| RAG 向量索引 | 进程内存 `server._INDEX["data"]` | dict（表/指标/字典/实体/文档 + 向量 + `_semantic`） | `rag.build_index()`（首请求懒加载 / `/api/index/rebuild`） | `agent`、`semantic_layer`、`rag.retrieve_dicts` | **否** | **是**（从 SQLite 知识 + ClickHouse schema 重建） | 重建整个索引 |
| 取数结果缓存 | `result_cache._STORE` | dict，key→{payload,perm,at,hits} | `result_cache.put` | `result_cache.get` | **否** | 是（清空/重启） | TTL 淘汰 + 容量淘汰 |
| 知识指纹缓存 | `result_cache._KFP` | {v,at} | `_knowledge_fp` 内部 | 同上 | **否** | 是 | 30s TTL |
| 知识缺口缓存 | `server._GAPS_CACHE` | {at,data} | 缺口扫描接口 | 知识治理页 | **否** | 是 | 手动刷新/过期 |
| 自动表注释任务 | `server._PREP_STATE` + `_DOC_QUEUE` | dict + Queue | 授权/预生成接口 | 任务状态接口 | **否** | 否（进度丢） | daemon 线程消费 |
| 批量注释补全任务 | `server._FILL_TASKS` | task_id→进度 dict | 批量补全接口 | 任务进度接口 | **否** | 否（进度丢） | daemon 线程执行 |
| ClickHouse 长连接 | `db_clickhouse._LOCAL`（threading.local） | client | `_shared_client` | `_run` | 否 | 是（自动重连） | 复用 + 超龄重建 |

### B.5 文件 / 日志 / 本地目录

| 路径 | 内容 | 是否持久化 | 说明 |
|---|---|---|---|
| `.env` | 密钥与连接配置（`DEEPSEEK_API_KEY`、`CK_*`、`SSH_*` 等） | 是 | 已 gitignore；模板 `.env.example` |
| `agent_config.db` | 配置/知识/状态库（当前 7.2 MB） | 是 | 最核心资产 |
| `agent_config.db.bak-*` / `.workbuddy/backup/` | 历史备份 | 是 | 手工/脚本备份 |
| `demo_shop.db` | 演示数据 | 是 | 可重建 |
| `config.db` | 0 字节 | 是 | **遗留空文件，未被使用** |
| `models/` | fastembed `bge-small-zh-v1.5` 模型缓存 | 是 | 内网离线部署关键 |
| `festivals.json` | 2020–2030 节假日静态表 | 是 | `calendar_util` 读取 |
| `logs/prompt/` | Prompt dump | 仅 `PROMPT_DUMP=1` 时 | 默认关闭 |
| `evaluation/reports/`、`docs/` | 评测报告与设计文档 | 是 | 产物 |
| `dist/`、`zhishu-ai-code-bundle/` | 代码打包副本 | 是 | 非运行时依赖 |
| stdout | 运行日志（`print`） | 否 | **无 logging 模块/无文件日志**；生产中由容器采集 |

---

## C. 数据源与缓存关系

### C.1 是否存在缓存？

存在，且**只有进程内缓存，没有 Redis/外部缓存**。

| 缓存 | 位置 | 缓存什么 | key 设计 | TTL | 失效方式 |
|---|---|---|---|---|---|
| 取数结果缓存 | `result_cache.py` `_STORE` | 成功的取数响应 payload + 权限判定 | `sha1(q+username+role+可见表指纹+知识指纹+方言+模型+经验开关)` | `RESULT_CACHE_TTL` 默认 120s | TTL 到期 / 容量淘汰（默认 200）/ `/api/index/rebuild` 时 `clear()` |
| 知识指纹缓存 | `result_cache._KFP` | 6 张知识表的「条数+最大 updated_at」指纹 | 单例 | `RESULT_CACHE_KFP_TTL` 默认 30s | 到期重算 |
| 知识缺口缓存 | `server._GAPS_CACHE` | 缺口扫描结果 | 单例 | `_GAPS_TTL`（代码内常量） | 手动刷新 / 到期 |
| RAG 索引 | `server._INDEX` | 表/指标/字典/实体/文档向量 | 单例 | 无（常驻） | 手动 `/api/index/rebuild` / 重启 |

### C.2 缓存与事实源的关系

- **事实源是 SQLite**（知识/权限/记录），**缓存是派生副本**：缓存内容随时可丢弃重建，不承担"唯一来源"职责。
- 缓存 key 含**知识指纹**：改指标口径、补字段注释、发布经验后，指纹变化 → 旧 key 不再命中（最多延迟 30s，因为指纹本身缓存 30s）。
- 缓存 key 含**用户+角色+可见表指纹**：不同用户/不同权限不会命中彼此的结果，**单实例下不存在跨用户数据泄露**；代码注释明确「缓存不能变成权限旁路」。
- **缓存命中仍写审计**：`server._run_start` 命中缓存时调用 `_record_ask`（`server.py:954`），会写 `query_records` 与权限审计。
- **只缓存成功结果**：`should_cache()` 排除 `ok=False` 与 `degraded`，避免把偶发故障固化。

### C.3 跨用户 / 跨租户风险

- 单实例：key 含 `username + role + ACL 指纹`，**无跨用户串号风险**。
- 多 worker / 多实例：每个进程独立 `_STORE`，**不共享**（不会串号，但命中率下降、内存翻倍）。
- **当前无多租户概念**（`config_store` 无 `tenant_id`），因此不存在"跨租户"缓存隔离问题；未来做多租户需重新设计 key 与存储隔离。

---

## D. 任务状态持久化情况

| 问题 | 结论 | 证据 |
|---|---|---|
| 1. 用户会话保存在哪里？ | **服务端 SQLite `sessions` 表**，随机 token，默认 12 小时过期；前端仅在 `localStorage` 保存 token | `config_store.create_session` / `get_session_user`；`_SESSION_HOURS = 12`；`frontend/static/js/core.js` |
| 2. 查询任务状态保存在哪里？ | **`query_records`**（SQL/ok/attempts/trace/understanding/req_verdict/review_status）+ **`clarify_sessions`**（多轮澄清状态机）+ **`query_context`**（增量修改锚点） | `server._record_ask`、`config_store.clarify_save`、`save_query_context` |
| 3. SQL 生成结果是否持久化？ | **是**：`query_records.sql`、`query_context.payload.sql`、`clarify_sessions.final_sql`、`sql_experience.sql_raw`、`golden_cases.expected_sql`；结果行存 `query_records.result_preview` + `query_result_snapshots`（≤2000 行） | 上述表结构 |
| 4. 用户反馈是否保存？ | **是**：`feedbacks` 表（当前 5 条），含问题/SQL/结果摘要/原因/处理状态 | `config_store.add_feedback`（`config_store.py:2580`） |
| 5. 管理员审核状态是否保存？ | **是**：`query_records.review_status/reviewer/reviewed_at`；另 `sql_experience.review_status`、`golden_cases.review_status`、`permission_approvals` | `config_store.review_record`（`:3533`） |
| 6. 服务重启后哪些状态会丢失？ | **丢**：RAG 索引、结果缓存、知识指纹、缺口缓存、自动注释/批量补全任务进度、ClickHouse 长连接、SSH 隧道 | 见 B.4 |
| 7. 前端与后端记录是否可能不一致？ | **可能**，见 E.6/E.7/E.8 | 见下 |

**重启后的恢复行为**：
- 首次请求触发 `server.get_index()` → `rag.build_index()` 重建索引（需加载 embedding 模型 + 拉 schema + 向量化，耗时数秒；若 ClickHouse/隧道不可用则索引构建失败）。
- 缓存从空开始（命中率短暂下降，不影响正确性）。
- 后台任务进度不可恢复，需重新发起。

---

## E. 数据不一致与丢失风险

| # | 风险 | 证据 | 影响 | 等级 |
|---|---|---|---|---|
| E1 | **SQLite 未启用 WAL**，默认回滚日志，单写者 | `config_store._connect()` 只设 `timeout=10`，无 `PRAGMA journal_mode=WAL` | 多管理员/多请求并发写时写事务串行，极端情况 `database is locked` | 中 |
| E2 | **知识改了但索引没重建** → 检索仍用旧知识 | `server.rebuild_index()` 需显式调用；`index_state` 仅记状态不存向量；无"知识变更自动置脏" | 改了口径/注释后答案不变，排障困难 | 中高 |
| E3 | **缓存命中不写 `query_context`** | `server.py:943-965`：命中缓存只调 `_record_ask`（954），未调 `_attach_query_context` | 命中缓存的结果 `payload` 无 `query_context_id`，前端"当前窗口增量修改"可能无锚点 | 中 |
| E4 | **结果缓存进程内、不跨实例** | `result_cache._STORE` 为模块级 dict | 多 worker/多实例时命中率低、内存重复；不影响正确性 | 低 |
| E5 | **知识指纹有 30s 缓存窗口** | `_knowledge_fp` 缓存 `RESULT_CACHE_KFP_TTL=30` | 改知识后最多 30s 内仍可能命中旧缓存 | 低 |
| E6 | **前端全局状态在 JS 变量**，刷新即丢 | `ask.js` `lastAnswer` / `lastQueryContextId` / `currentClarify` | 刷新后无法直接"继续上一轮修改"，需从历史重新打开 | 低 |
| E7 | **历史回看的 `status_chain` 只能部分还原** | 2026-10-30 记录：落库只有 sql/ok/req_verdict/review_status，`partial=True`，部分状态 `None` | 历史记录上"已校验/已执行/结果合理"可能显示"未留存" | 低 |
| E8 | **澄清前后端契约曾不一致** | 2026-11-05：前端判 `r.kind==="done"`，后端实际在 `r.clarify.kind` → 成功路径卡死（已修） | 同类字段契约漂移可能再次发生 | 低（已修） |
| E9 | **后台任务进度纯内存** | `server._PREP_STATE` / `_FILL_TASKS` / `_DOC_QUEUE` + daemon 线程 | 重启丢失进度，批量补全需重跑 | 低 |
| E10 | **无结构化日志/无 trace 落盘** | 全仓无 `logging`（仅 `.venv` 依赖）；`print` 到 stdout；`trace_json` 仅随 `query_records` 落库 | 问题排查依赖 stdout，容器重启即丢；无级别/无轮转 | 中 |
| E11 | **单实例假设** | `sessions`/`_INDEX`/`_STORE` 均为单进程内存 + 本地 SQLite 文件 | 横向扩容会出现缓存不共享、索引各自构建、SQLite 文件锁竞争 | 中（架构约束） |
| E12 | **快照/预览有行数上限** | `_SNAPSHOT_MAX_ROWS=2000`；`result_preview` 仅前几行 | 超 2000 行的结果导出/回看不完整 | 低 |
| E13 | **仓库含密钥文件与备份** | `.env` 含真实密钥；多个 `*.db.bak-*` | 备份/打包文件若外泄即泄露密钥与数据 | 中（治理） |

> **未确认项**：
> - ClickHouse 只读账号的实际授权范围（需连库验证，代码层面仅知 `CK_USER` 默认 `ai_reader`）。
> - 生产是否已启用 WAL / 是否已挂定时备份（`deploy.md` 有脚本建议，但**代码未强制**，无法从仓库确认线上是否执行）。
> - 多实例部署现状（仓库无容器编排文件，无法确认实际副本数）。

---

## F. 最小必要优化建议

> 原则：**只解决已识别的真实风险，不为"架构完整"而引入新组件**。当前单实例 + 本地 SQLite 下，内存缓存足够；**不建议现在引入 Redis**，仅在明确要多实例/多 worker 时才需要共享缓存与会话。

| 优先级 | 建议 | 改动范围 | 解决的风险 |
|---|---|---|---|
| P0 | **SQLite 开启 WAL + busy_timeout**：`config_store._connect()` 里 `PRAGMA journal_mode=WAL`、`PRAGMA busy_timeout=10000` | 1 个函数 | E1 |
| P0 | **知识写入口置"索引脏"标记**：写指标/注释/字典/实体/关系/文档后写 `index_state.status='dirty'`，前端提示"需重建索引"；或提供一键重建入口 | 知识写入口 + 前端提示 | E2 |
| P0 | **缓存命中路径补 `_attach_query_context`**（或缓存时一并存 `query_context_id`） | `server._run_start` 命中分支 | E3 |
| P1 | **加结构化日志**：引入 `logging` + `trace_id`，落文件/采集，替代纯 `print` | 入口 + 关键链路 | E10 |
| P1 | **后台任务状态轻量落库**：新增 `tasks` 表记录 task_id/进度/结果，重启后可查（不必恢复执行） | 1 张表 + 2 个接口 | E9 |
| P2 | **前端刷新恢复**：从 `/api/records` 或 `query_context` 恢复 `lastAnswer`/`lastQueryContextId` | 前端 JS | E6 |
| P2 | **确认并固化备份任务**：把 `deploy.md` 的每日备份脚本纳入部署清单，验证已执行 | 部署脚本/文档 | E13 |
| P2 | **清理遗留**：`config.db`（0 字节）、`dist/`、`code-bundle/` 等非运行时文件移出运行目录 | 文件整理 | 降低误用/泄露面 |

**明确不建议现在做的事**：
- 不引入 Redis / 消息队列（单实例、量级未到瓶颈）。
- 不引入 Milvus/Qdrant（当前知识量级为 57 指标/22 关系/23 字典，内存向量足够）。
- 不把缓存改成"事实源"——缓存永远只是派生层。

---

## 附：本次审查的只读边界

- 未修改任何 `.py` / `.html` / `.js` / `.env` / `.db`。
- 对 `agent_config.db` 仅执行 `SELECT COUNT(*)` 统计行数，未写入。
- 未读取 `users` 表密码哈希字段（安全策略拦截）。
- 行数为审查时点快照，可能随使用变化。
