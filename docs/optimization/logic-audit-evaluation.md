# 知数AI 逻辑自审耗时优化 — 评估与验收报告

> 阶段：第四阶段（评估与验收）
> 日期：2026-11-03
> 依赖：`logic-audit-baseline.md`、`logic-audit-optimization.md`

---

## 1. 评测方法

由于 `golden_cases` 表当前为空（0 条标准问数案例），无法在真实黄金集上跑「SQL 准确率 / 业务结果准确率」的正式前后对比。因此本报告采用：

1. **结构正确性论证**：逻辑自审是「执行之后、返回之前」的**非阻断诊断**（只诊断、不改 SQL、不重跑、不拦截），因此分层/跳过自审**不可能改变 SQL 文本、执行结果或业务结果**——它只影响耗时与 `review` 字段内容。
2. **回归测试**：覆盖生成/校验/自愈/需求审核/权限/Join 基数的全套测试。
3. **端到端实测**：同一问题优化前后耗时对比。
4. **函数级实测**：三档自审耗时。

---

## 2. 优化前后性能对比

### 2.1 端到端（同一问题「最近一周每天的订单数量」，MEDIUM 风险）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 请求总耗时 | 43.6s | **19.2s** |
| 逻辑自审耗时 | ~14.5s（深审，无超时） | ~10s（轻量，超时有界） |
| 自审是否被 SDK 重试放大 | 是（8s×3≈24s） | 否（max_retries=0） |
| 自然语言回答 | 已改按钮按需（前一专项） | 默认不生成 |

> 注：优化前 43.6s 里，除了深审 14.5s，还有一次「自审超时 + SDK 重试」放大；本次同时修复了 `max_retries`，故改善幅度大于单纯的「深审→轻量」。

### 2.2 三档自审耗时（函数级实测）

| 风险等级 | 优化前 | 优化后 | 说明 |
|---|---|---|---|
| LOW | 14.5s（无条件深审） | **0s（跳过）** | 简单单表查询不再无条件自审 |
| MEDIUM | 14.5s | **~7s（轻量）** | 只出整体结论+风险点，仍能识别真实问题 |
| HIGH | 14.5s | ~17s（深度，超时 20s 有界） | 仅少数高风险查询付出此成本 |

### 2.3 LLM 调用次数（首轮查询）

| 场景 | 优化前 | 优化后 |
|---|---|---|
| LOW 查询 | 生成 1 + 自审 1 = 2 次 | 生成 1 + 自审 0 = **1 次** |
| MEDIUM 查询 | 2 次 | 2 次（但自审变轻量） |
| 自愈修复上限 | 最多 3 次 | 最多 **1 次**（MAX_AUTO_FIX=1） |

---

## 3. 准确率保障（为什么不会下降）

1. **自审不碰 SQL / 结果**：`_review_sql` 只产出 `review` 报告，任何分支（含跳过、超时返回 None）都不影响 `sql`、`execute`、`rows`、`answer`。
2. **安全与业务校验未动**：`guard.sql_guard`、`check_tables_ex`、`sql_validator.validate_sql_ex`、`permission.evaluate`、行级权限、`_pre_execute_check`、`_exec_sql` 全部保留，与 LLM 自审解耦。
3. **唯一可能影响准确率的改动**：自愈修复上限 3→1。这只在「校验失败且 1 次修复后仍不达标」的少数场景下发生，此时**拒绝执行并说明原因**（fail-closed），而非「带着错误结果执行」。相关行为由 `test_gate_req.py` ⑫ 覆盖。
4. **风险分级不误伤高风险**：HIGH 判定覆盖一对多/多对多 JOIN、子查询、CTE、条件聚合、校验 error；这些仍走深度自审，不会被跳过。

---

## 4. 回归测试结果（全部通过）

| 测试 | 结果 |
|---|---|
| test_gate | 121/121 |
| test_gate_req | 72/72（⑫ 断言已随 MAX_AUTO_FIX=1 更新） |
| test_pipeline | 17/17 |
| test_pipeline_convergence | 26 |
| test_modify_query | 49/49 |
| test_prompt_assembly | 23 |
| test_requirement_review | 52 |
| test_guard | 通过 |
| test_join_cardinality | 15/15 |
| test_status_chain | 46 |
| test_golden_cases | 19 |
| test_sql_experience | 62 |
| test_imports | 26/26 |

---

## 5. 验收标准对照

- ✅ 所有必要安全校验保留。
- ✅ 简单查询（LOW）不再无条件完整 LLM 自审。
- ✅ 高风险查询（HIGH）仍深度检查。
- ✅ 自愈循环有明确上限（MAX_AUTO_FIX=1）。
- ✅ 无新增无限循环、重复提交（max_retries=0 消除重试放大）。
- ✅ 准确率无结构性下降（自审非阻断；唯一变化点由测试覆盖）。
- ⚠️ 正式黄金集准确率对比：因 `golden_cases` 为空，待有数据后补充。

---

## 6. 第三阶段 P1 优化项评估（分析结论）

| 项 | 结论 | 建议 |
|---|---|---|
| 并行检索 | `retrieve_semantic` 内 metric/entity/table/doc 四路召回**串行**，且每路都重复 embed 同一 question（`rag._hybrid_retrieve` 内部 embed） | 低价值（单次 embed 仅几十 ms，收益 ~150ms）。可做 question 向量一次计算复用，但风险/收益比不高 |
| 检索缓存 | 经验检索已有进程内缓存（按行数+更新时间失效） | 已具备，无需重复建设 |
| resolve_prompt 2.7s | 主因是 `sql_experience.retrieve`（2 次 query embed + 相似度） | 可把两处 embed 合并；中等价值 |
| 按风险选模型 | 当前仅 `deepseek-flash` 单一模型 | **高价值但需引入第二模型档位**，建议后续评估 |
| Token 打点 | `llm.chat` 丢弃 `usage` | 建议补充（支撑成本/P95 报告），本次未做 |
| SSE 等待自审 | 无 SSE，前端一次性拿结果 | 无此问题 |

### 关键观察：模型本身慢

实测 `deepseek-flash` 生成吞吐约 **40 字/秒**（baseline 往返 ~2.3s，705 字自审 ~14s）。这是自审慢的**根本原因**，非代码问题。若后续接入更高吞吐的模型档位（或确认真实 DeepSeek 官方端点），自审可进一步提速。**建议排查当前 `LLM_BASE_URL=https://api.deepseek.com` 与 `deepseek-flash` 是否为官方/低延迟端点。**

---

## 7. 遗留与建议

1. `golden_cases` 补数据后，跑一次正式的前后准确率对比（简单/中等/复杂三类，P50/P95）。
2. 接入「按风险选模型」：LOW 规则判定、MEDIUM 快速模型、HIGH 深度模型。
3. `llm.chat` 打点 `usage`（token 数）与首 token 时间。
4. 排查模型端点延迟（见 §6 关键观察）。
