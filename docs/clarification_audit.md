# 知数AI 多轮澄清交互（Query Clarification）审计与设计

> 时间：2026-10-29　方法：逐文件核对行号与真实数据库，不以文档推测实现。
> 状态：审查完成 → 直接进入实现（见 `clarification.py` / `test_clarification.py` / 后端接线 / 前端卡片）。

## 一、当前问数完整调用链（实测）

```
server.api_ask (/api/ask)
  → get_index()                      # rag.build_index()，首次构建并缓存
  → result_cache.get(key)            # 整段结果缓存（本轮已加）
  → agent.ask(index, question, user)
      → ask_v2(index, question, user)
          0. sidx = enrich_index(index)              # 实体/关系/文档向量 + 治理过滤
          ①. query_understanding.understand(q, sidx) # 意图/指标/实体/时间/过滤/歧义（LLM）
          ②. semantic_layer.retrieve_semantic(sidx, q, understanding)  # P0~P4 分层检索
          ①.5 rag.match_disabled_metric → 未开放指标直接拒绝
          ③. semantic_layer.resolve(ctx)             # 冲突解决 + bindings
          ③.5~⑦ 字典/硬约束/经验 注入 → _build_system_prompt
          ④. LLM 生成 SQL（≤MAX_RETRY 轮自愈）
          ⑤. _pre_execute_check(sql,...)             # 唯一执行前闸门（hard/soft 分离）
              ├ guard.sql_guard（只读）+ check_tables_ex（表白名单）
              ├ sql_validator.validate_sql_ex（五维）
              └ permission（字段级 DENY/MASK + 行级改写）
          ⑥. _exec_sql(final_sql)                    # 唯一执行出口（拒答不执行）
          ⑦. 结果校验 + NL 回答 + 脱敏
  → _build_ask_payload → add_query_record → 经验使用日志 → 快照 → 权限审计
```

**关键结论**：澄清环节应插在 `① Query Understanding` 之后、`② retrieve_semantic` 之前——
此刻已经拿到结构化意图（指标/实体/时间/过滤/歧义），可以决定「直接执行 / 需要澄清 / 知识不足」。

## 二、已有能力盘点

| 能力 | 现状 | 落点 |
|---|---|---|
| 意图结构化 | 已有：`query_understanding.understand()` 返回 intent/metrics/entities/dimensions/time_range/filters/ambiguity | `query_understanding.py` |
| 权威指标 | 已有：`metrics` 表 52 条，字段 `definition/metric_type/tables/conflict_key/required_predicates/forbidden_predicates` | `config_store.get_metrics()` |
| 时间解析 | 已有：`calendar_util.parse_time_expressions()` 确定性解析「上月/本周/节日/近N天」等 | `calendar_util.py` |
| 语义映射 | 已有：Semantic Layer P0~P4，`retrieve_semantic` 按指标/实体/表分层召回 | `semantic_layer.py` |
| 知识审核 | 已有：`metrics/entities/kb_docs/...` 均有 `status` 列（approved/candidate/...），`governance.visible_status()` 统一判定 | `governance.py` |
| SQL 校验 | 已有：`guard` + `sql_validator`（五维）+ `permission`（字段/行级） | 各模块 |
| 反馈 | 已有：`feedbacks` 表 + `/api/feedback`，未审核反馈不升级为正式知识 | `config_store.py` |
| 会话 | **薄弱**：`sessions` 表只有 `token→username`，**无任何对话上下文/状态** | `config_store.py` |

## 三、本次新增需改动的最小文件

1. **新增 `clarification.py`**：澄清决策路由、候选汇总、问题生成、回答合并、轮次/状态机、时间区间计算（复用 calendar_util）。
2. **`config_store.py`**：新增 `clarify_sessions` 表 + `clarify_save/load/delete`（会话状态持久化，复用 `_connect/_now/_json`）。
3. **`agent.py`**：`ask/ask_v2` 增加 `understanding_override` 参数（已确认意图注入，跳过 LLM 意图识别），默认 None，行为不变。
4. **`server.py`**：新增 `/api/clarify/*` 接口 + 抽取 `_record_ask` 记录助手（复用取数落库/经验日志/快照/权限审计），`_required_role` 放行 `/api/clarify` 为 user 级。
5. **`frontend/static/js/ask.js`**：澄清卡片 UI（候选/其他/取消/自然语言）。

**不新建问数 Agent**、**不重写 RAG/校验/执行**、**不绕过 SQL Guard**。

## 四、澄清决策路由（设计）

对已解析意图里的「指标」做**可信候选消歧**，候选只来自 `status ∈ {approved, 无status}` 且 `enabled` 的指标：

```
resolve_metric_candidates(question, metrics)  —— 只认两条硬证据，不做概念级模糊匹配
  ① 精确命中（指标名整体出现在问题里）→ exact，锁死口径
  ② 关键词命中（指标注册的 keywords 出现在问题里）→ keyword

  ★ 为什么砍掉「概念命中」（共享「单量/金额/率」词根）？
    实测「查上个月爽约赔付金额」会把代驾订单金额、大板订单金额等全当候选，
    把「用户在问一个不存在的具体指标」误判成「多口径需澄清」—— 臆造歧义，违反「不编造」铁律。

decide(...)
  A 直接执行：问题点名指标名 / 候选唯一 → 走现有 agent.ask
  B 发起澄清：≥2 个可信候选且口径实质不同 → build_clarification（一次一个 slot）
  C 合并上下文：answer 里携带的指标/时间/过滤写入 confirmed_slots，其余保留
  D 知识不足：有指标意图但 0 可信候选 → needs_knowledge，明确缺口，不猜
```

澄清轮次上限 `MAX_CLARIFY_ROUNDS=2`；仍无法消歧 → 转「补充定义/提交审核/管理员维护」。

## 五、会话状态（逻辑字段 → 持久化映射）

逻辑字段全部落 `clarify_sessions` 表（SQLite，复用现有 `_connect`）：

`session_id(用户会话)` 复用 `sessions.token`；`query_id` 为新主键；
`original_question / intent / confirmed_slots / pending_clarifications / candidate_evidence /
clarification_round / status / final_sql / execution_result` 存 JSON 列。
关联标识用 `query_id`，多任务切换不串状态；按 `username` 做归属校验（防越权读写）。

## 六、安全与兼容

- 候选**只来自 approved 正式知识**；`candidate`（AI 生成，如「小板完单量 src=ai」）**不进候选**——顺带修掉当前 `enrich_index` 未按 status 过滤 metrics 的既有缺口。
- 用户选择只写 `confirmed_slots`，**不改变指标定义**；定义仍由 `retrieve_semantic` 从权威指标取。
- 用户回答经后端校验（选项必须在候选内；自由文本走 needs_knowledge，不自动当正式口径）。
- 执行仍走 `agent.ask → _pre_execute_check → _exec_sql`，SQL Guard 不绕过。
- `/api/ask` 单轮接口**零改动**，兼容 CLI/eval/旧前端。
- 权限：澄清会话按 username 隔离；最终执行仍走字段级权限闸门。

## 七、测试基线（改动前）

`run_tests.py unit` 18 文件、`config` 4 文件全绿（本轮已实测）。integration 层需 ClickHouse 隧道，本轮不跑。

## 八、实现结果与验收自检（2026-10-29 交付）

### 8.1 实际改动文件清单

| 文件 | 性质 | 内容 |
|---|---|---|
| `clarification.py` | 新增 | 澄清决策路由 / 候选消歧 / 问题生成 / 回答合并 / 轮次状态机 / 时间区间（左闭右开） |
| `config_store.py` | 修改 | 新增 `clarify_sessions` 表 + `clarify_save/load/delete`；新增 `get_metrics_with_governance()` |
| `agent.py` | 修改 | `ask/ask_v2` 增加 `understanding_override`（默认 None，行为不变） |
| `server.py` | 修改 | 新增 `/api/clarify/start`、`/api/clarify/answer`、`GET /api/clarify/{id}`、`POST /api/clarify/{id}/cancel`；抽取 `_record_ask` 复用记录语义 |
| `frontend/static/js/ask.js` | 修改 | 取数改走 `/api/clarify/start`；新增澄清卡片（候选/自由输入/取消/知识缺失） |
| `frontend/index.html` | 修改 | 新增澄清卡片容器 `#clarifyBox` |
| `frontend/static/app.css` | 修改 | 新增澄清卡片样式 |
| `test_clarification.py` | 新增 | 41 项：16 场景 + 时间边界 + 持久化隔离 + 端点行为 + 越权 + `/api/ask` 兼容 |
| `test_gate.py` | 修改 | 同步 `boom` 替身签名为 `ask_v2(index, q, user=None, understanding_override=None)` |
| `run_tests.py` | 修改 | REGISTRY 登记 `test_clarification.py`（config 层） |

### 8.2 测试基线（改动后，实测全绿）

- **unit 层**（18 文件）：全绿，含 `test_gate.py` 126/126。
- **config 层**（5 文件）：全绿——acl 88、governance 47、sql_experience 62、frontend_structure 23、clarification 41。
- integration 层需真实 ClickHouse 隧道，本轮未跑（不据其判定代码缺陷）。

### 8.3 按验收标准逐条自检

| # | 验收标准 | 结果 |
|---|---|---|
| 1 | 能可靠确定就直行，不增加不必要交互 | ✅ `decide`：点名/唯一候选 → `execute` |
| 2 | 存在实质业务歧义先确认，不擅自选口径 | ✅ ≥2 可信且口径不同 → `clarify` |
| 3 | 候选只来自已审核正式知识 | ✅ `_trusted`：`enabled` + `governance.visible_status`；`candidate`(src=ai) 一律不进候选 |
| 4 | 用户选择=确认口径，不改变业务定义 | ✅ 只写 `confirmed_slots`，定义仍取权威指标 |
| 5 | 找不到可信定义→明确知识缺失，不编造 | ✅ `unknown`/`needs_knowledge`，附原因 |
| 6 | 已确认条件跨轮次保留 | ✅ `apply_answer` 只改指定 slot，其余保留 |
| 7 | 不重复建设 / 不重构 / 不降低 SQL 安全 | ✅ 复用 `agent.ask → _pre_execute_check → _exec_sql`，Guard 不绕过 |
| 8 | 指定「上个月订单量」多口径澄清场景 | ✅ 冒烟验证：`metrics=['发单量','完单量']` → clarify；用户点名「订单量」→ 直行 |
| 9 | 时间区间按业务时区，左闭右开 | ✅ `prev_natural_month`：2026-10 → `[2026-09-01, 2026-10-01)` |
| 10 | ≥16 场景 + ≥7 项评估指标 | 机制部分 ✅（16 场景、41 断言）；**最终业务结果准确率暂不可测**，见 8.4 |
| 11 | 会话状态字段完整 | ✅ `clarify_sessions` 含 session_id/query_id/original_question/intent/confirmed_slots/pending_clarifications/candidate_evidence/clarification_round/status/final_sql/execution_result |
| 12 | 前端不决定口径、后端校验候选/会话/回答 | ✅ 前端只传用户回答，后端校验归属/选项，自由文本不自动当正式口径 |
| 13 | 单轮 `/api/ask` 兼容 | ✅ 接口零改动，端点测试覆盖 |

### 8.4 尚未完成 / 需人工介入项（如实标注）

- **最终业务结果准确率**：本方案自动化只验证了「澄清机制」是否按预期路由与保留口径；
  「业务上选对口径、结果数正确」需要**人工标注标准问题 + 权威口径 + 预期结果**作为 ground truth，
  不能用「SQL 执行成功」代替。此项**暂不可测**，未编造数字。
- **澄清决策阈值**：`exact/keyword` 两条硬证据偏保守（宁漏不臆造歧义）。若后续发现该澄清却没澄清，
  应通过补指标 `keywords` / 定义（而非放松匹配规则）来覆盖，保持「不编造」铁律。
- **integration 层**：需连真实 ClickHouse 后复核端到端效果。
