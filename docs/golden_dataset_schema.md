# Golden Dataset 标准结构（Golden SQL 升级规范）

> 用途：把「问题-SQL 样例」从演示功能升级为可复用的标准评测数据集。
> 原则：**可选字段允许为空，不为了满足模板而虚构预期结果**；无法确认标准答案的题目必须显式标记，不得伪造基准值。

---

## 1. 标准样例字段（对齐任务书 §6.1）

### 1.1 必填字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `id` | string | 稳定唯一标识（如 `g001`） |
| `question` | string | 原始业务问题 |
| `expected_ok` | bool | 期望系统是否成功执行（拒答类为 false） |

### 1.2 强烈建议字段

| 字段 | 类型 | 说明 |
|---|---|---|
| `name` | string | 用例名（简短） |
| `category` | string | 场景分类（simple/normal/join/time/abstain…） |
| `difficulty` | string | easy/medium/hard |
| `intent` | string | summary/trend/compare/detail/deny |
| `expected_metrics` | list | 标准指标名 |
| `expected_tables` | list | 预期数据表 |
| `expected_sql_contains` | list | SQL 必须包含的关键片段（口径/过滤/JOIN 键） |
| `expected_sql_not_contains` | list | SQL 不得包含的片段（负向口径） |

### 1.3 标准 SQL 与结果校验（本阶段升级重点）

| 字段 | 类型 | 说明 | 当前覆盖 |
|---|---|---|---|
| `expected_sql` | string | 经审核的标准全量 SQL（Golden SQL 核心） | ❌ 未提供 |
| `expected_result` | any | 期望结果或结果校验规则（`check_result` 消费） | ❌ 未提供 |
| `result_check` | object | 规则化结果校验（如 `{"row_count": 23, "column": "组合数", "regex": "..."}`） | ❌ 未提供 |
| `sql_dialect` | string | SQL 方言（默认取 `config.SQL_DIALECT`） | ⚠️ 默认隐式 |
| `knowledge_ids` | list | 关联知识 ID（指标/实体/关系） | ❌ 未提供 |
| `acceptance` | string | 验收方式：`auto`（自动比对）/ `manual`（人工）/ `rule`（规则） | ❌ 未提供 |
| `review_status` | string | draft / pending / approved | ❌ 未提供 |
| `failure_type` | string | 该样例曾暴露的失败类型（用于回归归类） | ❌ 未提供 |
| `version` / `updated_at` | — | 样例版本与变更记录 | ❌ 未提供 |

---

## 2. 三种用途的区分（对齐任务书 §6.2）

| 用途 | 含义 | 与其它用途的关系 |
|---|---|---|
| **知识示例** | 帮助模型理解业务与 SQL 写法 | 可被 Golden 引用，但本身不等同于标准答案 |
| **Golden SQL** | 经审核、有明确适用范围的**标准查询案例** | 必须是 `review_status=approved`，带 `expected_sql`/`expected_result` |
| **评测样例** | 比较系统不同版本输出质量 | 可关联 Golden，也可仅做 `expected_sql_contains` 弱断言 |

> **禁止**把用户历史 SQL 自动认定为 Golden SQL。所有 `expected_sql`/`expected_result` 必须经人工审核后写入。

---

## 3. 评测维度（对齐任务书 §6.3）

现有 `evaluation/evaluators/checks.py` 已实现以下维度（`DIMENSIONS` 注册表）：

| 维度 | check 函数 | 说明 |
|---|---|---|
| 执行成功 | `check_ok` | SQL 执行成功率（≠ 业务正确） |
| 拒答正确 | `check_abstain` | 应拒答时是否拒答 |
| 表召回 | `check_tables` | Table Retrieval Recall |
| 指标识别 | `check_metrics` | 正确指标识别率 |
| 负向指标 | `check_forbidden_metrics` | 不应命中的指标 |
| SQL 含片段 | `check_sql_contains` | 口径/过滤命中 |
| SQL 不含片段 | `check_sql_not_contains` | 负向口径 |
| 时间口径 | `check_time` | 时间范围正确 |
| **结果正确** | `check_result` | **仅在提供 `expected_result` 时生效** |
| 格式 | `check_format` | 输出格式（列名/正则） |
| 接地性 | `check_grounding` | 表/字段是否在召回范围内 |
| 幻觉 | `check_hallucination` | 是否编造表/字段 |

**关键区分**：`sql_execution_success_rate`（可执行）与 `result_accuracy`（业务正确）是**两个独立指标**。仅凭 SQL 执行成功不能判定样例通过。

---

## 4. 结果校验规则（`result_check`）

对可稳定执行、结果确定性的题目，优先比对**规范化结果集**；对动态数据/非确定性/无标准答案的题目，用 `acceptance` 明确标注人工或规则验收：

```json
{
  "acceptance": "rule",
  "result_check": {
    "row_count": 23,
    "columns": ["信息费默认比例", "信息费提升比例", "信息费降低比例", "司机数"],
    "cell": {"信息费默认比例": "0.06"}
  }
}
```

- `acceptance=auto`：`expected_result` 提供完整基准值，自动逐值比对。
- `acceptance=rule`：`result_check` 提供行数/列名/关键单元格的规则断言。
- `acceptance=manual`：无可靠自动基准，必须人工验收，**禁止伪造结果**。

---

## 5. 现状与补齐路径

- **现状**：94 条样例（golden 35 / regression 14 / boundary 13 / abstention 10 / paraphrase 9 / counterfactual 6 / adversarial 6 / inbox 1），字段覆盖到 `expected_sql_contains`/`expected_sql_not_contains` 一级；**无 `expected_sql`、无 `expected_result`**，故 `result_accuracy` 恒为 `null`。
- **补齐路径**（不虚构）：
  1. 对**口径已人工确认**的用例（如「代驾完单量 status=40」），逐步补 `expected_sql` + `expected_result`（需在真实 ClickHouse 上跑通标准 SQL 取得基准）。
  2. 对动态数据题，用 `acceptance=manual` 或 `result_check` 规则验收。
  3. 其余题目保持现状，并在 `audit` 报告中显式标记「待补基准」。

> 审计工具见 `evaluation/schema.py`：`python evaluation/schema.py` 可输出 94 条样例的字段覆盖报告，明确指出哪些题目缺 `expected_sql`/`expected_result`。
