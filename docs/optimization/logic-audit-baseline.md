# 知数AI 逻辑自审耗时优化专项 — 现状审计报告（Baseline）

> 阶段：第一阶段（现状审计，未改任何代码）
> 日期：2026-11-03
> 范围：`agent.py` 取数主流程 `ask_v2` 中的「逻辑自审」及其上下游

---

## 0. 结论速览

1. **最耗时函数是 `_review_sql`（SQL 逻辑自审）**，单次 LLM 调用实测 **14.48s**（生成 705 字逐字段诊断 JSON），占首轮取数总耗时的大头。
2. 它是**无条件执行**的：只要 `SQL_REVIEW_ENABLED=true`（默认），每条 SQL 都会过一遍，**不区分简单/复杂**。
3. 它**没有 timeout、没有 max_tokens**（`llm.chat` 默认 `timeout=None`），因此会"生成到模型自己停"。
4. 程序化校验（`guard.sql_guard` + `sql_validator.validate_sql_ex`）已经相当完整，**与逻辑自审存在部分重叠**（JOIN 基数、时间窗口、聚合、除零）。
5. 自愈循环上限为 `MAX_RETRY=3`（超过任务书要求的"最多 1 次定点修复"）。
6. 检索（metric/entity/table/doc 多路向量召回 + 经验检索）是**串行**执行的。
7. Token 消耗当前**未打点**（`llm.chat` 只返回 `content`，丢弃了 `usage`）。

---

## 1. 模型与推理参数

| 项 | 值 | 位置 |
|---|---|---|
| 模型 | `deepseek-flash` | `config.py:37` |
| Base URL | `https://api.deepseek.com` | `config.py:36` |
| 温度 | `0.0` | `config.py:38` |
| max_tokens | **未设置** | `llm.py:29` |
| timeout | **未设置（None）** | `llm.py:20/38` |
| 重试 | 无（openai 客户端默认） | `llm.py` |

`llm.chat(system, user, timeout=None)` 是唯一的 LLM 出口（`llm.py:20`）。它只返回 `resp.choices[0].message.content.strip()`，**不返回 `usage`（token 统计）**。

---

## 2. 当前实际调用链路（一次首轮直接执行的取数）

```
自然语言问题
  │
  ├─ [enrich]         实体/关系/文档向量增强（缓存，非 LLM）
  ├─ [understand]     查询理解（确定性关键词/字典，实测 0.2~0.4ms，非 LLM）
  ├─ [retrieve]       ★ 多路向量召回：指标→实体→表→文档【串行】
  │                   + rag.match_disabled_metric（指标权限）
  │                   + sql_experience.retrieve（经验检索，串行）
  ├─ [resolve_prompt] 冲突解决 + 分层 Prompt 组装（非 LLM）
  ├─ [generate]       ★ LLM 调用 #1：llm.chat(system_prompt, question) 生成 SQL
  ├─ [validate]       ★ 规则校验（非 LLM）：
  │                    guard.sql_guard（只读/语法）→ check_tables_ex（表权限）
  │                    → sql_validator.validate_sql_ex（P0/P1/P2 规则）
  │                    → permission.evaluate + apply_row_filters（字段/行级权限）
  │                    └─ 失败则自愈回灌：LLM 调用 #N（fix_user，最多 MAX_RETRY=3 次）
  ├─ [requirement]    （仅 modify 模式）requirement_review.review：LLM 调用（timeout 8s）
  ├─ [execute]        db.execute_sql（ClickHouse，实测 60~408ms）
  └─ [result_validate_answer]
                       ├─ nl_answer.answer   ★ LLM（2.3s，现已改为按钮按需，want_answer=False）
                       └─ _review_sql        ★ LLM（14.5s，逻辑自审，本次优化对象）
```

### 首轮查询的 LLM 调用次数（当前，want_answer=False）

| 调用 | 次数 | 耗时 | 说明 |
|---|---|---|---|
| SQL 生成 | 1 | 1.5~3.9s | 必需 |
| 自愈修复 | 0~3 | 每次 ~2~4s | 仅校验失败时 |
| 需求审核 | 0 | — | `REQ_REVIEW_MODE=modify_only`，首轮不建契约 |
| **逻辑自审 `_review_sql`** | **1** | **~14.5s** | **无条件，瓶颈** |
| 自然语言回答 | 0 | — | 已改按钮按需 |

→ 首轮稳定为 **2 次 LLM 调用**（生成 + 自审），其中自审占绝对大头。

---

## 3. 逻辑自审定位

| 函数 | 文件:行 | 职责 |
|---|---|---|
| `_review_sql(question, sql, definitions)` | `agent.py:267-281` | 自审入口：开关判断 + 调 LLM + 容错解析 |
| `_parse_review(raw)` | `agent.py:250-264` | 容错抽取自审 JSON |
| `build_review_prompt(question, sql, definitions)` | `prompt.py:387` | 构造自审 Prompt（要求逐字段 JSON 诊断） |
| 调用点 | `agent.py:1021/1026` | 与 `nl_answer` 并行（`ThreadPoolExecutor(max_workers=2)`） |

自审开关：`config.SQL_REVIEW_ENABLED`（`config.py:199`，默认 `true`，环境变量可关）。

自审语义：**执行之后、返回之前**的"字段级业务逻辑诊断"，只诊断、不改 SQL、不重跑、不拦截（失败返回 None 不影响主流程）。

---

## 4. 各环节耗时实测（`query_records.trace_json`，毫秒）

| 阶段 | id=237 | id=236 | id=235 |
|---|---|---|---|
| enrich | 0.0 | 0.0 | 0.0 |
| understand | 0.4 | 0.2 | 0.0 |
| retrieve | 174.0 | 88.4 | 127.5 |
| resolve_prompt | 46.6 | 2749.0 | 28.9 |
| generate（LLM 生成 SQL） | 1488.9 | 3524.1 | 3877.3 |
| validate（规则校验） | 23.4 | 29.8 | 18.7 |
| execute（ClickHouse） | 59.5 | 408.2 | 90.5 |
| **result_validate_answer** | **26252.2** | **29466.6** | **15330.9** |

分步计时（独立实验，`llm.chat` 直接测）：

| 调用 | 耗时 | 输出长度 |
|---|---|---|
| 纯往返 + 极短生成（baseline） | 2.34s | 2 字 |
| `nl_answer.answer` | 2.29s | 147 字 |
| **`_review_sql`（build_review_prompt）** | **14.48s** | 705 字 |

`result_validate_answer` = max(nl_answer, review) ≈ review 的 14.5s（nl 只有 2.3s，且现已可跳过）。

---

## 5. 最耗时函数

**`_review_sql`（agent.py:267）** —— 无条件、无超时、无 max_tokens 的 LLM 调用，实测 14.5s，占首轮总耗时的绝大部分。

次要慢点：`generate`（SQL 生成 1.5~3.9s）、`resolve_prompt` 偶发 2.7s（某次）、`nl_answer` 2.3s（已改按钮）。

---

## 6. 重复调用情况

1. **同一条 SQL 被两套 LLM 审核**：`requirement_review.review`（执行前，modify 模式）+ `_review_sql`（执行后，无条件）。二者都做"SQL 是否符合问题"的判断，**部分重复推理**。
2. **自审未随自愈重复**：自愈修复后只重过规则校验（`_pre_execute_check`），`_review_sql` 只跑一次 → **无"自审失败后重复自审"问题**（这点合规）。
3. **检索串行**：指标→实体→表→文档四路向量召回 + 经验检索，顺序执行、无并行。

---

## 7. 功能重叠分析

| 维度 | `guard.sql_guard` | `sql_validator.validate_sql_ex` | `_review_sql`（LLM） |
|---|---|---|---|
| SQL 语法 | ✅（sqlglot） | — | — |
| 只读限制 | ✅ | ✅（readonly） | — |
| 表/字段是否存在 | ✅（check_tables_ex） | ✅ | — |
| 表/字段/行级权限 | ✅ | — | — |
| JOIN 关系与基数 | — | ✅（join_pre_agg，P2） | ✅（重叠） |
| 聚合逻辑 | — | ✅（metric_*） | ✅（重叠） |
| 时间字段/筛选 | — | ✅（no_dateTrunc / 时间口径） | ✅（重叠） |
| 除零/类型 | — | ✅（ratio_denominator） | ✅（重叠） |
| 业务口径是否符合问题直觉 | — | ❌ | ✅（独有） |
| 别名语义是否准确 | — | ✅（alias_backtick，弱） | ✅（独有，强） |

**结论**：`_review_sql` 的独有价值是"业务口径直觉判断"（规则做不到的语义理解），但它对 JOIN 基数、时间窗口、聚合、除零的检查与 `sql_validator` **重复**。规则校验器已覆盖的部分，不应再无条件重复让 LLM 全量重查一遍。

---

## 8. 可以优化的部分

1. **风险分级 + 条件触发**：简单单表查询（LOW）规则校验通过后，跳过完整 LLM 自审；仅 MEDIUM/HIGH 触发。
2. **自审加 timeout + max_tokens**：给 `_review_sql` 的 `llm.chat` 设 `timeout`（如 6~8s）与 `max_tokens`（如 800），杜绝 14.5s 无界生成。
3. **压缩自审上下文**：当前自审 Prompt 已较精简（question+sql+metric_defs），可再按风险把 `metric_defs` 只保留命中指标、附带 JOIN 基数与已发现规则风险（供 LLM 定点判断而非全量重查）。
4. **自愈循环收紧**：`MAX_RETRY=3` → 至少把"自动定点修复"限制为 1 次（任务书要求），高风险修复后仍失败则拒绝执行。
5. **检索并行化**：多路向量召回（指标/实体/表/文档）互不依赖，可 `ThreadPoolExecutor` 并行；经验检索也可与主召回并行。
6. **检索结果缓存**：稳定的向量召回结果（同问题/同意图）可缓存，避免重复 embed + 重复检索。
7. **Token 打点**：`llm.chat` 返回 `usage`，把每次调用的 token 数记录到 trace，支撑 P95 报告与成本核算。
8. **按风险选模型**：LOW/规则判定可用更快路径；仅 HIGH 深度自审才走完整 LLM。

---

## 9. 不可删除的安全与业务校验（优化红线）

以下必须保留，任何优化不得绕过：

- `guard.sql_guard`：只读限制、SQL 语法、多语句/写操作拦截。
- `guard.check_tables_ex`：表级访问权限。
- `sql_validator.validate_sql_ex`：P0/P1/P2 规则（指标口径、JOIN 基数、除零、时间口径等确定性校验）。
- `permission.evaluate` + `apply_row_filters`：字段级 ALLOW/MASK/DENY + 行级权限注入（改写后重新校验）。
- `_pre_execute_check`：统一执行前闸门（自愈后必须重新校验，校验 SQL == 执行 SQL）。
- `_exec_sql`：唯一执行出口（拒答/非查询语句 fail-closed）。
- 自愈后不得跳过任何安全校验；高风险问题仍存在时禁止执行。

这些全部是**确定性规则**，不依赖 LLM，是"删了会出事"的部分，与可裁剪的 LLM 自审是两回事。

---

## 附：审计清单（对应任务书 §二 9 问）

1. 自审位置：`agent.py:267 _review_sql` / `agent.py:250 _parse_review` / `prompt.py:387 build_review_prompt`。
2. 是否独立调 LLM：是，`_review_sql` 内单独 `llm.chat("", rp)`。
3. 模型与参数：`deepseek-flash`、temp 0、无 max_tokens、无 timeout。
4. 自审输入上下文：`question` + `sql` + `metric_defs`（指标口径定义），不含历史对话/全量知识库。
5. 生成 SQL 后是否重复自审：不重复（自审只跑一次）；但 `requirement_review`（modify 模式）与 `_review_sql` 对同一条 SQL 做了两次 LLM 审核，属部分重复。
6. 自审失败后是否重复生成/重复自审：否（自审失败返回 None，不触发重生成）。
7. 多条独立检索是否串行：是（指标/实体/表/文档 + 经验检索，顺序执行）。
8. 与 Validator/Guard 是否重叠：与 `guard` 无重叠；与 `sql_validator` 在 JOIN 基数/时间/聚合/除零上**部分重叠**。
9. 模型调用次数/耗时/token：首轮 2 次 LLM（生成 ~3s + 自审 ~14.5s）；token 未打点（缺口）。
