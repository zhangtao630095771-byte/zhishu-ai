# 知数AI 语义中心重构 · 总交付索引

> 参考腾讯云 DataBuddy，把「知识库管理 + AI 取数」升级为「企业数据语义管理 + AI 智能取数」。
> 完成日期：2026-11-16 · 八阶段全部交付 · 取数链路零破坏。

## 交付文档

| 文档 | 内容 |
|---|---|
| [01-现状审查报告](01-现状审查报告.md) | 只读审查：前端/后端/表结构/知识资产映射 |
| [02-重构方案设计](02-重构方案设计.md) | 四交付物：映射表/路由/数据模型/迁移回滚 |
| [03-阶段一交付](03-阶段一交付.md) | 导航重构（5 组分层）+ 语义工作台 |
| [04-阶段二交付](04-阶段二交付.md) | 本体建模（三栏 Object/Attribute/Relation/Rule） |
| [05-阶段三交付](05-阶段三交付.md) | 语义建模（关系模型/指标/维度三 Tab） |
| [06-阶段四交付](06-阶段四交付.md) | 逻辑视图（血缘/上下游/冲突提醒，只读） |
| [07-阶段七交付](07-阶段七交付.md) | AI 智能建模 + 取数融合 |
| [08-阶段八交付](08-阶段八交付.md) | 回归测试 + Golden 评测 |

## 新增代码

| 模块/文件 | 职责 |
|---|---|
| `ontology.py` | 本体建模：实体属性/规则登记 + 实体详情聚合 |
| `semantic_modeling.py` | 语义建模：关系模型表清单/表级Join/维度/指标分类 |
| `lineage.py` | 逻辑视图：语义资产血缘（只读） |
| `ai_ingest.py`（扩展） | AI 整链建模 `model_scenario` + 维度类型 |
| `frontend/static/js/ontology.js` | 本体建模三栏页面 |
| `frontend/static/js/semantic-modeling.js` | 语义建模四 Tab |
| `frontend/static/js/lineage.js` | 逻辑视图页面 |
| `test_semantic_center.py` | 语义中心不变量测试（5 项） |

## 数据模型变更（全部复用优先，只增不改）

| 落地 | 类型 |
|---|---|
| `ontology_attributes` / `ontology_rules` | 新增（实体↔属性/规则登记） |
| `semantic_model_tables` / `semantic_model_joins` | 新增（关系模型表清单/表级Join） |
| `semantic_dimensions` | 新增（维度） |
| `entities.entity_code` | 加列 |
| `metrics.metric_code` / `metric_category` | 加列 |
| 实体/关系/指标/字段注释/字典本体 | 全部复用，零复制 |

## 核心不变量（始终守住）

1. **不建第二套语义存储**：新增表全是「引用/映射/登记」，资产本体仍在原 7 张知识表。
2. **AI 内容绕过审核不生效**：`source=ai → candidate`，严格模式下不进取数检索。
3. **AI 不覆盖人工**：实体/指标/维度均有 source 护栏。
4. **取数链路零破坏**：Golden 回归门禁 PASS（Overall 100% ≥ 基线 76.5%）。
5. **每阶段可回滚**：数据库备份（`*.bak-*`）+ `git checkout`。

## 回滚速查

```bash
# 数据库（各阶段备份）
ls agent_config.db.bak-*        # ontology / modeling 阶段备份

# 代码（前端 + 后端）
git checkout -- frontend/ server.py config_store.py ai_ingest.py
rm ontology.py semantic_modeling.py lineage.py test_semantic_center.py
rm frontend/static/js/ontology.js frontend/static/js/semantic-modeling.js frontend/static/js/lineage.js
```
