# 知数 AI 知识库治理升级 · 阶段 3 整改报告（检索质量与评估闭环）

> 时间：2026-10-22
> 目标：Golden Dataset 标准化（GAP-4）、用户反馈进入知识/评测闭环（GAP-6）。

---

## 1. 结论

本阶段完成两块**结构性**落地（不虚构任何基准值）：

1. **Golden Dataset 标准结构**：产出标准字段规范文档 + 94 条样例字段覆盖审计工具，明确「现有数据缺 `expected_sql`/`expected_result`」这一事实，给出补齐路径。
2. **反馈闭环**：`feedbacks` 表新增「关联知识 / 回归样例 / 根因」三列，反馈分类从 7 类扩展到 16 类，使「用户反馈 → 归类 → 修正 → 回归」可追溯。

---

## 2. 改动内容

### 2.1 Golden Dataset 标准化（GAP-4）

| 文件 | 改动 |
|---|---|
| `docs/golden_dataset_schema.md`（新） | 标准样例字段（必填/建议/升级重点）、三种用途区分（知识示例/Golden SQL/评测样例）、评测维度对照、结果校验规则（auto/rule/manual）、补齐路径 |
| `evaluation/schema.py`（新） | 审计工具：统计 94 条样例的字段覆盖，输出缺 `expected_sql`/`expected_result` 的样例清单（只审计、不生成、不虚构） |

**字段覆盖审计结论（golden 35 条实测）**：

| 字段 | 覆盖 |
|---|---|
| id / question / expected_ok / name / category | 35/35 |
| difficulty / intent | 34/35、33/35 |
| expected_metrics / expected_tables | 22/35、23/35 |
| expected_sql_contains / expected_sql_not_contains | 13/35、1/35 |
| **expected_sql** | **0/35** |
| **expected_result / result_check** | **0/35** |
| sql_dialect / knowledge_ids / acceptance / review_status / failure_type / version | 0/35 |

> 结论：`result_accuracy` 仍为 `null`，**不能宣称业务结果准确率**。补齐需在真实 ClickHouse 上跑通标准 SQL 取得基准，且必须经人工审核 —— 本阶段不虚构。

### 2.2 反馈闭环（GAP-6）

| 文件 | 改动 |
|---|---|
| `config_store.py` | `feedbacks` 新增 `related_knowledge_id` / `regression_sample_id` / `root_cause` 三列；`add_feedback` / `update_feedback` 支持写入；`FEEDBACK_REASONS` 从 7 类扩展到 16 类（新增找错字段/过滤缺失/JOIN 重复/聚合粒度/SQL 执行失败/数据源异常/结果不符/知识缺失/权限不足） |

---

## 3. 验证结果（实际执行）

- 反馈闭环端到端：`add_feedback` 带 `related_knowledge_id`/`root_cause` → `update_feedback` 关联 `regression_sample_id` → `get_feedbacks` 读回三列全部正确。
- Golden 审计：`evaluation/schema.py` 可运行，输出 94 条样例覆盖报告，golden 集 `expected_sql`/`expected_result` 覆盖率 0/35（如实报告）。
- 全量回归：unit/config/integration 全绿。

---

## 4. 尚未完成

- **Golden 基准值补齐**：需业务确认口径 + 真实库跑数，属后续人工+半自动工作，本轮不虚构。
- **反馈处理 UI**：反馈关联字段已落库，但管理页「关联知识/生成回归样例」的操作入口尚未接入前端（后端能力已就绪）。
- **检索命中可观测性**（`trace` 逐条「为什么没命中」）：P1 项，本阶段未做（现有 `route_info`/`trace` 已记录命中与被过滤项）。
