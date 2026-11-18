# 知数AI 知识中心整合与 Apache Ossie 标准兼容改造 — 第一阶段审查报告（P0）

> 审查日期：2026-11-12 · 方式：只读（Read / Grep / Glob / sqlite3 / WebSearch）
> 审查范围：后端 `server.py` / `agent.py` / `semantic_layer.py` / `rag.py` / `config_store.py` / `sql_validator.py` / `sql_experience.py` / `golden_cases.py` / `exporter.py` 等，前端 `frontend/index.html` + `frontend/static/js/`，以及 `docs/` 下历史审查文档。
> **本阶段只做审查与方案设计，未修改任何源码或数据库。**

---

## 0. 结论摘要（金字塔原理 · 结论先行）

### 0.1 总体判断

**本项目已大幅领先于任务书假设，无需推倒重建。** 任务书描述的"知识中心整合 + 语义模型标准化 + Agent 使用语义知识"三大目标中，前两项的"底层能力"已经存在或已过半，真正的增量工作集中在 **OSSIE 标准适配层** 和 **前端入口整合** 两处。

| 任务书假设 | 实际情况 | 影响 |
|---|---|---|
| 存在「知识管理」和「知识治理」两个并列一级入口，需合并 | 前端已有 `知识管理(tab-kb)` 与 `知识治理(tab-gov)` 两个并列入口，但**分工已明确**（管理=内容注册表，治理=运营动作），底层已共用同一套 `config_store` 与治理状态 | 整合是"入口/信息架构调整"，不是"两套系统合并" |
| 存在两条问数流水线，需兼容 | **Legacy 流水线已退役（2026-10 收敛）**，全仓唯一业务编排是 `agent.ask()` → `ask_v2()` | 任务书 §7.3「双流水线兼容」的前提已不存在，需改为"单流水线内语义接入" |
| Semantic Layer 需新建 Dataset/Metric/Dimension/Relationship 对象 | 已有 P0~P4 分层 + 7 张知识表 + `semantic_registry`（443 行统一登记）+ `metric_constraint`（派生） | 语义模型对象已具备雏形，缺"显式的 Semantic Model 容器"和"维度(Dimension)沉淀" |
| 需引入 OSSIE 导入导出 | 已有 `exporter.py` 导出（JSON/MD，`format_version=1`），但**无导入、无标准格式**；OSSIE 是全新增量 | OSSIE Adapter 是本次改造最大的一块**新增**工作 |
| 知识治理需统一状态 | 7 张知识表已统一 `status/source/stable_key/content_hash/knowledge_level/version` 治理字段 | 治理体系已成熟，仅需清存量 + 复用 |

### 0.2 三个必须澄清的关键事实

1. **双流水线已收敛为单流水线。** `docs/semantic-kb/03-agent-call-chain.md`（2026-11-10）已实测确认：`agent.py:1171` 注释「Legacy 兜底路径已退役」。`/api/ask`、`/api/ask?base_query_id`（增量修改）、`/api/clarify/*`（多轮澄清）三个入口全部汇聚到同一套 `ask_v2`。任务书 §7.3「不擅自合并两条流水线」「两条流水线分别验证」已无对象。

2. **Apache Ossie 标准真实存在，但版本与对象模型和任务书假设有出入**（详见 §8）：
   - 官方仓库 `apache/ossie`（Apache 孵化项目，前身 Open Semantic Interchange/OSI）。
   - 当前**稳定版为 `0.1.1`**（2025-12-11）；`main` 分支是 `0.3.0.dev0`（DRAFT，官方明确"不可在生产依赖"）。
   - **`Dimension` 不是独立对象**，而是 `Field` 的可选子对象（仅 `is_time: bool`）。任务书 §5.3 把 Dimension 当成独立对象与"时间/地区/客户"对应，需修正。
   - 任务书 §5.3 提到的 `Custom Extensions` 在 0.3.0 已改名 `nonportable_extensions`（`vendor_name` + `data` JSON）。
   - 官方已提供 **Python SDK（Pydantic v2）+ `ossie-schema.json` + `validation/` 校验工具**，Adapter 可复用而非从零手写解析。

3. **知识治理已基本落地。** 7 张知识表已统一治理字段，`governance.py` 提供生产检索准入（严格模式默认开启），`knowledge_history`（1263 行）做审计，`semantic_builder.py` 用注册表做命中审计。剩余 64 条 candidate 待复核（主要在 `column_comments` 49 条）。

### 0.3 建议的最小改造方向

一句话：**「复用骨架 + 新增 OSSIE Adapter + 前端入口整合 + 显式化 Semantic Model 容器」，不推倒、不双流水线、不绕安全闸门。**

- **P0（本次交付）**：审查报告 + 方案设计 + 固定 OSSIE 版本（建议 `0.1.1` 或锁定 commit）。
- **P1**：知识中心入口整合（前端）+ OSSIE Adapter（后端：解析/校验/双向转换/导入导出/往返测试）。
- **P2**：Agent 接入语义模型读取接口 + 订单主题试点 + Evaluation 对照。
- **P3**：对外语义模型 API / MCP 接入。

---

## 1. 当前架构全景

### 1.1 技术栈

| 维度 | 结论 |
|---|---|
| 后端 | Python 3.13 + FastAPI（`server.py` 约 4400 行，约 150 个接口） |
| 前端 | 原生 JS + 原生 CSS，单页应用 `frontend/index.html`（1474 行）+ 19 个 JS 文件，无 React/Vue、无构建工具 |
| 存储 | SQLite `agent_config.db`（38 张表：配置+知识+权限+经验+审计）+ ClickHouse（只读业务库，`DB_TYPE=clickhouse`） |
| LLM | `llm.py` 封装 `chat()`，默认 DeepSeek（`deepseek-flash`） |
| Embedding | `rag.py` 用 fastembed + BAAI/bge-small-zh-v1.5（本地离线优先） |
| 检索 | `semantic_layer.py` P0~P4 分层 + `rag.py` 混合检索（关键词精确优先 + 向量兜底）+ 字典第三条线 |

### 1.2 核心模块清单（实测行数/职责）

| 模块 | 职责 | 本次改造相关性 |
|---|---|---|
| `agent.py` (~1210 行) | 编排大脑：唯一业务流水线 `ask_v2` | ★ 语义模型接入点 |
| `server.py` (~4400 行) | FastAPI 路由（~150 接口） | ★ 新增语义模型/OSSIE 接口 |
| `config_store.py` (~4400 行) | SQLite 配置库 CRUD + 知识治理 + 账号权限 | ★ 语义模型表 + OSSIE 元数据 |
| `semantic_layer.py` (~590 行) | P0~P4 分层 + 冲突解决 + 多路检索 | ★ 语义模型读取接口 |
| `rag.py` (~724 行) | 检索引擎（关键词+向量混合、字典、点名表） | ★ Multi-RAG |
| `sql_experience.py` (~2105 行) | SQL 经验中心（成功/失败/规则） | ★ Golden SQL 语义重叠 |
| `golden_cases.py` (~495 行) | 标准问题/黄金 SQL（版本+验证） | ★ Golden SQL 语义重叠 |
| `sql_validator.py` (~679 行) | SQL 五维校验（P0~P3+指标） | 保留不动 |
| `guard.py` / `permission.py` | 安全护栏 + 字段/行级权限 | 保留不动 |
| `exporter.py` (~400 行) | 知识导出（JSON/MD） | ★ OSSIE 导出的参照 |
| `governance.py` (~37 行) | 知识治理状态准入 | ★ 复用 |
| `semantic_builder.py` (~137 行) | Semantic Context Builder（注册表审计） | ★ 复用/扩展 |
| `knowledge.py` (~127 行) | 知识访问入口（接口隔离） | 复用 |

---

## 2. 知识管理与知识治理完整功能清单

### 2.1 前端「知识管理」（tab-kb，4 个子模块）

| 子模块 | 功能 |
|---|---|
| 表结构信息 | 表注释覆盖统计；表列表搜索；表/字段注释编辑；"只看已授权表"开关；字段注释（含枚举取值提示） |
| 指标信息 | 指标覆盖统计；指标增删改查；关键词/业务口径/语义描述/依赖表/开放状态；停用指标 AI 不可见 |
| 数据字典 | 字典覆盖统计；从字典源表导入 / 从任意业务表导入（选编码列+含义列）；手写字典；适用字段/关键词/取值列表 |
| 语义注册表 | 业务实体（P2）：实体名/主表/粒度/别名/业务说明/启用；关联规则（P2）：左右实体/基数/join_sql/预聚合/推荐关联字段；业务文档（P4）：标题/正文/关键词 |

### 2.2 前端「知识治理」（tab-gov，5 个子模块）

| 子模块 | 功能 |
|---|---|
| 待复核 | 候选知识通过/拒绝（写 approved_at/approved_by + 审计）；各层治理状态统计 |
| 知识总览 | P0~P4 分层总览；跨全库关键词检索；高优先级不可被低优先级覆盖 |
| 知识缺口 | 字段级缺口（枚举字段缺注释）+ 表级缺口（缺表注释/缺实体）；一键 AI 补全（先落 candidate） |
| 知识录入 | 文字口径 AI 解析录入（自动判断 P1~P4）；SQL 投喂（问题+标准SQL → 提炼知识）；编辑弹窗二次确认 |
| 导出备份 | 完整/仅人工 × JSON/Markdown 导出；AI 生成内容标注"未复核" |

### 2.3 后端知识相关 API 清单（`server.py`，按域分组）

| 域 | 接口 |
|---|---|
| 指标 | `GET/PUT/DELETE /api/metrics`、`PUT /api/metrics/{name}/enabled` |
| 表 | `GET /api/tables`、`PUT /api/tables/{name}`、`GET/PUT /api/kb/table/{name}`、`POST /api/kb/table/{name}/suggest-comment` |
| 字段注释 | `PUT/DELETE /api/kb/column/{table}/{column}`、`POST /api/kb/column/{table}/{column}/suggest-comment` |
| 字典 | `GET /api/kb/dicts`、`PUT/DELETE /api/kb/dict/{name}`、`POST /api/kb/dict/import`、`POST /api/kb/dict/import-table`、`GET /api/kb/dict-sources` |
| 实体 | `GET/PUT/DELETE /api/kb/entities` |
| 关系 | `GET/POST/DELETE /api/kb/relationships`、`POST /api/kb/suggest-join` |
| 文档 | `GET/POST/DELETE /api/kb/docs` |
| AI 录入 | `POST /api/kb/ai-ingest`、`POST /api/kb/ingest-sql`、`PUT/DELETE /api/kb/ingest-item` |
| 治理 | `GET /api/kb/governance/pending`、`POST /api/kb/governance/action`、`GET /api/kb/overview` |
| 缺口 | `GET /api/kb/gaps`、`POST /api/gaps/auto-fill`、`GET /api/gaps/auto-fill/{task_id}`、`POST /api/gaps/fill-table` |
| 检索 | `GET /api/kb/search`、`GET /api/kb/priorities`、`GET /api/kb/table-columns` |
| 导出 | `GET /api/export` |
| 反馈 | `GET/POST/PUT/DELETE /api/feedback`、`POST /api/suggest`、`POST /api/suggest/apply` |

### 2.4 知识相关数据表清单（38 张中的知识主表）

| 表 | 层级 | 行数 | 说明 |
|---|---|---|---|
| `prompts` | P0 | 2 | 系统规则/角色 |
| `metrics` | P1 | 57 | 指标定义（权威口径） |
| `entities` | P2 | 52 | 业务实体 |
| `relationships` | P2 | 22 | 关联/Join 规则 |
| `table_comments` | P3 | 46 | 表业务注释 |
| `column_comments` | P3 | 233 | 字段业务注释 |
| `dict_entries` | P3 | 23 | 数据字典 |
| `kb_docs` | P4 | 10 | 业务文档 |
| `metric_constraint` | P1 派生 | 16 | 指标硬约束（派生表） |
| `semantic_registry` | 统一登记 | 443 | 统一注册表（派生投影） |
| `knowledge_history` | 审计 | 1263 | 知识变更历史 |
| `sql_experience` / `sql_experience_rules` / `sql_experience_usage` | 经验 | 165/0/1126 | SQL 经验中心 |
| `golden_cases` / `golden_case_versions` / `golden_case_verify_log` | 黄金 | 4/0/4 | 标准问题 |
| `feedbacks` | 反馈 | 5 | 用户反馈 |

---

## 3. 两个模块的功能重复项和差异项

### 3.1 重复项（需合并/明确分工）

| 重复点 | 现状 | 建议 |
|---|---|---|
| 指标编辑 vs 指标治理 | 知识管理页可直接编辑指标；治理页「待复核」对同一指标做审核 | 保留：编辑动作在管理页，状态流转在治理页，二者已指向同一资产（非副本） |
| SQL 经验 vs 标准问题 | `sql_experience`（165 行成功/失败案例）与 `golden_cases`（4 行标准 SQL）**语义重叠**，都存"标准 SQL" | 明确分工：`golden_cases`=人工核准的标准答案（评测基准），`sql_experience`=运行中沉淀的案例/失败经验 |
| `table_suggestions` vs `table_comments` | AI 草稿表注释遗留表（2 行，未启用）与正式表注释 | 废弃 `table_suggestions`，统一走 `table_comments` + `source=ai` |
| `metric_constraint` vs `metrics` 三列 | 硬约束双存储（metrics 的 required_predicates 列 + metric_constraint 派生表） | 明确唯一事实源=metrics 三列；metric_constraint 保持派生只读 |
| `sql_experience_rules` vs `prompts` 规则 | 归纳规则（0 行）与 prompts 的 sql_rules 定位重叠 | 明确：prompts.sql_rules=全局铁律；rules=从经验归纳的可发布规则 |

### 3.2 差异项（各司其职，无需合并）

| 维度 | 知识管理 | 知识治理 |
|---|---|---|
| 定位 | 「知识有什么」——内容注册表，可看可改 | 「知识缺什么/怎么用/怎么沉淀」——运营动作 |
| 核心动作 | 创建/编辑/删除/导入 | 审核/缺口扫描/录入/导出/追溯 |
| 状态视角 | 只看内容 | 只看状态流转（candidate→approved/rejected） |

**结论**：两者是同一套知识资产生命周期的不同视角，底层已共用 `config_store` 与治理字段，**不是两套独立系统**。整合重点在于"入口导航 + 信息架构"，而非"合并底层逻辑"。

---

## 4. 建议保留 / 合并 / 调整 / 废弃清单

### 4.1 保留（不重复建设）

- P0~P4 分层检索（`semantic_layer.py`）
- 7 张知识表 + 统一治理字段（status/source/stable_key/content_hash/knowledge_level）
- `semantic_registry` 统一登记 + `metric_constraint` 派生
- 治理状态准入（`governance.py`，严格模式）
- `knowledge_history` 审计
- 缺口扫描（`gaps.py`）+ AI 录入（`ai_ingest.py`）+ 反馈闭环（`suggest.py`）
- 六维安全闸门（guard + sql_validator + permission + requirement_review）
- `exporter.py` 导出（作为 OSSIE 导出的参照，保留其 JSON/MD 导出）

### 4.2 合并

- 知识管理 + 知识治理 → 统一「知识中心」入口（前端导航合并，底层不动）
- 知识总览（原"优先级"+"全局检索"已合并）→ 归入「知识资产」或「治理工作台」
- SQL 经验 + 标准问题 → 明确分工（见 §3.1）

### 4.3 调整

- 实体表增加 `related_tables`（卫星表显式登记，替代 `SATELLITE_KEEP_THRESHOLD` 阈值兜底的不确定性）
- 指标 `definition`（自由文本口径）与 `sql_template`（结构化 SQL）的关系需在 OSSIE 映射中明确（见 §8.5）
- 补全 9 条缺 `stable_key` 的指标、核对 `version` 递增机制（当前基本恒为 1）

### 4.4 废弃（建议，非强制）

- `table_suggestions` 遗留表（2 行，未启用）
- `config.db`（0 字节遗留文件）

---

## 5. 新知识中心信息架构图（建议）

> 结合现有页面与组件，**不机械新增四个独立页面**，而是在现有 15 个 tab 基础上重组。

```
知识中心（一级入口，合并原「知识管理」「知识治理」）
│
├─ 知识资产（原 tab-kb 知识管理，内容注册表）
│    ├─ 表结构信息（表/字段注释）
│    ├─ 指标信息（指标口径）
│    ├─ 数据字典
│    └─ 语义注册表（实体/关联/文档）
│
├─ 语义模型（★ 新增，OSSIE 核心）
│    ├─ 模型列表（名称/业务主题/Dataset数/Metric数/版本/审核状态）
│    ├─ 模型详情（Dataset/Field/Metric/Dimension/Relationship/AI Context/扩展属性/版本审核）
│    ├─ OSSIE 导入（上传→版本识别→校验→预览→冲突→确认）
│    └─ OSSIE 导出（按模型/业务主题→YAML/JSON→兼容性报告）
│
├─ 治理工作台（原 tab-gov 治理，运营动作）
│    ├─ 待复核（candidate→approved/rejected）
│    ├─ 知识总览（P0~P4 分层 + 全局检索）
│    ├─ 知识缺口（字段级/表级缺口 + 一键补全）
│    └─ 知识录入（文字口径 / SQL 投喂）
│
└─ 质量与反馈（原 tab-feedback 反馈处理）
     ├─ 问题反馈
     └─ 优化建议（AI 起草 → 人工拍板）
```

> 说明：现有「导出备份」「标准问题」「SQL经验」分别归入「知识资产（导出）」「治理工作台（或独立保留）」，需与用户确认是否保留「标准问题」「SQL经验」「评测看板」为独立 tab。本报告倾向**最小改动**：仅新增「语义模型」一个 tab，把「知识管理」重命名为「知识中心」下的「知识资产」，其余维持。

---

## 6. 当前知识资产数据结构关系图

```
                    ┌─────────────────────────────────────────────┐
                    │           semantic_registry（统一登记，443 行） │
                    │  stable_key / entity_type / level / status /  │
                    │  source_table / source_pk / content_hash       │
                    └───────────────▲───────────────────────────────┘
                                    │ 只读投影（DELETE+INSERT 全量重建）
   ┌──────────────┬──────────────┬───┴────┬──────────────┬──────────────┬──────────────┐
   │  prompts(P0) │ metrics(P1)  │entities(P2)│ relationships │ dict_entries │ kb_docs(P4)  │
   │  sql_rules   │  name PK     │ name PK   │  (P2) id PK   │   (P3)       │  id PK       │
   │  role        │  definition  │ table_name│  from_entity  │  field_hint  │  title       │
   │              │  sql_template│ grain     │  to_entity    │  items       │  content     │
   │              │  metric_type │ keywords  │  join_sql     │  keywords    │  keywords    │
   │              │  required_*  │ comment   │  cardinality  │              │              │
   │              │  conflict_key│ keywords  │  pre_agg_req  │              │              │
   └──────────────┴──────┬───────┴─────┬─────┴───────┬──────┴──────┬───────┴──────────────┘
                         │             │             │             │
          ┌──────────────┘   ┌─────────┘             │             │
          ▼                  ▼                       ▼             ▼
   metric_constraint   table_comments(P3)      column_comments(P3)  table_acl(白名单)
   (派生 16 行)         table_name PK           (table,column) 复合PK
                         comment                comment
                         source                 source

   关联方式：
   metrics.tables ──► 表名(JSON)
   entities.table_name ──► 主表
   relationships.from/to_entity ──► 实体名
   column_comments.(table,column) ──► 表.字段
   dict_entries.field_hint ──► 字段名
   metric_constraint.metric_id ──► metrics.stable_key
   feedbacks.related_knowledge_id ──► stable_key
   knowledge_history(entity_type, entity_id) ──► 各表主键
```

---

## 7. 现有 Semantic Layer 架构图

```
用户问题
   │
   ▼
query_understanding.understand()          # 意图/指标/实体/维度/时间/过滤
   │
   ▼
semantic_layer.retrieve_semantic()        # P0~P4 多路检索
   ├─ P0: prompts.sql_rules（始终全量注入，不可覆盖）
   ├─ P1: metrics（understanding 命中 + 向量兜底 + 实体过滤 + 度量类型过滤）
   ├─ P2: entities（主表）+ relationships（精确集合匹配，1:N 需预聚合）
   ├─ P3: table_comments/column_comments/dict_entries（混合检索 + 点名表豁免）
   └─ P4: kb_docs（向量补充，高阈值，绝不覆盖上层）
   │
   ▼
semantic_layer.resolve(ctx)               # 冲突解决 + bindings（硬约束绑定）
   ├─ 高优先级胜出；同层 manual > ai
   └─ bindings: {conflict_key: (口径, tier)}
   │
   ▼
agent._build_system_prompt()              # 唯一组装点（顺序：role→时间→P0→P1→P2→P3→P4→字典→硬约束→经验）
   │
   ▼
llm.chat() → _finalize_sql()              # 生成 + 清洗 + 补库名前缀
   │
   ▼
agent._pre_execute_check()                # 六维闸门（只读/白名单/五维校验/字段/行级/需求一致性）
   │
   ▼
agent._exec_sql()                         # 唯一执行出口
   │
   ▼
permission.mask_rows → result_validator → nl_answer
```

**关键结论**：`agent._build_system_prompt` 已经是 Semantic Context Builder 的雏形（唯一组装点 + 分段有序）。OSSIE 接入后，语义模型读取应**插入在 `retrieve_semantic` 之后、`resolve` 之前**，作为"结构化语义约束"的补充来源，而不是替换现有检索。

---

## 8. Apache Ossie 标准核实与对象映射

### 8.1 标准现状（实测核实）

| 项 | 结论 |
|---|---|
| 官方仓库 | `https://github.com/apache/ossie`（Apache 孵化项目，前身 Open Semantic Interchange/OSI） |
| 官网 | `https://ossie.apache.org` |
| 当前稳定版 | **`0.1.1`**（2025-12-11 初始发布） |
| main 分支版本 | `0.3.0.dev0`（DRAFT，官方明确"不可在生产依赖"） |
| 规范文件 | `core-spec/spec.md` + `spec.yaml` + `ossie-schema.json` + `expression_language.md` + `relational_semantics.md` |
| 官方工具 | `python/`（Pydantic v2 SDK）、`validation/`（校验）、`converters/`（已迁到 `apache/ossie-converters`：dbt/GoodData/Polaris/Salesforce） |
| 版本标识 | 根级 `version` 字段（唯一，无 `format_version`） |

**版本决策建议**：任务书要求"固定 Schema 版本或 Git Commit"。建议**固定 `0.1.1` 稳定版**，或将 `core-spec/` 锁定到某个 commit SHA；**不追 `main`（0.3.0.dev0）**。若需用到 0.3.0 的新能力（如 `nonportable_extensions`），则锁定到具体 commit 并自行内置一份 schema 副本。

### 8.2 对象映射表（含任务书假设修正）

| OSSIE 对象 | 知数AI 对应能力 | 映射难度 | 说明 |
|---|---|---|---|
| Semantic Model | 业务语义模型（**新增概念**） | 中 | 知数AI 无显式"模型"对象。建议：一个业务主题 = 一个模型（`name` + `datasets[]` 集合） |
| Dataset | 逻辑数据集 ≈ 业务实体(entities) + 主表 + 字段 | 中 | 实体只登记主表、无 primary_key、无字段清单；需"实体+表+字段注释"三者组装 |
| Field | 字段（column_comments）+ 字段表达式 | 中 | 知数AI 只有字段注释，无字段表达式/计算字段；`expression.sql` 需从注释+字典推断或留空 |
| Metric | 指标（metrics） | 中 | `metrics.definition` 是自由文本口径，`sql_template` 是结构化 SQL；OSSIE 要求结构化 `expression{sql}` |
| **Dimension** | **Field 的 `is_time` 子对象**（非独立对象） | 高 | ★ 任务书把 Dimension 当独立对象需修正。知数AI 维度仅存于 LLM 输出，未沉淀；需新增维度知识或仅映射时间维度 |
| Relationship | 关联规则（relationships） | 低 | `from_entity/to_entity/join_sql/cardinality` → `from/to/from_columns/to_columns`；注意 OSSIE 的 from_columns/to_columns 是**字段名**而非表列 |
| AI Context | keywords/doc/字段注释/字典 | 低 | 知数AI 已有同义词，映射顺畅 |
| Custom Extensions | **`nonportable_extensions`**（0.3.0 改名） | 中 | 用 `vendor_name + data` 保存 P0/P1/P2 分级、审核状态、权限、Join 基数、粒度、Golden SQL 关联、来源、版本、反馈、质量 |

### 8.3 知数AI 特有能力 → nonportable_extensions 映射

```yaml
# 导出时，知数AI 内部属性进 nonportable_extensions（data 必须为 JSON）
nonportable_extensions:
  - vendor_name: zhishu-ai
    data:
      knowledge_level: 1            # P0/P1/P2 分级
      status: approved              # 审核状态
      permissions: { ... }          # 权限控制
      join_cardinality: "1:N"       # Join 基数
      grain: "一行=一个订单"          # 数据粒度
      golden_sql_ref: "..."         # Golden SQL 关联
      source: "manual"              # 知识来源
      version: 1                    # 版本
      quality: { ... }              # 质量评估
```

### 8.4 关键映射风险

1. **Metric expression 无法无损双向**：知数AI `definition` 是自然语言口径文本，OSSIE 需要 `expression{sql: ...}`。往返时可能丢失"口径说明"或"SQL 结构"。需决策：`sql_template` 作为 `expression.sql`，`definition` 作为 `description`。
2. **Dimension 缺口**：知数AI 无独立维度对象。最小方案：仅映射时间维度（`dimension.is_time`，来源 `calendar_util` 与字段类型），地区/客户维度后续沉淀。
3. **Dataset 组装**：OSSIE Dataset 需要 `primary_key` 和 `fields[]`，知数AI 实体无主键、字段分散在 `column_comments`。需在 Adapter 内做"实体→表→字段"的组装逻辑。
4. **YAML 陷阱**：`nonportable_extensions.data` 内的标量可能被 YAML 加载器误转（`yes`→bool、`010`→int、`2024-01-01`→date）。需防御性处理。

---

## 9. OSSIE 对接需修改的后端文件 / 接口 / 数据表

### 9.1 新增后端模块（建议）

| 文件 | 职责 |
|---|---|
| `ossie_adapter.py`（**新增，独立 Adapter，禁止散落**） | Schema 版本管理、YAML/JSON 解析、标准格式校验、双向转换、扩展属性保存恢复、引用完整性校验、冲突识别、导入导出日志、兼容性报告、往返一致性测试 |
| `ossie_schema/`（**新增，内置固定版本 schema**） | 固定版本的 `ossie-schema.json` + `spec.yaml` 副本（不追 main） |

### 9.2 数据表变更（建议，只增不改）

| 表 | 变更 | 说明 |
|---|---|---|
| `semantic_models`（**新增**） | 语义模型容器：name/业务主题/datasets/version/审核状态/扩展属性 | 显式化 Semantic Model 容器 |
| `semantic_model_versions`（**新增**） | 模型版本记录 | 版本管理 |
| `ossie_import_log`（**新增**） | 导入日志 | 审计 |
| `ossie_export_log`（**新增**） | 导出日志 | 审计 |
| 7 张知识表 | **不改结构** | 复用现有治理字段 |

### 9.3 新增 API（建议）

| 接口 | 职责 |
|---|---|
| `GET/POST /api/semantic-models` | 模型列表/创建 |
| `GET/PUT/DELETE /api/semantic-models/{id}` | 模型详情/编辑/删除 |
| `GET /api/semantic-models/{id}/datasets` 等 | Dataset/Field/Metric/Dimension/Relationship 管理 |
| `POST /api/semantic-models/ossie/import` | 上传 YAML/JSON → 版本识别 → 校验 → 预览 |
| `POST /api/semantic-models/ossie/import/confirm` | 用户确认后执行导入 |
| `GET /api/semantic-models/ossie/export` | 导出 YAML/JSON + 兼容性报告 |
| `POST /api/semantic-models/ossie/validate` | 仅校验不导入 |

---

## 10. 前端需新增 / 复用 / 调整的页面和组件

| 类型 | 内容 |
|---|---|
| **新增** | 「语义模型」tab（模型列表 + 详情 + OSSIE 导入 + OSSIE 导出） |
| **调整** | 「知识管理」「知识治理」合并为「知识中心」一级入口（内部子导航保留） |
| **复用** | 审核组件（待复核的通过/拒绝）、资产详情、状态徽章、导出按钮、Loading/Empty/Error/Permission 状态处理 |
| **复用** | 现有 `kb.js`/`registry.js`/`dict.js` 的知识渲染与编辑表单 |
| **明确禁止** | 不重复开发审核系统；不用虚假数据模拟；必须走真实后端接口 |

---

## 11. 数据迁移、兼容性、安全、回滚风险

| 风险 | 说明 | 缓解 |
|---|---|---|
| SQLite 迁移 | `semantic_models` 等新表用 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN`（幂等，只增不删） | 迁移前备份 `agent_config.db`；已有 6 份 `.bak-*` 历史备份可参照 |
| 导入覆盖 | OSSIE 导入不允许未经预览覆盖现有模型 | 强制"预览→用户确认→执行"，同名冲突检测 |
| 未审核知识 | 导入后的模型默认 `candidate`，严格模式下不进检索 | 复用 `governance.visible_status` 准入 |
| SQL 安全 | OSSIE 导入的 metric expression 可能被 Agent 用作 SQL | 导入时校验 expression；Agent 侧仍过六维闸门，不绕过 |
| 往返一致性 | 内部→OSSIE→内部需语义无损 | 建立往返一致性测试（对象数/关系/表达式/扩展属性） |
| 双流水线回退 | 任务书假设的双流水线已不存在 | 单流水线内，语义模型读取接口为可插拔，异常时降级为纯 RAG（保留现有路径） |
| 导入文件安全 | 上传 YAML/JSON 需防恶意文件 | 大小限制、YAML 安全加载（禁止任意对象构造）、Schema 校验 |

---

## 12. 推荐最小改造方案及实施顺序

### 12.1 P0（本次已交付）

- [x] 只读审查（后端/前端/Semantic Layer/Agent/RAG/SQL Validator/流水线）
- [x] 输出功能清单、重复/差异、保留/合并/调整/废弃清单
- [x] 输出信息架构图、数据结构图、Semantic Layer 图、流水线图
- [x] 核实 OSSIE 标准 + 对象映射表
- [ ] **待确认**：固定 OSSIE Schema 版本（建议 0.1.1 或锁 commit）

### 12.2 P1（知识中心整合 + OSSIE 基础能力）

1. 前端：合并「知识中心」入口（知识管理→知识资产，新增「语义模型」tab）
2. 后端：新增 `ossie_adapter.py`（解析/校验/双向转换/扩展属性/冲突识别/日志/报告）
3. 后端：新增 `semantic_models` 等表 + 语义模型管理 API + OSSIE 导入导出 API
4. 前端：语义模型列表/详情 + OSSIE 导入预览 + 导出 + 兼容性报告
5. 往返一致性测试

### 12.3 P2（Agent 接入 + 评估）

1. Agent 在 `retrieve_semantic` 后接入语义模型读取（优先审核通过的语义资产）
2. 订单主题试点（从真实库取表/字段/口径）
3. 建立 Golden Dataset + Evaluation 对照（当前链路 vs 语义接入链路）

### 12.4 P3（对外标准化能力）

1. 语义模型 API + MCP 接入评估
2. 版本与兼容管理

---

## 13. 待确认事项（阻塞后续开发的决策点）

1. **双流水线前提**：任务书 §7.3 假设两条流水线，实际已收敛为单流水线。是否确认"按单流水线内语义接入"处理？（推荐）
2. **OSSIE 版本固定**：固定 `0.1.1` 稳定版，还是锁 main 的某个 commit（以获得 `nonportable_extensions` 等 0.3.0 能力）？（推荐锁 commit，因 0.1.1 的扩展机制叫 `custom_extensions`）
3. **信息架构**：是否接受"最小改动"方案（仅新增「语义模型」tab + 重命名），还是按任务书 §8.1 的四段式（知识资产/语义模型/治理工作台/质量与反馈）重组？
4. **Semantic Model 容器**：一个业务主题 = 一个模型？还是整个知数AI = 一个模型？
5. **Metric expression 映射**：`sql_template` → `expression.sql`、`definition` → `description` 的映射策略是否接受？
6. **Dimension 缺口**：是否本次先只映射时间维度，地区/客户维度后续沉淀？

---

## 附录 A：关键文件与行号索引

| 对象 | 位置 |
|---|---|
| 唯一业务流水线 | `agent.py:ask_v2`（580 行），入口 `ask`（1171 行） |
| Legacy 退役注释 | `agent.py:1171` |
| 执行前闸门 | `agent.py:_pre_execute_check`（345 行） |
| 唯一执行出口 | `agent.py:_exec_sql`（119 行） |
| P0~P4 检索 | `semantic_layer.py:retrieve_semantic`（260 行） |
| 冲突解决 | `semantic_layer.py:resolve`（506 行） |
| Prompt 组装 | `agent.py:_build_system_prompt`（429 行） |
| 知识治理准入 | `governance.py`、`config_store.get_visible_knowledge_ids`（1655 行） |
| 治理审核 | `config_store.list_pending_knowledge`（1771 行）、`set_knowledge_status`（1833 行） |
| 知识层级映射 | `config_store.KNOWLEDGE_LEVEL`（904 行） |
| 统一注册表 | `semantic_registry` 表 + `_seed_semantic_registry.py` |
| 语义上下文审计 | `semantic_builder.py:audit_context` |
| 导出 | `exporter.py:collect`（43 行） |
| Golden SQL | `golden_cases.py`、`sql_experience.py` |
| 评测框架 | `evaluation/`（runner/gate/report/evaluators） |
