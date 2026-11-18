# P0 核心能力升级 · 评测与实验设计（evaluation-design）

> 复用现有 `evaluation/`（8 类数据集 + Runner + Gate + 回流 + 消融），不重建独立评测系统。
> 本文补齐「Expected Result 评测」「六类核心评测」「消融实验」的显式口径。

---

## 一、六类核心评测 → 现有实现映射

| 任务书 §6.2 | 现有实现 | 判分器 |
|---|---|---|
| 1 意图理解 | `query_understanding.understand` + 用例 `intent` | `checks` + runner 的 `intent` 维 |
| 2 知识召回 | P0~P4 分层检索 | `check_tables` / `check_metrics`（Table/Metric Recall） |
| 3 SQL 生成 | `check_sql_contains` / `check_sql_not_contains` / `check_time` | 表/字段/JOIN/过滤/时间口径 |
| 4 SQL 安全 | `guard` + `sql_validator` + `permission` | `test_gate` / `test_audit_safety` / adversarial 集 |
| 5 结果正确性 | **`expected_result` + `check_result` / `result_compare`（本次新增）** | `check_result` 维 |
| 6 业务任务完成 | `status_chain.business_verified` / `requirement_fulfilled` | 需人工审核回填 |

> 缺口：第 6 类「业务任务完成」目前依赖人工审核回填，无自动判分维度（见 known-issues）。

## 二、核心指标口径（分母 / 分子 / 范围）

见 `data-model.md §四`。关键新增两个可自动计算的指标：

- **Exact Result Match Rate** = `verdict ∈ {exact, within_tolerance}` 的用例 / 有 `expected_result` 的用例。数据源：`golden-demo`（26 条已带标准结果）与后续 `golden_cases` 固定测试集。
- **Numeric Result Accuracy** = 数值归一后一致 / 可数值化用例。

> 铁律：`Business Verified Rate` 分子只含人工审核通过；`SQL Execution Success Rate` ≠ 结果准确率，二者并列独立展示（看板已落地）。

## 三、消融实验（A~G 七组对照）

`run_ablation.py` 现在支持 9 组（monkeypatch + finally 还原，不碰源码）：

| 组 | 模块 | 对应任务书 |
|---|---|---|
| A | 完整系统（baseline） | A |
| B | `semantic_layer` | 关闭 Semantic Layer |
| C | `dictionary` / `entity_filter` | 关闭 Multi-RAG 部分通道 |
| D | `trust_ranking`（trust/verify 权重归零） | 关闭知识可信等级排序 |
| E | `join_cardinality`（`_check_p2` 置空） | 关闭 JOIN 基数约束 |
| F | `sql_experience`（retrieve 恒 None） | 关闭 SQL 经验检索 |
| G | `sql_validator`（整体置空） | 关闭 SQL Validator |

- 安全/合规模块（guard / table_acl / disabled_metric_block / abstain_fallback）在 `NEVER_ABLATE` 里，**永不消融**。
- 消融只作为诊断手段，不修改磁盘源码；任何裁剪决策必须走 Regression Gate + Release Gate。

## 四、回归与发布门禁

- `evaluate.py --gate`：Release Gate（10 项阈值，指标缺失视为不通过）。
- `evaluate.py --regression`：Regression Gate（核心指标不得低于 baseline - 2% 容忍度；幻觉率不得恶化）。
- 门禁失败 → 非零退出码，CI 可据此拦截发布。

## 五、安全实验隔离

消融只在隔离测试环境跑；生产环境严禁关闭权限、安全闸门和 SQL Validator。`run_ablation.py` 对安全模块的消融请求直接拒绝并提示。
