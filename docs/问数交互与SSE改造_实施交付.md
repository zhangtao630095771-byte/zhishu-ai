# 知数AI 问数交互与 SSE 改造 —— 实施交付文档

> 实施时间：2026-11-12。原则：先审查 → 再设计 → 再修改 → 最后测试。
> 第一阶段审查见 `docs/问数交互与SSE改造_第一阶段审查.md`。

## 一、修改文件清单

| 文件 | 改动 |
|---|---|
| `task_state.py`（新增） | 任务状态注册表 + SSE 事件协议常量 + TaskCancelled |
| `agent.py` | `ask`/`ask_v2` 新增 `events`/`is_cancelled` 可选参数；阶段事件发射 + 取消探测 |
| `clarification.py` | 澄清结构补 `type`/`required`/`multiple` 字段 |
| `server.py` | 新增 `/api/ask/stream`、`/api/clarify/answer/stream`、`/api/task/{id}`、`/api/task/{id}/cancel`；鉴权中间件放行 |
| `frontend/index.html` | 新增 `#progressBox` 进度容器 |
| `frontend/static/app.css` | 进度条 / 澄清选中态 / 确认按钮 / 已选汇总样式 |
| `frontend/static/js/ask.js` | `streamAsk` SSE 客户端、进度渲染、统一澄清组件、`askQuestion`/`submitClarifyStream` 改造 |

## 二、原有调用链 → 改造后调用链

**原有**（同步一次性返回）：
```
askQuestion → POST /api/clarify/start → decide_full → {clarify|unknown|direct result}
澄清确认 → POST /api/clarify/answer → {clarify|done result}
```

**改造后**（SSE 流式，旧接口保留兼容）：
```
askQuestion → POST /api/ask/stream ──(SSE)──> task_started → understanding
    ├─ clarify   → clarification_required → (done, 状态 WAITING_CLARIFICATION)
    └─ execute   → retrieval → sql_generating → sql_validating
                 → sql_executing → result → done (状态 COMPLETED)

澄清确认 → POST /api/clarify/answer/stream (带 task_id) ──(SSE)──> 恢复同一任务
    ├─ next_clarify → clarification_updated
    ├─ needs_knowledge → error
    └─ ready   → agent.ask(锁口径) → … → result → done

取消/重连 → POST /api/task/{id}/cancel ；GET /api/task/{id} 查状态与结果
```

## 三、SSE 事件协议

每帧格式（`data: {json}\n\n`，Content-Type: text/event-stream）：

```json
{
  "task_id": "任务ID（全流程唯一锚点）",
  "event": "sql_generating",
  "data": {},
  "timestamp": 1762920000000
}
```

事件名（对应 agent.ask_v2 各阶段，后端真实事件驱动、禁用定时器模拟）：

| event | 阶段 | 前端展示 |
|---|---|---|
| `task_started` | 任务开始 | 进度条亮起 |
| `understanding` | 查询理解完成 | 正在理解问题 |
| `retrieval` | 多路检索完成 | 正在检索企业知识 |
| `sql_generating` | 生成 SQL 前 | 正在生成 SQL |
| `sql_validating` | 校验循环前 | 正在校验 SQL |
| `sql_executing` | 执行 ClickHouse 前 | 正在执行查询 |
| `result` | 结果（完整 payload，含 record_id） | 正在分析结果 → 查询完成 |
| `clarification_required` | 需要澄清 | 渲染澄清组件 |
| `clarification_updated` | 多轮澄清更新 | 渲染下一轮 |
| `error` | 失败/取消/知识不足 | 提示或渲染 |
| `done` | 结束 | 收尾 |
| `heartbeat` | 心跳（15s 无事件时） | 忽略 |

## 四、澄清数据结构

统一协议（在原有 `slot/question/options/allow_free_text/allow_cancel/round/hint` 基础上新增）：

```json
{
  "slot": "metric",
  "type": "single_choice",     // single_choice | time_range | text | multi_choice
  "required": true,
  "multiple": false,
  "question": "你想统计哪种口径？请选择对应指标。",
  "options": [{"value":"...","label":"...","definition":"...","source":"...","status":"approved"}],
  "allow_free_text": true,
  "allow_cancel": true,
  "round": 1,
  "max_rounds": 2,
  "hint": "..."
}
```

任务状态（`task_state`）：`RUNNING / WAITING_CLARIFICATION / COMPLETED / FAILED / CANCELLED`。

## 五、测试结果

- 分层测试（unit + config 层）关键项全部通过：
  - `test_imports.py`（26/26）、`test_clarification.py`（41）、`test_clarification_multimetric.py`（16）
  - `test_pipeline.py`（17/17）、`test_status_chain.py`（46）、`test_modify_query.py`（53/53）
  - `test_requirement_review.py`（52）、`test_gate_req.py`（72）、`test_ambiguity.py`（22/22）
  - `test_frontend_structure.py`（23）
- 冒烟测试：任务注册表、SSE 帧格式、线程→队列桥接、agent 阶段事件顺序（understanding→retrieval→sql_generating）、取消探测、新端点 401 鉴权，均通过。

## 六、未解决问题（待后续迭代）

1. **B 面（需求审核 needs_clarification）深度整合**：目前仍是 `renderReqClarify` 被动文本展示；尚未实现"执行中暂停 → 用户确认 → 恢复原任务上下文"的真中断恢复（需在 ask_v2 需求闸门处接入 WAITING_CLARIFICATION + resume 机制）。
2. **前端断线重连自动恢复**：后端 `/api/task/{id}` 已就绪，前端尚未接入"页面刷新后自动拉取未完成任务状态"。
3. **多选（multiple）后端 apply_answer**：前端已支持 `multiple` 标志渲染，后端 `apply_answer` 仍按单选处理（指标澄清本质是单选，多选留作扩展）。
4. **增量修改（modifyQuestion）仍走同步 `/api/ask`**：未切流式（独立路径，不影响）。

## 七、性能与兼容性风险

- **向后兼容**：`agent.ask` 新增参数默认 `None`，eval/CLI/评测直调签名不变；旧 `/api/ask`、`/api/clarify/*` 端点原样保留，前端已切到流式端点，但旧接口仍可用。
- **线程安全**：事件经 `loop.call_soon_threadsafe` 桥接；任务注册表用 `threading.Lock`；索引在事件循环主线程构建（与既有 `get_index()` 一致）。
- **断线**：客户端断开会触发 `request_cancel`，工作线程在执行 SQL 前停下，避免孤立 SQL 继续执行（防重复执行）。
- **资源**：任务注册表内存态、上限 200 条自动回收；心跳 15s 防代理超时。
- **缓存**：命中 `result_cache` 时秒回整段结果（不发阶段事件），与同步路径语义一致。
