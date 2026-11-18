# 知数AI 业务域与标准化业务语义模型 · 第五步交付报告（数据表迁移 + 后端改造）

> 日期：2026-11-12 · 阶段：第五步（数据表迁移 + 后端改造；前端 + Agent 为第六步）

---

## 1. 本阶段完成内容

| # | 内容 | 状态 |
|---|---|---|
| 1 | 表结构迁移（data_domains 加 department、business_subjects、topic_assets 加 scope） | ✅ |
| 2 | 业务域 CRUD（domain_type='business_domain'）+ 业务主题 CRUD | ✅ |
| 3 | 统一语义 API（/api/domains、/api/subjects、/api/semantic/objects、发布快照） | ✅ |
| 4 | 业务域初始化种子（5 域 + 40 实体 + 51 指标） | ✅ |

---

## 2. 修改 / 新增文件清单

| 文件 | 类型 | 说明 |
|---|---|---|
| `config_store.py` | 修改 | `_migrate_business_domain`（3 项迁移）；业务域/主题 CRUD（`list/upsert/delete_business_domain`、`list/upsert/delete_business_subject`、`list/add/remove_scope_asset`），替换原 business_topic 实现 |
| `semantic_object_builder.py` | 修改 | `_topic_entity_names`/`_topic_metric_names` 改用 `list_scope_assets(scope='subject')` |
| `server.py` | 修改 | 新增 BizDomainBody/BizSubjectBody/ScopeAssetBody + 14 个业务域/主题/关联/语义对象/发布 API |
| `_seed_business_domains.py` | 新增 | 业务域初始化种子（从现有实体/指标推断，幂等） |

---

## 3. 数据模型（复用 + 扩展，只增不改）

| 对象 | 承载 | 说明 |
|---|---|---|
| 业务域 Business Domain | `data_domains`（`domain_type='business_domain'`）+ `department` | 复用空表，不新建 |
| 业务主题 Business Subject | `business_subjects`（新表） | 挂在业务域下 |
| 资产关联（多对多） | `topic_assets` + `scope` 列 | scope=domain/subject；asset_type 扩展为 entity/metric/relationship/golden_sql/doc/rule/feedback |
| Semantic Model | 动态映射 + `semantic_models` 发布快照 | 编辑态动态组装，发布态版本固化 |

---

## 4. 业务域初始化结果（真实数据推断）

| 业务域 | 主题 | 实体 | 指标 | 资产合计 |
|---|---|---|---|---|
| 订单 order | order_operation | 19 | 27 | 46 |
| 司机 driver | driver_operation | 9 | 17 | 26 |
| 财务 finance | finance | 7 | 4 | 11 |
| 营销 marketing | marketing | 4 | 1 | 5 |
| 救援 rescue | rescue | 1 | 2 | 3 |

> 6 个违规/审核类实体未自动归类（用户登录活跃、系统用户表、转卖单审核表、转卖单违规表、违规信息表、违规处罚配置表），可人工归到「风控」域。
> 备份：`agent_config.db.bak-bizdomain-*`

---

## 5. 统一语义 API

| 接口 | 说明 |
|---|---|
| `GET/POST /api/domains`、`GET/PUT/DELETE /api/domains/{name}` | 业务域 CRUD |
| `GET/POST /api/subjects`、`DELETE /api/subjects/{name}` | 业务主题 CRUD |
| `GET/POST/DELETE /api/domains/{name}/assets`、`/api/subjects/{name}/assets` | 域/主题资产关联 |
| `GET /api/semantic/objects?subject=&domain=` | 统一语义读取（Dataset/Field/Metric/Dimension/Relationship/AI Context） |
| `POST /api/semantic/models/{name}/publish` | 发布快照（动态组装 → 固化，可追溯回滚） |

---

## 6. 测试结果

- `run_tests.py unit`：27 文件全绿；`config`：9 文件全绿
- 集成冒烟：业务域/主题/资产 CRUD、统一语义对象、发布快照全链路通过
- 按业务主题动态组装：订单经营（19 数据集 + 14 指标 + 1 关系，Schema 通过）、司机运营（9 + 14）

---

## 7. 兼容性与回滚

| 项 | 说明 |
|---|---|
| 表迁移 | ALTER ADD COLUMN + CREATE TABLE IF NOT EXISTS，幂等只增 |
| domain_type 取值 | 新增 `business_domain`，原 `data_domain`（数据域）零影响 |
| 旧 business_topic 代码 | 已替换为 business_domain + business_subjects（无外部引用残留） |
| 回滚 | 恢复备份 `agent_config.db.bak-bizdomain-*` 或删除新增列/表 |

---

## 8. 第六步剩余（前端 + Agent）

1. **前端业务域管理**：业务域列表/详情、业务主题管理、资产关联、语义模型可视化维护、域级质量检查（复用现有「数据权限→数据域」页或新增「业务域」页）。
2. **Agent 业务域识别接入**（第五阶段核心）：
   - 业务域识别：先用「实体/关键词 → 业务域」规则映射（已有 `_match_domain`），后用 LLM；
   - `ask_v2` 接入：`get_semantic_objects` 替换 `semantic_model_service` 快照读取；
   - 跨域查询：组合多个已发布 Semantic Model + 校验跨域关系与指标口径。
3. **真实业务评估**：对照测试 + 指标口径校验。

---

## 9. 关键原则回顾

**业务域组织企业业务范围，OSSIE 标准化语义表达，知识资产多对多关联，不重复建设 Semantic Layer。**
- 业务域/主题是知数AI 内部组织/治理模型，不强行改 OSSIE 原生定义；
- 结构化资产必须归属业务域，非结构化资产（Golden SQL/文档/规则）仅关联、不强制转换；
- Agent 业务域识别作为「选择语义模型 + 限定知识范围」的依据，不只是 RAG 标签。
