# 知数AI 逻辑自审耗时优化 — 方案设计与实现说明

> 阶段：第二阶段（分层自审机制，已实现）
> 日期：2026-11-03
> 依赖：`docs/optimization/logic-audit-baseline.md`

---

## 1. 设计思路

基线审计的结论是：`_review_sql`（逻辑自审）是**无条件**调用、**无超时/无 max_tokens**、耗时 ~14.5s，且对简单单表查询与复杂多表 JOIN 一视同仁。

因此采用**「风险分级 + 条件触发」**的分层自审，核心是「减少无效推理，而不是删除必要校验」：

```
SQL 生成 → 规则校验（guard + validator + permission，确定性）→ 风险分级
        → LOW：跳过 LLM 自审（规则已覆盖）
        → MEDIUM：轻量 LLM 自审（整体结论 + 风险点，短输出 + 短超时）
        → HIGH：深度 LLM 自审（逐字段诊断，长输出 + 较长超时）
        → 执行（自审始终是「事后诊断」，不阻断、不改 SQL、不重跑）
```

安全与业务校验（只读/语法/表权限/字段权限/指标口径/JOIN 基数/除零等**确定性规则**）**全部保留**，与 LLM 自审解耦——LLM 自审只做规则做不到的「业务直觉判断」。

---

## 2. 风险分级规则（`_sql_risk_level`）

`agent.py` 新增 `_sql_risk_level(sql, ctx, resolved, last_check)`，**只基于 SQL 结构 + 语义上下文 + 校验结果**，不看 SQL 长度：

| 等级 | 判定信号 |
|---|---|
| **HIGH** | 一对多/多对多 JOIN（关系基数提示或校验器 `join_pre_agg` 命中）、CTE、子查询、JOIN+条件聚合/除法、≥3 表 JOIN、校验报 error |
| **MEDIUM** | 任意 JOIN、命中指标口径（`p1_metrics` 非空）、含除法、含条件聚合、校验有 warning |
| **LOW** | 单表、简单过滤/聚合、无口径依赖、无 JOIN（上述都不满足） |

单元验证 10 例全过（简单单表→LOW、口径→MEDIUM、一对多 JOIN/子查询/CTE/校验 error→HIGH 等）。

---

## 3. 自审分层（`_review_sql`）

`_review_sql(question, sql, definitions, risk)` 按等级：

- **LOW** → 直接返回 `None`（跳过，省 ~14s）。
- **MEDIUM** → `build_review_prompt(..., deep=False)`（只出 `overall + needs_fix + risks`），`timeout=10s`。
- **HIGH** → `build_review_prompt(..., deep=True)`（逐字段 `fields`），`timeout=20s`。

### 两个实测踩坑（已修复）

1. **不能用 `max_tokens`**：实测 `deepseek-flash` 端点带 `max_tokens` 会返回**空串**（light 无 max_tokens=300 字，加 max_tokens=350 变 0 字）。因此靠「light 短 prompt + timeout」控时，不设 max_tokens。
2. **必须关 SDK 自动重试**：OpenAI SDK 默认 `max_retries=2`，超时会重试 3 次，把一次 8s 自审放大成 ~24s。已在 `llm.py` 客户端设 `max_retries=0`（自愈循环已自行负责重试）。

---

## 4. 自愈循环上限

- 新增 `config.MAX_AUTO_FIX`（默认 `1`）：生成 SQL 后，LLM 定点修复（校验自愈 + 需求修复合计）**最多 1 次**。
- 修复后**必须重过完整规则校验**（`_pre_execute_check`，含只读/表权限/字段权限），不因自愈跳过任何安全校验。
- 修复用尽仍未达标 → 拒绝执行，并说明原因（复用现有 fail-closed 闸门）。

---

## 5. 代码修改清单

| 文件 | 改动 |
|---|---|
| `agent.py` | 新增 `_sql_risk_level`；`_review_sql` 增加 `risk` 分层 + timeout；`ask_v2` 计算并透传 `risk`，结果回传 `sql_risk`；自愈循环加 `fix_calls` 上限 |
| `llm.py` | `chat()` 增加 `max_tokens` 参数（未用于自审）；客户端设 `max_retries=0` |
| `prompt.py` | `build_review_prompt(..., deep=True)` 增加轻量/深度两档 prompt |
| `config.py` | 新增 `SQL_REVIEW_TIMEOUT_MEDIUM/HIGH`、`MAX_AUTO_FIX` |
| `server.py` | payload 回传 `sql_risk`（可观测） |
| `test_gate_req.py` | 更新 ⑫ 断言：模型调用 3 次 → 2 次（修复上限从 2 次降为 1 次） |

---

## 6. 效果（端到端实测，同一问题「最近一周每天的订单数量」）

| 指标 | 优化前 | 优化后 |
|---|---|---|
| 总耗时 | 43.6s | **19.2s**（MEDIUM） |
| `sql_risk` | —（无） | MEDIUM |
| 自审是否返回 | 无（旧版 14.5s 深审） | 有（轻量，≤10s 有界） |
| 自审是否被重试放大 | 是（8s×3≈24s） | 否（max_retries=0） |

三档自审耗时（函数级实测）：

| 等级 | 耗时 | 说明 |
|---|---|---|
| LOW | **0s** | 跳过，简单查询不再无条件自审 |
| MEDIUM | ~7s | 轻量自审，仍能识别真实问题（实测抓出"年份 2026 误写"） |
| HIGH | ~17s | 深度逐字段诊断（仅少数高风险查询付出此成本） |

---

## 7. 验收对照

- ✅ 所有必要安全/业务校验保留（guard/validator/permission 未动）。
- ✅ 简单查询（LOW）不再无条件完整 LLM 自审。
- ✅ 高风险查询（HIGH）仍深度检查。
- ✅ 自愈循环有明确上限（MAX_AUTO_FIX=1）。
- ✅ 无新增无限循环/重复提交（max_retries=0 消除重试放大）。
- ⏳ 准确率无明显下降（待第四阶段用 Golden SQL/Evaluation 集验证）。

---

## 8. 待办（后续阶段）

- 第三阶段 P1：多路检索并行化、稳定检索缓存、`resolve_prompt`（含经验检索 ~2.7s）优化、按风险选模型。
- 第四阶段：Golden SQL / Evaluation 集前后对比（简单/中等/复杂三类，P50/P95、token、准确率）。
