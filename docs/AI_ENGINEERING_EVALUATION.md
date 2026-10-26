# 知数AI · AI 工程化评测体系（AI Engineering / Evaluation）

> 版本：v1 ｜ 建立时间：2026-10-08 ｜ 目标：**让每一次优化都能被自动化实验证明，而不是主观声称**
>
> 本文件是评测体系的说明与现状报告。核心原则：
> **证据优先于模型记忆；宁可拒答，不可幻觉；没有实验数据，不算优化成功。**

---

## 一、当前架构分析（现状）

### 1.1 分层（依赖单向，未做任何重写）

```
server.py (FastAPI API)
  └─ agent.py  (编排层: ask_v2 企业级流水线 / ask_legacy 兜底)
       ├─ calendar_util.py    时间口径解析(日历/节假日) → 明确公历区间
       ├─ query_understanding 意图/指标/实体识别（已注入当前日期与节日）
       ├─ semantic_layer.py   P0~P4 分层路由 + 实体级业务线隔离 + Prompt 组装
       │     └─ 消费 config_store.py / knowledge.py（知识层，SQLite）
       ├─ rag.py              混合检索(向量+关键词) + 字典/禁用指标匹配
       ├─ llm.py              DeepSeek（temperature=0）
       ├─ guard.py           只读 + 表白名单(sqlglot)
       ├─ sql_validator.py   生成后 6 类校验（P0/P1/P2/P3/列/指标）
       ├─ result_validator.py 执行后结果体检（比率越界/整列NULL/行数/量级）
       └─ db.py              数据源门面 → ClickHouse / SQLite
```

### 1.2 已有能力盘点（可复用，不重造）

| 能力 | 位置 | 现状 |
|---|---|---|
| 知识注册表 | `config_store.py` | 指标/实体/关系/字典/字段注释/表ACL/Prompt/反馈 |
| 语义分层路由 | `semantic_layer.py` | P0~P4 + 实体级业务线隔离（2026-09-23 修复） |
| 时间口径感知 | `calendar_util.py` | 2026-09-24 新增（节前N天/N号/近N天/去年中秋…） |
| SQL 生成后校验 | `sql_validator.py` | 6 类（P0规则/P1口径/P2Join/P3Schema/列/指标） |
| 结果合理性校验 | `result_validator.py` | 比率越界/整列NULL/行数异常/量级异常（仅 warning） |
| 表权限护栏 | `guard.py` | 只读 + 白名单（越权执行前硬拦） |
| 轻量回归 | `eval/run_eval.py` | 8 条（弱断言：ok/tables/metrics/sql_contains） |
| 一致性自检 | `check_consistency.py` | 知识库 ↔ 真实库结构漂移检测 |

### 1.3 缺失能力（本次建立 / 待建）

- ✅ 统一评测入口与多维指标（本次建立 `evaluate.py` + `evaluation/`）
- ✅ Golden Dataset（业务语义级，非 question+answer）
- ✅ Ablation / Regression / Release Gate
- ✅ 错误分析 / Grounding / 幻觉检测 / 拒答评测 / 一致性 / 对抗 / 边界 / 反事实测试集
- ⏳ Confidence Calibration（待 API 恢复后接入历史 Benchmark 校准）
- ⏳ Field Retrieval 判分（需 Schema 生效后开启）
- ⏳ 多轮对话（现状为单轮，`g022` 已作为已知缺口登记）

---

## 二、当前问题清单（基于真实 Baseline 实测，非推测）

> 数据来源：2026-10-08 有效运行（`evaluation/reports/baseline.json`）

| # | 问题 | 证据（用例） | 影响 | 优先级 |
|---|---|---|---|---|
| P1 | **复合指标表达理解不足** | `g009` "发单完单率"→拒答 | 可用性 | 高 |
| P2 | **同义词召回失败** | `g013` "妥投量"（知识库已有别名）→拒答 | 召回率 | 高 |
| P3 | **业务规则知识缺失** | `g015` "代驾调度发单"（缺 is_service_staff 规则）→拒答 | 召回率 | 高 |
| P4 | **模糊问题未兜底** | `g011` "最近大板情况怎么样"→拒答 | 可用性 | 中 |
| P5 | **泛问题过度召回跨业务线指标** | `g003` 问"订单量"同时召回大板/代驾指标 | 口径混淆风险 | 高 |
| P6 | **复合比率指标 SQL 执行失败** | `g032` `dateTrunc('MONTH',…)` → ClickHouse code 36 | 执行成功率 | 高 |
| P7 | **复合指标依赖表越权/不可见** | `g033` 发单率依赖表不在白名单 → grounding 失败 | 安全/可用性 | 中 |
| P8 | **多轮对话不支持** | `g022` "那上个月呢？" 无上下文 | 体验 | 中 |
| P9 | 结果准确性（Result Accuracy）未接入 | 用例缺 `expected_result` | 观测 | 中 |

---

## 三、主要幻觉来源分析

| 来源 | 机制 | 现有防线 | 缺口 |
|---|---|---|---|
| 召回无关指标/表 | 向量相似但业务线不同 | 实体级业务线隔离（P5 仍部分失效：泛问题无实体） | 泛问题需"业务线澄清"机制 |
| 字段/口径臆造 | 模型按字段名猜口径 | P1 权威指标注入 + SQL Validator | 复合指标（如"发单完单率"）无注册项 |
| 静默算错（能跑但错） | 时间窗口/关联放大/类型转换 | SQL Validator + Result Validator + SQL 自审 | Result Validator 仅 warning 不阻断 |
| 无依据结论 | 结果为空仍给数字 | `check_hallucination` 已覆盖此形态 | 需更强的 Grounding（答案-证据对齐） |
| 越权/不可见表 | 依赖表不在白名单 | guard 白名单（执行前拦截） | 应在生成前就约束可用表 |

> 实测：Baseline 的 **Hallucination Rate = 0%**、**Grounding Accuracy = 100%**（在 34 条 golden 上）。
> 说明现有"引用无依据表/空结果给数字"两类幻觉已被较好抑制；剩余风险集中在 **静默算错** 与 **口径误解**。

---

## 四、评测体系（本次交付）

### 4.1 目录结构

```
evaluation/
├── datasets/
│   ├── golden/golden_v1.jsonl            (34 条 / 23 类)
│   ├── regression/regression_v1.jsonl    (8 条，迁移自 eval/cases.json)
│   ├── abstention/abstention_v1.jsonl    (10 条：known/unknown/ambiguous/conflicting)
│   ├── paraphrase/paraphrase_v1.jsonl    (9 条，2 组同义表达)
│   ├── counterfactual/counterfactual_v1.jsonl (6 条，3 组单变量对照)
│   ├── boundary/boundary_v1.jsonl        (13 条：今天/本周/上月/跨年/未来日期…)
│   └── adversarial/adversarial_v1.jsonl  (6 条：错别字/无关/诱导/不完整)
├── evaluators/checks.py                  多维判分器（含 Grounding/Hallucination/CTE 排除）
├── benchmark/runner.py                   ← 实际在 evaluation/runner.py
├── experiments/ablation/run_ablation.py  消融实验（monkeypatch，不动业务代码）
├── reports/                              报告与基线
├── config/knowledge_snapshot.json        知识库快照（28指标/9实体/7字典/94字段注释）
└── run_consistency.py                    一致性评测
evaluate.py                               统一入口
```

### 4.2 评测流程（Evaluation Pipeline）

```
加载 Dataset → 调用 agent.ask（记录完整 Trace）
  → 多维判分（意图/指标/表/SQL谓词/时间/结果/Grounding/幻觉/拒答）
  → 聚合指标 → 生成 JSON+MD 报告 → 对比 Baseline → Regression/Release Gate
```

### 4.3 指标定义

| 指标 | 口径 |
|---|---|
| Overall Accuracy | 一条用例的**所有适用维度全部通过**才算通过 |
| Metric Accuracy | 期望指标是否被召回 |
| Table Retrieval Recall | 期望表是否被召回 |
| SQL Accuracy | SQL 关键谓词命中（含"禁止谓词"不得出现） |
| SQL Execution Success | 是否成功执行 |
| Time Accuracy | 时间边界命中（兼容系统左闭右开 `[start, end+1)`） |
| Grounding Accuracy | SQL 引用的表是否都在**召回表 ∪ 授权白名单**内（已排除 CTE 别名） |
| Hallucination Rate | 空 SQL / 无依据表 / 空结果给非零数字 三类占比 |
| Abstention Accuracy | 应拒答的拒答、该回答的回答 |
| Consistency Rate | 同问题 N 次运行的 SQL 一致性 |

---

## 五、Baseline（2026-10-08 有效运行）

```
Dataset : evaluation/datasets/golden   (Samples: 34, Executed: 23)
Overall Accuracy       73.53%
Intent Accuracy        82.14%
Metric Accuracy        95.24%
Table Retrieval        100.00%
SQL Accuracy           76.92%
SQL Execution Success  85.29%
Time Accuracy          90.00%
Grounding Accuracy    100.00%
Hallucination Rate      0.00%
Abstention Accuracy   100.00%
P50 / P95 / Avg        5.85s / 7.89s / 5.13s
```

> 报告文件：`evaluation/reports/baseline.json` / `baseline_report.md`
> 失败用例 9 条，明细见 `baseline.json → failed_cases`。

**解读**：召回类指标（表 100%、指标 95%）与安全类指标（Grounding 100%、拒答 100%）表现好；
**SQL Accuracy / Execution Success 是当前最大短板**，且主要来自"复合指标"与"生成 SQL 细节错误"。

---

## 六、使用手册

```bash
# 跑 Golden 评审并生成报告
.venv/bin/python evaluate.py

# 与基线对比 + 发布门禁（退出码 3=回归失败, 2=门禁失败, 便于 CI）
.venv/bin/python evaluate.py --regression --gate

# 把本次结果存为基线（有 API 健康保护：异常率>20% 拒绝覆盖）
.venv/bin/python evaluate.py --save-baseline

# 一致性（同问题跑 N 次）
.venv/bin/python evaluation/run_consistency.py --n 3 --limit 10

# 消融实验（逐模块移除，量化贡献）
.venv/bin/python evaluation/experiments/ablation/run_ablation.py --limit 20

# 其他数据集
.venv/bin/python evaluate.py --dataset evaluation/datasets/abstention
.venv/bin/python evaluate.py --dataset evaluation/datasets/adversarial
```

---

## 七、Release Gate 阈值（初版，待真实 Benchmark 校准）

| 指标 | 阈值 |
|---|---|
| Overall Accuracy | ≥ 93% |
| Metric Accuracy | ≥ 95% |
| Table Retrieval | ≥ 95% |
| SQL Accuracy | ≥ 94% |
| SQL Execution Success | ≥ 98% |
| Grounding Accuracy | ≥ 95% |
| Hallucination Rate | ≤ 3% |
| Consistency | ≥ 95% |
| P95 Latency | ≤ 8s |

Regression Gate：核心指标不得低于历史最佳 − 2 个百分点（容忍评测噪声）。

---

## 八、当前环境阻塞（重要）

- **DeepSeek API 额度已用尽（402 Insufficient Balance）**，在收尾重跑时触发，导致一次全量运行全部失败。
  该次结果**已隔离**为 `evaluation/reports/baseline_invalid_apibalance.json`，未污染基线。
- 已加入保护：**异常率 > 20% 时拒绝覆盖 baseline**（防止把环境故障当成系统能力）。
- 待额度恢复后需补跑：完整逐条 `results` 明细、Consistency、Ablation、其余数据集。

---

## 九、Roadmap（后续阶段）

1. **修复 P1–P8 问题**（每项：先加/改 Golden 用例 → 改代码 → 跑 evaluate 出 Before/After）
2. 接入 **Confidence Calibration**（历史 Benchmark 分桶校准）
3. 强化 **Grounding**：答案与 SQL 结果的数值对齐（UNSUPPORTED_CLAIM 标注）
4. 开启 **Field Retrieval** 判分（需 Schema 注入）
5. **多轮对话**支持（会话状态）
6. **Ablation 全量**跑通，产出各模块贡献度表
7. 阈值按首轮真实 Benchmark **自动校准**
8. 将真实错误持续回流 Golden Dataset，形成闭环

---

## 十、本次交付清单（对照）

| # | 交付物 | 状态 |
|---|---|---|
| 1 | 当前架构分析 | ✅ 本文档 §1 |
| 2 | 当前问题清单 | ✅ §2（基于实测） |
| 3 | Golden Dataset | ✅ 34 条 / 23 类 |
| 4 | Evaluation Framework | ✅ `evaluation/` + `evaluate.py` |
| 5 | Benchmark Runner | ✅ `evaluation/runner.py` |
| 6 | Ablation Runner | ✅ 骨架（待 API 恢复跑数） |
| 7 | Regression Test | ✅ `gate.compare_baseline` |
| 8 | Error Analysis | ✅ 报告自动生成失败维度分布 |
| 9 | Grounding 机制 | ✅ `checks.check_grounding` |
| 10 | Hallucination 检测 | ✅ `checks.check_hallucination` |
| 11 | Abstention 机制 | ✅ 数据集 + 判分 |
| 12 | Confidence 评估 | ⏳ Roadmap |
| 13 | Consistency Test | ✅ `run_consistency.py` |
| 14 | Robustness Test | ✅ paraphrase/adversarial 数据集 |
| 15 | Counterfactual Test | ✅ 数据集 |
| 16 | SQL Validator 增强 | ⏳ 现状 6 类，增强项见 §2 |
| 17 | Result Validator | ✅ 已有（仅 warning，增强见 Roadmap） |
| 18 | Experiment Report | ✅ JSON+MD + EXP-ID |
| 19 | Release Gate | ✅ `gate.check_release_gate` |
| 20 | 本文件 | ✅ |
