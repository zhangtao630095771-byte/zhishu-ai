# 04 · 数据迁移评估（企业业务语义知识库建设 · 第一阶段）

> 审查日期：2026-11-10 · 目标：评估现有知识结构向统一语义知识模型迁移的可行性
> 目标模型维度：`concept / metric / entity / rule / mapping / dimension / process / relation / analysis`

---

## 1. 现有结构 → 目标模型 映射兼容性

| 目标维度 | 现有落点（实测） | 兼容度 | 说明 |
|---|---|---|---|
| **concept**（概念/口径） | `kb_docs`（10 条 P4 文档，如「提现类口径」「信息费三类比例口径」） | ⚠️ 弱 | 概念以自由文本散落在文档中，未结构化 |
| **metric**（指标） | `metrics`（57 条，含 definition/sql_template/required_predicates/required_columns/forbidden_predicates/metric_type） | ✅ 高 | 已相当成熟，含机器可校验硬约束 |
| **entity**（实体） | `entities`（52 条，name/table_name/grain/comment/keywords） | ✅ 高 | 结构清晰，缺「卫星表」显式登记 |
| **rule**（规则） | `prompts.sql_rules`（文本）+ `relationships`（22 条 Join 铁律）+ `metric_constraint`（17 条硬约束）+ `sql_experience_rules`（0 条） | ⚠️ 中 | 规则分散在 4 处，缺统一规则实体 |
| **mapping**（映射） | `dict_entries`（23 条编码→中文）+ `column_comments`（233 条） | ✅ 高 | 映射能力已成熟（字典+字段注释） |
| **dimension**（维度） | **无独立落点** | ❌ 缺 | 维度仅存在于 query_understanding 的 LLM 输出，未沉淀为知识 |
| **process**（流程/过程） | **无独立落点** | ❌ 缺 | 取数流程是代码硬编码，未建模为知识 |
| **relation**（关系） | `relationships`（22 条，含 cardinality/pre_agg_required） | ✅ 高 | 已含一对多预聚合，较成熟 |
| **analysis**（分析/洞察） | `nl_answer`（即时生成）+ `query_records.answer`（325 条） | ⚠️ 弱 | 分析结论未结构化沉淀 |

**结论：** 现有 9 维中有 5 维已相当成熟（metric/entity/relation/mapping/rule 部分），2 维弱（concept/analysis），2 维完全缺失（dimension/process）。**迁移兼容性整体良好，是「补齐缺失维度 + 结构化已有文本」，而非「推倒重建」。**

---

## 2. 数据量评估（实测）

| 类别 | 行数 | 迁移量级 |
|---|---|---|
| 指标 metrics | 57 | 小 |
| 实体 entities | 52 | 小 |
| 关系 relationships | 22 | 小 |
| 字典 dict_entries | 23 | 小 |
| 表注释 table_comments | 46 | 小 |
| 字段注释 column_comments | 233 | 小 |
| 文档 kb_docs | 10 | 小 |
| 指标硬约束 metric_constraint | 17 | 小 |
| 变更历史 knowledge_history | 1263 | 中（审计数据，建议保留不动） |
| SQL 经验 sql_experience | 165 | 中 |
| 查询记录 query_records | 325 | 中（可作 analysis 维度素材） |

**总知识条目 ≈ 460 条（不含历史/经验/记录），迁移数据量小，可单脚本完成。**

---

## 3. 稳定 ID 方案（现有能力）

现有 `stable_key` 方案（`_migrate_governance.py`，144 行）：

| 类型 | stable_key 格式 | 示例 | 填充率（实测） |
|---|---|---|---|
| 指标 | `metric:{name}` | `metric:完单量` | 48/57（**9 条缺失**） |
| 实体 | `entity:{name}` | `entity:代驾订单` | 42/52 |
| 关系 | `rel:{id}` | `rel:5` | 16/22 |
| 字典 | `dict:{name}` | `dict:应用主体` | 18/23 |
| 表注释 | `table:{table_name}` | `table:ods_tu_order_all` | 39/46 |
| 字段注释 | `column:{table}.{col}` | `column:...` | 149/233 |

**缺失的 9 条指标**（实测）：代驾司机保证金缴纳司机数/缴纳数/缴纳金额、转出人数/转出金额、客户积分实付金额、小板完单量、小板完单金额、总积分 —— 这些是后期 seed 脚本新增、未跑治理回填。

---

## 4. 旧 ID → 新 ID 映射

| 旧 ID | 新 ID 建议 | 映射方式 |
|---|---|---|
| `metrics.name` | `metric:{name}` | 直接前缀拼接，天然可逆 |
| `entities.name` | `entity:{name}` | 同左 |
| `dict_entries.name` | `dict:{name}` | 同左 |
| `table_comments.table_name` | `table:{table_name}` | 同左 |
| `column_comments(table,col)` | `column:{table}.{col}` | 复合键拼接 |
| `relationships.id` | `rel:{id}` | 自增 id 前缀 |
| `kb_docs.id` | `doc:{id}`（当前 kb_docs **无 stable_key 填充**） | 需新增 |

**建议：直接复用现有 `stable_key` 作为统一语义模型的稳定 ID，无需另起一套。** 只需补齐缺失项（9 指标 + kb_docs 全表 + 部分关系/字典/字段注释）。

---

## 5. 迁移风险点

| # | 风险 | 等级 | 缓解 |
|---|---|---|---|
| 1 | `stable_key` 填充不全（9 指标 + 22 关系缺 6 + 7 字典缺 5 + 84 字段注释缺） | 中 | 迁移前先跑 `_migrate_governance.py` 补全，或迁移脚本内幂等回填 |
| 2 | `version` 字段无有效递增机制（审计有、版本无） | 中 | 迁移时统一 version=1 起步，后续靠 `knowledge_history` 追溯 |
| 3 | `metric_constraint` 与 `metrics.required_predicates` 双存储一致性窗口 | 低 | 迁移后明确唯一事实源，metric_constraint 保持派生表定位 |
| 4 | 维度（dimension）/流程（process）无现成数据，迁移无源 | 低 | 属「新增能力」，非迁移风险；需另行建设 |
| 5 | 复合键「表.字段」在 ID 拼接中的分隔符冲突（表名/字段名可能含 `.`） | 低 | 用不可逆分隔符或转义；实测当前表名均为 `ods_/dwd_` 前缀，无 `.`，风险有限 |
| 6 | `kb_docs` 内容为自由文本，结构化抽取需 LLM | 中 | 分阶段：先迁移元数据，正文结构化放 P2 |
| 7 | 迁移期间知识与 RAG 索引不同步 | 低 | 迁移后统一重建索引（`/api/index/rebuild`） |

---

## 6. 回滚可行性

- 现有 `agent_config.db.bak-*` 有 6 份备份，且迁移前应额外备份一份。
- 建议迁移脚本**只增不改**（新建 `semantic_*` 表 + 回填 stable_key，不动原表主键/字段），则回滚 = 删除新表即可，原表零风险。
- 若采用「新增统一语义表」而非「改造原表」，回滚成本极低，且可与现有 `config_store` 并存灰度。

**结论：迁移可行、数据量小、回滚成本低；建议「新增统一语义层表 + 复用 stable_key + 单脚本幂等回填」，不改动现有生产表结构。**
