# 知数AI · SQL 经验中心 设计与实施方案

> 版本：v1（P0 已落地，P1 机制已落地待数据积累，P2 评测通路已打通）
> 前置阅读：`docs/sql_experience_audit.md`（现状审计，所有结论均来自源码与实际库）

---

## 1. 设计目标与非目标

**目标**：让「审核通过 + 结果验证」的 SQL 能被后续同类问题复用；让「被驳回 / 执行失败 / 结果错」的 SQL 变成可检索的反例；让「人工修正」形成对照样本；让「多条案例」能归纳成带出处的规则。

**非目标**（明确不做，避免重复建设）：

| 不做 | 原因 |
|---|---|
| 重写 SQL 生成链路 | 现有 V2 链路（语义路由 → 分层 Prompt → 五维校验 → 唯一执行出口 → 自愈）已经是成熟资产 |
| 新建一套向量库 / RAG 框架 | 复用 `rag` 的 embedding 模型与余弦相似度，经验只是「另一条检索线」 |
| 自动执行历史 SQL | 复用前必须过现有闸门；默认只作为 Prompt 参考 |
| AI 自动发布规则 | AI 只能产出 `candidate`/`draft`，发布需人工 |
| 把历史数据自动标成"已验证" | 导入一律 `unverified`，升级需重跑验证或人工确认 |

---

## 2. 目标架构

```
                     ┌──────────────────────────── 取数链路（现有，未改结构） ────────────────────────────┐
用户问题 ──► 查询理解 ──► P0~P4 语义检索 ──► resolve ──► Prompt 组装 ──► LLM 生成 ──► 统一闸门 ──► 执行 ──► 结果校验/解读
                              │                │              ▲                │            ▲
                              │                │              │                │            │
                              └──────┬─────────┘       ③ 经验参考块      ④ 失败经验检查    │
                                     │              （成功案例/规则）    （确定性比对）    │
                                     ▼                                                  │
                        ┌──────────────────────── SQL 经验中心（新增） ─────────────────────┐
                        │  retrieve()  ：适用性硬校验 → 排序评分 → 决策（复用/参考/拒绝）    │
                        │  build_prompt_block()：带可信度与验证状态的参考块（不得覆盖 P0~P2）│
                        │  check_sql() ：指纹相同 / 命中历史错表 / 缺状态条件 → 告警或定向修 │
                        └───────────────┬───────────────────────────────┬─────────────────────┘
                                        │                               │
                          ┌─────────────▼─────────────┐   ┌─────────────▼──────────────┐
                          │ sql_experience（案例）     │   │ sql_experience_rules（规则）│
                          │ 成功 / 失败 / 修正         │   │ 指标/选表/实体/JOIN/模板/错 │
                          └─────────────▲─────────────┘   └─────────────▲──────────────┘
                                        │                               │
        导入（保留原状态）              │           AI 归纳（只产 candidate）│
   query_records ───────────────────────┘                               │
   feedbacks    ───────────────────────┐                                │
                                       ▼                                │
                        ┌──────────────────────────────┐                │
                        │ 人工：分类审核 / 结果验证 /  │────────────────┘
                        │ 发布黄金 / 撤销 / 补修正     │
                        └──────────┬───────────────────┘
                                   │ 每次召回/采用/放弃
                                   ▼
                        sql_experience_usage（效果归因：命中率、误召回、复发）
```

**关键接线点（只有两处改到主链路）**：

1. `agent.ask_v2` 在 Prompt 组装末尾追加 `exp_match.prompt_block`（位置在 P0~P2、字典、硬约束之后）；
2. `agent.ask_v2` 在「校验通过、执行之前」调用 `sql_experience.check_sql()` 做确定性检查。

其余全部是新增文件与新接口，不改既有行为。

---

## 3. 数据模型

### 3.1 `sql_experience`（案例）

成功 / 失败 / 修正三类共用一张表，用 `kind` 区分。**不复制 `query_records` 的结果摘要**，只用 `source_type + source_id` 反向引用，避免两份数据打架。

| 分组 | 字段 | 说明 |
|---|---|---|
| 身份 | `id` `case_no` `kind` `source_type` `source_id` | 人可读编号 + 来源可追溯 |
| 语义 | `question` `question_norm` `intent` | 归一化问题用于「同一问题」判定 |
| SQL | `sql_raw` `sql_norm` `sql_hash` `dialect` | 指纹用于「与历史错 SQL 完全相同」的硬判断 |
| 结构 | `tables_json` `metrics_json` `dicts_json` `entities_json` `rel_json` `grain` `time_field` `filters` | 适用性判定与排序的输入 |
| 审核 | `review_status` `reviewer` `review_note` `reviewed_at` | 与既有审核语义一致 |
| 验证 | `verify_status` `verify_method` `verify_evidence` `verified_by` `last_verified_at` | **独立于审核**，证据以 JSON 留存 |
| 资格 | `trust_level` `golden_published` `status` `version` | `candidate/golden_candidate/golden/revoked` |
| 失败 | `failure_type` `failure_symptom` `root_cause` `root_cause_confirmed` | `root_cause_confirmed` 只能人工置 1 |
| 修正 | `corrected_sql` `correction_note` `correction_of` | 修正案例回指原失败案例 |
| 关系 | `dup_of` | 重复案例挂到规范案例上（去重但保留引用） |
| 使用 | `reuse_count` `reuse_success` `reuse_fail` `last_used_at` | 由使用日志累加 |

### 3.2 `sql_experience_rules`（规则/模板）

`layer ∈ {metric_rule, table_selection, entity, join_rule, sql_template, error_pattern}`；`source_case_ids` 是**硬约束**（无来源不允许发布）；`conflict_note` 非空时禁止直接发布（需 `force` 显式确认并留痕）；`version` + `supersedes` 支持迭代。

### 3.3 `sql_experience_usage`（事件）

`event ∈ {recall, skip, verify, reuse, conflict}`；`skip` 按「原因」聚合记录（一次查询可能挡掉 30+ 候选，逐条记会把表撑爆）。这是「黄金命中率 / 误召回率 / 错误复发率」的唯一数据来源。

---

## 4. 状态流转（三层独立）

```
review_status    none ──提交──► pending ──审核──► approved
                                      └────────► rejected（必须写原因）

verify_status    unverified ──机器重跑──► reexec_ok
                            └──人工确认──► manual_confirmed ──再补机器证据──► result_verified
                            └──不一致/失败──► verify_failed

trust_level      candidate ──(approved + ok + verify≠unverified)──► golden_candidate
                           ──(+人工确认 +显式发布)──► golden
                           ──(撤销/失效)──► revoked
```

`trust_level` **不落人工输入**，全部由 `compute_trust()` 计算。四条硬规则：

1. `kind != success` → 永不 golden；
2. `exec_ok=0` → 永不 golden（实测库中 id=163 就是「审核通过但没跑通」）；
3. `verify_status ∈ {unverified, verify_failed}` → 永不 golden；
4. `golden` 必须 `golden_published=1`（显式发布动作）。

---

## 5. 检索与复用

### 5.1 流程

```
解析问题（已有 understanding/resolved）
  → 取候选池（SQL 侧过滤：kind/status/approved/exec_ok/trust）
  → 适用性硬校验（表权限、表交集、指标交集、方言、状态）
  → 打分排序（sim / table / metric / trust / verify / recency）
  → 阈值过滤 + TopN
  → 决策：direct_reuse（仅精确同问题 + 正式黄金 + 开关打开）/ reference / none
  → 组装 Prompt 块 / 失败检查清单
  → 写使用日志（含被拒绝的原因）
```

### 5.2 打分与权重

`score = 0.45·sim + 0.15·table + 0.20·metric + 0.12·trust + 0.05·verify + 0.03·recency`

- 权重的物理含义写在 `config.py` 注释里，可用 `EXP_W_*` 环境变量覆盖；
- **这是初始启发式**，最终值应由 `run_experience_eval.py` 的对照实验定；未做权重扫描前，不声称它是最优；
- 排序只决定「谁排前面」，**能不能用由适用性硬校验决定**（两者分离，防止「分数高就硬塞」）。

### 5.3 复用规则落地

| 情形 | 决策 | 实现 |
|---|---|---|
| 问题完全相同 + 正式黄金 + 开关开 | `direct_reuse` | 直接采用其 SQL 作为候选，**仍走统一闸门**；默认关 |
| 高度匹配（分数 ≥ 阈值） | `reference` | 作为 Prompt 参考，要求按本问题口径改写 |
| 部分匹配 / 方言不同 | `reference`（标记仅参考） | 同上，`reference_only=True` |
| 指标口径不一致 / 表无交集 / 无权限 | 拒绝 | 记 `skip` + 原因，不进 Prompt |
| 失败经验 | 只做预防与检查 | 单独一节，明示「禁止作为答案参考」 |
| 未验证案例 | 不进正向召回 | SQL 侧已过滤 |

---

## 6. 失败经验的三个用法（都不是「当答案」）

1. **生成前**：把「同类表/指标的历史错误模式」写进 Prompt，标注「根因待分析 / 已确认」；
2. **生成后**：`check_sql()` 做三条确定性判断——SQL 指纹与历史错 SQL 相同（error）、命中历史错表而未用正表（warning）、修正写法里的状态字面量缺失（warning）；
3. **修正**：`EXP_FAIL_REPAIR=1` 时对 error 级命中做一轮定向修复，修复版**必须重新过闸门**，不通过则保留原 SQL。

默认 `EXP_FAIL_REPAIR=0`：多一次 LLM 调用且会改变现有重试行为，先由评测验证收益再考虑默认开。

---

## 7. 安全边界

| 风险 | 措施 |
|---|---|
| 经验成为越权通道 | 召回阶段就按 `allowed_tables` 过滤；注入的是文本，执行仍走 `_pre_execute_check` |
| 历史 SQL 过时 | 复用前重过闸门；`affected_cases()` 支持按表/指标查影响面；`mark_stale()` 退出召回 |
| 经验压过权威口径 | Prompt 结构：P0→P1→P2→P3→P4→经验块，且标题写明「不得覆盖」 |
| AI 幻觉出处 | `mine_rules` 校验 `source_case_nos` 必须存在于本次输入，否则丢弃该条 |
| 自动升级 | `compute_trust` 硬规则 + `promote_case` 二次校验（页面绕不过） |
| 迁移破坏数据 | 只 `CREATE TABLE IF NOT EXISTS` / `ADD COLUMN`；迁移前自动备份；`--rollback` 只删新建对象 |

---

## 8. 运维与开关

| 环境变量 | 默认 | 作用 |
|---|---|---|
| `EXP_ENABLED` | 1 | 总开关；置 0 = 行为回到接入前（一键回滚） |
| `EXP_SUCCESS_RECALL` | 1 | 成功案例参考（B/C/D 组对照用） |
| `EXP_FAIL_PREVENT` | 1 | 失败经验预防 + 生成后检查 |
| `EXP_FAIL_REPAIR` | 0 | 命中后自动修一轮 |
| `EXP_RULES` | 1 | 已发布规则参与检索 |
| `EXP_EXACT_REUSE` | 0 | 精确复用（不经过模型） |
| `EXP_MAX_CASES/FAILURES/RULES` | 2/3/2 | 注入条数上限（控制 token） |
| `EXP_MIN_SCORE` 等 | 0.55/0.50/0.55 | 分数阈值 |

迁移与回滚：

```bash
.venv/bin/python migrate_sql_experience.py --dry-run     # 只看计划
.venv/bin/python migrate_sql_experience.py --backup --records   # 迁移 + 导入存量
.venv/bin/python migrate_sql_experience.py --rollback    # 回滚（只删新建表）
```
