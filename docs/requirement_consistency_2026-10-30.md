# 需求一致性审核 · 现状审查与最小改造方案

> 版本：v1（只做审查与设计，未改动任何线上代码）
> 日期：2026-10-30
> 前置：`docs/architecture_review_2026-10-29.md`、`docs/sql_experience_design.md`
> 结论先行：**生成链路不需要动，需要动的是「审核层」——它现在只有确定性规则，没有任何一层在判断"这条 SQL 是否回答了用户这次问的问题"。**

---

## 0. 一句话结论

现有系统已经具备"模型自主生成 + 规则校验 + 执行闸门 + 经验检索"四件套，但**四件套里缺了最关键的一环**：

| 你在问什么 | 系统当前的回答能力 |
|---|---|
| 这条 SQL 能不能安全执行？ | ✅ 有（只读 / 表权限 / 字段权限 / 列存在性） |
| 这条 SQL 口径对不对？ | 🟡 部分（指标 required_predicates / forbidden_predicates / JOIN 基数 / 比率分母） |
| 这条 SQL 回答的是"用户这次问的问题"吗？ | ❌ **没有**（`time_range` / `dimensions` / `filters` 被解析出来后从未被使用） |
| 这条 SQL 落实了用户这次的"修改"吗？ | ❌ **没有**（修改意图只注入 prompt，没有任何事后验证） |
| 失败原因能回喂模型让它自己改吗？ | 🟡 有循环，但只对"校验 error"生效，需求类问题进不来 |

一句话：**系统校验的是"SQL 合不合法"，没有校验"SQL 合不合题意"。**

---

## 1. 现有实现地图（改造前基线）

### 1.1 主链路（`agent.ask_v2`，`agent.py:756-1106`）

```
问题
 ├─ ① understand()            agent.py:798   → intent/metrics/entities/dimensions/time_range/filters/ambiguity
 ├─ ①' modify 合并            agent.py:806   → _merge_understanding_for_modify（展示用，缺项用上轮补）
 ├─ ② retrieve_semantic()     agent.py:839   → P0~P4 分层召回
 ├─ ②.5 零召回拒绝            agent.py:852
 ├─ ③ resolve()               agent.py:859
 ├─ ③.7 经验检索              agent.py:865   → sql_experience.retrieve() 严格适用性过滤
 ├─ ③.8 Prompt 组装           agent.py:874   → _build_system_prompt（唯一位置：role→时间→P0~P4→字典→硬约束→经验）
 ├─ ③.9 modify 段注入         agent.py:882   → _modify_prompt_block（上轮问题/意图/SQL + 3 条硬约束）
 ├─ ④ LLM 生成                agent.py:899   → 唯一例外：EXP_EXACT_REUSE 精确复用
 ├─ ④.5 拒答识别              agent.py:902
 ├─ ⑤ 校验循环（≤3 次）       agent.py:914-953
 │     _pre_execute_check()   agent.py:525   = 只读 + 表白名单 + validate_sql_ex + 字段权限 + 行级改写
 │     失败 → 回灌修复 → continue
 │     耗尽 → 拒绝执行（fail-closed）
 ├─ ⑤.5 失败经验检查          agent.py:963   → check_sql 确定性比对（默认只提示）
 ├─ ⑥ 执行（唯一出口）        agent.py:1006  → _exec_sql，异常自愈也必须重过闸门
 ├─ ⑦ Result Validator        agent.py:1076  → 比率越界/整列 NULL/行数异常/全 0
 ├─ ⑧ NL 回答                 agent.py:1082
 └─ ★ 事后逻辑自审            agent.py:1094  → _review_sql（执行完才跑、只诊断、不改、不拦）
```

### 1.2 各模块职责边界

| 模块 | 关键行 | 职责 | 备注 |
|---|---|---|---|
| `agent.py` | 756 | 编排 | 所有闸门的唯一串联点 |
| `sql_validator.py` | 593 | 生成前 5 维校验 | P0 规则 / P1 指标口径 / P2 JOIN / P3 Schema / Metric 比率 |
| `guard.py` | — | 只读 + 表解析 | 清洗、库名前缀、表级白名单 |
| `permission.py` | — | 字段级 ALLOW/MASK/DENY + 行级改写 | 改写后强制重校验 |
| `sql_experience.py` | 1182 / 1335 / 1414 | 经验检索 / prompt 块 / 生成后确定性检查 | 有严格适用性过滤（`_applicable`，1158） |
| `prompt.py` | 436 | `build_review_prompt` | **事后**逐字段逻辑诊断 |
| `query_understanding.py` | 78 | 查询理解 | 产出 `dimensions/time_range/filters` —— **无人消费** |
| `clarification.py` | 131 | 执行前指标歧义澄清 | 只在"指标选不准"时触发，与 SQL 审核无关 |
| `server.py` | 946 / 866 | 增量修改 / 上下文持久化 | 上下文只喂 prompt，无验证 |

---

## 2. 逐条对照你的 5 项要求

### 一、模型生成阶段

| 要求 | 现状 | 判定 |
|---|---|---|
| 1. 保留模型自主理解 | Prompt 只给知识，不给模板 | ✅ 已满足 |
| 2. 保留模型自主生成 SQL | `llm.chat(system_prompt, question)` 直出 | ✅ 已满足 |
| 3. 连续查询提供历史上下文 | `_modify_prompt_block`（`agent.py:694`）注入上轮问题/意图/SQL + 3 条硬约束；`_merge_understanding_for_modify`（676）补全展示层意图；上下文按用户持久化（`server.py:866`） | 🟡 **部分**：只带"上一轮"，且无"本轮改了什么"的显式解析；`base_sql` 是**行级权限改写后**的 SQL，权限过滤混进了"用户过滤"里 |
| 4. 不强制模板/AST 生成 | 代码里没有任何 SQL 模板或 AST 改写用于生成 | ✅ 已满足 |
| 5. 不限制业务语义/Golden SQL/成功失败经验推理 | 经验块作为 prompt 最后一段（`agent.py:669`），措辞明确"仅作参考、不得覆盖 P0~P2" | ✅ 已满足 |

### 二、审核阶段（新增"需求一致性"）

现状审核 = **纯确定性规则**，覆盖"表/字段/指标口径/JOIN/比率"。

| 审核内容 | 现状 | 差距 |
|---|---|---|
| 意图是否理解正确 | ❌ 无 | 无对比对象：没把 `understanding` 与 `SQL` 做过任何比对 |
| 是否正确继承上轮上下文 | ❌ 无 | `modify_context` 只进 prompt，出参不看 |
| 是否落实本次修改要求 | ❌ 无 | 同上 |
| 替换是否被写成追加 | ❌ 无 | `eval_modify_live` 里这条只靠人工看，无自动闸门 |
| 是否误删原指标/维度/过滤 | ❌ 无 | `_merge_understanding_for_modify` 只合并"展示用理解"，不校验 SQL |
| 表/字段/指标口径 | ✅ 有 | P1/P3（`sql_validator.py:199/539`） |
| JOIN 关系/基数/粒度 | 🟡 部分 | 已登记关系 error、未登记明细表 warning（`_check_p2`）；**粒度**只由 `result_validator` 事后看行数 |
| 命中已知失败经验 | 🟡 部分 | `check_sql`（`sql_experience.py:1414`）三条确定性规则；默认只 warning、不阻断 |
| 执行成功但业务未满足 | ❌ 无 | `_review_sql` 是**执行后**的事后诊断，不进闸门、不改、不重试 |

### 三、经验包检索

| 要求 | 现状 | 差距 |
|---|---|---|
| 优先参考已审核且结果验证通过的 Golden | ✅ `retrieve()` 只取 `review_status=approved AND exec_ok=1 AND trust_level IN (golden_candidate, golden)` | — |
| 参考相似成功查询的结构/规则 | ✅ `build_prompt_block` 带可信度/粒度/适用条件/限制 | — |
| 检索失败经验 | ✅ `failure_type` 非空才入池（去噪） | — |
| 参考已验证的修正经验 | 🟡 `corrected_sql` 仅用于 `check_sql` 的两条启发式 | 修正经验没有进"审核依据" |
| 结果作为推理与审核依据，不机械照搬 | 🟡 生成侧满足；**审核侧完全没有经验输入** | 审核 prompt 里既没有案例也没有失败模式 |
| （隐含）修改场景下的检索质量 | ❌ | 检索用 `question` 原文；"再加完成率"这种 5 字问句，embedding 命不中真正的基线案例 |

### 四、审核不通过的处理

| 要求 | 现状 | 差距 |
|---|---|---|
| 输出失败原因 | ✅ `validation` + `error` | 需求类问题无原因可输出 |
| 反馈给模型 | ✅ `fix_user` 回灌（`agent.py:931`） | 只回灌 validator issues |
| 允许模型自主调整 | ✅ | — |
| 重新审核 | ✅ 循环内重跑 `_pre_execute_check` | — |
| 通过后执行 | ✅ fail-closed（942） | — |
| 多次修正仍无法确认 → 向用户说明歧义 | ❌ | 现在只有"已重试仍未达标，已拒绝执行"，没有"我卡在哪、需要你确认什么" |

### 五、状态边界

现状只有 3 个可区分状态：

```
ok            = 执行返回了结果（把"生成成功/校验通过/执行成功"混成一个）
validation_status = passed/failed/error/degraded/blocked（只到校验）
review        = 事后逐字段诊断（与 ok 无关）
query_records.review_status = none/pending/approved/rejected（人工审核，事后）
```

缺 `requirement_verdict`、缺 `executed`（与 `ok` 分离）、缺"业务已验证/需求已满足"的回填位。

---

## 3. 关键差距（按严重度）

| # | 差距 | 证据 | 后果 |
|---|---|---|---|
| G1 | `understanding.time_range / dimensions / filters` 解析后**从未被校验** | 全仓库 `time_range` 只出现在 `query_understanding`、`clarification`、测试与评测中，`sql_validator` 里 0 处 | 模型漏掉 GROUP BY、把"近 30 天"写成"今年"、丢掉用户指定的过滤 —— 全部静默通过 |
| G2 | 修改意图零验证 | `modify_context` 只在 `_build_system_prompt` 之后作为文本追加（`agent.py:882`） | "换成 101"被写成"101 或 0"（追加）也能过闸门；"去掉分组"顺手删掉过滤也能过 |
| G3 | 逻辑自审在**执行之后** | `agent.py:1094`、`legacy:483` | 发现问题时数字已经给用户了；而且 `needs_fix=true` 不触发任何动作 |
| G4 | 审核不带经验 | `build_review_prompt(question, sql, definitions)` 只有口径文本 | 已知错误模式无法在审核期被"点名比对" |
| G5 | 修改场景经验检索用短问句 | `sql_experience.py:1211` 用 `question` 原文做 embedding | 多轮修改几乎召回不到基线案例，等于"每轮从零参考" |
| G6 | 审核失败无"歧义回退" | `agent.py:942` 只有统一拒绝文案 | 用户不知道该补什么，只能重问一遍 |
| G7 | 状态混装 | `ok` 单一布尔 | 无法回答"通过率/执行成功率/业务正确率"分别是多少，经验回流也分不清该学哪一类 |
| G8 | **评测判定器自己会误判** | `eval_modify_live.py:194` 用 `"app_entily_source" not in s` 判"已修正"，模型在**注释里**写了 `-- app_entily_source → app_entity_source` 就被判 ❌（报告第 ⑧ 条） | 现有 9/10 的成绩里含 1 个假阴性；同类"注释里出现危险词"的坑 `agent._strip_leading_comments` 已经踩过一次 |
| G9 | 失败经验不沉淀需求类错误 | `FAILURE_TYPES` 无 `requirement_mismatch` | 同一类"改错/漏改"会反复发生，经验中心学不到 |

---

## 4. 改造方案（最小改造，不动生成链路）

### 4.1 设计原则落点

| 你的原则 | 代码上的落法 |
|---|---|
| 保留模型自主生成 | **不新增任何 SQL 生成/改写入口**。所有修正仍然走 `llm.chat(system_prompt, fix_user)` |
| 知数AI 只做知识增强 + 审核 + 自检 + 纠错 + 执行管理 | 新增模块只**判定**、只**描述问题**，绝不拼 SQL |
| 不重构 Agent | 只在 `ask_v2` 的既有校验循环里插一个 `if`，其余为新文件 |
| 复用既有机制 | 复用：`_pre_execute_check` 闸门、`_format_issues` 回灌、`MAX_RETRY` 计数、`sql_experience` 检索/落库、`clarification` 的歧义表达、`query_records` 落库 |

### 4.2 改动清单

| 文件 | 类型 | 内容 | 规模 |
|---|---|---|---|
| `requirement_review.py` | **新增** | 需求契约抽取 + 确定性检查器 + LLM 语义审核 + 结果缓存 | ~350 行 |
| `prompt.py` | 改 | 新增 `build_requirement_review_prompt()`、`build_requirement_repair_prompt()` | +90 行 |
| `agent.py` | 改 | 在 `ask_v2` 校验循环里插入 `req_verdict` 判定与回灌分支；出参加 `status_chain` | +60 行 |
| `sql_experience.py` | 改 | `retrieve()` 加 `context_question`；`build_prompt_block` 抽取 `audit_digest()` 给审核用；新增 `record_requirement_failure()` | +80 行 |
| `config.py` | 改 | `REQ_REVIEW_*` 开关组 | +12 行 |
| `config_store.py` | 改 | `query_records` 加 `req_verdict` / `req_findings` 两列（沿用 `PRAGMA table_info` 迁移惯例） | +40 行 |
| `server.py` | 改 | 响应体透出 `status_chain` / `req_review` / `needs_clarification` | +15 行 |
| `frontend` | 改 | 四个状态徽章 + 审核结论条 | 小 |
| 生成链路（`_build_system_prompt` / `_modify_prompt_block` / `llm.chat` 调用点） | **不动** | — | 0 |
| `sql_validator.py` / `guard.py` / `permission.py` | **不动** | 需求审核是**并列的第六维**，不是替换 | 0 |

### 4.3 需求契约（Requirement Contract）

不是新的事实源，只是把已有的 `understanding` + `modify_context` **结构化拼一次**：

```python
{
  "question": "再看只看 101 的",          # 本轮原话
  "intent": "trend",
  "metrics": ["注册客户数"],              # 必须在输出列出现
  "dimensions": ["月份"],                 # 必须在 GROUP BY 出现
  "time_range": "2025-10-01~2026-11-01",  # 边界应落在 SQL 里
  "filters": ["user_type = 1"],
  "operation": "replace",                 # none|add|remove|replace（由本轮问句判定）
  "must_remove": ["app_entity_source = 0"],   # 本轮明确要去掉的
  "must_keep": {                              # 上轮有、本轮没提 → 必须保留
      "metrics": ["注册客户数"],
      "dimensions": ["应用主体"],
      "filters": ["user_type = 1"]
  },
  "replace_pairs": [                          # 替换语义：(旧, 新)
      {"field": "app_entity_source", "old": "0", "new": "101"}
  ],
  "unknown": ["..."],                          # 问句里没解析出来的部分（进 LLM 审核）
  "source": "modify|fresh"                     # 首轮没有 must_keep
}
```

`operation` 判定**不调模型**，用动词表 + `modify_context` 是否为空兜底（`去掉|不要|删掉|去掉分组` → remove；`换成|改为|改成|只看` → replace；`加上|再加|增加|补充` → add；否则 none）。判错也无害 —— 它只影响"哪条规则被启用"，最终仍由审核结论决定。

### 4.4 确定性检查器（零 LLM 成本，先跑）

`requirement_review.check_contract(sql, contract, ctx)` → `issues[]`（与 `sql_validator` 同构：`{severity, tier:"REQ", rule, message, suggestion}`）

| 规则 | 判据 | 级别 |
|---|---|---|
| `req_metric_missing` | 契约 `metrics` 任一在 `SELECT` 输出别名里找不到（别名包含指标名即可，容忍"注册客户数(人)"） | error |
| `req_dim_missing` | `must_keep.dimensions` 在 `GROUP BY` 中消失，且本轮未要求删 | error |
| `req_filter_missing` | `must_keep.filters` 的 `字段=值` 在 WHERE 中消失 | error |
| `req_replace_appended` | `replace_pairs` 的 `old` 与 `new` **同时**出现在 WHERE | **error**（最高频真实错误） |
| `req_replace_not_applied` | `replace_pairs` 的 `new` 既不在 WHERE、也没在正文任何位置出现（= 把这条过滤整体删了） | error（P2 新增，见 C.3） |
| `req_dim_not_removed` | 本轮要求删某维度，但 `GROUP BY` 仍在 | warning（可能是用户本意"保留分组"） |
| `req_filter_not_removed` | 本轮要求删的过滤仍在 WHERE（如"去掉应用主体等于 0"却还留着） | warning（分不清"模型没删"还是"权限层重新注入"） |
| `req_time_missing` | `time_range` 的两个边界字面量未出现在 SQL | warning（时区/函数写法可能不同形） |
| `req_group_absent` | `dimensions` 非空但 SQL 无 `GROUP BY` | error |
| `req_select_star` | 用了 `SELECT *` 但有契约指标 | warning（无法确认指标是否落实） |

实现要点：
- 一律**先剥注释**再判（`requirement_review.strip_comments`，字符串字面量内的不动），否则重犯 G8 的坑。
- 解析失败（sqlglot 抛错）→ 返回 `degraded=True`，**不报 error**，交给既有闸门兜底，避免"审核器自己崩了导致全量拒答"。
- 只要 `contract.source == "fresh"` 且 `dimensions/filters/time_range` 全空 → 契约视为"无约束"，直接 `pass`，杜绝首轮误拦。

### 4.5 LLM 语义审核层（只在需要时调）

`requirement_review.review_semantic(question, sql, contract, exp_digest, validator_issues)`
- 模型：复用 `llm.chat("", prompt)`，`timeout=8`
- 输出（严格 JSON，禁止输出 SQL）：

```json
{
  "verdict": "pass|fail|unsure",
  "findings": [
    {"code": "intent_misread|context_lost|change_not_applied|replace_as_append|"
             "dropped_metric|dropped_filter|grain_wrong|join_cardinality|"
             "metric_caliber|known_failure",
     "severity": "error|warning",
     "what": "一句话说明问题",
     "evidence": "SQL 中的具体位置/片段",
     "fix": "模型该怎么改（描述，不是 SQL）"}
  ],
  "assumptions": ["我为了通过审核所做的假设"]
}
```

**触发条件（成本闸门）**：

| 场景 | 确定性层 | LLM 层 |
|---|---|---|
| 首轮、无 ambiguity、无修改 | ✅ | 默认关（`REQ_REVIEW_MODE=modify_only`），评测后再议 |
| 有 `modify_context` | ✅ | ✅ 必开 |
| 问句命中修改动词 | ✅ | ✅ 必开 |
| `understanding.ambiguity` 非空 | ✅ | ✅ 必开 |
| 经验 `check_sql` 有 error 级命中 | ✅ | ✅ 必开 |

**审核失败的处置（落在现有循环里）**：

```
for attempt in 1..MAX_RETRY:
    chk = _pre_execute_check(...)              # 既有：安全 + 口径 + 权限（不变，永远先跑）
    if chk.hard: return 拒绝                    # 安全类不参与需求协商
    if not chk.ok: 回灌修复; continue           # 既有分支

    req = requirement_review.review(...)        # ★ 新增：只判定
    if req.verdict == "fail":
        if attempt >= MAX_RETRY:
            return 拒绝（带 req 原因 + 校验详情）  # 不执行
        回灌 build_requirement_repair_prompt(req.findings); continue
    break

if req.verdict == "unsure" and attempt 耗尽:
    return needs_clarification（把 findings.what 转成 1~2 个待确认问题）
```

三条硬约束（写进代码注释 + 测试守住）：
1. **需求审核永远在安全闸门之后**，绝不因"模型说这是用户要的"而放行越权/写操作。
2. **需求审核只 reject，不 mutate**。任何修正必须由模型重新生成、再走 `_pre_execute_check`。
3. **LLM 审核超时/解析失败 → `verdict="skipped"`**，不阻断执行，但状态链里必须如实标 `skipped`（≠ `pass`），否则等于"审核器崩了悄悄当通过"——这是本项目 `sql_validator` 明确修过的老问题。

### 4.6 状态链（响应体 + 落库）

```python
result["status_chain"] = {
  "generated":           True,      # 模型产出了可执行语句
  "gate_passed":         True,      # 只读 + 表权限 + 五维 + 字段权限
  "requirement_verdict": "pass",    # pass|fail|unsure|skipped（"跑过"≠"通过"）
  "executed":            True,      # 实际执行（与 ok 分离）
  "exec_ok":             True,      # 拿到了结果集
  "result_sane":         True,      # result_validator 无 warning
  "business_verified":   None,      # 事后：query_records.review_status=approved → 回填 True
  "requirement_fulfilled": None,    # 事后：用户反馈"结果对" → 回填 True
}
```

- `ok` 语义**保持不变**（= `exec_ok`），保证前端/缓存/评测不破。
- `status_chain` 是新字段，前端加一行四徽章：`已生成 · 已校验 · 需求已核 · 已执行`，`business_verified` 显示"待人工确认"。
- 落库：`query_records` 增 `req_verdict TEXT`、`req_findings TEXT(JSON)`；人工审核通过时（`set_review_status`）顺带回填 `business_verified`，让"业务正确"与"需求完成"有据可查。

### 4.7 经验中心增强（4 处小改）

1. **检索用上下文**：`retrieve(..., context_question=None)`，embedding 文本改为 `question + " " + (base_question or "")`。仅影响相似度，不影响 `norm_question` 精确复用判定（G5）。
2. **审核依据**：新增 `ExperienceMatch.audit_digest()`，输出"2 条 golden 的关键结构 + 1~2 条已验证修正 + 命中的失败模式"，作为 LLM 审核输入的独立段，措辞仍是"仅作对照，不作为答案"。
3. **失败回流**：需求审核 `fail` 且最终未执行（或用户反馈错）时，`upsert_case(kind="failure", failure_type="requirement_mismatch", failure_symptom=findings 摘要, source_type="req_review")`。注意：只进 failure，**绝不自动进 golden**（沿用 `sql_experience_design.md` 的非目标）。
4. **修正经验**：`attach_correction()` 已有的"修正后 SQL"在审核摘要里单独列出，标注"历史同题的正确写法（参考，不照搬）"。

### 4.8 歧义回退（G6）

审核连续失败且 `verdict=unsure` 时返回：

```json
{
  "ok": false,
  "needs_clarification": {
    "reason": "无法确认本次修改是否要保留应用主体过滤",
    "questions": [
      "「只看 101」是要把原来的 0 替换掉，还是在 0 的基础上再加 101？",
      "上轮的按月份分组还需要保留吗？"
    ]
  }
}
```

问题文本由 LLM 审核的 `findings[].what` + 契约里 `unknown[]` 生成，**不新建澄清会话**（避免与 `clarification.py` 的状态机打架）。P2 再考虑把它接到 `/api/clarify` 走完整两轮澄清。

### 4.9 开关与回滚

```ini
REQ_REVIEW_ENABLED=1          # 总开关（0 = 完全回到改造前行为）
REQ_REVIEW_MODE=modify_only   # always | modify_only | off
REQ_REVIEW_LLM=1              # 是否调用语义层（0 = 只跑确定性规则）
REQ_REVIEW_BLOCK=1            # 0 = 只告警不阻断（灰度期用）
REQ_REVIEW_TIMEOUT=8
EXP_MODIFY_CTX=1              # 修改场景检索带上下文问句
```

灰度顺序：`REQ_REVIEW_BLOCK=0` 观察 1 周 → 看 `req_verdict=fail` 的样本准确率 → 开 `BLOCK=1`。

---

## 5. 测试用例

### 5.1 确定性单测（新增 `test_requirement_review.py`，纯逻辑、不联网）

| # | 用例 | 输入 | 期望 |
|---|---|---|---|
| 1 | 指标被删 | 上轮 `注册客户数`，本轮 SQL 只有 `完成率` | `req_metric_missing` error |
| 2 | 维度被误删 | `must_keep.dimensions=["月份"]`，SQL 无 GROUP BY | `req_group_absent` + `req_dim_missing` |
| 3 | **替换写成追加** | `replace 0→101`，SQL 含 `=0 or =101` | `req_replace_appended` error |
| 4 | 过滤丢失 | `must_keep.filters=["user_type=1"]`，SQL 无 `user_type` | `req_filter_missing` error |
| 5 | 合法修改（对照组） | "去掉应用主体分组" → SQL 保留 `app_entity_source=0` 过滤、只按月份分组 | **0 error**（防误拦，必须存在） |
| 6 | 首轮无契约 | 无 `modify_context`，`dimensions/filters/time_range` 全空 | `verdict=pass`，0 issue |
| 7 | 注释里出现关键字 | SQL 注释含 `-- 原来 app_entity_source=0`，正文已替换 | 不报 `req_replace_appended`（剥注释后判定，**G8 回归**） |
| 8 | SQL 无法解析 | 非法 SQL | `degraded=True`、0 error（交既有闸门） |
| 9 | 时间窗缺失 | 契约 `2025-10-01~2026-11-01`，SQL 只有上界 | `req_time_missing` warning |
| 10 | 动词识别 | `只看|换成|不要|去掉|再加上` 五种问句 | `operation` 分别为 replace/replace/remove/remove/add |

### 5.2 集成闸门测试（新增 `test_gate_req.py`，复用 `test_gate._stubs` 替身风格）

| # | 场景 | 断言 |
|---|---|---|
| 11 | 审核 fail → 回灌 → 修好 → 执行 | `llm` 调用 ≥2、`exec`=1、`ok=True`、`req_verdict=pass` |
| 12 | 审核 fail 3 次 → 拒绝 | `exec=0`、`ok=False`、`status_chain.executed=False`、`error` 含具体原因 |
| 13 | 审核 LLM 超时 | `exec=1`（不阻断）、`req_verdict="skipped"`、`status_chain` 如实标记 |
| 14 | 审核 fail 但 SQL 越权 | 走 `hard` 分支先拒，`exec=0`，**不进入需求协商** |
| 15 | 审核不通过时失败结果不带数据行 | `rows=[]`（沿用既有不变量） |
| 16 | `ok=True` ⇒ `requirement_verdict ∈ {pass, skipped}` | 断言不存在"审核 fail 却执行成功" |
| 17 | 自愈后的 SQL 必须重跑需求审核 | 审核调用次数 = 修复次数 + 1 |
| 18 | 开关全关 | 与改造前完全等价（`status_chain` 缺失或 `requirements=off`） |

### 5.3 评测（真实模型，扩 `eval_modify_live.py`）

现有 10 个修改场景全部加两列断言：`must_keep`（人工写的"必须仍在 SQL 里"片段）与 `must_remove`（必须消失的片段）。判定器统一改为**先剥注释**（修 G8）。

新增 5 个"需求一致性"专杀场景：

| # | 场景 | 期望 |
|---|---|---|
| E1 | "只看 101"（原 `=0`） | 含 `=101`、不含 `=0` |
| E2 | "去掉应用主体分组" | 无 `GROUP BY app_entity_source`、**仍含** `app_entity_source=0` |
| E3 | "再加上完成率" | 同时含原指标与完成率 |
| E4 | "不要看代驾了，只看大板" | 表/口径切换，且不留代驾残留过滤 |
| E5 | "上面那个查询，时间改到上个月" | 时间两界都变，指标/维度/过滤全保留（指代消解 + 继承） |

指标：`req_pass_rate`（确定性层可直接算）、`false_block_rate`（合法 SQL 被拦比例，必须 < 5%）。

> **实施后补充（见 C.2）**：实际做法比本节多三件事 ——
> ① 断言分三类（`must_keep` / `must_remove` / `checks`）而不是一个布尔值；
> ② 每场景加"本轮理解"夹具，使审核器拿到与真实流水线同构的输入；
> ③ `--dry-run` 内置"审核器自检"：7 个基准 SQL 明显答非所问的场景必须被判 fail，
> 防止审核器悄悄腐化成"永远 pass"。
> 另：E5 的判定不能写死具体日期（"上个月"随当天漂移），只守"两界都在 + 旧边界已消失"。

### 5.4 回归不变量（加到 `test_gate.py`）

- `test_no_bare_exec_anywhere` 已守住唯一执行出口 → 新模块**不得** import `db`。
- 新增静态检查：`requirement_review.py` 中不得出现 `UPDATE/DELETE` 类 SQL 拼接、不得调用 `execute_sql`。
- `_pre_execute_check` 的调用顺序不可变：新增需求审核必须在其**之后**（静态检查：`agent.py` 中 `_pre_execute_check(` 的行号 < `requirement_review.review(` 的行号）。

---

## 6. 实施排期

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0（1~2 天）** | `requirement_review.py` 确定性层 + 契约抽取 + `config` 开关 + 单测 1~10 | 单测全绿；`BLOCK=0` 灰度，线上 0 影响 |
| **P1（2~3 天）** | 接入 `ask_v2` 循环（fail→回灌→重审）+ `status_chain` + 响应体/落库 + 单测 11~18 + 评测 E1/E2/E3 | `req_pass_rate` 有基线；`false_block_rate` < 5%；开 `BLOCK=1` |
| **P2（2 天）** | LLM 语义层 + 经验 `audit_digest` + 失败回流 + 歧义回退 + 评测 E4/E5 | 多轮修改场景 pass 率提升；`requirement_mismatch` 有沉淀 |
| **P3（可选）** | 前端四徽章 + 人工审核回填 `business_verified` | ✅ 已完成（附录 D）：状态链在页面可见，业务已核由审核状态派生 |

---

## 7. 明确不做的边界

| 不做 | 原因 |
|---|---|
| 用 AST 拼/改 SQL 来"修正"需求偏差 | 违反"保留模型自主生成"；且二次改写的正确性无从验证 |
| 把需求审核做成第二个 `sql_validator` 的硬编码规则集 | 语义类问题（指代、口径理解）无法穷举，必须交给模型判 |
| 重写 `_build_system_prompt` / `_modify_prompt_block` | 现有分层顺序有测试守着，动它等于重开一轮"prompt 静默失效"的风险 |
| 需求审核失败就自动降级 legacy | legacy 校验更弱，等于用"更不审核"解决"审核不过" |
| 把 `ok` 改成枚举 | 破坏前端/缓存/评测；用并列的 `status_chain` 承载新语义 |
| 自动把需求审核通过的案例升为 golden | 沿用 `sql_experience_design.md` 非目标：golden 必须人工/验证后升 |

---

## 附录 A · P0 实施记录（2026-10-30 已完成）

### A.1 已交付

| 文件 | 状态 | 内容 |
|---|---|---|
| `requirement_review.py` | 新增（约 470 行） | 契约抽取 + 8 条确定性规则 + `review()` / `enabled_for()` / `summary_line()` |
| `config.py` | 改（+38 行） | `REQ_REVIEW_ENABLED/MODE/LLM/BLOCK/TIMEOUT`、`EXP_MODIFY_CTX` |
| `test_requirement_review.py` | 新增 | 11 组、40 个断言，全绿 |
| `.env.example` | 改 | 新开关说明（含"灰度期 BLOCK 必须为 0"的告警） |
| `test_gate.py` | 改（2 行） | 修测试桩签名脱节（见 A.3） |

**未接线**：`agent.py` 一行未改 —— 这是 P0 的刻意选择。
`REQ_REVIEW_BLOCK=0` + 不接线 = 对线上零影响，先拿单测与影子数据验证规则准确率。

### A.2 实现要点（与设计稿的差异）

| 设计稿 | 实现 | 原因 |
|---|---|---|
| 文档写"必删维度 = 问句里点名的" | 增加 `_RE_ONLY_DIM` 反向处理 | "只按月份统计"里被删的是**没被点名**的应用主体。按原逻辑会把月份当删除项，并把正确 SQL 判成"应用主体维度丢失"——这是单测 ④ 抓出来的真实误判 |
| 文档写"维度缺失"单条规则 | 拆成 `req_dim_missing`（列消失）+ `req_group_absent`（无 GROUP BY），二者互斥上报 | 同一问题报两条只是噪音，回灌给模型时更难读。单测 ②-a/②-b 分别守住 |
| 文档写"时间窗缺失 = warning" | 保留 warning，且仅当 `time_range` 能解析出具体日期才判 | 相对时间（"近30天"）没有确定字面量，硬判必然误报 |
| 未提 | `_drop_before()`：删除动词只往前看 6 字 | 区分"去掉应用主体"（删后面那个）与"只按月份"（删其余）。窗口太大（如 15 字）会把"不要按月份，改成按周"里的"按"也算进来 |

### A.3 顺带修掉的既有缺陷

`test_gate.py` 的 `boom` 测试桩签名缺 `modify_context`，而 `agent.ask()` 早已传该参数
（增量修改功能上线时未同步测试桩）。后果：桩抛的是 `TypeError` 而不是预期的
`TimeoutError`，被 fail-closed 判成"不可恢复"→ ③ 回退用例长期假红。
本次补齐签名后 `test_gate.py` 恢复 **126/126**。

> 这与设计稿 G8 是同一类问题：**测试桩/判定器与实现脱节，比被测代码更容易出错**。

### A.4 回归结果（2026-10-30）

| 套件 | 结果 |
|---|---|
| `test_requirement_review.py` | 40/40 ✅（新增） |
| `test_gate.py` | 126/126 ✅（修复前 123/126） |
| `test_modify_query.py` | 49/49 ✅ |
| `test_sql_experience.py` | 62/62 ✅ |
| `test_prompt_constraints.py` | 52/52 ✅ |
| `test_clarification.py` | 41/41 ✅ |
| `test_permission.py` | 60/60 ✅ |

### A.5 P1 待办（未开始）

1. `agent.ask_v2` 校验循环插入 `requirement_review.review()`（位置：`_pre_execute_check` 之后）；
2. `fail` → `prompt.build_requirement_repair_prompt()` 回灌 → 重过闸门 → 重审；
3. `status_chain` 七态分离 + `server._build_ask_payload` 透出；
4. `query_records` 增 `req_verdict` / `req_findings` 两列；
5. 集成测试 11~18（`test_gate_req.py`）。

---

## 附录 B · P1 实施记录（2026-10-30 已完成）

### B.1 接线方式（`agent.py` 唯一改动点）

在 `ask_v2` 既有校验循环内部插入，**不动循环结构**：

```
for attempt in 1..MAX_RETRY:
    chk = _pre_execute_check(...)        # 既有，安全闸门永远先跑
    last_check = chk
    if chk["ok"]:                        # ★ 只在安全校验通过后才审需求
        chain["gate_passed"] = True
        req = review(final_sql, contract)     # ← 新增
        if req.fail and BLOCK:
            if attempt >= MAX_RETRY: blocked = True; break
            sql = llm.chat(system_prompt, build_requirement_repair_prompt(...))
            continue                     # 修完重过完整闸门 + 重审
        break
    if chk["hard"]: return 拒绝           # 越权/写操作 → 不参与需求协商
    ...既有自愈...
```

三条不变量（由 `test_gate_req.py` ⑭⑳ 守住）：
1. 需求审核**永不早于**安全闸门；
2. 需求审核**只 reject，不 mutate**（修正一律由模型重生成）；
3. `degraded`（判不了）→ 记 `skipped`，照常执行，**绝不当 pass**。

### B.2 状态链

`ask_v2` 内新增可变字典 `chain`，`_rv()` 在**每个出口**取一份 `dict(chain)` 快照：

| 字段 | 含义 | 何时置位 |
|---|---|---|
| `generated` | 模型产出了可执行语句 | 通过拒答判定后 |
| `gate_passed` | 只读+表权+五维+字段权限全过 | `chk["ok"]` |
| `requirement_verdict` | pass/fail/unsure/skipped | 需求审核后 |
| `executed` | 真的调了执行 | `_exec_sql` 前 |
| `exec_ok` | 拿到结果集 | `_exec_sql` 返回 |
| `result_sane` | 结果体检无疑点 | `result_validator` 后 |
| `business_verified` | 人工审核通过（事后回填） | 预留 |
| `requirement_fulfilled` | 用户确认结果对（事后回填） | 预留 |
| `requirement_attempts` | 需求修复次数 | 每次回灌 |

`ok` 语义**未改**（= `exec_ok`），前端/缓存/评测均不受影响。

### B.3 落库

`query_records` 增两列（`PRAGMA table_info` + `ALTER TABLE` 迁移，老库原地升级）：
`req_verdict TEXT`、`req_findings TEXT(JSON)`。
已在真实库**副本**（202 条历史记录）上验证：加列成功、历史记录零丢失、新写入可读回。

### B.4 顺带修掉的两个真实缺陷

1. **替换判定的自相矛盾**（严重）：`build_contract` 把"只看 101"里的旧过滤
   `app_entity_source = 0` 同时放进了 `must_keep.filters` 和 `replace_pairs`。
   结果**正确替换的 SQL 反而被 `req_filter_missing` 判死**，模型怎么改都通不过。
   修法：替换对生成后，把对应旧过滤从 `must_keep.filters` 剔除。单测 ③ 已加
   `errors == 0` 的强断言（原来只断言"没报追加"，漏掉了这条）。
   > 这个缺陷是纯函数单测漏掉、由 P1 的端到端场景（⑪）暴露的 ——
   > 说明"规则单测通过"≠"规则组合起来不打架"。

2. **修改场景经验检索召不回**：`retrieve()` 新增 `context_question`，
   检索文本改为"本轮问题 + 上一轮问题"。只影响向量相似度，
   `norm_question` 精确复用判定仍用本轮原话。

### B.5 回归结果（2026-10-30）

| 套件 | 结果 |
|---|---|
| `test_gate_req.py` | 44/44 ✅（新增，⑪~⑳） |
| `test_requirement_review.py` | 41/41 ✅ |
| `test_gate.py` | 126/126 ✅ |
| `test_modify_query.py` | 49/49 ✅ |
| `test_sql_experience.py` | 62/62 ✅ |
| `test_prompt_constraints.py` | 52/52 ✅ |
| `test_prompt_assembly.py` | 23/23 ✅ |
| `test_clarification.py` | 41/41 ✅ |
| `test_permission.py` | 60/60 ✅ |

### B.6 上线前必须知道的风险

| 风险 | 说明 | 处置 |
|---|---|---|
| `req_metric_missing` 误报 | 指标名与输出别名措辞不同（"完单率" vs "完成率"）会被判缺失 | **保持 `REQ_REVIEW_BLOCK=0`**，先看 `req_verdict=fail` 样本；确认误报率 < 5% 再开 |
| 首轮查询不审 | `MODE=modify_only` 下首轮只靠五维校验 | 若 G1（漏 GROUP BY/时间窗）在首轮高发，再改 `always`（每次多 3~6 秒 + 一次 LLM 成本） |
| 连续多轮 | 契约只取**上一轮**，第 4 轮之后早期条件不再被约束 | 后续可把 `must_keep` 沿链累积（需要 query_context 存 contract） |
| 前端未展示 | `status_chain` / `req_review` 已进响应体，页面还没画 | 属 P3；在画之前，用户看不到"执行成功但需求未满足" |

### B.7 P2 / P3 待办

- P2：LLM 语义层（`build_requirement_review_prompt` + `REQ_REVIEW_LLM`）、
  经验 `audit_digest` 进审核输入、`requirement_mismatch` 失败回流、
  评测 E4/E5（指代消解 + 口径切换）。→ **已完成，见附录 C**
- P3：前端四徽章（已生成 · 已校验 · 需求已核 · 已执行）、人工审核回填
  `business_verified`。

---

## 附录 C · P2 实施记录（2026-10-30 已完成）

### C.1 已交付

| 文件 | 改动 |
|---|---|
| `requirement_review.py` | 语义层 `review_semantic`（pass/fail/unsure/skipped + 缓存）；新增 2 条确定性规则 `req_replace_not_applied`(error)、`req_filter_not_removed`(warning)；契约构造修 2 处自相矛盾 |
| `prompt.py` | `build_requirement_repair_prompt`（回灌修复，5 条硬约束）、`build_requirement_review_prompt`（只裁决不出 SQL，严格 JSON） |
| `sql_experience.py` | `ExperienceMatch.audit_digest()`、`record_requirement_failure()`、`retrieve(context_question=)`、失败类型 `requirement_mismatch` |
| `agent.py` | 审核失败 → 回灌修复 → 重审的循环；`unsure` 走澄清；`status_chain` 全链打点 |
| `test_requirement_review.py` | 41 → **52** 项（新增 ⑫ 替换未落实 / ⑬ 指标替换不自相矛盾 / ⑭ 黑话口径切换） |
| `test_gate_req.py` | **72** 项（⑪~㉖ 集成场景） |
| `eval_modify_live.py` | 10 → **15** 场景；三类断言；剥注释判定器；审核器探针 + 空跑自检 |

### C.2 评测是怎么改的（`eval_modify_live.py`）

1. **修 G8**：所有断言改跑在 `judge_sql()` 上 —— 剥注释 → 压空白 → 等号两侧归一 → 大小写无关。
   老版本用 `"app_entily_source" not in sql`，模型在注释里写一行 `-- 把 app_entily_source 改成 …`
   就判失败。等号归一那一步很关键：`app_entity_source=0` 与 `= 0` 是同一件事，
   断言只认一种形态本身就是评测器不可靠。
2. **三类断言分组**，因为它们回答的是不同问题：
   `must_keep`（漏改/误删）、`must_remove`（替换残留/旧上界没消失）、`checks`（分组键、DISTINCT 等结构）。
   混成一个布尔值，只能知道"错了"，不知道"错在哪一类"。
3. **每个场景加"本轮理解"夹具**（`UNDERSTANDING`，按场景编号索引）。
   真实流水线里 `understanding` 是审核的正式输入；现场调 `query_understanding` 会让
   成本翻倍且结果不可复现，于是手写一份忠实夹具，把"解析正确时审核器表现如何"单独隔离出来量。
4. **审核器探针**：每条 SQL 再跑一遍 `review(..., use_llm=False)`，同一批数据顺带算出
   `req_pass_rate` 与 `false_block_rate`（人工断言全过、审核器却 fail 的比例，上线门槛 < 5%）。
5. **空跑自检**（`SELFTEST_MUST_FAIL`）：`--dry-run` 用基准 SQL 占位，其中有 7 个场景的
   基准 SQL **明显没有执行用户要求的修改**，审核器必须判 fail。
   审核器最容易的腐化方式不是报错，而是悄悄变宽松 —— 变成"永远 pass"后，
   在真实评测里只会表现为"这次模型表现不错"。空跑自检是唯一零成本的守门人。

### C.3 评测反查出的 3 个真实缺陷（都由代码修复，不是调宽断言）

| # | 缺陷 | 症状 | 修法 |
|---|---|---|---|
| 1 | **规则空洞**：替换只做一半就静默通过 | 用户说"只看 101"，模型把 `app_entity_source = 0` 整条删了。旧值消失（R3 不管）、新值从未出现（R4 不管）→ 判定 pass，结果集变成全主体，数字变大还没人发现 | 新增 `req_replace_not_applied`(error)：新值既不在 WHERE、也没在正文任何位置出现 → 报错。"正文里出现过"是留给子查询/HAVING/CTE 实现的兜底，宁可漏判不误拦 |
| 2 | **契约自相矛盾（指标/维度版）** | 与 P1 修过的过滤版完全同源：`must_keep.metrics` 无条件继承上轮指标 → "把注册客户数改成注册司机数"时，**正确** SQL（只有司机数）被 R1 判成"删了原指标"，模型无论怎么改都通不过 | 替换/删除语义下，把"本轮 understanding 里没有、且问句点了名"的旧指标/旧维度从 `must_keep` 剔除 |
| 3 | **黑话替换判不出来** | "不要看代驾了，只看大板" —— 问句里既无字段名也无数字，"大板"是业务黑话。只按问句抠数字必然漏判 → 正确 SQL（只有 `=201`）被 R3 判成"丢了原过滤"（误拦） | 用本轮 `understanding.filters` 做同字段比对反推替换对：同一字段、值变了就是替换。它本来就是"本轮用户要什么"的正式载体，不需要再解释语义 |

顺带补了 `req_filter_not_removed`(warning)：用户明确要求去掉的过滤仍在 WHERE。
为什么不设 error —— 权限层会按行级规则重新注入等值过滤，模型删了、权限层又加回来，
报 error 等于"因为权限生效而拒绝回答"；这种误拦代价高于漏判。

### C.4 评测结果（2026-10-30，真实 DeepSeek）

| 指标 | 结果 |
|---|---|
| 人工断言 | **15/15** 场景通过（含 E1~E5） |
| `req_pass_rate` | 15/15 = **100%** |
| `false_block_rate` | **0/15 = 0%**（门槛 < 5%） |
| 空跑自检 | **7/7**（基准 SQL 答非所问时必须判 fail） |

★ **这两个 100% 不能读成"审核器总能发现问题"**：它只说明"模型答对时审核器不误拦"。
"审核器能发现错误"的证据是空跑自检的 7/7 —— 同一批契约、同一套规则，
面对没做修改的 SQL 全部判 fail。两者合起来才是完整结论：
**不过严（0 误拦）也不过松（7/7 该拦的全拦了）**。

几个值得记下的真实产出：
- E4：模型把 `= 102` 直接改写成 `= 201`，没有 OR 残留 —— 这是"替换不是追加"在真实模型上的确认。
- E5：模型给的是 `create_time >= toStartOfMonth(now() - INTERVAL 1 MONTH)` 且 `< toStartOfMonth(now())`，
  **比写死日期更好**（查询不会随日期失效）。原断言要求日期字面量，把这个更优解判成未通过；已改为只守"两界都在且不是 `<=`"。

### C.5 评测器自己也会错（两次，都修了夹具）

1. ⑦ 原夹具的 `SCHEMA_BLOCK` 没写 `user_id`，模型正确地拒答"表里没有客户唯一标识，无法去重统计"。
   这不是模型错，是**夹具缺字段** → 补 `user_id`。
2. ⑧ 报错纠错沿用正确基准 SQL，而问句说报错字段是 `app_entily_source`，
   模型正确地指出"上一轮 SQL 与报错信息不一致，请确认报错来源"并拒答 → 补 `BASE_SQL_TYPO`
   （上一轮真的拼错了，报错才成立）。

★ 教训：**评测失败先怀疑夹具**。这两次的原始信号都是"模型不听话"，实际都是"题目本身有矛盾"。
按旧判定器，⑧ 那条还会因为注释里出现错误字段名被判失败（G8）——
一个假阴性的夹具，可以一直安静地待上几个月。

### C.6 回归结果（2026-10-30）

`test_gate` 126、`test_modify_query` 49、`test_sql_experience` 62、`test_prompt_constraints` 52、
`test_prompt_assembly` 23、`test_clarification` 41、`test_permission` 60、
`test_requirement_review` 52、`test_gate_req` 72 —— 九套全绿，合计 537 项断言。

### C.7 剩余风险与 P3

| 项 | 状态 |
|---|---|
| `REQ_REVIEW_BLOCK` 仍为 0 | 灰度期只记录不拦截；上线前用线上 `req_verdict` 分布复核 false-positive |
| 日期维度粒度分不清 | `_dim_satisfied` 把"任何日期函数分组键"都算作任何日期维度已落实 → "把月份改成按周"会被 R5 报一条 warning。不影响放行，但噪音存在 |
| `false_block_rate` 只在 15 个场景上测过 | 场景是任务点名的骨干形态，线上长尾（多表 JOIN、CTE、窗口函数）未覆盖 |
| 前端未展示 | 属 P3：四徽章（已生成 · 已校验 · 需求已核 · 已执行）+ 人工审核回填 `business_verified` → **已完成，见附录 D** |

---

## 附录 D · P3 实施记录（2026-10-30 已完成）

### D.1 交付

| 文件 | 改动 |
|---|---|
| `frontend/index.html` | 结果区顶部新增 `#chainBar`（四徽章）与 `#reqClarifyBox`（需求澄清） |
| `frontend/static/js/ask.js` | `renderStatusChain()`（五态渲染）、`renderReqClarify()`、`refreshBizChip()` |
| `frontend/static/js/history.js` | 历史回看也画状态链；`renderReviewState` 末尾同步"业务已核"徽章 |
| `frontend/static/app.css` | `.chain-*` / `.req-clarify` 样式（五态配色 + 澄清卡片） |
| `config_store.py` | `_chain_from_record()`：用落库字段还原状态链；`_record_row_to_dict` 自动带上 |
| `test_status_chain.py` | 新增 46 项断言（后端还原语义 + 前端接线 + 语义不变量） |
| `run_tests.py` | 补登记 4 个此前未被 runner 执行的套件（见 D.5） |

### D.2 为什么是"四徽章"而不是"一个成功勾"

流水线上有四件事，任何一件成立都不蕴含下一件：

```
模型生成了 SQL  ⇏  校验通过  ⇏  需求被满足  ⇏  执行成功  ⇏  业务正确
```

最危险的是**执行成功但需求没满足**：数字是真的、SQL 也不报错，但它回答的不是用户问的问题
（少了个过滤、把替换写成 OR 追加、丢了分组）。这种结果以前在界面上和"完全正确"长得一模一样。
四徽章把"哪一步没成立"变成看得见的，而不是要求用户去读 SQL。

判定口径（与 `agent.py` 的 `status_chain` 一一对应）：

| 徽章 | 来源字段 | 三态处理 |
|---|---|---|
| 已生成 | `generated` | `False` → 红「未生成 SQL」（拒答/知识缺失/被拦截） |
| 已校验 | `gate_passed` | `None` → 虚线「校验未知」，**不当作通过也不当作失败** |
| 需求已核 | `requirement_verdict` | pass 绿 / fail 红 / unsure 橙 / skipped 灰，**四种样子都不同** |
| 已执行 | `executed` + `exec_ok` | `executed=False`（确实没走到）→ 灰「未执行」；`None`（没留档）→ 虚线「执行未知」 |

★ 后两行的区分是刻意的：把"没审 / 没执行"画成"不通过 / 失败"，用户会去查一个根本不存在的问题。

### D.3 `business_verified` 只派生、不另存（一个必须写下来的判断）

"人工审核回填 `business_verified`"我没有加一列去存，而是让它**从 `review_status` 派生**：

```
approved → True      rejected → False      none / pending → None
```

三个理由：

1. **两处存同一件事必然漂移。** 审核被撤销后，链上的 `True` 不会自己消失，
   于是"业务已核"亮着、审核状态却是"未提交"——同一屏两句话互相打脸。
2. **`pending` 不能算 `False`。** 第一版就是这么写的（`rs == "approved"`），
   界面上"还没人看"变成了"业务未通过"，等于替审核人先判了死刑。测试 ① 专门守这条。
3. **撤销审核是真实操作**（`review_record` 支持改判），派生就没有同步问题。

**同时明确不做的一件事**：不顺手把经验中心的 `verify_status` 也标成"人工确认"。
代码里 `api_exp_review` 特意注释了「改 review_status，**不动** verify_status」，
`promote_case` 也把"分类审核通过"和"结果验证通过"当两个条件分别卡。
审核通过 = "这条 SQL 的**口径**没问题"，结果验证 = "这条 SQL 的**数**对得上"——两件事。
合并会让"审核点了通过"直接获得黄金 SQL 资格，正是那个注释要防的误解。

### D.4 历史回看：能推的推，推不出来就说"不知道"

落库只有 `sql / ok / req_verdict / review_status` 四列，所以 `_chain_from_record` 明确标 `partial=True`：

| 字段 | 成功记录 | 失败记录 |
|---|---|---|
| `gate_passed` | `True`（执行成功 ⇒ 闸门必过） | `None`（分不清是闸门拦的还是执行报错） |
| `executed` | `True` | `None`（老记录只有 ok，分不出"执行了但失败"） |
| `result_sane` | `None`（从未落库） | `None` |

界面上对应一行小字：「※ 历史记录：本次只留档了执行结论与需求审核结论，
校验过程与结果体检没有留档，因此显示为「未知 / 未审」。」
—— 宁可承认不知道，也不给历史记录补一个看起来完整的假状态。

### D.5 顺带补上的一个测试治理问题

`run_tests.py` 的分层注册表里**漏了三个套件**：`test_modify_query`(49)、
`test_requirement_review`(52)、`test_gate_req`(72) —— P0~P2 建了文件但没登记，
等于 173 项断言从来没被统一 runner 跑过（只能靠人手单独跑，而人手总会忘）。
本次连同 `test_status_chain`(46) 一起登记，unit 层 20 套、config 层 8 套现在全绿。

★ 教训与前面的"契约只在成功分支补"同源：**测试文件写完不等于测试在跑**，
注册表才是真正的执行边界。

### D.6 回归结果（2026-10-30）

- `run_tests.py unit`：20 套全绿（含 `test_gate` 126、`test_modify_query` 49、
  `test_requirement_review` 52、`test_prompt_constraints` 52、`test_permission` 60 …）
- `run_tests.py config`：8 套全绿（含 `test_gate_req` 72、`test_status_chain` 46、
  `test_sql_experience` 62、`test_acl` 88、`test_knowledge_governance` 47、
  `test_clarification` 41、`test_clarification_multimetric` 16、`test_frontend_structure` 23）
- 视觉验证：用真实 `app.css` + 真实 `ask.js` 渲染四种状态（全绿 / 需求未满足 / 需求待确认 /
  历史 partial）逐张核对，修掉两处显示缺陷：
  - 执行步骤把 `executed=false` 与 `null` 都画成虚线"未知" → 前者改为灰「未执行」
  - 原因清单与澄清问题的项目符号样式不一致（`list-style` 未统一）

### D.7 仍然没做的（P4 候选）

| 项 | 说明 |
|---|---|
| `requirement_fulfilled`（用户确认结果对） | 字段已在链里留位，但"用户点了对"目前只落在 feedback，没有回写记录 |
| 审核页卡片未画状态链 | 审核队列卡片仍是老样式；审核人看的是处理过程 + SQL，四徽章收益较低 |
| `REQ_REVIEW_BLOCK` 仍为 0 | 灰度口径不变，先用线上 `req_verdict` 分布复核误报率 |
