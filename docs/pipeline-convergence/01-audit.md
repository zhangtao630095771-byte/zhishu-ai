# 双流水线收敛专项 · 阶段一审计报告（01-audit）

> 生成时间：2026-11-02　口径：**以当前实际代码为准**（逐函数核对，不采信架构文档结论）。
> 本报告只审计，不改任何代码。

---

## 一、结论先行（TL;DR）

1. **当前代码里 `ask_legacy` 已经退化为「纯兜底」**：全仓唯一调用点是 `agent.ask()` 的异常降级分支，没有任何生产入口直接调用它。
2. **真实生产几乎不走 Legacy**：`query_records` 实测 `pipeline` 分布 = `v2` 203 条、`(空)` 8 条（加 pipeline 字段前的旧记录）、**`legacy` 0 条**。
3. **两条流水线的安全/校验已高度收敛**：Legacy 已经复用了 `guard`、`check_tables_ex`、`sql_validator.validate_sql_ex`、`permission.evaluate`、`_exec_sql`、`mask_rows` 同一套实现（这是此前整改的成果）。
4. **Legacy 不承载任何 V2 不具备的业务能力**，它是 V2 的「功能子集 + 更弱的检索/无意图理解/无经验/无结果体检/无状态链」。
5. **收敛风险低、条件基本成熟**：核心工作不是「迁移能力」，而是「决策兜底策略 + 清理重复实现 + 回归验证」。

---

## 二、实际调用链（逐函数核对）

### 2.1 入口分流：`POST /api/ask`（`server.py:844`）

```
POST /api/ask
 ├─ body.base_query_id 存在？ ──→ _modify_query()（增量修改）
 │                                └─ agent.ask(..., modify_context=...)   ← 走 V2
 └─ 否则 ──→ _run_start()（澄清/直行决策，do_cache=True）
              ├─ query_understanding.understand()
              ├─ clarification.decide() → ACTION_EXECUTE / ACTION_CLARIFY / unknown
              ├─ EXECUTE：查缓存 → agent.ask(index, question, user)   ← 走 V2
              │            └─ _build_ask_payload + _record_ask + 写 query_context + 写缓存
              └─ CLARIFY / unknown：clarification.new_state + save，【不执行 SQL】
```

澄清确认后的执行（`server.py:1123` `/api/clarify/answer`、`server.py:1190` `/api/clarify/{id}/...`）同样调用 `agent.ask`。

### 2.2 真正的 V2 / Legacy 分流：`agent.ask()`（`agent.py:1303`）

```python
def ask(index, question, user=None, understanding_override=None, modify_context=None):
    try:
        return ask_v2(...)                      # ① 永远先走 V2
    except Exception as e:
        mode = config.LEGACY_FALLBACK_MODE      # 默认 "whitelist"
        if mode == "always" and APP_ENV == "production":
            mode = "whitelist"                  # 生产环境强制收敛
        recoverable = _is_recoverable(e)        # 仅 TimeoutError/ConnectionError/关键词
        allow = (mode == "always") or (mode == "whitelist" and recoverable)
        if allow:
            r = ask_legacy(...)                 # ② 仅此处降级 Legacy
            r["pipeline"]="legacy"; r["degraded"]=True; r["degrade_stage"]="v2"
            return r
        return _result(... fail-closed ...)     # ③ 不降级：fail-closed 如实报错
```

### 2.3 是否有人直接调 Legacy

`grep "ask_legacy"`（排除 `def ask_legacy`、`zhishu-ai-code-bundle/` 旧快照、test）：

- **当前代码：0 处直接调用**。仅 `agent.ask()` 内部降级分支调用。

### 2.4 历史原因（代码注释可确认）

- `agent.py:286` 注释：「原单路 RAG 流水线，保留作兜底」。
- `agent.py:1305~1311` 注释：早期是「V2 异常无脑回退 legacy」，后因「legacy 校验更弱，静默回退=不校验地执行」被改为 fail-closed 白名单降级。
- `docs/pipeline_summary_2026-10-30.md` 与 `docs/stage1-01-架构与执行路径盘点.md` 均确认：legacy 已与 v2 补齐五维校验，仅作为瞬时故障兜底。

### 2.5 配置开关

| 开关 | 默认 | 位置 | 作用 |
|---|---|---|---|
| `LEGACY_FALLBACK_MODE` | `whitelist` | `config.py:199` | `always`(仅 dev)/`whitelist`/其它=fail-closed |
| `APP_ENV` | `production` | `config.py:186` | 生产下强制 `always→whitelist` |
| `MAX_RETRY` | 3 | `config.py:180` | 两条流水线共用 |
| `EXP_ENABLED` 等 | True | `config.py:304` | 经验中心开关（仅 V2 用） |

---

## 三、功能差异矩阵

标注：✅ 已实现；🟡 两者实现但逻辑不同；⬜ 均未实现；— 不适用。

| 功能 | V2（ask_v2） | Legacy（ask_legacy） | 是否重复 | 需统一 | 迁移风险 |
|---|---|---|---|---|---|
| 用户问题处理 | ✅ | ✅ | 否（各自独立入口） | 是 | 低 |
| 意图理解（Query Understanding） | ✅ `query_understanding.understand` | ❌ 无（直接进检索） | 否 | 是 | 低 |
| 澄清机制 | ✅ `clarification` 模块（入口层，独立） | ✅ 复用同一 `clarification`（在 server 层） | 否（已共享） | 否 | — |
| P0-P4 知识检索 | ✅ `semantic_layer.retrieve_semantic`（分层+实体过滤+冲突消解） | 🟡 `rag.retrieve`（单路混合：指标+表） | 部分重复 | 是 | 中（检索质量不同） |
| 指标权限拦截 | ✅ `rag.match_disabled_metric` | ✅ 同一函数 | 是（已共享） | 否 | — |
| 零召回兜底拒绝 | ✅ | ✅ 同逻辑 | 是（已共享） | 否 | — |
| Prompt 组装 | ✅ `_build_system_prompt`（role+时间口径+P0~P4+字典+硬约束+经验） | 🟡 `prompt.build_system_prompt`（role+schema+字典+metric+rules+硬约束） | 部分重复 | 是 | 中（口径不同） |
| 时间/节假日口径 | ✅ `calendar_util.build_time_context` | ❌ 无 | 否 | 是 | 低 |
| SQL 经验检索 | ✅ `sql_experience.retrieve/check_sql` | ❌ 无 | 否 | 是 | 低 |
| SQL 生成 | ✅ `llm.chat`（单次，含精确复用分支） | ✅ `llm.chat`（单次） | 部分重复 | 是 | 低 |
| 需求契约/审核 | ✅ `requirement_review` | ❌ 无 | 否 | 是 | 低 |
| SQL 安全校验（只读/表/字段/行级） | ✅ `_pre_execute_check` | ✅ 内联同序调用（guard→表→validator→permission） | 是（已收敛） | 否 | — |
| 业务校验（五维） | ✅ `sql_validator.validate_sql_ex` | ✅ 同一函数（构造最小 SemanticContext） | 是（已收敛） | 否 | — |
| SQL 自愈 | ✅ 校验+执行两处自愈，重过闸门 | ✅ 校验+执行自愈 | 部分重复 | 是 | 中（Legacy 自愈不重过字段/行级完整闸门） |
| 权限控制（字段级） | ✅ `permission.evaluate`（user 非空） | ✅ 同一函数 | 是（已收敛） | 否 | — |
| 行级权限改写 | ✅ `permission.apply_row_filters`（改写后重校验） | ❌ 无（Legacy 不做行级改写） | 否 | 是 | 中（能力缺口） |
| SQL 执行 | ✅ `_exec_sql`（唯一出口） | ✅ `_exec_sql`（唯一出口） | 是（已共享） | 否 | — |
| 结果体检（result_sane） | ✅ `result_validator.validate_result` | ❌ 无 | 否 | 是 | 低 |
| 结果脱敏 mask_rows | ✅ | ✅ 同一函数 | 是（已共享） | 否 | — |
| 自然语言回答 | ✅ `nl_answer.answer` | ❌ 无（只返回表格） | 否 | 是 | 低 |
| 逻辑自审 `_review_sql` | ✅ 尾部并行 | ✅ 成功/失败均追加 | 部分重复 | 是 | 低 |
| 状态链（7 态） | ✅ `chain` | ❌ 无（无 status_chain） | 否 | 是 | 低 |
| 审计与日志 | ✅ trace + 落库 | ✅ 落库（无 trace） | 部分重复 | 是 | 低 |
| 异常处理 | ✅ fail-closed + 分类拒绝 | ✅ 重试+拒绝（无 degrade 标记） | 部分重复 | 是 | 低 |
| 缓存 | ✅ `result_cache`（server 层，与流水线无关） | 同（server 层） | 已共享 | 否 | — |
| 反馈与经验沉淀 | ✅ `sql_experience` + `record_requirement_failure` | ❌ 无（Legacy 不写经验） | 否 | 是 | 低 |

### 关键差异（代码位置 + 行为）

1. **检索**：V2 = `semantic_layer.retrieve_semantic`（`semantic_layer.py:257`，P0~P4 分层 + 实体业务线隔离 + `resolve` 冲突消解）；Legacy = `rag.retrieve`（`rag.py:696`，指标→带出表→再检索表 的单路混合）。**Legacy 检索是 V2 的弱化子集**，无实体过滤、无分层、无冲突消解。
2. **Prompt**：V2 = `_build_system_prompt`（`agent.py:611`，权威性排序 P0→P1→P2→P3→P4→字典→硬约束→经验）；Legacy = `prompt.build_system_prompt`（`prompt.py:365`，role+schema+dict+metric+rules+hard）。**Legacy 缺时间口径、实体/关系、经验块**。
3. **自愈**：V2 的自愈（执行失败分支）会「重新过完整闸门（含字段/行级）」（`agent.py:1166`）；Legacy 的字段级校验在自愈循环**之外**（`agent.py:462` 字段级只查一次，执行自愈后直接 `_exec_sql`，未重过字段级/行级）——这是 Legacy 的**安全不对称点**。
4. **行级权限改写**：V2 有（`apply_row_filters` + 改写后重校验）；**Legacy 完全没有行级改写**。

---

## 四、实际使用统计（以真实库为准，不虚构）

| 指标 | 值 | 证据 |
|---|---|---|
| `query_records` 总记录 | 211 | `SELECT COUNT(*)` |
| `pipeline=v2` | **203** | `GROUP BY pipeline` |
| `pipeline=legacy` | **0** | 同上 |
| `pipeline=(空)` | 8 | 加 pipeline 字段前的旧记录 |
| V2/Legacy 占比 | 100% / 0%（有 pipeline 标记的记录） | — |
| Legacy 成功率/耗时/P95 | **无法统计**（0 条记录） | 不虚构 |

> 补充：`query_records` 无 `degraded`/`trace` 落库列，无法从库中统计「V2 降级 Legacy」的触发次数。日志层面的 `degrade_stage/degrade_reason` 只存在于运行内存与响应体，未持久化。**因此「V2 失败率、降级率、Legacy 调用次数」在无日志采集的情况下无法精确统计**，已如实标注。

## 五、审计结论（对应任务书 §3.4 八问）

1. **为什么存在双流水线？** V2 是后加的「企业级语义层流水线」，Legacy 是早期「单路 RAG」原型；出于「不破坏现有能力」的稳妥，V2 上线后 Legacy 保留作兜底。
2. **功能重复程度？** 安全/校验/执行/脱敏/拒答/字典/指标权限 **已 100% 共享同一实现**；重复的是「检索、Prompt 组装、自愈循环、结果处理」这四块（各有一份独立实现，Legacy 是弱化版）。
3. **Legacy 是否仍承担不可替代能力？** **否**。逐一核对，Legacy 没有任何 V2 不具备的业务能力；它反而缺行级权限改写、结果体检、状态链、经验沉淀。
4. **哪些可直接统一？** 意图理解、P0-P4 检索、Prompt、经验检索、需求契约、结果体检、nl 回答、状态链、经验沉淀 —— 全是「V2 已有、Legacy 无」，直接「以 V2 为准」即可。
5. **哪些需先迁移？** 唯一需要「迁移」的是**兜底策略本身**：Legacy 的存在意义是「V2 遇瞬时故障（超时/连接）时的降级」。需决策：是保留一个「轻量只读兜底」还是「直接 fail-closed 不再降级」。
6. **哪些逻辑应废弃？** `ask_legacy`、`rag.retrieve`（仅 `ask_legacy` 与 `check2.py` 用）、`prompt.build_system_prompt`（仅 `ask_legacy` 与 `check2.py` 用）——均可废弃/改造，但需先迁移依赖方。注：`eval_modify_live.py` 里同名 `build_system_prompt` 是**文件内本地函数**（2 参），与 `prompt.build_system_prompt`（6 参）无关。
7. **哪些应保留为公共组件？** `guard`、`sql_validator`、`permission`、`_exec_sql`、`_review_sql`、`llm.chat`、`nl_answer`、`result_validator`、`rag.retrieve_dicts/match_disabled_metric/named_tables` —— 已是公共组件，保持不动。
8. **是否具备迁移条件？** **具备**，且风险低（0 条 Legacy 生产调用）。但必须先做三件事：① 确认降级策略（保底 vs fail-closed）；② 补齐对照测试（阶段四）；③ 补齐 `degraded`/`trace` 落库以便灰度观测「实际走的流程版本」。

---

## 六、风险提示（进入迁移前必须正视）

- **R1 降级语义**：Legacy 是「瞬时故障兜底」。若直接删除且不提供替代，V2 遇 ClickHouse 超时时用户将拿到 `fail-closed` 错误而非重试结果。需确认「fail-closed 不再降级」是可接受的产品行为。
- **R2 安全不对称**：Legacy 自愈不重过字段/行级权限、且无行级改写——这条不对称在「只读兜底」里可接受，但若有人把 Legacy 重新当主流程用则危险。收敛时应彻底关闭而非留下半活路径。
- **R3 检索差异**：Legacy 与 V2 检索结果可能不同，对照测试需覆盖「同一问题两路结果是否一致」，不能只看 SQL 字符串。
- **R4 依赖清理**：`rag.retrieve` / `prompt.build_system_prompt` 还挂着 `check2.py`、`eval_modify_live.py` 两个工具脚本，清理时需一并处理，不能留 dangling 引用。
