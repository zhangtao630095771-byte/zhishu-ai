# 知识中心整合 + OSSIE 标准兼容改造 · P1 交付报告

> 交付日期：2026-11-12 · 阶段：P1（知识中心入口整合 + OSSIE Adapter 基础能力）
> 前置：`docs/knowledge-center-ossie/01-review-report.md`（P0 审查，已获用户确认按推荐方案执行）

---

## 1. 采纳的 6 项决策（P0 审查的待确认项）

| # | 决策点 | 采纳方案 |
|---|---|---|
| 1 | 双流水线前提 | 按「单流水线内语义接入」处理（Legacy 已退役，无需兼容两条流水线） |
| 2 | OSSIE 版本固定 | 锁定 main commit `e6ad94ee`（0.3.0.dev0，含 `nonportable_extensions`），内置 schema 副本 |
| 3 | 信息架构 | 最小改动：「知识运营」组改名「知识中心」，「知识管理」改名「知识资产」，新增「语义模型」tab |
| 4 | Semantic Model 容器 | 一个业务主题 = 一个模型 |
| 5 | Metric 映射 | `sql_template` → `expression.sql`、`definition` → `description` |
| 6 | Dimension 缺口 | 本次只映射时间维度（字段类型含 date/datetime/time → `is_time: true`） |

---

## 2. 修改 / 新增文件清单

### 2.1 新增文件

| 文件 | 说明 |
|---|---|
| `ossie_schema/ossie-schema.json` | 内置固定版本的 JSON Schema（draft 2020-12） |
| `ossie_schema/spec.yaml` | 内置规范 YAML |
| `ossie_schema/spec.md` | 内置规范全文 |
| `ossie_schema/VERSION.md` | 版本锁定记录（commit / 版本 / 日期 / 升级约定） |
| `ossie_adapter.py` | OSSIE 适配层（唯一入口，禁止散落） |
| `frontend/static/js/semantic-model.js` | 语义模型前端逻辑 |
| `test_ossie_adapter.py` | 适配层测试（23 项） |
| `docs/knowledge-center-ossie/01-review-report.md` | P0 审查报告 |
| `docs/knowledge-center-ossie/02-p1-delivery.md` | 本报告 |

### 2.2 修改文件

| 文件 | 改动 |
|---|---|
| `config_store.py` | 新增 `_migrate_semantic_models` + 语义模型 CRUD / 版本 / 日志函数；`init_store` 注册迁移 |
| `server.py` | import `ossie_adapter`；新增 3 个 Body 模型；新增 12 个语义模型/OSSIE API |
| `frontend/index.html` | 导航组改名 + 新增「语义模型」tab + section + script 引入 |
| `frontend/static/js/core.js` | `switchTab` 增加 `semantic` 懒加载 |
| `run_tests.py` | 注册 `test_ossie_adapter.py`（unit 层） |

---

## 3. OSSIE Adapter 能力（`ossie_adapter.py`）

| 能力 | 函数 | 说明 |
|---|---|---|
| Schema 版本管理 | `OSSIE_VERSION` / `SCHEMA_COMMIT` / `detect_version` / `is_supported_version` | 锁 commit，不追 main |
| YAML/JSON 解析 | `parse_document` / `detect_format` / `to_yaml` / `to_json` | 自动识别格式，PyYAML safe_load |
| 标准格式校验 | `validate_document` | 内置精简 JSON Schema 校验器（支持 $ref 多层 Pointer + anyOf/oneOf/const/enum/additionalProperties） |
| 双向转换 | `build_model` / `parse_model` | 内部语义模型 ⇄ OSSIE 标准模型 |
| 扩展属性 | `nonportable_extensions`（`vendor_name=zhishu-ai`） | 保留 P0/P1/P2 分级、审核状态、粒度、Join 基数等 |
| 引用完整性 | `validate_references` | 关系 from/to 指向、字段数、指标表达式 dataset 引用 |
| 冲突识别 | `detect_conflicts` | 与现有对象名对比，区分新增/已存在/冲突 |
| 兼容性报告 | `compatibility_report` | 对象计数 + 扩展属性说明 + 告警 |
| 往返一致性 | `roundtrip_check` | 内部→OSSIE→内部语义无损对比 |

## 4. 新增 API（12 个）

| 接口 | 方法 | 说明 |
|---|---|---|
| `/api/semantic-models` | GET | 列表（含计数统计） |
| `/api/semantic-models` | POST | 新建（默认 draft） |
| `/api/semantic-models/{id}` | GET | 详情 |
| `/api/semantic-models/{id}` | PUT | 编辑（version+1，写版本快照） |
| `/api/semantic-models/{id}` | DELETE | 删除 |
| `/api/semantic-models/{id}/status` | PUT | 审核状态流转 |
| `/api/semantic-models/{id}/versions` | GET | 版本列表 |
| `/api/semantic-models/ossie/info` | GET | OSSIE 版本信息 |
| `/api/semantic-models/ossie/preview` | POST | 导入预览（解析+校验+冲突，不落库） |
| `/api/semantic-models/ossie/import` | POST | 确认导入（落库 candidate + 记日志） |
| `/api/semantic-models/{id}/ossie/export` | GET | 导出 YAML/JSON（校验后下载） |
| `/api/semantic-models/ossie/logs` | GET | 导入导出日志 |

全部默认 `admin` 权限（走现有 RBAC 中间件，新增接口自动受保护）。

## 5. 数据库结构变化（只增不改）

| 表 | 说明 |
|---|---|
| `semantic_models`（新） | 语义模型容器：name(唯一)/description/status/source/version/model_json/source_system/审核字段 |
| `semantic_model_versions`（新） | 版本快照（append-only） |
| `ossie_import_log`（新） | 导入日志 |
| `ossie_export_log`（新） | 导出日志 |

- 全部 `CREATE TABLE IF NOT EXISTS`（幂等），迁移失败不挡启动。
- 未改动现有 7 张知识表结构；`semantic_models.model_json` 是快照，事实源仍在原知识表。

## 6. 前端页面变化

| 项 | 说明 |
|---|---|
| 导航 | 「知识运营」组 → 「知识中心」；「知识管理」→「知识资产」；新增「语义模型」tab |
| 语义模型页 | 模型列表（含状态徽章 + 计数）· 模型详情（datasets/metrics/relationships）· 新建/删除/审核 · OSSIE 导入（文件上传→预览→确认）· OSSIE 导出（YAML/JSON 下载） |
| 复用 | 复用现有 `api()` / `esc()` / `.list-item` / `.badge` 组件与样式，未另建审核系统 |

## 7. 测试结果

- `test_ossie_adapter.py`：**23/23 通过**（Schema 校验、版本识别、双向转换、往返一致性、引用完整性、冲突识别、兼容性报告、时间维度映射）
- `run_tests.py unit`：**25 个文件全部通过**（含新增 test_ossie_adapter）
- `run_tests.py config`：**9 个文件全部通过**
- 接口集成冒烟：登录→改密→创建→OSSIE 导入预览→导入（candidate）→导出 YAML→审核→日志，全链路通过；错误版本导入被 400 正确拦截

## 8. 遗留问题（P2/P3 处理）

1. **OSSIE 导入暂不写回 7 张知识表**：导入的模型存入 `semantic_models`（candidate），「审核通过后应用到知识库」尚未实现（P2）。
2. **Agent 尚未接入语义模型读取**：`retrieve_semantic` 之后接入语义模型约束属 P2，本次未动 Agent 主链路（符合「不破坏现有流水线」）。
3. **Dimension 仅映射时间维度**：地区/客户维度沉淀需新增维度知识类型（P2）。
4. **Metric expression 双向无损**：`definition`（自然语言口径）与 `sql_template`（结构化 SQL）分离存储，往返时自然语言口径存 `description`；复杂口径表达式的等价性验证未覆盖（P2 评估）。
5. **`test_ossie_adapter.py` 未覆盖 config_store 语义模型 CRUD**：CRUD 已通过接口冒烟验证，但未纳入自动化测试（P2 补 `test_semantic_model.py`）。

## 9. 回滚方式

| 项 | 回滚 |
|---|---|
| 前端 | `git checkout frontend/index.html frontend/static/js/core.js`，删除 `semantic-model.js` |
| 后端 | `git checkout server.py config_store.py`，删除 `ossie_adapter.py` |
| 数据库 | 新表为 `CREATE TABLE IF NOT EXISTS` 只增；删除 `semantic_models` / `semantic_model_versions` / `ossie_import_log` / `ossie_export_log` 四张表即可，不影响现有知识 |
| OSSIE schema | 内置副本不联网，删除 `ossie_schema/` 目录即无副作用 |
| 依赖 | 未新增第三方依赖（复用已有 PyYAML；校验器为内置实现） |

## 10. 兼容性保证

- 现有 7 张知识表结构未动；知识管理/治理页面 data-tab 未变（仅改显示文案），现有 `switchTab('kb'/'gov')` 引用全部兼容。
- Agent 主链路（`ask_v2`）、六维安全闸门、SQL Validator、权限引擎零改动。
- 未新增 pip 依赖；`requirements.txt` 未改。
