# 知数AI 取数流程与功能总结

> 时间：2026-10-30　口径：**以当前代码为准**（`agent.py` / `server.py` / `semantic_layer.py` 逐文件核对），
> 与 `docs/architecture_review_2026-10-29.md`（当日快照）冲突处，以本文为准并标注差异。
>
> **一句话定位**：把一句中文业务问题，经过「分层语义检索 → 单次 LLM 生成 → 四道确定性闸门 → 单出口执行」，
> 翻译成 ClickHouse SQL 并给出自然语言解读；工程重心不在"生成"，而在**"生成之后如何不让错的东西被执行"**。

---

## 一、规模与形态（实测）

| 维度 | 现状 |
|---|---|
| 后端 | Python 3 + FastAPI + uvicorn，**全同步**（`asyncio.to_thread` 包住阻塞调用），无队列 |
| 前端 | `frontend/index.html`（骨架，78 KB）+ `static/css` 2 个 + `static/js` **12 个模块**，原生 JS，零构建 |
| API | **139 个** `/api/*` 路由；前端 **11 个页签** |
| 配置库 | SQLite `agent_config.db`，**36 张表**（知识 / 账号 / 权限 / 反馈 / 审计 / 经验） |
| 数据源 | ClickHouse 生产（`ctc_data_warehouse`，779 表 / 18523 字段，ACL 后可见 33 张）/ SQLite 演示，`db.py` 统一门面 |
| 向量模型 | fastembed `BAAI/bge-small-zh-v1.5`（512 维，本地缓存，查询侧加 BGE 指令前缀） |
| 知识量（本机） | 指标 52 条、SQL 经验案例 165 条、取数记录 208 条 |
| 测试 | `test_*.py` **28 个**，按 unit / config / integration 三层由 `run_tests.py` 跑 |

---

## 二、六层架构

| 层 | 主要文件 | 职责 | 边界 |
|---|---|---|---|
| 接入层 | `server.py`、`frontend/*` | JWT 会话、按「方法+路径」定角色、139 个 API | 只做协议转换 |
| 编排层 | `agent.py` | `ask → ask_v2 / ask_legacy`；重试、自愈、拒答、降级、trace | **唯一**流程控制点 |
| 语义层 | `semantic_layer.py`、`rag.py`、`query_understanding.py`、`prompt.py`、`calendar_util.py` | 意图识别 → P0~P4 分层检索 → 冲突消解 → Prompt 组装 | 只产知识上下文，不写 SQL |
| 安全层 | `guard.py`、`sql_validator.py`、`permission.py`、`requirement_review.py` | 只读 / 表白名单 / 五维业务校验 / 字段级+行级权限 / 需求一致性 | 纯函数，可脱离服务单测 |
| 知识层 | `config_store.py`、`sql_experience.py`、`gaps.py`、`suggest.py`、`ai_ingest.py` | 36 张表 CRUD、经验中心、知识缺口扫描、AI 起草 | 只存知识，不参与执行 |
| 数据层 | `db.py` → `db_clickhouse.py` / `db_sqlite.py`、`ssh_tunnel.py` | 连接、限流参数、SSH 隧道自愈 | 只执行，不校验 |

**刻意的设计**：安全层"旁挂"在编排层之外，是三个可独立调用的纯函数模块 —— 好处是能被单测直接喂 SQL 验证；
代价是"每个执行点都必须调闸门"靠约定而非编译器，所以用静态测试（`test_gate.py` 的 `test_no_bare_exec_anywhere`）守住。

---

## 三、取数主流程（端到端）

### 3.0 入口分流（`POST /api/ask`）

```
登录用户提问
   │  鉴权中间件：Bearer token → 会话用户；首次登录强制改密；非 /api/ask 等白名单路径默认需 admin
   ▼
/api/ask
   ├── 带 base_query_id ? ──→ ③ 增量修改（带上轮 SQL/意图，只改被要求的部分）
   └── 不带 ─────────────→ _run_start：先做澄清决策
                              ├── execute  → 查结果缓存 → 命中直接返回（payload + cache_hit）
                              │              未命中 → agent.ask(...)（线程池）→ 落库 → 回写缓存
                              ├── clarify   → 只返回澄清问题 + query_id，**不执行 SQL**
                              └── unknown   → 知识不足，转知识治理，**不猜**
```

- **缓存**（`result_cache.py`）：key = 问题 + 用户 + 角色 + 可见表指纹 + 知识指纹 + 方言 + 模型 + 经验开关；
  只缓存成功结果；TTL 默认 120s；`/api/index/rebuild` 自动清空。命中时同样写审计（缓存不能变成权限旁路）。
- **澄清**（`clarification.py`）：候选**只来自已审核且已开放的正式指标**；"点名或识别出多个不同指标 = 一起查"，
  只有"同一概念多个可选口径且用户没点名"才发起澄清；最多 2 轮，超限转知识治理。

### 3.1 主链路 `agent.ask_v2()`（12 步）

| # | 步骤 | 关键点 | 失败后果 |
|---|---|---|---|
| 0 | 语义索引增强 | 实体/关系/文档向量并入索引，缓存在 `index["_semantic"]` | — |
| 1 | Query Understanding | LLM 出意图 / 指标 / 实体 / 维度 / 时间；澄清已锁口径时直接用 override | 识别空 → 后续全靠向量 |
| 2 | P0~P4 分层检索 | 关键词长词优先 + 向量 + 元数据过滤；实体过滤去掉跨业务线表；用户点名表豁免过滤 | — |
| 3 | **指标级权限拦截** | 问的是未开放指标 → 直接拒（否则模型自己拼口径 = 静默算错） | **拒绝** |
| 4 | **零召回兜底** | 一张表一个指标都没命中 → 拒绝，不给模型编造机会 | **拒绝** |
| 5 | 冲突消解 + Prompt 组装 | 唯一入口 `_build_system_prompt()`：role → 时间口径 → P0 → P1 → P2 → P3 → P4 → 字典 → 同名字段硬约束 → 经验 | 顺序错 = 静默答错 |
| 6 | 时间/节假日口径 | `calendar_util` 把"中秋节前 7 天""上月"解析成左闭右开公历区间；"配置分布类"问题显式禁止套默认时间窗 | 猜日期 |
| 7 | 数据字典注入 | 编码 → 中文（"钻石会员"→`vip_level=3`）；召回却没注入会打 `⚠️` 留痕 | 模型瞎猜编码 |
| 8 | SQL 经验检索 | 成功案例参考 / 失败模式预防 / 已发布规则；失败只降级不影响取数；`EXP_EXACT_REUSE` 默认关 | 少一层参考 |
| 9 | LLM 生成 SQL | 单次调用（`temperature=0`）；输出清洗、补库名前缀；**拒答识别**用结构判定（非 SELECT/WITH 即拒答） | — |
| 10 | 需求契约（可选） | `requirement_review.build_contract` 把"这次必须满足什么"写成可比对期望，**只判定不生成**；默认 `modify_only` 模式（首轮零开销） | — |
| 11 | **执行前唯一闸门 + 自愈** | 见 §四；业务类问题回灌模型修正（最多 3 次），安全类直接拒 | 拦截 / 重试 |
| 12 | 执行 → 脱敏 → 结果体检 → 中文回答 | `_exec_sql` 单出口；MASK 字段行级脱敏；比率越界/整列 NULL/漏 GROUP BY/全 0 告警；`nl_answer` 生成解读 | 执行失败也走自愈，**自愈 SQL 必须重过闸门** |

### 3.2 唯一执行出口

`agent._exec_sql()` 是全仓**唯一**真正执行 SQL 的地方：先做"是否查询语句"的 fail-closed 判定（`_refusal_reason`），
不是查询就抛 `NotExecutable`，**不重试、不降级**。`test_gate.py` 静态扫描全仓禁止裸 `db.execute_sql`（当前 0 处）。

---

## 四、执行前唯一闸门 `_pre_execute_check()`（顺序不可交换）

```
① guard.sql_guard            只读 / 禁 DDL·DML·SETTINGS       → hard=True  直接拒
② guard.check_tables_ex      表白名单（sqlglot AST 提表）       → hard=True  直接拒
③ sql_validator.validate_sql_ex  五维业务校验                  → hard=False 回灌自愈
      P0 规则 / P1 指标口径(required·forbidden predicates) /
      P2 JOIN 基数(一对多必须预聚合) / P3 Schema 存在性 / 比率写法
④ permission.evaluate        字段级 ALLOW/MASK/DENY（AST）      → hard=True  直接拒
      含 SELECT * 展开、别名/子查询/表达式归属判定；DENY 任意位置禁止，MASK 仅允许作输出列
⑤ permission.apply_row_filters 行级权限 AST 改写               → 改写后【重新过 ①~④】
⑥ requirement_review.review  需求一致性（第六维，仅当建了契约）  → 由 REQ_REVIEW_BLOCK 决定是否拦
```

- **`hard` 是整道闸门的灵魂**：安全问题不给模型第二次机会，业务口径问题才值得重试 ——
  避免"被拒 → 改一版 → 绕过安全"的经典漏洞。
- **执行 SQL == 校验 SQL**：闸门返回 `final_sql`（含行级改写），执行用它，而不是原始 `sql`。
- **fail-closed**：解析不出表名、字段元数据缺失、策略加载失败 → 一律拒绝，不默认放行。

---

## 五、双流水线与降级策略

| | `ask_v2`（主） | `ask_legacy`（兜底） |
|---|---|---|
| 检索 | P0~P4 分层 + 实体过滤 | 单路混合检索 |
| 校验 | 六维（含需求审核） | 只读 + 表权限 + 五维业务校验（**已补齐**，见下） |
| Prompt | `_build_system_prompt` | `prompt.build_system_prompt` |

- **差异已收敛**：`ask_legacy` 现在也调用 `sql_validator.validate_sql_ex`（构造最小 `SemanticContext`），
  不再存在"回退即弱校验"。当日审查文档中"legacy 无五维校验"一条**已修复**。
- **降级白名单**：只有 `TimeoutError` / `ConnectionError` / 超时·连接拒绝·连接重置这类瞬时故障才允许回退；
  生产环境 `LEGACY_FALLBACK_MODE=always` 被强制收敛为 `whitelist`。其余异常 → **中止并如实报错**，
  返回 `degraded / degrade_stage / degrade_reason` 便于统计降级率。

---

## 六、七个状态分开记（`status_chain`）

```
generated → gate_passed → requirement_verdict → executed → exec_ok → result_sane → business_verified → requirement_fulfilled
```

**生成成功 ≠ 校验通过 ≠ 需求已核 ≠ 执行成功 ≠ 结果合理 ≠ 业务正确 ≠ 需求完成。**
前端不能用"一个绿勾"表示成功 —— 那会把"执行成功但需求没满足"也画成成功。

---

## 七、知识体系

### 7.1 P0~P4 分层（顺序即权威性）

| 层 | 内容 | 检索方式 | 为什么单独成层 |
|---|---|---|---|
| P0 | 系统规则（SQL 铁律） | **全量注入** | 通用规则，必须不可被覆盖 |
| P1 | 指标口径（含 required/forbidden predicates、metric_type） | 关键词最长匹配 → 向量 → 按实体过滤 | 口径错误是第一杀手 |
| P2 | 实体 + 关系（JOIN 规则、基数、预聚合要求） | 实体名匹配 → 关系传递 | 关联靠猜必错 |
| P3 | 表 Schema（列/注释/类型） | 向量+关键词混合，ACL 过滤 | 779 张表塞不进 prompt，收敛到 3~5 张 |
| P4 | 业务文档 / FAQ | 向量检索（高阈值） | 兜底背景知识 |

字典、同名字段硬约束、SQL 经验依次追加在最后 —— **经验是参考，结构上不允许它覆盖指标定义**。

### 7.2 知识的治理准入

`status`（candidate / approved / rejected）+ `enabled` 双开关；`GOVERNANCE_STRICT_STATUS=1` 时 AI 生成的
指标/实体/关系/文档**不进检索**，必须在「知识治理」页复核。

### 7.3 经验中心（`sql_experience.py`）

三张表：案例（成功/失败/修正）、规则（归纳出的模板，带版本与生效状态）、使用日志（召回/采用/放弃/复用/冲突）。
铁律写在代码里：**审核通过 ≠ 结果正确**，`review_status` 与 `verify_status` 独立，`trust_level` 由两者联合推导，
"approved 但执行失败"的案例永远不能成为 golden。

---

## 八、功能总结（按页签）

| 页签 | 面向 | 做什么 |
|---|---|---|
| **智能取数** | 所有人 | 提问 → SQL → 结果表 + 中文解读；展示查询理解、状态链、校验明细、经验命中、结果告警；支持澄清问答、当前窗口增量修改、导出、收藏、提交审核、反馈 |
| 收藏取数 | 所有人 | 收藏的问题+SQL+审核状态，随时回看 |
| 取数审核 | 所有人/管理员 | 提交复核 → 管理员审批（approved/rejected）→ 回填 `business_verified`；可催办 |
| **知识管理** | 管理员 | 表/字段注释、指标口径、数据字典、业务实体、关联规则、业务文档的 CRUD；AI 起草 + 人工确认；SQL 投喂（问题+标准 SQL → 提炼知识） |
| 知识治理 | 管理员 | 知识缺口扫描（枚举字段没字典、表没注释）、P0~P4 优先级视图、AI 录入、全局检索、导出备份、待复核队列 |
| 反馈处理 | 管理员 | 用户反馈（错误的表/字段/口径/时间/枚举/结果不符…）→ 标记处理 → 建议生成 → 一键应用为知识修改 |
| SQL经验 | 管理员 | 成功/失败/修正案例、黄金 SQL 晋升与验证（重跑证据）、规则归纳与发布、影响面分析、使用归因 |
| Prompt 配置 | 管理员 | role / sql_rules 在线编辑与恢复默认（改完需重建索引） |
| 表与权限 | 管理员 | 表级 ACL（生产默认白名单）、注释自动起草、授权前体检 |
| 账号管理 | 管理员 | 账号 CRUD、角色、启停、重置密码、首次登录强制改密 |
| **数据权限** | 管理员 | 角色、数据域、敏感字段清单、字段级策略（ALLOW/MASK/DENY）与版本、行级规则模板（`${user.attr}`）、AI 起草策略草稿、权限审计日志 |

### 后端能力（不占页签）

- 索引重建（`/api/index/rebuild`，自动清缓存）、缓存统计/清空、`/api/status` 运行状态。
- 结果快照（成功取数落一份，供导出/回看）、CSV/JSON 导出、`/api/records/{rid}/export`。
- 审计：`query_records`（每次取数）+ `permission_audit_logs`（权限判定，SQL 字面量脱敏为 `?`）+ `knowledge_history`（知识变更）。
- 评测：`evaluation/`（8 类数据集 + Release/Regression Gate + 反馈回流 `reflow.py`）、`eval/run_eval.py`、`evaluate.py --gate/--regression`。
- 运维：`serve.sh` / `restart.sh` / Dockerfile / `deploy.md` / SSH 隧道自愈 / `.env` 全量开关。

---

## 九、关键设计取舍（值得保留的）

1. **单出口 + 单闸门**：执行收敛到 `_exec_sql`，校验收敛到 `_pre_execute_check`，并配静态测试守住。
2. **fail-closed 优先**：解析不出 → 拒；V2 异常 → 不静默降级；行级改写后 → 重新校验。
3. **拒答不执行**：模型写"无法回答"却附了 SQL 也不跑 —— 否则用户会把那个数字当答案。
4. **零召回即拒**：宁可答"没找到相关表"，也不让模型凭训练记忆编表名。
5. **口径优先于召回率**：P1 按实体/度量类型过滤指标，宁可少召回，不制造口径冲突。
6. **确定性优先于模型**：时间解析、枚举翻译、权限判定、需求核对，能确定性做的都不交给模型。
7. **审核与执行分离**：`requirement_review` 只判定不改写；修正永远由模型重新生成。

---

## 十、现状缺口（截至本文）

| 缺口 | 影响 |
|---|---|
| 评测集缺 `expected_sql` / `expected_result` | 结果正确率算不出来，优化只能看代理指标 |
| 无 SSE 流式 | 一次取数平均 20~35 秒（P95 ~57s），用户只能等 |
| 同步阻塞、无队列 | 并发受 worker 数限制，长查询占住 worker |
| 双流水线并存 | legacy 仍需与 v2 保持行为一致（本轮已补齐五维校验） |
| 需求审核默认只记录不阻断（`REQ_REVIEW_BLOCK=0`） | 灰度期能发现偏差，但还不能拦 |
| `config_store.py` / `server.py` 仍是千行级上帝文件 | 上手与评审成本高 |
| ClickHouse 单点 | DB 不可达则整链路失败，无只读副本降级 |
| 无 API 版本化 | 前后端强耦合，前端硬编码路径 |

---

## 附：一次取数的完整字段产出（前端可用的观测面）

`question / sql / ok / error / columns / rows / tables / metrics / dicts / info / attempts / understanding /
pipeline / experience / experience_warnings / trace / validation / validation_status / result_warnings /
req_review / status_chain / needs_clarification / table_swap / review / answer / perm_decision / masked /
mask_map / cache_hit / degraded / record_id / query_context_id`
