# 知识中心整合 + OSSIE 标准兼容改造 · P3 交付报告（知识闭环 + 治理）

> 交付日期：2026-11-12 · 阶段：P3（语义模型「应用到知识库」闭环 + 关系端点治理清单）

---

## 1. 本阶段目标

1. **打通治理闭环**：审核通过的语义模型 → 同步回 7 张知识表 → 进入 RAG/Agent 检索（任务书 §2.3「知识治理贯穿生命周期」）。
2. **关系端点数据治理**：只读审查 `relationships` 表的数据不一致，输出治理清单（不改数据）。

---

## 2. 修改 / 新增文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `semantic_model_service.py` | 修改 | 新增 `apply_model_to_kb`：把 approved 语义模型的 dataset/metric/relationship 同步回知识表（只新增、绝不覆盖） |
| `server.py` | 修改 | import semantic_model_service；新增 `POST /api/semantic-models/{id}/apply` 接口（仅 approved 可应用） |
| `frontend/static/js/semantic-model.js` | 修改 | 详情页新增「应用到知识库」按钮（仅 approved 显示）+ `smApplyToKb` 处理函数 |
| `docs/knowledge-center-ossie/04-relationship-endpoint-audit.md` | 新增 | 关系端点数据不一致治理清单 |

---

## 3. 「应用到知识库」闭环设计

### 3.1 同步映射

| 语义模型对象 | 同步到知识表 | 字段映射 |
|---|---|---|
| Dataset | `entities` | name→name，source→table_name，grain→grain，description→comment，synonyms→keywords |
| Metric | `metrics` | name，sql_template，definition，metric_type，tables，keywords + 扩展属性里的硬约束（required_predicates 等） |
| Relationship | `relationships` | from/to_entity，join_sql，cardinality，pre_agg_required，rule_note |

### 3.2 保守策略（可回滚、可审计）

- **只新增，绝不覆盖**：同名对象已存在 → 跳过（不动人工维护的知识），记录 skipped。
- **状态推导**：模型已审核 → 同步对象 `source='manual'` → 状态推导为 `approved`（复用 `config_store._derive_gov_status` 唯一决策点）。
- **全程审计**：走 `config_store.upsert_*`，自动写 `knowledge_history`，可追溯。
- **前置校验**：仅 `approved` 模型可应用（API 层拦截，否则 400）。

### 3.3 为什么这样设计（消融视角）

现有 upsert 函数已带「AI 不覆盖人工」护栏 + 状态推导 + 审计。`apply_model_to_kb` 不重复造这些，只补一层「同步映射 + 先查存在再决定是否新增」，复用现有能力，避免第二套写入逻辑。

---

## 4. 关系端点治理清单（只读）

扫描 `relationships` 表（22 条）+ 与 `entities` 交叉核对，结论：

| 类别 | 数量 | 处理 |
|---|---|---|
| 端点正常（实体名 或 已登记主表） | 19 | 无需处理 |
| 端点缺失（需治理） | **3** | 见下表 |

需治理的 3 条：

| 关系 | 缺失端点 | 根因 | 建议 |
|---|---|---|---|
| 大板订单 → 大板费用 | `大板订单` | 实体已改名「大板订单表」，关系未同步 | from 改为「大板订单表」或补别名实体 |
| tu_guaranty_order → tu_guaranty_order_scheme | 后者用表名 | 端点用表名，实体「保障订单方案」存在 | to 改为实体名「保障订单方案」 |
| ods_tu_customer_all → dwd_ctc_user_login_record_all | 后者无实体 | 端点用表名且无对应实体 | 补实体「客户登录记录」或改 to 为已有实体 |

**根因**：`relationships.from_entity/to_entity` 取值约定不统一（实体名 vs 物理表名混用），加上实体改名/去重后关系未同步。

> 本清单为**只读审查**，未改数据。修复需你确认后（在「语义注册表」页人工操作，或由我生成幂等修复脚本）。

---

## 5. 测试结果

- `test_semantic_model_service.py`：10/10 通过
- `test_ossie_adapter.py`：23/23 通过
- `run_tests.py unit`：27 个文件全绿
- `apply_model_to_kb` 冒烟验证：已存在对象跳过（不覆盖）、新对象新增且 status=approved、写 knowledge_history 审计

---

## 6. 使用方式

```bash
# ① 审核订单主题模型（页面「语义模型」→ 选模型 → 点「审核通过」）
# ② 应用（页面点「应用到知识库」，或 API）：
curl -X POST http://127.0.0.1:8000/api/semantic-models/4/apply \
  -H "Authorization: Bearer <token>"
# ③ 点「重建索引」后，同步的实体/指标/关系进入 RAG
```

> 当前「订单主题」模型为 candidate，需先审核为 approved 才能应用（API 会拦截非 approved）。

---

## 7. 遗留（P3 剩余 / 后续）

1. **对外语义模型 API / MCP 接入**（任务书 P3 正题）：对外提供语义模型读取 API、评估 MCP、支持外部 BI/AI Agent 读取语义资产——尚未做。
2. **3 条关系端点修复**：需你确认后执行（当前只出了清单）。
3. **「应用到知识库」的字段级同步**：当前 dataset 的 fields（字段注释）未同步回 `column_comments`（同步的是实体/指标/关系；字段注释同步需额外映射 `column_comments`）。
4. **对照评测**：审核订单主题模型 + 开启 `SEMANTIC_MODEL_ENABLED=1`，跑 golden 集做接入前后对照（需真实库 + LLM）。

---

## 8. 回滚方式

| 项 | 回滚 |
|---|---|
| apply 接口 | `git checkout server.py frontend/static/js/semantic-model.js` |
| apply 函数 | `git checkout semantic_model_service.py` |
| 已同步的知识 | 同步动作走 `knowledge_history` 审计；如需回滚，按审计记录逐条删除新增对象，或恢复 `agent_config.db` 备份 |
| 治理清单 | 删除 `04-relationship-endpoint-audit.md`（只读文档，无副作用） |

## 9. 兼容性保证

- `apply_model_to_kb` 是纯新增函数，不影响现有链路。
- 同步动作「只新增不覆盖」，不改动任何现有知识对象。
- 未新增 pip 依赖；未改 7 张知识表结构。
