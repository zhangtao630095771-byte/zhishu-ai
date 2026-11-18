# 知识中心整合 + OSSIE 标准兼容改造 · P2 交付报告

> 交付日期：2026-11-12 · 阶段：P2（Agent 接入语义模型 + 订单主题试点 + 评估框架）

---

## 1. 本阶段目标

1. **Agent 接入语义模型**：让 Agent 在生成 SQL 前，优先使用审核通过的语义资产（任务书 §7 第五阶段）。
2. **订单主题试点**：从真实知识表组装订单语义模型（任务书 §9.1）。
3. **评估框架**：给出可重复运行的对照评测方式（任务书 §9.3/§9.4）。

---

## 2. 修改 / 新增文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `semantic_model_service.py` | 新增 | 语义模型读取与匹配：`match_models`（匹配 approved 模型）+ `build_context_block`（渲染权威约束注入片段） |
| `agent.py` | 修改 | `_build_system_prompt` 增加 `sm_block` 参数（在硬约束后、经验前插入）；`ask_v2` 在 resolve 后注入语义模型约束；import semantic_model_service |
| `config.py` | 修改 | 新增 `SEMANTIC_MODEL_ENABLED`（默认 False，一键回滚开关）+ `SEMANTIC_MODEL_MAX` |
| `ossie_adapter.py` | 修改 | 修正 `validate_references`：移除「指标表达式 dataset 引用」误报检查（知数AI 指标是物理 SQL，非 dataset.field 语义） |
| `_seed_order_semantic_model.py` | 新增 | 订单主题试点种子脚本（从真实知识表组装语义模型，幂等） |
| `test_semantic_model_service.py` | 新增 | 语义模型服务测试（10 项） |
| `run_tests.py` | 修改 | 注册 `test_semantic_model_service.py`（unit 层） |

---

## 3. Agent 接入设计（关键决策）

### 3.1 接入位置

在 `ask_v2` 的 `resolve(ctx)` 之后、`_build_system_prompt` 之前，注入语义模型约束段：

```
retrieve_semantic (P0~P4 多路检索)
   → resolve (冲突解决)
   → ★ 语义模型匹配 + 注入（本次新增，配置开关控制）
   → _build_system_prompt（唯一组装点，sm_block 插入在「硬约束」后、「经验」前）
   → SQL 生成 → 六维闸门 → 执行
```

### 3.2 语义定位（不冲突、不重复）

| 层 | 定位 | 关系 |
|---|---|---|
| semantic_layer P0~P4 | 向量/关键词多路召回「可能相关」的知识 | 保留不动 |
| 语义模型（approved） | 人工审核过的、业务主题级的「确定事实」 | ★ 作为权威约束注入，优先级高于经验参考 |
| sql_experience 经验 | 历史案例「参考」 | 语义模型结构上高于经验 |

### 3.3 安全与可回退保证

- **默认关闭**：`SEMANTIC_MODEL_ENABLED=False`，行为与接入前完全一致（`test_prompt_assembly` 23 项字节序等价性守护）。
- **只读不写**：`semantic_model_service` 只读 config_store，绝不写数据。
- **不绕过校验**：注入的是「上下文约束」，SQL 仍由 LLM 生成 + 过六维闸门。
- **静默降级**：匹配失败/异常一律返回空串，绝不影响取数。

---

## 4. 订单主题试点（真实数据）

### 4.1 落地结果

已运行 `_seed_order_semantic_model.py`，从现有知识表组装出「订单主题」语义模型（**candidate 状态，待审核**）：

| 项 | 值 |
|---|---|
| 模型 | 订单主题（id=4，candidate） |
| datasets | 24（含「代驾费用」等关系端点补全） |
| metrics | 40（订单量/完单量/完单率/GMV 等） |
| relationships | 1（代驾订单→代驾费用，1:N） |
| Schema 校验 | ✅ 通过 |
| 引用完整性 | ✅ 通过 |
| 往返一致性 | ✅ 通过（对象 24/40/1 全部保留，扩展属性无丢失） |

> 备份：`agent_config.db.bak-seed-order-20261112-134622`（落地前已备份）

### 4.2 发现的既有数据问题（引用完整性校验的价值）

种子脚本运行时报出 **21 条关系**端点缺失/异常，归因于现有 `relationships` 表的数据不一致：

1. **关系端点用物理表名而非实体名**：如 `ods_tu_record_type_all->ods_tu_record_all`、`ods_tu_customer_all->dws_day_user_login_activity_all` 等，端点存的是表名，无法与实体匹配。
2. **「大板订单」实体缺失**：关系「大板订单→大板费用」的 from_entity 是「大板订单」，但实体表里只有「大板订单表」（疑似曾被改名/去重，关系未同步）。

**建议（后续治理，不在本阶段范围）**：在「语义注册表」补齐这些关系端点对应的实体，或统一关系的 from_entity/to_entity 语义（实体名 vs 表名）。

---

## 5. 评估框架与对照测试

### 5.1 现状

- 已有 `evaluation/` 框架（runner/gate/report/evaluators）+ `evaluate.py` CLI。
- 已有 golden 数据集 `evaluation/datasets/golden/golden_v1.jsonl`（含「订单量」「完单量」等订单主题问题，真实表 `dwd_tu_order_all`）。
- 已有历史 baseline（`overall_accuracy=0.8571`）。

### 5.2 对照测试步骤（接入前 vs 接入后）

```bash
# ① 先审核订单主题模型（语义模型页点「审核通过」，或 API）
# ② 接入前基线（语义模型关闭）
SEMANTIC_MODEL_ENABLED=0 .venv/bin/python evaluate.py \
  --dataset evaluation/datasets/golden --tag semantic-off --save-baseline

# ③ 接入后（语义模型开启）
SEMANTIC_MODEL_ENABLED=1 .venv/bin/python evaluate.py \
  --dataset evaluation/datasets/golden --tag semantic-on --baseline baseline.json --regression

# ④ 对比指标：表选择/字段选择/指标匹配/Join 关系/SQL 执行/业务结果/拒答率/知识引用准确率
```

> 实际评测需真实 ClickHouse + DeepSeek 环境（`evaluate.py` 会连库执行 SQL），请在生产/隧道环境运行。

### 5.3 评估口径（任务书 §9.3 强调）

- 必须区分「SQL 能执行」与「业务结果正确」，不能只用执行成功率衡量问数质量。
- 现有 baseline 的 `result_accuracy=null`，建议为关键 golden 题补 `expected_sql`/`expected_result` 后再对照。

---

## 6. 测试结果

- `test_semantic_model_service.py`：**10/10 通过**（匹配命中/不命中/开关关闭、注入片段、`_build_system_prompt` 字节序等价性）
- `test_ossie_adapter.py`：**23/23 通过**（validate_references 修正后仍全绿）
- `run_tests.py unit`：**27 个文件全绿**（含新增 2 个）

---

## 7. 遗留问题（P3 处理）

1. **「审核通过后应用到知识库」未实现**：语义模型审核通过后，目前仅作为语义接入的来源，尚未自动同步回 7 张知识表（需定义双向同步策略 + 冲突处理）。
2. **对照评测未实际执行**：需真实库 + LLM 环境，待审核订单主题模型后由用户/运维执行（见 §5.2）。
3. **21 条关系端点数据不一致**：属既有数据问题，建议治理（见 §4.2）。
4. **Dimension 仍仅时间维度**：地区/客户维度沉淀需新增维度知识类型。
5. **`semantic_model_service` 的字段级注入未做**：当前只注入 dataset 名/source + 指标 MUST + 关系，未注入完整字段清单（控制 token 成本，后续可配置）。

---

## 8. 回滚方式

| 项 | 回滚 |
|---|---|
| Agent 接入 | `SEMANTIC_MODEL_ENABLED=0`（默认即关闭，零行为变化）；或 `git checkout agent.py config.py` |
| 服务层 | 删除 `semantic_model_service.py`，撤销 `agent.py` 两处 Edit |
| 订单主题模型 | 删除 `semantic_models` 表中「订单主题」记录（或恢复备份 `agent_config.db.bak-seed-order-*`） |
| Adapter 修正 | `git checkout ossie_adapter.py` |

## 9. 兼容性保证

- Agent 主链路 `ask_v2` 零逻辑改动（仅新增一个可空参数 + 一段 `if 开关` 包裹的注入，默认关闭）。
- 六维安全闸门、SQL Validator、权限引擎零改动。
- 现有知识表结构未动；`semantic_models` 只是新增的派生快照表。
- 未新增 pip 依赖。
