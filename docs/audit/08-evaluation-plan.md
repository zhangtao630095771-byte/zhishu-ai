# 08 · 评测计划（测试集设计 / 场景 / 评分 / 回归）

---

## 一、现状与目标

- **现状**：已有 8 类数据集约 94 条（golden 35 + regression 14 + abstention 10 + boundary 13 + paraphrase 9 + adversarial 6 + counterfactual 6 + inbox 1），基线 `overall_accuracy=85.7%`，但 `result_accuracy`/`field_retrieval_recall` 未实现。
- **目标**：建立**第一版 30~50 条真实业务测试集**（对齐任务书第九问），补齐标准答案对比，把基线推过 Release Gate 阈值（`overall_accuracy≥93%`）。

---

## 二、第一版真实业务测试集设计（30~50 条）

### 2.1 来源（真实业务，非合成）

| 来源 | 采集方式 | 目标条数 |
|---|---|---|
| 线上 `query_records` 审核通过的高频取数 | 从 `query_records` 筛选 `review_status=approved` 且被收藏/复用的 | 15~20 |
| 业务方真实提数需求 | 与业务对齐，记录标准 SQL + 期望结果 | 10~15 |
| 线上反馈（错例） | `feedbacks` 表 `root_cause` 明确的错例，回流后转回归集 | 5~10 |

### 2.2 场景覆盖矩阵（每条用例须标注）

| 维度 | 覆盖要求 |
|---|---|
| 指标类型 | 计数/金额/比率（完单量、信息费、完单率、客单价） |
| 时间口径 | 单日/区间/同比环比/节假日/近 N 天/月底月初 |
| 实体线 | 大板/小板/代驾/司机/客户（跨业务线区分） |
| 复杂度 | 单表单指标 → 多表 JOIN → 一对多预聚合 → 多指标联合 |
| 口径陷阱 | 发单≠完单、status 枚举、费用字段（information_fee vs service_fee）、创建时间 vs 完成时间 |
| 易混淆 | 同义词（妥投量）、同名字段（city）、同名表 |
| 拒答 | 信息不足、跨业务线、口径冲突、无关问题 |
| 数据字典 | 编码取值（车拖车/钻石会员/资金流水类型） |

### 2.3 用例 schema（必须字段，对齐 `evaluation/schema.py`）

```json
{
  "id": "biz-001",
  "question": "查询2026年9月代驾完单量",
  "category": "metric_count",
  "intent": "summary",
  "difficulty": "easy",
  "expected_ok": true,
  "expected_metrics": ["代驾完单量"],
  "expected_tables": ["dwd_tu_order_all"],
  "expected_sql_contains": ["status = 40"],
  "expected_sql_not_contains": ["status = 6"],
  "expected_result": {"完单量": 12345},   // ★ 补齐后 result_accuracy 才可计算
  "expected_sql": "SELECT ...",            // ★ 经人工审核的标准 SQL
  "time_range": "2026-09-01~2026-09-30",
  "review_status": "approved",             // ★ 必须人工审核
  "source": "query_record#123",
  "version": 1
}
```

**关键约束**（继承 `docs/golden_dataset_schema.md`）：`expected_sql`/`expected_result` 必须经**人工审核**后写入，禁止把历史 SQL 自动认定为标准答案。

---

## 三、评分方法

### 3.1 维度（继承现有 checks.py + 补齐缺口）

| 维度 | 指标 | 当前 | 目标 |
|---|---|---|---|
| 检索 | `table_retrieval_recall` | ✅ 100% | 保持 |
| 检索 | `metric_accuracy` | ✅ 95.5% | ≥95% |
| 检索 | `field_retrieval_recall` | ❌ None | 实现（依赖 Schema 生效） |
| SQL | `sql_accuracy` | ✅ 84.6% | ≥94% |
| SQL | `sql_execution_success_rate` | ✅ 91.4% | ≥98% |
| 结果 | `result_accuracy` | ❌ None | 实现（数值集合包含） |
| 完整 | `overall_accuracy` | ✅ 85.7% | ≥93% |
| 可信 | `hallucination_rate` | ✅ 0% | ≤3% |
| 拒答 | `abstention_accuracy` | ✅ 100% | 保持 |
| 延迟 | `p95_latency` | 待测 | ≤8s |

### 3.2 判分原则（继承现有）

- 只做"可判定的弱断言"（子串/正则/集合包含/数值包含），避免误报；
- **无 LLM 判分**（现有已确定，保持——LLM 判分引入二次幻觉与成本）；
- 用例未提供某维度期望 → 该维度记 None 跳过，不计入分母。

---

## 四、质量指标与门禁

- **Release Gate 阈值**（`evaluation/gate.py`，已存在）：`overall_accuracy≥0.93`、`metric_accuracy≥0.95`、`table_retrieval_recall≥0.95`、`sql_accuracy≥0.94`、`sql_execution_success_rate≥0.98`、`grounding_accuracy≥0.95`、`hallucination_rate≤0.03`、`p95_latency≤8.0s`。
- **Regression Gate**：核心指标不得低于历史最佳 −0.02，幻觉率不得恶化。
- **门禁落地**：CI 中跑 `evaluate.py --gate`，不通过阻断合并（当前无 CI，需补）。

---

## 五、回归方案

1. **基线管理**：`evaluate.py --save-baseline`（有 API 健康保护，防止余额不足污染基线）。
2. **每次改造回归**：`run_tests.py`（unit/config 两层，秒级）+ `evaluate.py --dataset golden --regression`。
3. **线上反馈回流**：`evaluation/reflow.py --collect`（feedback → inbox 草稿 → 人工补期望 → promote 到 golden/regression），形成"上线→反馈→回流→回归"闭环。
4. **消融实验**：`experiments/ablation/run_ablation.py` 按模块消融，量化每模块贡献（安全/合规模块列 NEVER_ABLATE）。
5. **一致性评测**：`run_consistency.py` 同题跑 N 次，监控 SQL/结果稳定性（temperature=0 应高度一致）。

---

## 六、验证方式与验收标准

| 里程碑 | 验收标准 |
|---|---|
| 测试集建成 | 30~50 条真实业务用例，`review_status=approved`，`expected_result` 覆盖 ≥80% |
| result_accuracy 启用 | 评测报告出现非 None 的 `result_accuracy` 数值 |
| 门禁达标 | `evaluate.py --gate` 通过（overall≥93%） |
| CI 接入 | 合并请求自动跑测试 + 门禁，失败阻断 |

---

## 七、不编造声明

- 本报告**未执行**任何评测（本次为只读审查），上述基线数字均引自 `evaluation/reports/baseline.json` 与历史报告，非本次新跑。
- 所有"目标值"为计划阈值，非已达成结果；达成情况以实际跑 `evaluate.py` 输出为准。
