# 知数AI 业务域与标准化业务语义模型 · 第六步交付报告（Agent 接入）

> 日期：2026-11-13 · 阶段：第六步（Agent 接入业务域语义；前端业务域页 + 真实评估待续）

---

## 1. 本阶段完成内容

| # | 内容 | 状态 |
|---|---|---|
| 1 | 业务域识别服务（`business_domain_service`） | ✅ |
| 2 | Agent 接入：语义注入从「快照读取」切换到「业务域动态映射」 | ✅ |

---

## 2. 修改 / 新增文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `business_domain_service.py` | 新增 | `match_subjects`（实体/指标反查业务主题→域）+ `build_context_block`（只注入命中的实体/指标/关系渲染为 Prompt 约束段） |
| `config_store.py` | 修改 | 新增 `get_subjects_by_assets`（资产反向查主题）+ `get_subject_domain_map` |
| `agent.py` | 修改 | import business_domain_service；`ask_v2` 语义注入从 `semantic_model_service` 切换到 `business_domain_service` |

---

## 3. Agent 使用语义知识链路（第六部分要求）

```
用户问题
  → query_understanding（意图/实体/指标识别）
  → semantic_layer.retrieve_semantic（P0~P4 多路检索）
  → ★ 业务域识别（实体/指标 → topic_assets 反查 → 主题 → 域）【新增】
  → ★ 语义对象获取（semantic_object_builder 动态组装，只取命中的实体/指标/关系）【新增】
  → 语义约束注入 Prompt（业务域语义约束段，优先级高于经验参考）
  → SQL 生成 → 六维闸门 → 执行
```

### 3.1 业务域识别的依据

- **不只是 RAG 标签**：业务域识别用于「选择语义模型 + 限定知识范围」；
- 规则映射（先规则后 LLM）：实体名/指标名 → `topic_assets` 反查归属主题 → 主题 → 域；
- 命中多个域时允许组合多个主题的语义对象（`SEMANTIC_MODEL_MAX` 限制数量）。

### 3.2 只注入命中的资产（避免 Prompt 过长）

优化后，注入只含**本次命中的实体/指标/相关关系**（不是整个主题），实测：

```
## 业务主题「order_operation」（业务域 order）
### 数据集（Dataset）
- 代驾订单（物理表 ods_valet_order_all）
  时间维度：order_time
### 指标（Metric，必须严格按此口径）
- 完单量：订单状态为已完成(status=6)的订单数，即 COUNT(*) WHERE status=6
### 关系（Relationship）
- 代驾订单 → 代驾费用
```

---

## 4. 安全与可回退保证

- 默认关闭（`SEMANTIC_MODEL_ENABLED=False`），行为与接入前完全一致；
- 只读知识表（反查 + 动态组装），不写数据；
- 未命中业务域/主题 → 空串，走原有 P0~P4 检索，零影响；
- 指标 expression 未审核（`expression_status != approved`）不注入权威 SQL，只用 description（决策 5）；
- 整段包 try，注入失败绝不影响取数。

---

## 5. 测试结果

- `run_tests.py unit`：27 文件全绿；`config`：9 文件全绿
- 冒烟：业务域识别（代驾订单/完单量 → order_operation/order）、注入片段精准、开关关闭空串

---

## 6. 剩余（第六步收尾）

1. **前端业务域管理页**：业务域列表/详情、业务主题管理、资产关联、语义模型可视化维护、域级质量检查、YAML 预览。
2. **真实业务评估**：对照测试（`SEMANTIC_MODEL_ENABLED=0/1`）+ 指标口径校验 + 跨域查询校验。

---

## 7. 回滚方式

| 项 | 回滚 |
|---|---|
| Agent 接入 | `SEMANTIC_MODEL_ENABLED=0`（默认即关闭）；或 `git checkout agent.py` |
| 服务层 | 删除 `business_domain_service.py`，撤销 `agent.py` 两处 Edit |
| 数据 | `get_subjects_by_assets`/`get_subject_domain_map` 为只读，无副作用 |

---

## 8. 关键原则回顾

**业务域组织企业业务范围，OSSIE 标准化语义表达，Agent 先识别业务域再取语义资产。**
- 业务域识别是「选择语义模型 + 限定知识范围」的依据，不只是标签；
- 语义对象动态派生自知识表，不重复存储；
- 未审核的 expression 不作为权威口径（决策 5）。
