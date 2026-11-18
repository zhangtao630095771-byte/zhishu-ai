# 知数AI · SQL 经验中心 现状审计报告

> 审计时间：2026-10-29
> 审计对象：当前工作区源码（`agent.py` / `semantic_layer.py` / `rag.py` / `config_store.py` / `server.py` / `sql_validator.py` / `guard.py` / `permission.py` / `evaluation/`）
> 审计方式：**读源码 + 查真实库（`agent_config.db`，160 条取数记录 / 5 条反馈）**，不含任何文档推测。
> 结论先行：**「经验沉淀」这一环是当前系统唯一完全没有落地的环节** —— 数据在，但没有任何代码把历史 SQL 读回去影响下一次生成。

---

## 0. 结论摘要

| 维度 | 现状 | 判定 |
|---|---|---|
| SQL 生成/检索/校验/执行/自愈链路 | 完整，且有统一执行出口与统一校验闸门 | ✅ 可直接复用，**不要重写** |
| 取数历史（`query_records`） | 160 条，含问题/SQL/命中表指标/结果预览/查询理解/审核状态 | ✅ 可作为经验原料 |
| 审核状态 | `none/pending/approved/rejected` 四态，带审核人/时间/意见 | ✅ 可复用为「分类审核」 |
| **结果验证状态** | **不存在**。`approved` 被默认为「可用」，`ok` 只表示「SQL 跑通了」 | ❌ 核心缺口（必须新增，且与审核解耦） |
| 历史 SQL 参与检索 | **完全没有**。全库 grep `示例/样例/few/example` 在 `prompt.py` 零命中 | ❌ 核心缺口 |
| 失败经验 | 无独立载体；`query_records` **没有 error 字段**，执行失败原因根本没存 | ❌ 核心缺口 |
| 人工修正记录 | 无。`feedbacks.note` 是自由文本，未与原始 SQL 关联 | ❌ 核心缺口 |
| 归纳规则/模板 | 无独立载体；`kb_docs` 是人写口径文档，不是从案例归纳且带出处的规则 | ❌ 核心缺口 |
| 知识治理（状态机） | 六层知识 `candidate/approved/rejected` + `knowledge_history` + `audit_log` | ✅ 约定可直接沿用 |
| Semantic Layer / Multi-RAG | P0~P4 分层检索 + 关键词/向量混合 + 实体与业务线过滤 | ✅ 复用，经验作为「另一条检索线」接入 |
| SQL Validator / 只读护栏 / 表白名单 / 字段级权限 | 五维校验 + 唯一执行出口 + ACL + ALLOW/MASK/DENY | ✅ 复用，经验**不得绕过** |
| 评测体系 | 94 条用例 / 8 个数据集 / runner + 12 维判定 + gate + 消融 | ✅ 可承载 A/B/C/D 实验 |

**一句话结论**：不需要新建生成系统，也不需要新建一套 RAG。需要的是**在「审核通过」之外补一层「结果验证」，把历史案例做成可检索、可追溯、可撤销的经验对象，并接到现有 prompt 组装与执行前校验的两个既有缝隙上**。

---

## 1. 真实调用链（源码核对，非文档）

### 1.1 主链路（V2，生产默认）

```
server.py:717  api_ask
  └─ agent.py:907  ask()                    ← 仅白名单内瞬时故障才降级 legacy
       └─ agent.py:601  ask_v2()
            ① semantic_layer.enrich_index            （语义索引增强，缓存于 index["_semantic"]）
            ② query_understanding.understand         （意图 + 指标 + 实体）
            ③ semantic_layer.retrieve_semantic:257   （P0~P4 多路检索）
            ④ rag.match_disabled_metric              （未开放指标 → 立即拒绝）
            ⑤ 兜底：p3_tables 与 p1_metrics 全空 → 拒答（防编造）
            ⑥ semantic_layer.resolve:494             （冲突解决 + 分层文本）
               semantic_layer.build_prompt_parts:556 （role → 时间口径 → P0→P1→P2→P3→P4）
               + calendar_util.build_time_context     （日期/节假日口径）
               + rag.retrieve_dicts → prompt._build_dict_block
               + prompt.build_hard_constraints_block  （同名字段跨表串味硬约束）
            ⑦ llm.chat → agent._finalize_sql:135     （清洗 + 补库名前缀）
            ⑧ for attempt in 1..MAX_RETRY:
                 agent._pre_execute_check:517         （★ 统一闸门：只读 + 表 ACL + 五维校验 + 字段级权限 + 行级改写）
                 失败且非 hard → 回灌模型自愈（保留原问题与上一版 SQL）
               用尽仍未通过 → 拒绝执行（fail-closed）
            ⑨ agent._exec_sql:114                    （★ 唯一执行出口，拒答/非查询语句绝不可能执行）
                 执行异常 → 再自愈一轮，且自愈 SQL 必须重新过 ⑧
            ⑩ permission.mask_rows                   （结果脱敏）
            ⑪ result_validator.validate_result       （结果合理性 warning）
            ⑫ nl_answer.answer                       （自然语言结论）
            ⑬ 返回 dict（含 trace / validation / understanding / perm_decision）
```

### 1.2 落库与审核链路

```
server.py:791  config_store.add_query_record      ← 每次取数自动落一条（服务端落，前端无法绕过）
server.py:817  config_store.save_result_snapshot  ← 成功且有行 → 结果快照（≤2000 行）
server.py:826  _audit_permission                  ← 权限判定审计（SQL 字面量脱敏）

server.py:2630 PUT /api/review/{rid} → config_store.review_record:3147
     approved / rejected + reviewer + review_note + reviewed_at
     ★ rejected 强制要求写原因（server 层校验）
```

### 1.3 反馈链路

```
server.py:2201 POST /api/feedback → config_store.add_feedback:2399
     字段：question / sql / result / exec_ok / reason(枚举 16 种) / detail /
           tables / metrics / dicts / status / note /
           related_knowledge_id / regression_sample_id / root_cause
server.py:2237 PUT /api/feedback/{fid} → update_feedback（可挂知识 ID 与回归样例 ID）
suggest.py:177  generate()  ← 把未处理反馈汇总成「改知识」的 AI 建议
```

### 1.4 评测链路

```
evaluation/runner.py:184  run()      → 加载数据集 → agent.ask(index, q) → 12 维判分 → aggregate
evaluation/checks.py                 → ok/abstain/tables/metrics/sql_contains/time/result/format/grounding/hallucination
evaluation/gate.py                   → 回归门禁（对比 baseline）
evaluation/experiments/ablation/run_ablation.py → monkeypatch 禁用模块做消融
数据集：golden 35 / regression 14 / boundary 13 / abstention 10 / paraphrase 9 /
        counterfactual 6 / adversarial 6 / inbox 1 = 94 条
★ 现状：无 expected_sql / expected_result → result_accuracy 恒为 null（见 docs/golden_dataset_schema.md §5）
```

---

## 2. 数据模型现状（真实字段，逐表核对）

### 2.1 `query_records`（160 行）—— 经验的唯一原料

| 字段 | 含义 | 对经验中心的价值 |
|---|---|---|
| `id` / `username` / `created_at` | 主键 / 提问人 / 时间 | 追溯来源 |
| `question` | 原问题 | 语义检索主键 |
| `sql` | 最终执行的 SQL（含行级改写后的版本） | 案例主体 |
| `ok` | 是否执行成功 1/0 | **仅代表「跑通了」** |
| `tables` / `metrics` / `dicts` | JSON：命中表/指标/字典 | 结构化特征，用于适用性判定 |
| `columns` / `row_count` / `result_preview` | 结果形状与前几行 | 验证证据（弱） |
| `answer` | 自然语言结论 | 审核参考 |
| `review_status` / `review_note` / `reviewer` / `reviewed_at` | 审核四件套 | 直接继承 |
| `submitted_at` / `remind_count` / `reminded_at` | 审核流程 | 与本任务无关 |
| `info` / `attempts` / `pipeline` / `understanding` | 检索说明 / 重试次数 / 链路标记 / 查询理解 | 诊断与归因 |
| **缺失** | **`error`（执行错误原因）**、**修正 SQL**、**结果验证状态** | ❌ 必须补 |

### 2.2 真实数据分布（本次实测）

```
review_status × ok
  approved × ok=1 :   1 条      ← 唯一「审核通过且跑通」的样本
  approved × ok=0 :   1 条      ← ★ 审核通过了，但这条 SQL 根本没执行成功
  rejected × ok=1 :   1 条
  pending  × ok=1 :   2 条
  none     × ok=0 :  48 条      ← 失败样本，且没有 error 字段可归因
  none     × ok=1 : 107 条      ← 未提交审核的成功样本（占 67%）
```

**三条必须写进设计的结论：**

1. **`approved` 与「结果正确」在现有数据里已经出现反例**（id=163：`review_status=approved`、`ok=0`）。所以「审核通过 ⇒ 可作黄金 SQL」在任何情况下都不成立，必须在代码层用硬规则拦住，而不是靠流程约定。
2. **67% 的成功取数从未提交审核**（`none × ok=1` = 107 条）。因此「只有 approved 才能进经验」会让经验库长期空转；需要允许**未提交审核的案例先入库、但只作低可信候选**。
3. **48 条执行失败没有任何错误原因**。历史失败案例的最大价值（「为什么会错」）在现有库里已经丢了，只能从现在起补 `error` 字段；对存量只能标记为「待分析」，**不许 AI 反推成已确认事实**。

### 2.3 `feedbacks`（5 行）

字段见 1.3。`reason` 是 16 项固定枚举（`FEEDBACK_REASONS`），与任务书要求的 13 类失败类型**高度重合**，可直接做映射表，不需要另造一套分类。

现状：`status` = resolved 3 / pending 2；`related_knowledge_id` 与 `regression_sample_id` 字段已存在但**基本为空**（反馈到知识的链路建了没通）。

### 2.4 `kb_docs`（10 行）与知识治理

- `kb_docs.status`：approved 9 / candidate 1；`source`：manual 9 / ai 1。
- 六层知识（metrics/entities/relationships/dict_entries/table_comments/column_comments）+ `kb_docs` 均已有 `status` / `approved_at` / `approved_by` / `source` / `version` 等治理列。
- `governance.visible_status()` 是**唯一**的可见性判定入口；`config_store.get_visible_knowledge_ids()` 供 `rag.build_index` 过滤。
- `knowledge_history` 390 条、`audit_log` 452 条 —— 变更审计已成型。

**可复用约定（经验中心必须对齐，不许另立一套）**：
`status ∈ {candidate, approved, rejected, revoked}`；`source ∈ {manual, ai}`；每次人工裁决写 `knowledge_history` + `audit_log`。

### 2.5 `index_state`

只有一行 `rag_index`（tables/metrics/dicts/disabled_metrics + built_at）。**没有任何 SQL 案例的索引状态**。经验索引要么并入同一次重建，要么自带状态行。

---

## 3. 已有能力 / 待补齐能力清单

### 3.1 已有、必须复用（不要重复建设）

| 能力 | 实现位置 | 复用方式 |
|---|---|---|
| 统一执行出口（拒答/非查询绝不可执行） | `agent._exec_sql:114` | 复用验证/复用时**只走这里** |
| 统一执行前闸门（只读+ACL+五维+字段级+行级） | `agent._pre_execute_check:517` | 经验 SQL 复用前**必须重新过闸** |
| 五维 SQL 校验 | `sql_validator.validate_sql_ex:505` | 不改 |
| 只读/表名解析护栏 | `guard.sql_guard:151` / `guard.check_tables_ex:411` | 不改 |
| 字段级权限 ALLOW/MASK/DENY | `permission.evaluate` / `apply_row_filters` / `mask_rows` | 经验召回**先过权限**，不过权限的案例不得进 prompt |
| 分层 Prompt 组装 | `semantic_layer.build_prompt_parts:556` | 经验块**追加**在 P2 之后、明确「不得覆盖 P0~P2」 |
| 混合检索（关键词+向量） | `rag._hybrid_retrieve:508` + `rag.cosine_similarity:497` | 复用 embedding 模型与余弦，经验用独立打分（表/指标/可信度参与排序） |
| 知识状态机与历史 | `governance.visible_status` / `config_store.set_knowledge_status:1608` / `knowledge_history` / `audit_log` | 完全沿用约定 |
| 评测与消融 | `evaluation/runner.py` / `checks.py` / `gate.py` / `run_ablation.py` | 新增 A/B/C/D 组合开关，不改 runner |
| 结果快照 | `query_result_snapshots` + `config_store.get_result_snapshot` | 作为「结果验证」的证据之一 |

### 3.2 待补齐（本次工作范围）

| # | 缺口 | 影响 | 优先级 |
|---|---|---|---|
| G1 | 无「结果验证状态」，与审核状态混为一谈 | 未经业务确认的 SQL 会被当成可信答案 | **P0** |
| G2 | 历史 SQL 完全不参与检索/生成 | 160 条经验零复用，同类问题重复犯错 | **P0** |
| G3 | 失败经验无载体，执行错误原因未持久化 | 无法做错误预防，无法归因 | **P0** |
| G4 | 原始 SQL 与修正 SQL 无关联 | 学不到「错在哪、怎么改」 | **P0** |
| G5 | 无归纳规则/模板载体（带出处、版本、生效状态） | 无法把个案沉淀成可复用规则 | P1 |
| G6 | 无检索/复用日志（召回什么、为什么没用） | 效果无法归因，误召回无法定位 | **P0** |
| G7 | 无经验管理页面 | 运营无法审核/发布/撤销 | P2 |
| G8 | 评测无 A/B/C/D 能力开关 | 无法量化收益 | P2 |
| G9 | `query_records` 缺 `error` 字段 | 新失败案例也无法归因 | **P0** |

---

## 4. 目标数据模型与状态流转

### 4.1 三张新表（不重复造表，不复制 `query_records`）

**`sql_experience`（案例主表，成功/失败/修正统一）**
一个案例 = 一次「问题→SQL」的具体事实，`kind` 区分三类。**通过 `source_type + source_id` 反向引用原始记录**，不复制结果摘要。

**`sql_experience_rules`（归纳规则/模板）**
由多条案例归纳出的规则，`source_case_ids` 是硬约束（必须能回指案例），`status` 决定是否进检索。

**`sql_experience_usage`（使用与验证事件）**
每次召回/采用/放弃/复用/验证/冲突各一条，用于「复用次数、误召回率、错误复发率」的统计。

### 4.2 三个状态**必须彼此独立**

```
review_status（分类审核，人看的是「这条 SQL 口径对不对」）
    none / pending / approved / rejected
                      ↑ 复用现有审核，不新增语义

verify_status（结果验证，人/机器看的是「结果是不是真的对」）
    unverified      未验证
    reexec_ok       重跑一致（机器证据：重新执行 + 与快照比对）
    manual_confirmed 人工确认业务含义
    result_verified  人工确认 + 机器证据（最高）
    verify_failed   验证不通过（自动降级，禁止正向召回）

trust_level（能否作为「正向答案参考」）
    candidate → golden_candidate → golden → revoked
```

**硬规则（写在代码里，改配置也改不掉）**

1. `review_status=approved` **且** `ok=0` → `trust_level` 永远只能是 `candidate`。
2. `verify_status ∈ {unverified, verify_failed}` → **永远不能是 `golden`**。
3. `golden` 需要同时满足：`approved` + `ok=1` + `verify_status ∈ {manual_confirmed, result_verified}` + `golden_published=1`。
4. 任何 `status=revoked` 或 `status=stale` → 立即退出正向召回（但仍保留在失败/诊断检索里）。
5. 失败案例**永远不进正向召回**，只在「错误预防 / 生成后检查 / 修正建议」三个位置被检索。

### 4.3 生命周期

```
取数记录 ──导入──> 原始案例(candidate) ──人工分类审核──> approved / rejected
                          │
                          ├──机器重跑──> reexec_ok ──人工确认──> manual_confirmed
                          │                                   └─> result_verified
                          └──> golden_candidate ──发布──> golden ──使用反馈──> 复核
                                                              └──> revoked / stale
多条案例 ──AI 归纳──> 规则候选(draft) ──人工确认/评测──> approved ──> revoked
```

---

## 5. 最小改造方案（改动面清单）

| 文件 | 改动 | 风险 |
|---|---|---|
| **新增** `sql_experience.py` | 领域逻辑：迁移、导入、状态机、验证、检索排序、Prompt 块、失败检查、规则提炼、使用日志 | 低（新文件） |
| **新增** `migrate_sql_experience.py` | 独立迁移 + `--rollback`，可在不动服务的情况下执行 | 低 |
| `config_store.py` | ① `init_store` 末尾追加 `_migrate_sql_experience(cur)`（幂等）；② `query_records` 增 `error` 列 | 中（DDL 只增不删） |
| `config.py` | 新增 `EXP_*` 开关与排序权重（默认值可解释、可用环境变量覆盖） | 低 |
| `agent.py` | ① `ask_v2` 中检索经验并追加 Prompt 块；② 生成后失败经验检查；③ 结果里带 `experience` 字段；④ `_result` 增加透传 | **中**（唯一改到主链路的文件，必须零回归） |
| `server.py` | `/api/exp/*` 管理接口（列表/详情/审核/验证/发布/撤销/导入/提炼/使用日志）；`/api/ask` 返回体带 `experience` | 低 |
| `frontend/index.html` | 新增「SQL经验」页（成功/失败/修正/规则四视图） | 低 |
| `test_sql_experience.py` | 状态机、适用性判定、排序、失败检查、来源校验等纯逻辑单测 | 低 |
| `evaluation/experiments/experience/run_experience_eval.py` | A/B/C/D 四组对照 | 低 |

**设计上的三个「不做」：**

1. **不做「一条 SQL 全量重写」**：经验只作为 Prompt 的一段参考 + 执行前的检查清单，SQL 仍由现有链路生成、现有闸门校验。
2. **不做「智能体自动发布规则」**：AI 只能产出 `draft`，必须人工确认或评测通过才 `approved`。
3. **不做「历史数据自动升级为已验证」**：导入时 `verify_status` 一律 `unverified`，`trust_level` 一律 `candidate`。

---

## 6. 风险、兼容性与回滚

| 风险 | 说明 | 缓解 |
|---|---|---|
| Prompt 变长导致成本/延迟上升 | 每轮最多 2 条成功案例 + 3 条失败模式（可配） | 条数上限 + 分数阈值 + 无匹配时不注入（零开销） |
| 经验 SQL 过时（表结构/口径变了） | 把错误写法教给模型 | ① 复用前重新过闸门；② 每次召回记录 `last_used_at`；③ 提供 `stale` 标记与影响面查询；④ 失败经验与成功经验分开管理 |
| 误召回（同类问题但口径不同） | 最危险的失败模式 | 适用性硬校验（表集合/指标交集/粒度）+ 低于阈值不注入 + 拒绝原因写日志 |
| 绕过权限 | 历史 SQL 含无权表 | 召回前用 `allowed_tables` + `permission.evaluate` 过滤；**注入的是文本，执行仍走统一闸门** |
| 主链路回归 | `agent.py` 是唯一改到生产链路的文件 | 所有新逻辑包在 `try/except` + `config.EXP_ENABLED` 开关；异常一律降级为「无经验」，不阻断取数 |
| 迁移破坏现有数据 | 只 `CREATE TABLE IF NOT EXISTS` + `ALTER TABLE ADD COLUMN` | 迁移前自动备份 `agent_config.db`；回滚脚本只删新建对象/新增列 |

**回滚方案**：`migrate_sql_experience.py --rollback`（删 3 张新表 + `query_records.error` 列）+ `config.EXP_ENABLED=0`（经验逻辑整体短路，行为回到现状）。两步都可独立执行。

---

## 7. 阶段实施清单

| 阶段 | 内容 | 验收 |
|---|---|---|
| **P0** | 建表/迁移、存量导入（保留原状态）、成功/失败/修正分类、结果验证、成功案例接入检索、失败经验接入检查、使用日志、结果验证状态与审核状态解耦 | 160 条存量按真实状态正确识别；`approved+ok=0` 不进正向召回；无经验时行为与现状一致（回归通过） |
| **P1** | 按主题分组去重、AI 归纳候选规则（带出处校验）、冲突检测、规则发布/撤销/版本 | 每条规则都能回指原始案例；AI 不能自动发布 |
| **P2** | 管理页面、A/B/C/D 评测、权重调优、过期识别与复核 | 有量化对照结果，或明确列出「暂不可测」及原因 |

---

## 8. 本轮不解决（明确写清，避免验收争议）

1. **存量 48 条失败案例没有错误原因** → 只能标 `待分析`，`root_cause_confirmed=0`；不反推。
2. **`result_accuracy` 仍不可自动计算** → 94 条用例无 `expected_sql/expected_result`，评测报告只能给「可执行率/召回率/校验通过率/启用经验后的差值」，业务正确率需人工抽查。
3. **跨库 SQL 方言差异** → 经验库记录 `dialect`，非当前方言的案例只作参考、不参与精确复用。
4. **DataEase 类 BI 联动** → 与本任务无关，不引入。
