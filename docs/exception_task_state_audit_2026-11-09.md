# 知数AI · 异常处理与任务状态管理审查报告

> 审查类型：**只读分析，未修改任何代码**
> 审查日期：2026-11-09 ｜ 范围：`agent.py` / `server.py` / `llm.py` / `db_clickhouse.py` / `query_understanding.py` / `config.py` / 前端 `ask.js`
> 口径：以真实代码为准；✅=已实现，❌=未实现，⚠️=部分实现

---

## 摘要（结论先行）

1. **异常处理是"分层兜底"而非"分类治理"**：`agent.ask` 会把所有异常转成 `ok=False` 的结构化失败（不会伪装成功），但**没有任何按错误码分类的逻辑**——402 余额不足、401 密钥失效、429 限流、超时，全都被当成同一种 `Exception`。
2. **402 场景的实际表现**：查询理解层会**吞掉 402 并重试 2 次**（两次都必然失败），随后 SQL 生成阶段抛错、被 `agent.ask` 兜底成"企业级流水线异常"，最终以 **HTTP 200 + `ok=false` + 技术文案**返回。即：**能停下，但要多烧 2 次模型调用，且提示不可读、错误码不落结构化字段**。
3. **重试是"多层叠加上界有限"**：理解 2 次 → 生成 1 次 → 校验修复 1 次 → 执行修复 2 次 → ClickHouse 重连 1 次；**无指数退避、无随机抖动、无无限重试**。最坏情况下一次提问可触发 **5 次模型调用、3 次 SQL 执行**。
4. **没有任务队列**：请求由 uvicorn worker + `asyncio.to_thread` 直接执行；后台任务用 daemon 线程 + 进程内存 dict。**进行中的任务不落库、重启不可恢复、无取消 API**。
5. **前端 180s abort 不会终止后端**：客户端断开后，`asyncio.to_thread` 里的 LLM/SQL 仍会继续跑完。

---

## A. 当前异常处理调用链

```
[浏览器] ask.js
   └─ AbortController + setTimeout(180000)   ← 仅客户端超时，不通知后端
        │ HTTP
        ▼
[FastAPI] server.py
   ├─ _auth_middleware → 401/403
   ├─ 各业务端点 try/except → HTTPException(400/404/500/502)
   ├─ 全局 handler：_CHOpsErr / _MaxRetryErr → 503 "数据库未连接"
   └─ /api/ask（无 try/except）
        │
        ▼
   _run_start() → query_understanding.understand()   ← 吞异常，重试 2 次
        │
        ▼
   agent.ask()  ── try/except Exception ──▶ _result(ok=False, degrade_reason=...)
        │
        ▼
   agent.ask_v2()
        ├─ 语义检索 / 冲突消解 / Prompt 组装
        ├─ llm.chat()  生成 SQL              ← ❌ 无 try/except、无 timeout
        ├─ 校验循环 for attempt in 1..MAX_RETRY
        │     ├─ 校验失败 → llm.chat() 修复   ← ❌ 无 try/except
        │     └─ 需求审核（内部 catch → skipped）
        ├─ _exec_sql() → db.execute_sql() → db_clickhouse._run()
        │     └─ 连接类错误 → 丢弃重连 + 重试 1 次
        └─ 执行失败 → 修复循环 for ex_attempt in 2..MAX_RETRY
              └─ llm.chat() 修复              ← ❌ 无 try/except
        │
        ▼
   结果体检 / 脱敏 / nl_answer.answer()       ← 吞异常，降级表格
   _review_sql()                              ← 吞异常，返回 None
```

**关键事实**：
- `llm.py:14-19` 用 `max_retries=0` 关闭了 OpenAI SDK 自带重试，所有重试都由上层业务代码自行控制。
- `llm.chat` 本身**不做任何异常包装**（`llm.py:46`），错误原样抛给调用方。
- `agent.py:761`（生成 SQL）与 `agent.py:820/870/893/1034`（各处修复）**都没有 try/except**，是异常向上冒泡的主要路径。

---

## B. 错误分类清单

| 错误类别 | 典型来源 | 识别 | 处理位置 | 当前行为 | 是否重试 |
|---|---|---|---|---|---|
| **402 余额不足** | DeepSeek `APIStatusError` | ❌ 无专项识别 | `agent.ask` 兜底 | 理解层吞 2 次 → 生成抛 → `ok=false` + 技术文案 | 理解层会重试 2 次（无效） |
| **401 密钥失效** | `AuthenticationError` | ❌ | 同上 | 同上 | 理解层 2 次（无效） |
| **429 限流** | `RateLimitError` | ❌ | 同上 | 同上 | 理解层立即重试 2 次（无退避，可能加剧） |
| **LLM 超时** | `APITimeoutError` | ⚠️ 部分 | 理解/自审/审核有 timeout；生成无 | 理解降级；生成可能长时间阻塞 | 理解 2 次 |
| **LLM 返回非 JSON** | 解析失败 | ✅ | `query_understanding._parse_json` / `requirement_review` | 降级 `{}` / `skipped` | 理解重试 |
| **CH 连接错误** | `OperationalError`/`MaxRetryError`/`ConnectionError`/`TimeoutError` | ✅ `_is_conn_error` | `db_clickhouse._run` | 丢连接 + 重连 + 重试 1 次；仍失败 → 503 | 是（1 次） |
| **CH 服务端超时** | `TimeoutExceeded`/`timeout exceeded` | ✅ 明确排除 | `_is_conn_error` 返回 False | 直接抛，不重试 | 否 |
| **CH SQL 语法/权限错误** | 其他 `Exception` | ✅ 非连接即不重试 | 抛出 → `agent` 执行循环 | 让模型修 SQL 后重跑 | 是（执行循环） |
| **CH 注释抓取失败** | 任意异常 | ✅ | `db_clickhouse.fetch_comments` | 吞掉 + 打印，注释为空 | 否 |
| **SQLite 错误** | `sqlite3.Error` | ❌ 无包装 | 直接抛 | `agent.ask` 兜底 / 500 | 否 |
| **业务校验失败** | `sql_validator` | ✅ | `_pre_execute_check` | 回灌模型修复 | 是（MAX_AUTO_FIX=1） |
| **需求不一致** | `requirement_review` | ✅ | `ask_v2` 循环 | fail→修复；unsure→暂停 | 是（受 AUTO_FIX） |
| **拒答/非查询** | `_refusal_reason` | ✅ | `NotExecutable` | 不执行、不重试 | 否 |
| **参数/请求体错误** | Pydantic/ValueError | ✅ | 端点 | 400 | 否 |

> **结论**：数据库层有明确的"可重试/不可重试"判定（`_is_conn_error`），但**模型层完全没有对应机制**。

---

## C. 当前重试规则

| 重试层 | 触发条件 | 最大次数 | 退避 | 抖动 | 备注 |
|---|---|---|---|---|---|
| OpenAI SDK | — | **0** | — | — | `llm.py` 显式关闭 |
| 查询理解 | 任意异常 | **2** | 无 | 无 | 立即重试；失败置 `degraded` |
| 生成 + 校验自愈 | 校验失败（非 hard） | 循环 `MAX_RETRY=3`，但修复次数 `MAX_AUTO_FIX=1` | 无 | 无 | 实际最多 2 次生成 |
| 执行自愈 | 执行异常（非 `NotExecutable`） | `ex_attempt` 2..3 = **2 次** | 无 | 无 | **未受 `MAX_AUTO_FIX` 约束** |
| ClickHouse 连接 | 连接类错误 | **1** | 无 | 无 | 立即重连再查 |
| 经验定向修复 | `EXP_FAIL_REPAIR=1` | **1** | 无 | 无 | 默认关闭 |
| 前端请求 | — | 0（180s abort） | — | — | 仅客户端 |

**逐项回答**：
1. **哪些异常自动重试**：理解层（所有异常）、校验失败（业务类）、执行失败（非拒答）、CH 连接错误。
2. **最大尝试次数**：理解 2；生成 1+1；执行 1+2；CH 连接 1。
3. **指数退避/随机抖动**：❌ **全仓没有**（无 `sleep`、无 backoff 计算）。
4. **多层重试**：✅ **存在**（理解 → 生成 → 校验修复 → 执行修复 → CH 重连）。
5. **重复调用模型/执行 SQL**：✅ **可能**。模型最多 5 次；SQL 最多执行 3 次（+CH 重连 1 次）。SQL 为只读，无副作用，但会重复读库。
6. **无限重试**：❌ **不存在**。所有网络/执行重试都有上界；`agent.py:62` 的 `while True` 是注释字符串剥离循环，不是网络重试。

---

## D. 当前任务状态流转图

### D.1 状态链（`status_chain`，8 态）

```
generated → gate_passed → requirement_verdict → executed → exec_ok → result_sane → business_verified → requirement_fulfilled
   生成        过闸门          需求判定           执行        执行成功     结果合理        业务已核            需求完成
```

- 状态链在 `ask_v2` 内逐态打点，随响应返回前端（`#chainBar` 四徽章）。
- **不落库为独立列**：`query_records` 只存 `trace_json`、`req_verdict`、`error`、`degrade_reason`；历史回看由 `config_store._chain_from_record` **部分重建**（`partial=True`，推不出的状态为 `None`）。

### D.2 任务标识与持久化

| 项 | 现状 | 证据 |
|---|---|---|
| 任务唯一 ID | ✅ `query_id`（澄清，`token_urlsafe(12)`）、`query_records.id`、`query_context.id` | `server.py:968` |
| 记录当前步骤 | ✅ `status_chain` + `trace`（各阶段 ms） | `agent.ask_v2`、`_mark()` |
| 保存任务状态 | ⚠️ **仅结束时**落 `query_records`；**过程态不落库** | `config_store.add_query_record` |
| 重启恢复 | ❌ 进行中请求丢失，无法 Resume | 无任务表 |
| 重复提交 | ⚠️ 无幂等键；120s 结果缓存可挡"成功查询"的重复，失败不缓存 | `result_cache` |
| 取消 | ❌ 无服务端取消 API | 前端仅 abort 请求 |
| 超时 | ❌ 无服务端超时；仅前端 180s + 各 LLM 局部 timeout（生成无） | `ask.js:50` |

### D.3 过程态持久化的唯一例外

`clarify_sessions` 是**唯一**把"进行中任务"落库的表（`status`：pending/clarifying/needs_knowledge/ready…），因此多轮澄清**可以跨请求继续**；但普通取数（`/api/ask`）执行期间没有任何中间状态落库。

```
用户提交 ──▶ 生成 query_id / record_id
              │
              ├─ 澄清分支：clarify_sessions(status=pending→clarifying→ready)  ✅ 持久化
              │
              └─ 执行分支：ask_v2 全在内存跑
                            │
                            └─ 结束才写 query_records + snapshot + audit
                                  （中途崩溃/重启 → 无记录、无法恢复）
```

---

## E. 重复执行风险

### E.1 最坏链路推演（一次提问）

| 阶段 | 模型调用 | SQL 执行 |
|---|---|---|
| 查询理解 | 1（+1 失败重试） | 0 |
| 生成 SQL | 1 | 0 |
| 校验失败修复 | 1（`MAX_AUTO_FIX=1`） | 0 |
| 执行失败修复 | 2（`ex_attempt` 2..3） | 1 + 2 |
| CH 连接重连 | 0 | +1 |
| **合计** | **最多 5** | **最多 4** |

### E.2 402 场景实测推演（结合历史故障）

```
理解层：llm.chat → 402 抛错 → except 打印 → 第 2 次再 402 → degraded=True   （2 次无效调用）
   ↓ 继续
生成层：llm.chat → 402 抛错（无 try/except）
   ↓
agent.ask：except Exception → ok=False, error="企业级流水线异常…APIStatusError: Error code: 402…"
   ↓
server：HTTP 200 + ok=false 返回前端
```

**结论**：能停下（不会执行 SQL、不会返回假数据），但：
- ❌ 没有识别"402 不可重试"；
- ❌ 理解层仍会无意义重试 2 次；
- ⚠️ 错误码只以**自由文本**出现在 `query_records.error`（截断 2000 字），无结构化 `error_code`；
- ❌ 用户看到的是技术堆栈文案，不是"模型余额不足，请联系管理员"。

### E.3 其他重复风险

| 风险 | 说明 | 证据 |
|---|---|---|
| 执行自愈不受 `MAX_AUTO_FIX` 约束 | 校验修复用掉 1 次后，执行循环仍可再修 2 次 | `agent.py:1032` |
| CH 重连重试 | 连接抖动时会再查一次（只读，幂等） | `db_clickhouse.py:146-149` |
| 前端重复点击 | 无幂等键，每次生成新记录；缓存只对成功结果生效 | `server.py` / `result_cache` |
| 前端 abort 不终止后端 | `to_thread` 线程无法被取消，继续消耗 LLM/SQL | `ask.js:50` |

---

## F. 需要优化的地方

> 原则：只补真实缺口，不引入任务队列/不改架构。

### P0（直接对应 402 事故）

1. **给模型调用加错误分类层**（新增包装，不改 `llm.chat` 签名）：
   - 捕获 `openai.APIStatusError`，按 `status_code` 判定：
     - `401/402/403` → `LLMFatalError`（**不可重试**，立即中止）
     - `429` → `LLMRateLimitError`（可重试 + 退避）
     - 超时/连接 → `LLMTransientError`（可重试）
   - 建议位置：`llm.py` 新增 `chat_checked()`，或在 `agent` 调用点统一包装。
2. **理解层只对瞬时错误重试**：`query_understanding.understand` 的 2 次循环改为"仅超时/网络错误重试"，4xx 立即停止。
3. **结构化记录错误码**：`query_records` 增加 `error_code` / `error_type`（或把 `degrade_reason` 改为结构化 JSON），便于统计 402/429 占比。
4. **用户友好提示**：402→"模型服务余额不足，请联系管理员充值"；401→"模型密钥失效，请联系管理员"；429→"请求过于频繁，请稍后重试"；超时→"模型响应超时，请重试"。
5. **服务端超时**：`asyncio.wait_for(asyncio.to_thread(agent.ask, ...), timeout=N)`；并给 `agent.py:761` 及修复调用补 `timeout`（当前生成无超时，可无限阻塞）。

### P1

6. **统一 LLM 调用超时**：与理解（8s）、自审（10/20s）、需求审核（8s）对齐，生成/修复也设上限。
7. **执行自愈受 `MAX_AUTO_FIX` 约束**：避免"校验修复 1 次 + 执行修复 2 次"叠加。
8. **幂等键**：前端提交带 `client_request_id`，后端去重（缓存只能挡成功结果）。
9. **协作式取消**：前端 abort 后，后端在阶段间检查取消标志（Python 线程无法强杀，至少停止后续 LLM 调用）。
10. **任务状态落库**：新增轻量 `tasks` 表（task_id/阶段/状态/错误码/开始结束时间），支持"卡在哪"排查与重启后查询；不要求恢复执行。

### P2

11. **退避 + 抖动**：仅对可重试错误（429/超时/连接）加指数退避 + 随机抖动。
12. **`status_chain` 落库**：当前只回放重建，关键状态（gate_passed/executed）无法回溯。
13. **完善错误分类**：区分"用户输入问题 / 知识缺失 / 权限拒绝 / 模型故障 / 数据库故障"，前端分型展示。

---

## 附：已实现 vs 未实现（速查）

| 能力 | 状态 |
|---|---|
| 全局异常兜底（不伪装成功） | ✅ `agent.ask` fail-closed |
| 数据库连接错误分类与重连 | ✅ `_is_conn_error` + `_run` |
| 连接类错误 → 503 人话提示 | ✅ `server.py:67-75` |
| 业务校验失败 → 模型修复 | ✅ `MAX_AUTO_FIX=1` |
| 拒答/非查询不执行 | ✅ `NotExecutable` |
| 模型 4xx/5xx 分类 | ❌ 无 |
| 402/401 快速失败 | ❌ 无（理解层还重试 2 次） |
| 结构化错误码 | ❌ 无（仅自由文本） |
| 用户友好错误文案 | ❌ 无（回显技术异常） |
| 指数退避/抖动 | ❌ 无 |
| 服务端请求超时 | ❌ 无 |
| 服务端取消 | ❌ 无 |
| 幂等/去重键 | ❌ 无 |
| 任务过程态落库 | ⚠️ 仅 `clarify_sessions` |
| 重启恢复进行中任务 | ❌ 无 |

---

*本报告仅依据真实代码与调用链推断；`openai` SDK 的具体异常子类以实际运行版本为准，代码中未做任何 `status_code` 判断，因此 402 一定落入通用 `Exception` 分支。*
