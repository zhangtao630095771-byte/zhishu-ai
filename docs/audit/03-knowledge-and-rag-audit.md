# 03 · 知识库、RAG 与 Semantic Layer 审查

---

## 一、元数据与 Semantic Layer（任务书 5.1）

### 1.1 已实现的结构化能力

| 维度 | 实现 | 源码依据 |
|---|---|---|
| 表/字段 Schema | 表名+字段名+类型+注释，`column_list` 结构化列清单 | `rag.py:87-175`（`_build_tables`）；`db_clickhouse.py:66-114` |
| 字段注释三级来源 | 人工 > AI(未复核) > 数据库注释 > 表名兜底 | `rag.py:99/130`（优先级明确） |
| 指标定义结构化 | `definition`、`sql_template`、`metric_type`、`depends_metrics`、`conflict_key` | `config_store.py:393-428` |
| 指标硬约束 | `required_predicates`、`required_columns`、`forbidden_predicates` | `config_store.py:426-428`；`sql_validator.py:155-234` |
| 实体关系 | `grain`（粒度）、`cardinality`（1:1/1:N）、`pre_agg_required` | `config_store.py:589-627` |
| 分层优先级 | P0 系统规则 → P1 指标 → P2 实体关系 → P3 Schema → P4 文档，高优先不覆盖 | `semantic_layer.py:30-31` |
| 冲突解决 | 同名指标 `conflict_key` 冲突检测 + 记录 | `semantic_layer.py:489-495`（`resolve`） |

### 1.2 缺口

- **P0/P3 未显式建模**：P0 是 `prompts` 表的文本规则（`config_store.py:41-146`），P3 是 `column_list` + 权限表，没有统一的 `knowledge_level` 字段——分层靠"不同表 + 注释"隐式表达，跨层一致性靠约定而非约束。
- **元数据过期检测不完整**：有 `index_state`（`config_store.py:850`）记录重建状态与知识计数，但**无"表结构变更 vs 索引"的自动 diff 检测**；表结构变了需人工触发 `/api/index/rebuild`。
- **同义指标/口径冲突**：`conflict_key` 字段存在但**未见冲突判定消费逻辑**（`config_store.py:420` 仅存储）。

---

## 二、RAG 检索（任务书 5.2）

### 2.1 检索能力现状

| 能力 | 实现 | 源码依据 |
|---|---|---|
| 混合检索 | 关键词精确命中（长词优先）+ 向量余弦兜底 | `rag.py:480-567`（`_hybrid_retrieve`） |
| 多路检索 | 表/指标/字典/实体/文档五条线，各自阈值+top_k | `semantic_layer.py:244-474`；`rag.py:668-754` |
| 元数据过滤 | 表白名单过滤（`_filter_visible`） | `rag.py:30-59` |
| 关键词优先 | 命中关键词即不走向量，确定性高 | `rag.py:552-554` |
| 字典召回 | 独立检索线，BGE 检索指令特殊处理（`query_prefix=""`） | `rag.py:729-754` |
| 用户点名表 | LCS 最长公共子串 ≥4 字识别，豁免实体过滤 | `rag.py:639-662`（`named_tables`） |
| 字段关键词 | 人工字段注释短语命中表，等同点名 | `rag.py:614-636`（`field_keyword_tables`） |
| 实体过滤 | 摘掉"明确属于别的业务线"的表，卫星表按阈值保留 | `semantic_layer.py:412-451` |
| 指标降噪 | 按问题度量类型（ratio/amount/count）过滤无关指标 | `semantic_layer.py:110-212` |

### 2.2 缺口

- **无重排（Rerank）**：仅"关键词+向量"两路，无精排模型。向量分在"该留的 vs 噪声"上分离度差（源码注释自述 `rag.py:645-650` 实测仅差 0.024），靠"点名表/字段关键词"规则硬补。
- **无过期/冲突/低质量知识识别**：检索侧不判断知识时效性、不判断冲突、不判断质量。
- **检索贡献追踪不完整**：`route_info` 记录了命中方式（`semantic_layer.py:458`），但**无"每条检索结果对最终答案的贡献"量化**，仅日志级说明。

---

## 三、知识分级与权限（任务书 5.3）

### 3.1 分级现状（结论：部分实现，P0/P3 隐式）

| 层级 | 存储 | 强制方式 |
|---|---|---|
| P0 系统规则 | `prompts` 表 `sql_rules`/`role` | Prompt 注入 + `guard`/`sql_validator` 代码硬校验 |
| P1 指标 | `metrics` 表 | Prompt 标注 MUST + `sql_validator._check_p1` 硬约束 |
| P2 实体/关系 | `entities`/`relationships` | Prompt 标注 JOIN 铁律 + `sql_validator._check_p2` |
| P3 Schema | `column_list` | `sql_validator._check_p3` 列存在性 |
| P4 文档 | `kb_docs` | 仅 Prompt 注入"仅供参考" |

### 3.2 权限是"硬约束"还是"软提示"（关键结论）

**硬约束，不是软提示。** 权限在多层强制执行，不依赖模型自觉：
- 表级：`guard.check_tables_ex` 执行前硬校验（`guard.py:411-457`）
- 字段级：`permission.evaluate` AST 判定 DENY/MASK（`permission.py:258-412`）
- 行级：`permission.apply_row_filters` AST 改写注入过滤（`permission.py:685-741`）
- 数据源级：ClickHouse 只读账号 + 表授权（第一道防线）

### 3.3 越权风险点

- **管理员是受审计的超级用户**：字段级不做 ALLOW/MASK/DENY 限制（`permission.py:300-302`），仅记录审计。风险可控（管理员是规则发布者），但需保证管理员账号不泄露（呼应 S-02 弱密码风险）。
- **`admin.py` 无鉴权**：本地命令行直接操作配置库，无留痕（`admin.py` 全文）。
- **导出接口** `/api/export` 依赖中间件默认 admin（`server.py:119`），无额外校验——依赖中间件默认 deny 策略正确配置。

---

## 四、黄金 SQL 与反馈闭环（任务书 5.4）

### 4.1 现状（结论：黄金 SQL 能力**基本缺失**，反馈闭环**部分实现**）

| 能力 | 状态 | 依据 |
|---|---|---|
| 保存经审核 SQL | ❌ 缺失 | `config_store.py` **无 golden_sql 表**；黄金 SQL 仅以 JSONL 文件存在于 `evaluation/datasets/golden/` |
| 记录业务问题/适用条件/口径/依赖表/版本 | ❌ 缺失 | `docs/golden_dataset_schema.md` 中 `expected_sql/expected_result/version/review_status` 均标注"未提供" |
| 用户反馈审核（不直接进生产） | ✅ 部分 | `feedbacks` 表 + `submit_for_review`/`review_record` 审核状态机（`config_store.py:2724/2771`） |
| 已审核案例用于检索/评测/回归 | ⚠️ 部分 | `evaluation/reflow.py` 支持反馈回流到 golden/regression，但**需人工补期望值**，且与运行时检索无联动 |
| 识别重复/过期/冲突黄金 SQL | ❌ 缺失 | 无相关逻辑 |

### 4.2 反馈闭环已实现链路

`feedbacks`（含 `related_knowledge_id`/`regression_sample_id`/`root_cause`，`config_store.py:840-846`）→ `suggest.generate` 生成优化建议 → 人工 `apply_suggestion` → `reflow` 回流评测集。**"人工审核后才生效"的铁律已落实**：`docs/golden_dataset_schema.md:55` 明确"禁止把历史 SQL 自动认定为 Golden SQL"。

---

## 五、汇总

### 已具备能力
- 分层知识（P0~P4）+ 高优先不覆盖 + 冲突记录
- 五路混合检索 + 关键词优先 + 点名表/字段关键词豁免实体过滤
- 指标硬约束（required/forbidden predicates）+ 负向约束防"发单≠完单"幻觉
- 字段级/行级权限硬执行 + 结果脱敏 + 审计
- 反馈审核状态机 + 回流评测集

### 已具备但不完整
- 知识分级（P0/P3 未显式建模）
- 黄金 SQL（有数据集无运行时闭环）
- 元数据过期检测（有状态无自动 diff）
- 反馈闭环（回流需人工补期望值）

### 关键缺口
1. **黄金 SQL 运行时闭环缺失**（保存→检索→复用→去重→过期识别全无）
2. **无 Rerank**（向量分离度差，靠规则硬补）
3. **无知识时效/冲突/质量识别**
4. **元数据变更无自动检测**

### 最小改进方案（按性价比排序）
1. **P1：黄金 SQL 落库闭环**——新增 `golden_sql` 表（字段对齐 `docs/golden_dataset_schema.md`），审核通过的历史记录一键入库，检索时作为 P2 补充上下文，检索贡献可追溯。
2. **P2：元数据变更检测**——`fetch_schema` 时对比 `index_state` 记录的表/字段指纹，变更自动告警 + 提示重建。
3. **P3：指标约束显式化**——所有 enabled 指标强制声明 `required_predicates` 或"无约束"。
4. **P4：Rerank 实验**——先离线用 cross-encoder 验证对"该留 vs 噪声"的分离度提升，再决定是否上线。

### 对取数正确率的预期影响
- 黄金 SQL 闭环：预计可修复"同义改写/易混淆指标"类错误（golden 集 g013/g019 等），预估 +3~5pp。
- 元数据变更检测：避免"表结构变了 SQL 还按旧字段跑"的静默错误，预估减少线上错误率。
- 指标约束显式化：直接拦截口径错误，预估 +2~4pp。
- **验证方式**：每项改造后跑 `evaluate.py --dataset golden --regression`，对比 `overall_accuracy` 与各维度 delta。
