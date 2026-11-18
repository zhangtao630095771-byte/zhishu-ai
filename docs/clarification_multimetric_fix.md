# 知数AI 多轮澄清机制专项修复报告：恢复多指标直接问数能力

> 时间：2026-10-29　范围：澄清决策逻辑 + 指标公式过滤作用域 + 多指标查询规划
> 方法：先以代码与真实知识库证据定位根因，再实施修复并补回归测试。

## 一、根因定位（代码证据）

### 根因1：`clarification.decide()` 把「命中多个指标」误判为「语义歧义」

原实现（`clarification.py` 原 `decide` 的 named 分支）：

```python
if named:
    ...
    if len(uniq_named) == 1:
        return ACTION_EXECUTE
    if len(uniq_named) >= 2:          # ← 问题在这
        return ACTION_CLARIFY         #    多个指标 → 单选澄清
```

当 LLM 从问题里识别出多个指标名（发单量、完单量、接单量、接单率、完单率），
`len(uniq_named) >= 2` 直接返回 `clarify`，把五个**独立并列**的指标当成五个**互斥选项**，
要求用户单选。这违反了「多指标是一次查询的多个输出字段，不是互斥口径」的语义。

同时，`exact_named` 的「直接执行」分支排在 named 分支**之后**，
只要 named 非空就永远走不到 exact_named 的直行，导致用户明明逐字写出了指标名也照样被追问。

### 根因2：`required_predicates` 把「指标公式内部条件」误存为「查询级全局过滤」

真实知识库中（`agent_config.db` metrics 表）：

| 指标 | 原 required_predicates | 正确语义 |
|---|---|---|
| 完单量 | `["status = 6"]` | ✅ 全局过滤（COUNT(*) WHERE status=6） |
| 完单率 | `["status = 6"]` | ❌ status=6 是**分子**条件（SUM(CASE WHEN status=6…)/COUNT(*)，分母为全部订单） |
| 接单量 | `["is_picked = 1"]` | ❌ is_picked=1 是**条件聚合**（SUM(CASE WHEN is_picked=1…)），不是 WHERE |
| 接单率 | `["is_picked = 1"]` | ❌ is_picked=1 是**分子**条件（分母为总订单量） |

`required_predicates` 的语义是「必须出现的**过滤**片段」（`config_store.py` 注释），
把分子条件塞进去，等于暗示「完单率也要 WHERE status=6」——若模型照做，分母只剩已完单订单，
完单率恒为 1。这正是方案第四节点名的风险。

## 二、修复内容

### 1. 澄清决策路由重构（`clarification.py`）

按方案规则3把判断拆成「识别 / 绑定 / 消歧 / 可行性」四步：

- 新增 `_candidate(m, match)` 统一候选构造。
- `decide` 重写为三路：
  - **① 用户明确写出指标名（exact_named）→ 直接执行，全部保留**（多指标是一起查的集合）。
  - **② LLM 识别出明确指标名（named 非空）→ 直接执行，多个指标一起查，不单选**。
  - **③ 用户未点名、LLM 也未识别出指标名，仅 keyword 命中多个候选 → 澄清**
    （这才是「同一概念对应多口径」的真实歧义，保留澄清能力）。
  - 0 可信候选且有指标意图 → unknown（知识不足，不编造）。

效果：`查询…发单量完单量，接单量接单率，完单率` → `execute`（5 个指标全保留）；
`查上个月订单量`（笼统词命中多口径）→ 仍 `clarify`。

### 2. 指标过滤条件作用域修正（数据 + 校验）

- 新增幂等迁移脚本 `_fix_metric_predicates.py`：把「完单率 / 接单量 / 接单率」的
  `required_predicates` 清空（它们的状态/接单条件是公式内部条件，非全局过滤），
  保留「完单量」的 `status=6`（这是真正的全局过滤）与「发单量」的 `forbidden_predicates=["status"]`。
- 新增 `sql_validator` 分母保护校验 `_check_ratio_denominator`（接入 `_check_metric`）：
  从比率指标 definition 提取 `CASE WHEN` 分子条件（status=6 / is_picked=1），
  若该条件出现在**最外层 WHERE**（用括号深度扫描 `_extract_outer_where` 提取），
  报 `ratio_denominator` error——防止分子条件污染分母。

### 3. 多指标查询规划

多指标本身由现有链路原生支持：`query_understanding` 的 `metrics` 是数组、
`semantic_layer.retrieve_semantic` 的 `p1_metrics` 是列表、`resolve` 会把每个指标定义
逐条注入 Prompt（`- 【MUST】{name} = {definition}`）。本次修复只是**不再在澄清层把它拆成单选**，
执行仍走 `agent.ask → _pre_execute_check → _exec_sql`，SQL Guard / Validator / 权限 / 结果校验不绕过。

## 三、测试结果

新增 `test_clarification_multimetric.py`（16 项，config 层），覆盖方案第七节：

| 要求 | 测试 | 结果 |
|---|---|---|
| 1 单指标直接执行 | test_single_metric_direct | ✅ |
| 2 五指标不单选、全保留 | test_five_metrics_not_single_select / test_llm_named_multi_not_select | ✅ |
| 4 同名多冲突定义仍澄清 | test_genuine_ambiguity_still_clarify | ✅ |
| 5 缺正式定义不编造 | test_missing_formal_no_fabricate | ✅ |
| 6 发单量不被 status 过滤 | test_fadan_not_filtered_by_status | ✅ |
| 7 完单率分母为全部订单 | test_wandan_rate_denominator_protected | ✅ |
| 8 接单率分母为全部订单 | test_jiedan_rate_denominator_protected | ✅ |
| 9 接单量条件聚合不全局化 | test_jiedan_amount_not_global_filter | ✅ |
| 10 澄清后其他条件保留 | test_answer_keeps_other_conditions | ✅ |
| 12 单轮/SQL校验/执行回归 | 全量 unit+config 回归 | ✅ |

**全量回归**：unit 18 文件 + config 6 文件（含新增 16 项）全绿；
`test_gate.py` 126/126、`test_clarification.py` 41 项、`test_legacy_validation.py` 9/9 均通过。

## 四、已实现 / 已测 / 待验证 / 未完成（如实区分）

### 已实现且已通过自动化测试
- 多指标不触发单选澄清、已审核唯一指标不重复确认。
- 真正的「同一概念多口径」歧义仍触发澄清（不因减少交互而取消所有澄清）。
- 指标内部条件聚合与查询级全局过滤的语义区分（数据迁移 + 分母保护校验）。
- 单轮 `/api/ask`、SQL 安全校验、结果校验、执行链路的回归全绿。

### 仅通过模拟/单元测试（未连真实库端到端）
- 决策路由与校验规则用**模拟候选 / 构造 ctx** 验证；真实 LLM 生成 SQL + ClickHouse 执行的端到端
  效果需连库后复核（integration 层）。

### 待真实业务验证
- 五个指标的真实 SQL 生成正确性（发单量/完单量/接单量/接单率/完单率在同一次查询里
  是否都被正确计算、分母口径正确）——需在真实 ClickHouse 上人工核对结果。

### 未完成（明确列出，不掩盖）
1. **「混合场景逐指标精细澄清」未实现**：当用户同时给出「唯一指标 + 一个笼统歧义概念」时，
   当前 `decide` 采取**整体直行**（把笼统概念按 keyword 命中一起执行），而不是「只澄清歧义那一项、其余指标照常」。
   真实数据下因「订单量」等本身是指标名、exact 命中后不歧义，此场景罕见；
   若需严格语义，需把 `decide` 从整体判断改为逐指标判断。
2. **「指标数据粒度/关联不兼容」的安全处理未实现**：多指标若分属不同数据源/粒度，
   当前交给 SQL 生成器自行判断，无显式「预聚合或分步计算」的查询规划器；
   极端不兼容时靠 SQL Validator / 结果校验兜底，而非主动规划。
3. **integration 层（真实 ClickHouse 端到端）本轮未跑。**

## 五、修改文件清单

| 文件 | 性质 | 内容 |
|---|---|---|
| `clarification.py` | 修改 | 新增 `_candidate`；重写 `decide`（多指标→execute，保留真实歧义澄清） |
| `sql_validator.py` | 修改 | 新增 `_extract_outer_where` / `_extract_ratio_numerators` / `_check_ratio_denominator`，接入 `_check_metric` |
| `_fix_metric_predicates.py` | 新增 | 幂等迁移：清空完单率/接单量/接单率的 required_predicates |
| `test_clarification_multimetric.py` | 新增 | 16 项专项回归 |
| `run_tests.py` | 修改 | 登记新测试（config 层） |
| `agent_config.db` | 数据 | 3 个指标 required_predicates 语义修正（含变更历史） |

## 六、剩余风险

- 分母保护校验用「括号深度扫描最外层 WHERE」实现，对极端复杂/多子查询 SQL 可能漏检或
  （理论上）误报；当前按 error 级拦截，若线上出现误拦可下调为 warning。
- `decide` 情况②（LLM 识别多指标即直行）依赖 LLM 指标识别的准确度；若 LLM 把笼统词误识别成
  多个具体指标名，会直接执行而非澄清。该风险靠 `semantic_layer` 的实体过滤与 `query_understanding`
  的「只能从权威清单选名」约束缓解。
