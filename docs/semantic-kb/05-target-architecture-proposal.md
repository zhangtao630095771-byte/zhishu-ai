# 05 · 目标架构建议（企业业务语义知识库建设 · 第一阶段）

> 审查日期：2026-11-10 · 基于真实代码（非臆测）
> 目标：统一业务语义知识模型 + Semantic Context Builder 的落地建议
> 铁律：**不推倒重建、不引入第二套取数流水线、不绕过权限与 SQL 安全**

---

## 1. 现状判断（为什么不需要推倒重建）

现有代码已经**具备了「统一语义层」的骨架**，且质量很高：

- `semantic_layer.py` 已是 P0~P4 分层 + 冲突解决 + 多路检索的完整编排。
- 七张知识表已统一治理字段（status/source/stable_key/content_hash/knowledge_level）。
- `agent._build_system_prompt` 已是「Semantic Context Builder」的雏形（唯一组装点，分段有序）。
- 指标已具备机器可校验硬约束（required/forbidden predicates）。

**结论：目标不是新建，而是「给已存在的语义层补上缺失维度 + 显式化统一模型」。**

---

## 2. 三类落地建议

### 2.1 复用（Reuse）—— 现有能力直接沿用

| 现有能力 | 复用方式 |
|---|---|
| P0~P4 分层 + `semantic_layer.resolve` | 作为 Semantic Context Builder 的**检索与组装核心**，不改 |
| `stable_key` 稳定 ID | 作为统一语义模型的唯一业务 ID，补全缺失项 |
| `status/source/content_hash` 治理字段 | 作为统一模型的质量门禁，不改 |
| `guard` + `sql_validator` + `permission` + `requirement_review` | 安全/校验/权限六维闸门，**完全不动** |
| `sql_experience` + `golden_cases` | 作为 rule/analysis 维度的数据源，不改 |
| `knowledge_history` | 作为统一模型的审计/版本追溯，不改 |

### 2.2 扩展（Extend）—— 现有能力补强

| 项 | 现状 | 扩展建议 |
|---|---|---|
| 实体卫星表 | `entities` 只登记主表，卫星表靠 `SATELLITE_KEEP_THRESHOLD` 阈值兜底 | 给 `entities` 增加 `related_tables`（JSON）字段，显式登记卫星表，去掉阈值兜底的不确定性 |
| 指标 hard 约束 | 双存储（metrics 列 + metric_constraint 派生表） | 明确唯一事实源：metrics 三列为源，metric_constraint 保持派生只读，迁移时加一致性校验 |
| 文档结构化 | `kb_docs` 自由文本 | 增加 `doc_type`（口径/FAQ/流程）+ 结构化提取（P2 阶段） |
| 维度沉淀 | 维度仅在 LLM 输出，未沉淀 | 新增 `dimension` 知识类型，从 query_records 高频维度提取 |

### 2.3 新增（New）—— 缺失维度落地

统一语义知识模型建议（**新增表，不改现有表**）：

| 维度 | 落地表（建议） | 数据来源 | 优先级 |
|---|---|---|---|
| concept（概念） | `semantic_concepts` | 从 `kb_docs` 结构化提取 | P2 |
| dimension（维度） | `semantic_dimensions` | 从 `query_records` + 字段注释提取 | P2 |
| process（流程） | `semantic_processes` | 手工/LLM 建模 | P3 |
| analysis（分析） | 复用 `query_records.answer` + 新增 `semantic_insights` | 历史结论沉淀 | P3 |

**统一模型建议为「视图/注册表」而非「替换」：** 新增一张 `semantic_registry`（type + stable_key + level + 指向各源表的引用），把已有 7 类知识 + 新增维度统一登记，Semantic Context Builder 只读这张注册表构建上下文。原表仍是唯一事实源。

---

## 3. Semantic Context Builder 落地路径

现有 `agent._build_system_prompt`（`agent.py:429`）已是唯一组装点。建议在其上做**最小增量**：

```
SemanticContextBuilder（建议独立为 semantic_builder.py，或继续收敛在 agent）
  ├─ 输入：question + understanding + SemanticContext
  ├─ 步骤：
  │   1. resolve(ctx)                    # 沿用，冲突解决 + bindings
  │   2. 新增：dimension 检索             # 从 semantic_dimensions 补维度知识
  │   3. 新增：concept 检索               # 从 semantic_concepts 补概念
  │   4. _build_system_prompt(...)       # 沿用，分段有序组装
  │   5. 顺序维持：role → 时间 → P0~P4 → 字典 → 硬约束 → 经验
  └─ 输出：system prompt + parts + bindings（不变契约）
```

**关键约束（遵守铁律）：**
1. **不改 `ask_v2` 主流程**：新增维度作为「附加知识段」插入现有组装序列，不改变 SQL 生成/校验/执行逻辑。
2. **不引入第二套流水线**：Semantic Context Builder 是 `ask_v2` 内部的一次重构，不是新入口。
3. **不绕过权限与安全**：新增知识类型同样经过 `governance.visible_status` 准入 + 表权限过滤 + 六维闸门。
4. **SQL 仍由 LLM 生成 + 闸门校验**：统一语义模型只提供「更好的上下文」，不直接产出 SQL（避免绕过校验）。

---

## 4. 分期建议

| 阶段 | 内容 | 风险 |
|---|---|---|
| **P0（本次后立即）** | 补全 stable_key（9 指标 + kb_docs + 关系/字典/字段注释缺口）；清 64 条 candidate | 极低 |
| **P1** | 统一治理：metric_constraint 一致性校验；实体卫星表显式化 | 低 |
| **P2** | 新增 `semantic_concepts` / `semantic_dimensions`，从 kb_docs/query_records 提取；Semantic Context Builder 落地 | 中 |
| **P3** | `semantic_processes` 流程建模；`semantic_insights` 分析沉淀 | 中 |
| **P4** | 协作/分享/长期记忆（对齐既有产品构想） | — |

---

## 5. 一句话总结

> 本项目已具备统一语义层的**骨架与治理能力**，建设方向是「**复用骨架 + 补维度 + 显式化统一注册表**」，把分散在 `metrics/entities/relationships/dict/column_comments/kb_docs/prompts/sql_experience` 的知识收敛到一个统一语义知识模型视图下，让 `_build_system_prompt` 进化为真正意义上的 Semantic Context Builder —— 全程不推倒、不双流水线、不绕安全闸门。
