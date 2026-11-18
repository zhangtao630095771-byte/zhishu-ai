# SQL 经验中心 · 交付报告

> 对应方案：《知数AI SQL经验包与持续学习机制建设方案》§12 交付物 / §13 验收标准
> 完成时间：2026-10-29　范围：P0 闭环 + P1 归纳机制 + P2 评测通路

---

## 0. 一句话结论

P0 的空环已经**真实跑通**（不是演示）：真实数据库里 165 条历史记录被分类入库，一次真实取数召回到了 1 条黄金候选并写入了使用日志；

**但有 3 项能力只是"骨架已通、结论未出"**：AI 归纳出的规则 0 条（还没跑过真实归纳）、评测只有 6 条样本（不具统计意义）、最终业务结果正确率**无法计算**（用例缺 `expected_sql` / `expected_result`）。这三项在下方都有明确标注，没有用估算值填。

---

## 1. 交付物对照表（方案 §12 的 11 项）

| # | 要求交付物 | 状态 | 位置 |
|---|---|---|---|
| 1 | 现状审计报告 | ✅ 完成 | `docs/sql_experience_audit.md`（295 行，含真实行号与真实数据分布） |
| 2 | 目标架构与流程图 | ✅ 完成 | `docs/sql_experience_design.md` |
| 3 | 分类 / 数据模型 / 状态流转设计 | ✅ 完成 | 同上 §2–§3；代码内 `sql_experience.py` 顶部常量区 |
| 4 | 代码变更清单 | ✅ 完成 | 本文件 §2 |
| 5 | 数据库迁移与回滚脚本 | ✅ 完成 | `migrate_sql_experience.py`（支持 `--dry-run` / `--backup` / `--rollback`）+ 本文件 §3 |
| 6 | 成功经验检索 + 失败经验辅助校验的实现 | ✅ 完成 | `sql_experience.retrieve()` / `check_sql()` / `build_repair_hint()`；接线在 `agent.ask_v2` |
| 7 | AI 归纳机制与来源追踪 | ⚠️ 机制完成，**尚未产出真实规则** | `sql_experience.mine_rules()`；见本文件 §8 的限制 |
| 8 | 管理页面 | ✅ 完成 | `frontend/index.html` 新增「SQL经验」Tab（5 个子页 + 详情弹层） |
| 9 | 自动化测试与回归结果 | ✅ 完成 | `test_sql_experience.py`（62 项）+ 全量回归 18 个测试文件全绿；见 §10 |
| 10 | 基线与改造后对照报告 | ⚠️ 通路完成，**样本不足** | `evaluation/experiments/experience/experience_eval_report.md`；见 §11 |
| 11 | 已知限制 / 风险 / 后续清单 | ✅ 完成 | 本文件 §13 |

---

## 2. 代码变更清单

### 2.1 新增文件

| 文件 | 行数 | 职责 |
|---|---|---|
| `sql_experience.py` | 2004 | 经验中心全部后端逻辑：建表 / 导入分类 / 状态机 / 检索排序 / 校验 / 归纳 / 日志 |
| `migrate_sql_experience.py` | 139 | 迁移与回滚 CLI，带备份 |
| `test_sql_experience.py` | 368 | 62 项断言，纯逻辑 + 临时库两条路径 |
| `evaluation/experiments/experience/run_experience_eval.py` | 163+ | A/B/C/D 四组对照评测器 |
| `docs/sql_experience_audit.md` | 295 | 第一阶段现状审计报告 |
| `docs/sql_experience_design.md` | 199 | 目标设计 |
| `docs/sql_experience_final_report.md` | 本文件 | 交付报告 |

### 2.2 修改的既有文件（`git diff --stat`）

| 文件 | 增/删 | 改了什么 |
|---|---|---|
| `agent.py` | +99 / -8 | ① `import sql_experience`；② `_result()` 增加 `experience` 字段与 `**extra`（透传私有的 `_exp_match`）；③ `ask_v2` 在 `hard_block` 之后、生成之前插入 ③.7 段做经验检索并拼接 Prompt 块；④ 生成阶段在 `EXP_EXACT_REUSE` 下支持精确复用；⑤ ⑤.5 段做生成后失败经验校验（可选取修一轮）；⑥ 成功/被拒两条返回路径都带 `experience_warnings` |
| `server.py` | +340 | 新增 9 个请求体模型 + 16 个 `/api/exp/*` 接口；`/api/ask` 透传 `experience` / `experience_warnings`；取数记录写入 `error` 字段；拿到 `record_id` 后异步性质地写使用日志 |
| `config_store.py` | +470 / -21 | ① `init_store()` 末尾追加第 ⑭ 块，启动时幂等执行 `exp.migrate_schema()`（try/except 包裹，失败不影响启动）；② `add_query_record()` 落库 `error` 字段（此前 48 条失败记录查不出原因） |
| `config.py` | +69 | 13 个 `EXP_*` 开关 + 排序权重字典，全部可由环境变量覆盖 |
| `frontend/index.html` | +768 / -12 | 导航新增「SQL经验」；新增 `tab-exp` 整段（含独立样式）与约 450 行 JS |
| `evaluation/runner.py` | +17 | `run_one` 透出 `experience` / `experience_warnings`；`aggregate()` 新增 `exp_case_hit_rate` / `exp_failure_hit_rate` / `exp_warning_total` / `exp_case_hits` |
| `run_tests.py` | +2 | 注册 `test_sql_experience.py` 到 config 层 |

**总计：新增 3168 行，改动 7 个既有文件 +1696 / -69 行。**

### 2.3 明确**没有**改的东西（守边界）

- 没有改 `guard.py` / `permission.py` / `sql_validator.py` 的任何判定逻辑——经验机制只在它们**之前**注入参考、在它们**之后**读结果；
- 没有新增第二条 SQL 生成链路，全部接在 `agent.ask_v2` 上；
- 没有改 Semantic Layer 的检索优先级，经验块是独立追加的 Prompt 段；
- 没有改任何评测数据集、判分逻辑、基线文件。

---

## 3. 数据库迁移与回滚

### 3.1 变更内容

**3 张新表**（全部带 `IF NOT EXISTS`，幂等）：

| 表 | 字段数 | 作用 |
|---|---|---|
| `sql_experience` | 51 列 | 经验案例主表，成功/失败/修正三类同表，用 `kind` 区分 |
| `sql_experience_rules` | 28 列 | 归纳出的规则/模板，含 `source_case_ids` 溯源与 `conflict_note` 反例 |
| `sql_experience_usage` | 13 列 | 使用日志：`recall` / `skip` / `verify` 三类事件 |

**1 个新列**：`query_records.error TEXT`——这是审计阶段发现的缺口（48 条失败记录没存错误原因），不补就无法做失败分类。

**8 个索引**：`idx_exp_kind` / `idx_exp_trust` / `idx_exp_hash` / `idx_exp_source` / `idx_exp_no` / `idx_rule_status` / `idx_exp_usage_ts` / `idx_exp_usage_case`。

### 3.2 迁移命令

```bash
# 1) 只看计划，不动库
.venv/bin/python migrate_sql_experience.py --dry-run

# 2) 备份 + 建表 + 从历史记录导入（推荐）
.venv/bin/python migrate_sql_experience.py --backup

# 3) 只建表不导入
.venv/bin/python migrate_sql_experience.py --no-import

# 4) 回滚（删 3 张表；query_records.error 列保留，SQLite 删列代价高于收益）
.venv/bin/python migrate_sql_experience.py --rollback
```

备份文件：`agent_config.db.bak-exp-<时间戳>`。

**另有一条自动路径**：服务启动时 `config_store.init_store()` 会自动调用一次 `migrate_schema()`。即老环境升级代码后**不需要手工执行迁移**也能起来（只建表，不导入）。

### 3.3 回滚与兼容性

| 场景 | 处理 |
|---|---|
| 想完全停用经验机制 | 设 `EXP_ENABLED=0`，`retrieve()` 直接返回空 `ExperienceMatch`，`ask_v2` 走原路径。**不需要回滚数据库。** |
| 想回滚数据库 | `--rollback` 删 3 张表。**所有经验模块调用都在 try/except 内**：`agent.py:730`（检索）、`agent.py:820`（失败检查 + 修复）、`server.py:826`（使用日志）——任一处抛异常只打印「不影响取数」并继续，表不存在也不会让取数失败 |
| 老库没有 `query_records.error` 列 | `migrate_schema()` 会 `ALTER TABLE ADD COLUMN`；`add_query_record()` 用 `item.get("error") or ""` 兜底 |
| 历史记录被重复导入 | `upsert_case()` 以 `(source_type, source_id, kind)` 唯一，重复导入只会更新，不会翻倍（测试 `test_write_flow` 已验证） |
| 人工已改过的字段被重新导入覆盖 | `upsert_case()` 有 `keep` 元组，审核状态、验证状态、黄金发布、根因确认等**人工决策字段永不被导入覆盖** |

---

## 4. 迁移后的真实数据（不是演示数据）

```
成功案例 110 条   审核通过 1  待审 2   未验证 109
失败案例  55 条   审核通过 1  待审 0   未验证 55
其中 approved × exec_ok=0 共 1 条  ← 这条就是"审核通过 ≠ 结果正确"的活样本
```

两条已审核通过案例的真实身份：

| 案例号 | 类型 | 来源记录 | 审核 | 验证 | 可信等级 |
|---|---|---|---|---|---|
| `EXP-20261029-0002` | success | query_record #6（国庆小板订单，执行成功） | approved | reexec_ok | golden_candidate |
| `EXP-20261029-0152` | failure | query_record #163（积分记录，执行失败） | approved | unverified | candidate |

★ 关键点：`#163` 在**旧的审核流程里是"审核通过"的**，但它的 SQL 根本没执行成功。新模型把它正确归到 **failure / candidate**，验证状态保持 `unverified`——它**没有**因为"审核通过"被升级成成功经验，也没有被算进黄金候选池。这就是红线②在真实数据上的表现。

失败类型分布：`sql_error` 49 / `other` 4 / `wrong_field` 1 / `unknown`（待分析）1。

> ★ `other` 4 条与 `unknown` 1 条**故意没有被强行归类**。审计阶段已确认：这 5 条在 `query_records` 里没有错误信息，任何自动归类都是编造。管理页把它们标成「待分析」，等人工或重跑补证据。

审核通过率只有 2/165，这是历史真实现状（审计报告里写过：67% 的成功样本从未提交过审核），不是迁移丢数据。

---

## 5. 五条红线在代码里的落点

| 红线 | 落点 | 怎么保证 |
|---|---|---|
| 审核通过 ≠ 结果已验证 | `review_status` 与 `verify_status` 两个独立列；`set_case_review()` **只改** `review_status` | `test_no_write_on_rejected_review` 断言：驳回审核不会改 `verify_status` |
| 未验证的 AI 归纳不得自动升为高可信 | `publish_rule()` 检查 `source_case_ids` 非空；`mine_rules()` 校验 `source_case_nos` 必须能对上真实案例，对不上直接丢弃 | `promote_case('golden')` 在 `verify_status` 未达 `manual_confirmed` 时抛错（真实验证过：返回「正式黄金 SQL 需要人工确认业务含义」） |
| 失败 SQL 不得作为推荐答案 | 失败案例进 `failures` 池，`build_prompt_block()` 里失败段开头显式写「禁止作为答案参考」，且只用于 3 种用途：预防 / 诊断 / 修正 | `check_sql()` 只会因"与已知坏 SQL 相同"而**警告或拦截**，从不把失败 SQL 拼成可执行建议 |
| 不绕过权限 | `retrieve()` 接收 `allowed_tables`，在**召回阶段**就过滤（不是等执行阶段） | 使用日志里已真实出现 `涉及当前不可访问的表：['tu_guaranty_order', ...]` 的拒绝记录 |
| 不重复建设 | 复用 `query_records` / `feedbacks` / `config_store` / `agent._exec_sql` / `guard` / `permission` | 新增代码里**没有**任何一处直接执行 SQL，全部走 `agent._exec_sql` |

---

## 6. 检索链路：方案 §3 的 9 步对应情况

| 方案要求 | 实现 | 状态 |
|---|---|---|
| 1. 理解用户问题意图 | 复用 `understanding` | ✅ 已有能力 |
| 2. 解析指标 / 维度 / 时间 / 过滤 | 复用 `semantic_layer.resolve()` | ✅ 已有能力 |
| 3. 确定候选表 | 复用 Multi-RAG P3 | ✅ 已有能力 |
| 4. 检索相似经验 | `retrieve()`：向量相似 + 表重合 + 指标重合 + 可信度 + 验证 + 时效 | ✅ 新增 |
| 5. 适用性硬校验 | `_applicable()`：表越权 / 无交集 / 指标口径不一致 → **直接拒**，不降分 | ✅ 新增 |
| 6. 排序并给理由 | `reasons` 字段逐项写明「语义相似 0.886；表重合 0.333；可信 golden_candidate；验证 reexec_ok」 | ✅ 新增 |
| 7. 注入生成 Prompt | `build_prompt_block()` 三个独立段落 | ✅ 新增 |
| 8. 过安全闸门 | 复用 `_pre_execute_check()` + `guard` + `permission` | ✅ 已有能力 |
| 9. 记录召回与选择理由 | `log_usage()`：`recall` 记命中，`skip` 记**为什么没用** | ✅ 新增 |

**第 9 步是本方案最容易被忽略但最有价值的一步**：`skip` 事件把「召回了但没采用」的原因也落库了（表集合无交集 / 指标口径不一致 / 分数不够），这是后续调权重唯一的真实依据。

排序公式（权重全部可由 `EXP_W_*` 环境变量覆盖）：

```
score = 0.45·语义相似 + 0.15·表重合 + 0.20·指标重合 + 0.12·可信度 + 0.05·验证等级 + 0.03·时效
```

> ⚠️ 这组权重是**工程初值，不是调优结果**。方案明确要求"权重由评测确定，不得拍脑袋"，当前样本量不支持调参，见 §13 第 1 条。

---

## 7. 生成后校验：3 个确定性检查

`check_sql(sql, match)` 只做确定性判断，不调用大模型：

| 检查码 | 级别 | 触发条件 | 处理 |
|---|---|---|---|
| `same_as_known_bad_sql` | error | 归一化后 `sql_hash` 命中已知失败案例 | 默认报告；`EXP_FAIL_REPAIR=1` 时触发一轮修正 |
| `known_wrong_table` | warning | 用了失败案例里被证实错误的表 | 警告 |
| `missing_status_filter` | warning | 失败经验显示该类问题必须带状态过滤，但 SQL 里没有 | 警告 |

★ 这些检查**不阻断执行**（除 `EXP_FAIL_REPAIR` 下的修正轮），最终拦截仍由原有 `guard` / `permission` / `validator` 负责。经验机制不夺取安全闸门的权力。

---

## 8. AI 归纳机制（P1）

`mine_rules()` 的 4 道闸门：

1. **分组**：按 `topic`（指标/表/失败类型）聚合，`min_support` 不足不归纳；
2. **去重**：同组内按 `sql_hash` 去重，避免同一 SQL 的多次执行被算成多个独立证据；
3. **溯源校验**：模型返回的 `source_case_nos` 必须能在库里查到，**查不到就整条丢弃**（防止模型编造案例编号）；
4. **冲突检测**：`_conflict_of()` 用确定性逻辑比对表集合与指标口径，发现矛盾就写入 `conflict_note`；`publish_rule()` 在有 `conflict_note` 且未 `force` 时**拒绝发布**。

### ⚠️ 当前状态：0 条规则

规则表是空的。原因不是代码没写好，而是**还没有一次带足够样本的归纳被人工确认发布**——已导入的 165 条案例里，`verify_status` 全部是 `unverified`，按设计它们不足以支撑"正式规则"。

这一点必须写清楚：**方案 §4 要求"每条规则必须回答：依据哪些案例、哪些已验证、适用范围、不适用的场景、有无反例、是人工确认还是评测确认"。当前 0 条规则 = 这道题还没答，不是答了满分。**

---

## 9. 管理页面

`frontend/index.html` 新增「SQL经验」Tab，5 个子页：

| 子页 | 内容 |
|---|---|
| 成功案例 | 案例卡片（问题 / SQL / 可信等级 / 验证等级 / 审核状态 / 来源 / 复用次数），支持按 `kind`、`review_status`、`verify_status`、`trust_level`、关键词筛选 |
| 失败经验 | 同样结构 + 失败类型、症状、根因、根因是否已确认 |
| 修正对照 | 原错误 SQL 与修正后 SQL 左右并排，`correction_of` 双向可跳 |
| 经验规则 | 规则正文 + 依赖案例列表（可点进详情）+ 冲突说明 + 发布/撤销 |
| 使用日志 | `recall` / `skip` / `verify` 三类事件，含分数、排名、决策、原因 |

详情弹层支持：改审核状态、改验证状态、重跑验证（真的连 ClickHouse 执行）、提交黄金候选、发布黄金 SQL、关联修正、撤销、填写失败原因。

**页面顶部有一条固定提示**，明确写出两条红线：

> 审核通过 ≠ 业务结果验证正确；失败 SQL 只会用于错误预防，不会作为推荐答案。

### 9.1 取数页的可追溯标签

取数结果的可信标签条（`renderTrustBar`）新增 3 个 chip，让运营在**取数当场**就能看出这次有没有用经验：

| Chip | 触发条件 | 悬停内容 |
|---|---|---|
| `参考经验 N` | 召回到 N 条高可信成功案例 | 每条案例的编号 + 分数 + 可信等级 |
| `避坑 N` | 召回到 N 条失败经验 | 每条案例编号 + 失败类型 |
| `经验预警 N` | 生成后被 `check_sql` 判定疑似重犯 | 每条预警的具体说明 |

这样「这条回答参考了哪条历史经验」在界面上是可点、可查的，不用去翻日志。

---

## 10. 自动化测试与回归结果

### 10.1 新测试 `test_sql_experience.py` —— 62 项全部通过

| 测试 | 覆盖内容 |
|---|---|
| `test_normalize` | SQL 归一化（大小写、空白、注释、分号）与 `sql_hash` 稳定性 |
| `test_trust_state_machine` | 5 条红线全覆盖，**含 `kind=success + approved + ok=0` 必须停在 candidate 的断言** |
| `test_applicable` | 越权表 / 无交集 / 指标不一致 三个拒绝分支 |
| `test_check_sql` | 3 个检查码的触发与不误触发 |
| `test_prompt_block_wording` | 失败段落必须含「禁止作为答案参考」；无有效内容时整段省略 |
| `test_group_and_conflict` | 分组与确定性冲突检测 |
| `test_parse_json` | 模型输出解析容错（代码块包裹、多余文字） |
| `test_write_flow` | 临时库：导入幂等、分类正确、升级闸门、撤销、修正关联、日志计数、总览、受影响案例 |
| `test_no_write_on_rejected_review` | 审核驳回不动验证状态；失败案例不可能被标成 `result_verified` |

### 10.2 全量回归

```
run_tests.py unit    → 15 个文件全绿
  test_imports 26 · test_pipeline 19 · test_prompt_constraints 52 · test_gate 122
  test_join_suggest 10 · test_guard ✓ · test_labels 33 · test_retrieve 22
  test_permission ✓ · test_legacy_validation 9 · test_join_cardinality 15
  test_audit_safety 9 · test_result_check 16 · test_golden_demo_dataset 9 · test_calendar ✓

run_tests.py config  → 3 个文件全绿
  test_acl 88 · test_knowledge_governance 47 · test_sql_experience 62
```

**结论：既有功能零回归。** 新增代码没有削弱任何一项原有校验（`test_gate` 122 项、`test_permission`、`test_guard` 全部原样通过）。

### 10.3 真实端到端验证（连真实 ClickHouse，非 mock）

| 验证项 | 结果 |
|---|---|
| 迁移真实库 | 160 条记录 → 110 成功 + 50 失败；5 条反馈 → 5 失败案例 |
| 重跑验证 | 案例 2 重跑成功，`row_count=5`，`elapsed_sec=11.15` → 升级为 `golden_candidate` |
| 升级闸门 | 直接升 `golden` 被拒：`正式黄金 SQL 需要人工确认业务含义（或更高的等级）` ✅ 符合预期 |
| 真实取数 | 问「查询国庆期间小板订单不同车辆状态的订单数据」→ `ok=True`，5 行，召回案例 `EXP-20261029-0002`，`score=0.7806`，`trust=golden_candidate`，生成 SQL 与黄金候选结构一致 |
| 使用日志 | `record_id=177` → 2 条 `recall`（案例 2 分数 0.781 `reference`；案例 129 分数 0.534 `prevent`） |
| 越权拦截 | 日志中出现「涉及当前不可访问的表」的拒绝记录 → 权限过滤在召回阶段生效 |

---

## 11. 四组对照评测（A/B/C/D）

数据集 `evaluation/datasets/golden` 前 6 条，四组同题同模型，**只改经验开关**。

| 指标 | A 基线 | B +成功检索 | C +失败校验 | D +归纳规则 |
|---|---|---|---|---|
| 综合通过率 | 0.8333 | 0.8333 | 0.8333 | 1.0000 |
| SQL 执行成功率 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| SQL 片段命中率 | 0.7500 | 0.7500 | 0.7500 | 1.0000 |
| 表召回率 | 0.8000 | 0.8000 | 0.8000 | 1.0000 |
| 指标识别准确率 | 0.8333 | 0.8333 | 0.8333 | 1.0000 |
| 时间口径准确率 | 1.0000 | 1.0000 | 1.0000 | 1.0000 |
| 幻觉率（越低越好） | 0 | 0 | 0 | 0 |
| **★ 黄金 SQL 命中率** | **0.0000** | **0.1667** | **0.1667** | **0.3333** |
| **★ 失败经验覆盖率** | **0.0000** | **0.0000** | **0.5000** | **0.5000** |
| ★ 疑似重犯历史错误条数 | 0 | 0 | 0 | 0 |
| 平均耗时（秒） | 32.66 | 22.37 | 20.58 | 21.31 |

### 11.1 可以下结论的（强归因）

- **成功检索确实接上了**：黄金命中率从 A 组 `0` 变为 B/C/D 非 0，A 组全程没有召回动作，这是开关级跳变。
- **失败预防确实接上了**：失败覆盖率 A/B 为 `0`，C/D 变 `0.5`，`EXP_FAIL_PREVENT` 正是 C/D 组唯一新增开关。

### 11.2 **不可以**下结论的（必须写明）

- **D 组综合通过率 1.0 高于 A/B/C，不能算作"规则生效"**。规则表当前 0 条已发布规则，D 组比 C 组只多了 `EXP_RULES` 一个开关，机制上不可能起作用。这个差异只能来自大模型生成随机性。
- **黄金命中率 D 组 0.3333 高于 B/C 的 0.1667，同样不能归因于规则**。已定位原因：`retrieve()` 的适用性硬校验依赖模型识别出的「表集合 / 指标集合」，识别本身有随机性，会连带改变召回结果。
- 平均耗时 A 组最高（32.66s vs 20~22s）只能算**弱证据**，单条慢请求就能拉高 P95。

### 11.3 暂不可测的指标（不编造数字）

| 指标 | 原因 |
|---|---|
| 最终业务结果准确率 `result_accuracy` | 94 条用例均无 `expected_sql` / `expected_result`，无法自动比对 |
| 审核一次通过率 `review_first_pass_rate` | 离线评测不产生人工审核动作 |
| 错误复发率 `repeat_error_rate` | 需要同一问题在改造前后的错误集合配对，当前无配对样本 |
| 经验误召回率 `misrecall_rate` | 需人工逐条判定召回是否适用；当前高可信案例仅 1 条 |
| 经验复用后的成功率 `reuse_success_rate` | 复用次数 <10，统计无意义 |

> **样本量声明**：本次每组仅 6 条，只用于验证评测通路与机制接线，**不具统计意义**。要得出"经验机制把准确率提升了多少"的结论，必须在完整 94 条数据集上重跑，且最好做多轮取均值。

---

## 12. P0 验收 10 条 · 逐条自检

| # | 验收标准 | 结论 | 证据 / 未通过原因 |
|---|---|---|---|
| 1 | 正确识别历史已审核 / 已驳回 SQL | ✅ 通过 | 165 条分类入库；`approved` 的 2 条正确进入"已审核"态，未提交审核的按 `none` 保留，不伪造审核结论 |
| 2 | 不混淆审核状态与验证状态 | ✅ 通过 | 两列独立；真实库里存在 `approved × exec_ok=0` 的 1 条，页面显示为「审核通过 / 未验证」，未被算成成功经验 |
| 3 | 高可信成功 SQL 在适用时优先进入检索 | ✅ 通过 | B 组黄金命中率 0 → 0.1667；真实取数召回 `EXP-20261029-0002` 并改写生成结果 |
| 4 | 失败 SQL 可检索用于预防，但不作正向推荐 | ✅ 通过 | 失败案例只进 `failures` 池，Prompt 段显式写"禁止作为答案参考"；`test_prompt_block_wording` 有断言 |
| 5 | 人工修正与原错误案例关联 | ✅ 通过 | `attach_correction()` 建 `kind=correction` 案例并回写 `correction_of`；管理页"修正对照"可双向跳转 |
| 6 | AI 候选规则可溯源 | ⚠️ **机制通过，无真实数据** | `source_case_nos` 查不到即整条丢弃；但当前 0 条规则，**没有真实规则可验收** |
| 7 | 未验证 / 冲突经验不自动升级 | ✅ 通过 | `promote_case` 闸门真实验证被拒；`publish_rule` 对 `conflict_note` 与空 `source_case_ids` 双重拦截 |
| 8 | 新经验不绕过安全 / 权限 | ✅ 通过 | 召回阶段按 `allowed_tables` 过滤（日志有真实拒绝记录）；执行仍走原 `_pre_execute_check` + `guard` + `permission` |
| 9 | 回归通过 | ✅ 通过 | unit 15 文件 + config 3 文件全绿，既有 122 项 gate 测试原样通过 |
| 10 | 有量化效果，或明确说明缺失验证条件 | ⚠️ **部分通过** | 已给出 A/B/C/D 对照并明确标注 5 项暂不可测；但**样本 6 条不具统计意义**，"效果提升多少"仍无数值结论 |

**结论：10 条中 7 条完全通过，3 条为"机制已完成但缺真实数据验收"（第 6、10 条 + 第 10 条的量级问题）。这 3 条不因代码已写就宣告完成。**

---

## 13. 已知限制 / 风险 / 后续清单

### 13.1 已知限制（当前就能感知到的）

1. **高可信案例只有 1 条**（`golden_candidate`），检索池几乎是空的。经验中心现在的价值主要是"管路通了"，不是"经验够用了"。
2. **排序权重是工程初值**，没有经过调优。方案要求权重由评测确定，当前样本量不具备调参条件。
3. **失败类型 55 条里 49 条是 `sql_error`**，粒度太粗——只说明"SQL 报错了"，没有区分是字段错、表错、语法错还是权限错。要细分必须补 `error` 字段的历史数据（已无法回溯）或靠后续真实报错积累。
4. **`other` 4 条 / `unknown` 1 条无法归类**，缺错误信息，只能等人工。
5. **精确复用 `EXP_EXACT_REUSE` 默认关闭**：只做了代码通路，没有在生产开路。原因是精确复用会跳过生成环节，风险高于收益，需要先在评测上验证。
6. **归纳机制依赖大模型输出 JSON 的稳定性**，虽有 `_parse_json_obj` 容错，但复杂分组下仍可能解析失败；失败即丢弃，不会污染库。

### 13.2 风险

| 风险 | 影响 | 缓解 |
|---|---|---|
| 历史案例被当成权限旁路 | 高 | 已在召回阶段按 `allowed_tables` 过滤，日志可审计 |
| 误召回把口径不对的 SQL 喂给模型 | 高 | `_applicable()` 硬校验宁缺勿滥；`rejected` 列表落库可复盘 |
| 规则悄悄覆盖既有高优先级业务定义 | 高 | Prompt 段显式写"不得覆盖系统规则与指标口述"；`EXP_RULES` 可单独关闭 |
| 表结构 / 指标口径变更后旧经验失效 | 中 | `affected_cases()` + `mark_stale()` 已实现，但**尚未接定时任务**，需人工触发 |
| 使用日志增长过快 | 低 | `skip` 事件已按 `(stage, reason)` 聚合，一次查询最多几行 |

### 13.3 后续建议（按优先级）

1. **补 `expected_sql` / `expected_result`**——这是解锁 `result_accuracy`、`repeat_error_rate`、`misrecall_rate` 三项指标的唯一前提，也是本方案最该先做的事。
2. **在 94 条全量数据集上重跑四组对照，多轮取均值**，再做权重调优。
3. **把 `affected_cases()` 接入定时任务**，表结构变更后自动标记受影响案例为待复核。
4. **沉淀失败类型细分规则**，把 49 条 `sql_error` 拆成字段错 / 表错 / 语法错 / 权限错，为 `check_sql` 增加更多确定性检查码。
5. **在积累到 30+ 条高可信案例后**，再考虑打开 `EXP_EXACT_REUSE`。

---

## 14. 附：环境变量速查

| 变量 | 默认 | 作用 |
|---|---|---|
| `EXP_ENABLED` | 1 | 经验中心总开关 |
| `EXP_SUCCESS_RECALL` | 1 | 成功经验检索（B 组开关） |
| `EXP_FAIL_PREVENT` | 1 | 失败经验预防（C 组开关） |
| `EXP_FAIL_REPAIR` | 0 | 命中已知坏 SQL 后自动修一轮 |
| `EXP_RULES` | 1 | 归纳规则注入（D 组开关） |
| `EXP_EXACT_REUSE` | 0 | 高相似时直接复用 SQL（跳过生成，慎开） |
| `EXP_MAX_CASES` / `EXP_MAX_FAILURES` / `EXP_MAX_RULES` | 2 / 3 / 2 | 各类最多注入几条 |
| `EXP_MIN_SCORE` / `EXP_FAIL_MIN_SCORE` / `EXP_RULE_MIN_SCORE` | 0.55 / 0.50 / 0.55 | 各类最低分阈值 |
| `EXP_W_SIM` / `EXP_W_TABLE` / `EXP_W_METRIC` / `EXP_W_TRUST` / `EXP_W_VERIFY` / `EXP_W_RECENCY` | 0.45 / 0.15 / 0.20 / 0.12 / 0.05 / 0.03 | 排序权重覆盖 |

**日常运维只有两条命令**：

```bash
.venv/bin/python migrate_sql_experience.py --dry-run   # 看现状
.venv/bin/python test_sql_experience.py                # 改完代码自检
```
