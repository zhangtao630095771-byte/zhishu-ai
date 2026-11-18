# 知数AI 问数交互与 SSE 改造 — 第一阶段代码审查报告

> 审查时间：2026-11-12。原则：只审查不修改。审查对象为当前最新代码。

## 一、当前完整调用链

### 前端（frontend/static/js/ask.js + core.js）

```
用户输入 → askQuestion() [ask.js:27]
  ├─ POST /api/clarify/start  {question}        ← 统一"澄清决策"入口（非 /api/ask）
  │    ├─ kind=clarify  → renderClarify() → paintClarify()  渲染候选卡片
  │    ├─ kind=unknown  → renderUnknown()    知识缺失提示
  │    └─ kind=direct   → renderAnswer()     直接执行结果
  │
  ├─ 澄清交互：clarifyPick(i) → submitClarifyAnswer(answer)
  │    POST /api/clarify/answer {query_id, answer}
  │    ├─ clarify.done → renderAnswer()
  │    ├─ kind=clarify → 下一轮 paintClarify()
  │    └─ kind=unknown/cancelled
  │
  └─ 增量修改：modifyQuestion() → POST /api/ask {question, base_query_id}

鉴权：core.js 的 api() 统一 fetch，自动带 Authorization: Bearer <token>，
      401→handleUnauthorized 回登录页；403 must_change_password→改密页。
```

### 后端（server.py + agent.py + clarification.py）

```
/api/clarify/start [server.py:1151]
  → _clarify_index() → query_understanding.understand(LLM)
  → clarification.decide_full()  ── execute / clarify / unknown
      ├─ execute → asyncio.to_thread(agent.ask) → _build_ask_payload → _record_ask → 返回
      └─ clarify/unknown → clarification.new_state + clarify_save → 返回 {kind,query_id,clarification}

/api/clarify/answer [server.py:1208]
  → clarify_load(query_id) → clarification.apply_answer()
      ├─ next_clarify → 返回下一问
      ├─ ready → build_final_question + build_understanding_override
      │         → agent.ask → _record_ask → 返回 payload + clarify.done
      └─ cancelled / needs_knowledge

/api/ask [server.py:858]
  → base_query_id? → _modify_query()  :  _run_start()（do_cache=True）

agent.ask → ask_v2 [agent.py:581]  阶段打点(_mark)：
  enrich → understand → retrieve → resolve_prompt → generate → validate(循环自愈)
  → execute → result_validate_answer
```

### 落库收口（每处执行成功/失败都走）

`_record_ask` [server.py:1051] → `add_query_record`（历史/审核）+ 经验使用日志 + 结果快照 + `_audit_permission`（字段级权限审计）。

## 二、澄清机制存在的问题

1. **两套澄清面，其中一套无上下文（最严重）**
   - A 面（澄清前）：clarification.py + /api/clarify/*，结构化、有 query_id、有会话持久化（clarify_sessions 表）、支持 metric/time/entity 三类 slot。
   - B 面（需求审核后）：agent.py:960 的 needs_clarification，由 renderReqClarify [ask.js:539] 渲染。只返回被动问题文本列表 questions[]，无交互、无 query_id、无状态，用户只能"再问一次"→ 重新走完整流程 = 无上下文重建任务。
2. **澄清结构缺字段**：现有 {slot, question, options[], allow_free_text, allow_cancel, round, max_rounds, hint}，无 type（single/multi/time_range/text）、required、多选、时间范围组件；一次只问一个 slot，不支持多问题一次展示。
3. **前端点击即提交**：clarifyPick [ask.js:221] 点选项立刻 submitClarifyAnswer，无"选中态→取消重选→确认提交"，无已选条件汇总，无修改已确认条件入口。
4. **任务状态不统一**：clarification.py 的 STATUS_* 只管澄清会话，不覆盖取数全生命周期；执行阶段无 task_id（只有落库后生成的 record_id）。
5. **必填校验缺失**：required 语义靠后端 apply_answer 不返回 ready 就不执行兜底，前端无必填拦截。

## 三、SSE 缺失的具体位置

全仓零 SSE 实现（StreamingResponse / text/event-stream / EventSource / ReadableStream 均无）。

| 位置 | 现状 |
|---|---|
| server.py 三入口 | 同步 await asyncio.to_thread(agent.ask)，等跑完一次性 return JSON |
| agent.py ask_v2 | 纯同步阻塞函数，无事件回调/生成器；trace[] 只在末尾返回 |
| llm.py chat() | 非流式（stream=False）阻塞返回 |
| ask.js askQuestion() | spinner 文案写死，非后端驱动 |

## 四、最小改造方案（概要）

1. 后端事件发射器：轻量事件通道，在 ask_v2 的 _mark() 打点处发 understand/retrieve/sql_generating/sql_validating/sql_executing/result 事件；agent.ask 增加可选 events 参数（默认 None，eval/CLI 签名不变）。
2. 新增流式端点 /api/ask/stream（及澄清确认流式路径），StreamingResponse(text/event-stream)，线程跑 agent.ask + asyncio.Queue 桥接；统一 task_id 注册表（内存+落库）支持取消/状态恢复/去重。
3. 澄清结构统一：补 type/required/多选/时间/文本/多问题；把 B 面 needs_clarification 并入同一澄清组件与 WAITING_CLARIFICATION 状态。
4. 前端：fetch + ReadableStream 读流（兼容 POST+Token）；统一澄清组件；进度由后端事件驱动，禁用定时器模拟。
5. 复用 renderAnswer / renderStatusChain / renderValidation / renderResultWarnings。

## 五、可能影响的现有功能

1. /api/ask 与 /api/clarify/* 返回结构 → 旧字段不动，只新增字段/端点。
2. result_cache 缓存命中 → 需决策：命中直接整段返回还是也发流。
3. _record_ask 收口链（历史/审核/经验日志/权限审计）→ SSE 完成时必须仍走同一收口。
4. eval/CLI 直调 agent.ask → 保持同步签名兼容。
5. 前端 renderAnswer 的 AppState 增量修改锚点 + CURRENT_RECORD 审核状态 → 保持。
6. 中间件 _required_role → /api/ask/stream 必须归入 user 级。
7. 180s AbortController 超时兜底 → 被 SSE + cancel + 心跳取代。
