# 03 · Agent 执行链路审查（企业业务语义知识库建设 · 第一阶段）

> 审查日期：2026-11-10 · 方式：通读 `agent.py` / `server.py` / `semantic_layer.py` / `rag.py` 真实代码
> 核心结论：**当前只有一套取数流程（`ask_v2`），不存在第二套取数流水线。**

---

## 0. 结论先行：是否两套取数流程？

**否。** 全仓唯一业务编排入口为 `agent.ask()` → `agent.ask_v2()`（`agent.py:1171` 注释明确「Legacy 兜底路径已退役，不再存在」）。

- `/api/ask` 与 `/api/clarify/start` 都汇聚到 `server._run_start`（933 行）→ `agent.ask`。
- 增量修改 `/api/ask?base_query_id` → `server._modify_query`（990 行）→ `agent.ask(modify_context=...)`，**仍是同一套 `ask_v2`**。
- 残留的 `degraded` 字段（`server.py:837-839`、`result_cache.py:224`）为历史观测字段，`agent.ask` 异常分支恒写 `degraded=False`（`agent.py:1203`），**无降级到弱校验路径的语义**。

---

## 1. 完整调用流程图

```
用户提问
   │
   ▼
[0] server.api_ask (server.py:859)
   │  ├─ 拿索引 get_index()
   │  ├─ base_query_id? ──是──► _modify_query (server.py:990)  ──► agent.ask(modify_context=...)
   │  └─ 否 ──► _run_start (server.py:933)
   │            ├─ query_understanding.understand (query_understanding.py:79)
   │            └─ clarification.decide (clarification.py:131)
   │                 ├─ ACTION_EXECUTE ──► agent.ask
   │                 └─ ACTION_CLARIFY/UNKNOWN ──► 发起澄清（不执行）
   ▼
[1] agent.ask (agent.py:1171)  →  ask_v2 (agent.py:580)
   │
   ├─ ①.0 semantic_layer.enrich_index (semantic_layer.py:41)    缓存语义索引
   ├─ ①   query_understanding.understand                          意图+指标+实体识别
   │       （澄清 override 时跳过 LLM 理解，直接锁口径）
   ├─ ②   semantic_layer.retrieve_semantic (semantic_layer.py:260)  P0~P4 多路检索
   ├─ ①.5 rag.match_disabled_metric (rag.py:441)                   停用指标拦截
   ├─ ②.5 零召回兜底：无表无指标 → 拒绝
   ├─ ③   semantic_layer.resolve (semantic_layer.py:506)           冲突解决
   ├─ ③.7 sql_experience.retrieve (sql_experience.py:1277)         历史经验检索
   ├─ ③.9 _build_system_prompt (agent.py:429)                      组装 system prompt
   │       顺序：role → 时间口径 → P0→P1→P2→P3→P4 → 字典 → 硬约束 → 经验
   ├─ ④   llm.chat 生成 SQL（或 EXP_EXACT_REUSE 精确复用）
   ├─ ④.9 requirement_review.build_contract                         需求契约（仅判定）
   │
   ├─ ⑤   [循环 MAX_RETRY 次] _pre_execute_check (agent.py:345)
   │        ├─ guard.sql_guard（只读）
   │        ├─ guard.check_tables_ex（表白名单）
   │        ├─ sql_validator.validate_sql_ex（五维校验）
   │        └─ permission.evaluate + apply_row_filters（字段/行级权限）
   │        └─ 未通过 → 自愈回灌 LLM 修复（最多 MAX_AUTO_FIX 次）
   ├─ ⑤.0 sql_modify.diff_structure（增量修改结构保护）
   ├─ ⑤.1 requirement_review.review（需求一致性第六维审核）
   ├─ ⑤.5 sql_experience.check_sql（失败经验确定性检查）
   │
   ├─ ⑥   _exec_sql (agent.py:119)  ★ 唯一执行出口（chokepoint）
   │        └─ db.execute_sql → db_clickhouse.execute_sql（只读）
   │        执行失败 → prompt.build_repair_prompt 自愈 + 重过完整闸门
   ├─ ⑥.5 permission.mask_rows（字段级结果脱敏）
   ├─ ⑦   result_validator.validate_result（结果体检）
   ├─ ⑧   nl_answer.answer ∥ _review_sql（自然语言回答 ∥ 分层自审，并行）
   │
   ▼
[2] server._build_ask_payload (server.py:787) → 响应
   └─ _record_ask 落 query_records + _attach_query_context 存增量锚点
```

---

## 2. 逐步拆解：函数 / 文件 / 输入 / 输出 / 知识来源 / 异常处理

### 步骤 ① 问题预处理 & 意图识别

| 项 | 值 |
|---|---|
| 调用函数 | `query_understanding.understand(question, index)` |
| 文件 | `query_understanding.py:79` |
| 输入 | `question`（str）、`index`（语义索引，含 metrics/entities 名称） |
| 输出 | `{intent, metrics[], entities[], dimensions[], time_range, filters[], ambiguity, degraded}` |
| 知识来源 | 指标名清单 + 实体名清单（来自索引） |
| 异常处理 | LLM 失败重试 2 次；4xx 不可重试立即降级（`degraded=True`），超时才重试；降级结果不缓存 |

### 步骤 ② 知识检索（Semantic Router）

| 项 | 值 |
|---|---|
| 调用函数 | `semantic_layer.retrieve_semantic(index, question, understanding)` |
| 文件 | `semantic_layer.py:260` |
| 输入 | 语义索引、问题、结构化理解 |
| 输出 | `SemanticContext{p0_rules, p1_metrics, p2_entities, p2_relationships, p3_tables, p4_docs, route_info}` |
| 知识来源 | P0 `prompts.sql_rules`；P1 指标；P2 实体/关系；P3 表结构；P4 文档（均过治理准入） |
| 检索方式 | 每层 `rag._hybrid_retrieve`（关键词优先 + 向量兜底）；用户点名表/字段关键词豁免实体过滤 |
| 异常处理 | P0 取不到则置空；整体包在调用方正常流程中 |

### 步骤 ③ 上下文构建

| 项 | 值 |
|---|---|
| 调用函数 | `semantic_layer.resolve(ctx)` → `agent._build_system_prompt(...)` |
| 文件 | `semantic_layer.py:506`、`agent.py:429` |
| 输出 | 最终 system prompt 文本 + `parts` 分段 + `bindings`（硬约束供校验器比对） |
| 顺序 | role → 时间口径 → P0 → P1 → P2 → P3 → P4 → 字典 → 硬约束 → 经验（经验必须最后，不可覆盖权威口径） |

### 步骤 ④ SQL 生成

| 项 | 值 |
|---|---|
| 调用函数 | `llm.chat(system_prompt, question)`（或精确复用黄金 SQL） |
| 文件 | `llm.py:22`、`agent.py:762` |
| 输出 | `raw_sql` → `_finalize_sql`（`guard.clean_sql` + `guard.qualify_tables` 补库名前缀） |
| 异常处理 | 拒答识别 `_refusal_reason`：非 SELECT/WITH 开头 → 不执行直接返回 |

### 步骤 ⑤ SQL 验证（执行前闸门）

| 项 | 值 |
|---|---|
| 调用函数 | `agent._pre_execute_check(sql, ctx, resolved, allowed_tables, user, meta)` |
| 文件 | `agent.py:345` |
| 校验内容 | ① `guard.sql_guard`（只读）② `guard.check_tables_ex`（表权限）③ `sql_validator.validate_sql_ex`（五维）④ `permission.evaluate`（字段级）⑤ `permission.apply_row_filters`（行级，改写后重校验） |
| 输出 | `{ok, hard, issues, status, perm_decision, final_sql}` |
| 异常处理 | hard 问题（安全类）直接拒绝；业务问题回灌 LLM 自愈（最多 `MAX_AUTO_FIX=1` 次），用尽仍失败 → fail-closed 拒绝执行 |

### 步骤 ⑥ SQL 执行

| 项 | 值 |
|---|---|
| 调用函数 | `agent._exec_sql(sql)`（唯一出口）→ `db.execute_sql` |
| 文件 | `agent.py:119`、`db.py:162`、`db_clickhouse.py:513` |
| 异常处理 | `NotExecutable` 拒答 → 不重试；执行错误 → `prompt.build_repair_prompt` 自愈并**重过完整闸门**（防自愈绕过权限，已实测修复过此漏洞） |

### 步骤 ⑦ 结果处理

| 项 | 值 |
|---|---|
| 调用函数 | `permission.mask_rows`（脱敏）→ `result_validator.validate_result`（体检）→ `nl_answer.answer`（结论）∥ `agent._review_sql`（自审，并行） |
| 文件 | `agent.py:1095-1142` |
| 输出 | 统一 `_result()` 结构（question/sql/ok/columns/rows/tables/metrics/dicts/understanding/pipeline/experience/status_chain/validation/result_warnings/perm_decision/masked/trace...） |

### 步骤 ⑧ 用户反馈

| 项 | 值 |
|---|---|
| 调用函数 | `config_store.add_feedback`、`suggest.generate/apply_suggestion` |
| 文件 | `config_store.py:2580`、`suggest.py:177/264` |
| 闭环 | 反馈 → 定位知识缺口 → `suggest` 生成建议 → 应用建议改知识 → `knowledge_history` 留痕 |

---

## 3. 两条「入口」但不是「两套流程」

| 入口 | 路径 | 本质 |
|---|---|---|
| `/api/ask`（无 base_query_id） | `_run_start` → 澄清决策 → `agent.ask` | 全新查询，同一套 `ask_v2` |
| `/api/ask`（有 base_query_id） | `_modify_query` → `agent.ask(modify_context)` | 增量修改，同一套 `ask_v2` + 保留上一轮上下文 |
| `/api/clarify/start` → `/api/clarify/answer` | 澄清状态机 → 确认后仍 `agent.ask(understanding_override)` | 多轮澄清锁口径，同一套 `ask_v2` |

三者共用 `ask_v2`，区别仅在「是否带 modify_context / understanding_override」，**SQL 生成、校验、执行、权限闸门完全一致**。

---

## 4. 关键安全铁律（已实测确认）

1. **唯一执行出口**：`agent.py` 内不允许裸 `db.execute_sql`（`test_gate` 静态检查守护）。
2. **校验的 SQL == 执行的 SQL**：`final_sql`（可能含行级改写）为准，界面展示、权限校验、实际执行三者同一份字符串。
3. **自愈后必须重过完整闸门**：执行失败自愈、校验失败自愈、经验定向修复，均回到 `_pre_execute_check`。
4. **fail-closed**：任何异常不降级到弱校验路径。
5. **拒答 fail-closed**：模型声明「无法回答」即不执行（`_refusal_reason` 结构判定，非措辞判定）。
