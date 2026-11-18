# 知数AI 整体模块架构重构方案（第一阶段审查 + 前端整合）

> 版本 v1.0 ｜ 2026-11-09 ｜ 依据：全量源码只读审查（后端 70 个 `.py` / 前端 13 个 `.js` + `index.html` + 2 份 CSS）
> 说明：本文整合两轮审查——「后端模块重构（第一~四节）」与「前端页面整合（第五~七节）」，「第八节」给出统一分阶段方案。

---

# 第一部分 · 后端模块重构

## 一、后端现状结论（摘自已完成的只读审查）

1. **扁平单层 + 两个上帝文件**：`config_store.py`（4,372 行，36 张表 + 账号 + 权限 + 审计 + JSON 工具）、`server.py`（4,107 行，139 路由 + 鉴权 + 后台任务 + 索引 + 缓存）。
2. **3 处循环依赖**（靠函数内延迟 import 化解）：`agent ↔ sql_experience`（`sql_experience.py:1023`）、`config_store ↔ sql_experience`（`config_store.py:851`）、`config_store ↔ golden_cases`（`config_store.py:859`）。
3. **4 处跨层反向依赖**：`gaps.py:82` import `agent`；`sql_experience.py:1023/1136` import `agent`/`rag`；`prompt.py` import `rag`。
4. **黄金 SQL 两套并存**：`sql_experience.py`（案例/规则/trust_level）与 `golden_cases.py`（标准问题集）无关联字段。
5. **重复实现**：`NOISE_TABLE_MARKERS` 三处（`knowledge.py:121`/`check_consistency.py:41`/`list_tables.py:44`）；`_to_json`/`_from_json` 两套（`config_store.py:318/3495/323/3499`）。

## 二、后端目标目录树

```
zhishu-ai/
├─ app/
│  ├─ core/           config.py · exceptions.py[新] · logging.py[新] · llm.py · cache.py · calendar_util.py · utils.py[新]
│  ├─ datasource/     facade.py(db.py) · clickhouse.py · sqlite.py · ssh_tunnel.py · metadata.py[新]
│  ├─ infrastructure/ embedding.py[拆自rag] · vector_index.py[拆自rag]
│  ├─ knowledge/      store.py[拆自config_store] · knowledge.py · governance.py · retrieval.py · semantic_layer.py
│  │                  gaps.py · suggest.py · ai_ingest.py · exporter.py · enum_parse.py · join_suggest.py
│  │                  experience.py(sql_experience) · golden_cases.py
│  ├─ query/          understanding.py · prompt.py · sql_validator.py · guard.py · requirement_review.py
│  │                  sql_modify.py · result_validator.py · nl_answer.py · colname.py
│  ├─ agent/          agent.py · clarification.py
│  ├─ tasks/          task_state.py[新]
│  ├─ analysis/       __init__.py[新]
│  ├─ permission/     permission.py · store.py[拆自config_store]
│  ├─ admin/          account_store.py[拆自config_store] · cli.py(admin.py)
│  └─ api/            app.py · middleware.py · routes_{knowledge,query,admin,permission,eval}.py
├─ frontend/  tests/  evaluation/  scripts/  data/  docs/  models/  dist/
```

## 三、后端新旧映射表（节选，完整版见上轮审查）

| 当前 | 建议新路径 | 迁移风险 | 关键依据 |
|---|---|---|---|
| config_store.py | 拆 app/knowledge/store.py + permission/store.py + admin/account_store.py | 高 | 4,372 行上帝文件，30+ 模块引用 |
| server.py | 拆 app/api/routes_*.py + app/tasks | 高 | 4,107 行，业务逻辑混入 API |
| rag.py | 拆 infrastructure/embedding.py + vector_index.py + knowledge/retrieval.py | 中 | 三职责合一 |
| gaps.py / sql_experience.py | 入 knowledge/，先移除 `import agent` | 中 | 跨层反向依赖 |
| 其余 | 按域平移到 app/* | 低 | 依赖单向 |

## 四、后端拆分/合并/保留清单

- **拆分**：`config_store.py`、`server.py`、`rag.py`、`agent.py`（抽异常→core、状态→tasks）。
- **合并**：`NOISE_TABLE_MARKERS`、`_to_json`/`_from_json` → `core/utils.py`。
- **保留**：`guard.py`/`sql_validator.py`/`requirement_review.py`/`permission.py`（纯函数、边界清晰）、`db.py` 门面、`evaluation/`、`frontend/`。

---

# 第二部分 · 前端页面整合

## 五、前端现状审查

### 5.1 基本形态（实测）

- **技术栈**：原生 HTML/CSS/JS 单页应用，**无框架、无构建、无模块系统**；脚本用普通 `<script src>` 靠**全局函数 + 固定加载顺序**协作（`index.html:18` 注释明确"不用 type=module"）。
- **规模**：`index.html` 1,469 行 + `app.css`（:root ~34 token）+ `experience.css` + 13 个 JS（共 7,683 行）。
- **关键事实**：13 个 JS 文件**全部是"由一个巨型内联脚本按行拆分而来"**（每个文件头部都写明"由 index.html 内联脚本按行拆分而来，代码逐字节未改动"）。这意味着它们是**机械切分，而非按业务领域重构**。

### 5.2 文件 ↔ 页签映射（错位是核心问题）

13 个页签：ask / fav / review / prompt / kb / gov / feedback / exp / tables / users / perm / golden / dash。

| JS 文件 | 真实职责 | 覆盖页签 |
|---|---|---|
| core.js | 工具、登录态、`api()` fetch 封装、`switchTab()`、`enterApp()` | 全局基础设施 |
| boot.js | 重建索引、`bootstrapAuth()` 启动 | 全局 |
| ask.js | 取数 + 结果渲染 + 结果反馈 | ask |
| history.js | 取数审核 + 个人历史 + 收藏 + 审核轮询 | **fav + review**（3 关注点） |
| kb.js | Prompt 配置 + RAG 知识库 + 复核队列 + AI 录入 | **prompt + kb + gov(部分)** |
| metrics.js | 指标（业务口径） | kb 子页 |
| dict.js | 数据字典 | kb 子页 |
| registry.js | 语义注册表（实体/关系/文档）+ 模糊推荐 | kb/gov |
| gaps.js | 知识缺口扫描与补录 | gov |
| feedback.js | 问题反馈 + 优化建议 | feedback |
| experience.js | SQL 经验中心 + `esc()` 转义工具 | exp |
| golden.js | 标准问题库 + AI 取数质量中心（评测看板） | **golden + dash** |
| admin.js | 表与权限 + 账号管理 + 字段级数据权限 + 3 个导航角标 | **tables + users + perm** |

**错位结论**：
- **知识域被拆在 5 个文件**（kb/metrics/dict/registry/gaps），一个"知识管理"页要跨 5 个文件协作。
- `admin.js`、`kb.js`、`history.js`、`golden.js` 各跨 2~3 个页签/关注点。
- 文件划分与后端 `knowledge/query/admin` 领域边界**不对齐**。

### 5.3 跨文件耦合（真实函数定位）

核心问题是 **`core.js` 是一个"集线器"**，它的 `switchTab()`/`enterApp()` 硬编码调用分散在 8 个文件里的 14+ 个函数：

| 调用方 | 被调函数 | 定义位置 |
|---|---|---|
| core.js `enterApp()` | `loadStatus` | ask.js:9 |
| core.js `enterApp()` | `refreshFbBadge` / `refreshGovBadge` | admin.js:686 / :703 |
| core.js `enterApp()` | `refreshReviewBadge` / `loadHistory` / `loadFavorites` / `startReviewPolling` | history.js:155 / :169 / :205 / :134 |
| core.js `switchTab()` | `loadExpPage`/`loadTables`/`loadUsers`/`loadPermPage`/`loadReviewQueue`/`loadGoldenPage`/`loadDashPage`/`kbLoadOverview`/`kbLoadGaps`/`kbLoadFeedback` 等 14 个 | experience/admin/history/golden/kb/gaps/feedback.js |
| golden.js | `esc()` | experience.js:484（golden.js 头部注释自述依赖） |

**结果**：`core.js` 与几乎所有业务文件双向隐式耦合（core 调它们、它们调 core 的 `api()`），加载顺序一变即崩（`index.html:1455-1467` 固定顺序）。

### 5.4 状态管理散落（实测 ~50 个全局变量）

| 文件 | 全局状态变量 |
|---|---|
| core.js | `TOKEN_KEY`、`CURRENT_USER` |
| ask.js | `currentClarify`、`lastAnswer`、`lastQueryContextId`、`fbReasonsCache` |
| history.js | `CURRENT_RECORD`、`REVIEW_TIMER`、`HIST_ITEMS`、`RV_ITEMS`、`FAV_RECORD_IDS` |
| experience.js | `xpTab`、`xpOverview`、`xpCases`、`xpRules`、`xpUsage`、`xpDetailId`、`xpState`、`xpTimer` |
| golden.js | `GOLDEN_ITEMS`、`GOLDEN_EDIT_ID`、`DASH_FAILURES` + 中文映射常量 |
| kb.js / metrics.js / dict.js / registry.js / admin.js / gaps.js / feedback.js | `promptCache`、`metricCache`、`dictCache`、`regCache`、`ALL_TABLES`、`ALL_USERS`、`gapData`、`fbListData` 等 |

**问题**：状态散落在 10 个文件的模块级 `let`，无统一 store、无命名空间（全靠文件名前缀约定），刷新即丢（`lastAnswer`/`lastQueryContextId` 无法恢复，与后端 `query_context` 可能不一致——见异常/状态审查 §E6）。

### 5.5 CSS 现状（实测）

- `app.css` `:root` 已定义约 **34 个 design token**（`app.css:10-55`），但 `var(--)` 仅被引用 **24 次**，硬编码 `#hex` 仍有 **60 处** → token 采纳率约 30%。
- `experience.css` **0 个 `var(--)`**、2 处硬编码，`.xp-*` 12 个类与 app.css 无重复但未走 token。
- 历史遗留：两套状态标签（`.badge-*` 旧 / `.status-tag` 新，迁移未完成）、两套表格（`table.data` 旧 / `.u-table` 新）。

---

## 六、前端目标目录树（保持原生 JS，不引入框架）

```
frontend/
├─ index.html                  （保留；nav/section 与 js 目录同构）
└─ static/
   ├─ css/
   │  ├─ tokens.css            [新] 抽出 :root 设计 token
   │  ├─ components.css        [新] 统一 .status-tag/.empty-state/.table
   │  └─ app.css               （合并 experience.css 的 .xp-*，唯一入口）
   └─ js/
      ├─ core/
      │  ├─ api.js             [新] 抽自 core.js 的 api()/fetch 封装
      │  ├─ auth.js            [新] 抽自 core.js 的登录/登出/改密/401
      │  ├─ state.js           [新] 统一状态 store（收口散落 let）
      │  └─ router.js          [新] 抽自 core.js 的 switchTab/enterApp
      ├─ knowledge/
      │  ├─ kb.js              （合并 kb+metrics+dict 的知识管理部分）
      │  ├─ governance.js      （合并 kb.js 复核队列 + gaps.js）
      │  ├─ registry.js        （实体/关系/文档）
      │  ├─ experience.js      （经验中心）
      │  └─ golden.js          （标准问题 + 评测看板）
      ├─ query/
      │  └─ ask.js             （取数 + 结果 + 澄清）
      ├─ admin/
      │  ├─ tables.js          （拆自 admin.js 表与权限）
      │  ├─ users.js           （拆自 admin.js 账号管理）
      │  ├─ permission.js      （拆自 admin.js 数据权限）
      │  └─ badges.js          [新] 角标逻辑收口
      ├─ feedback.js
      ├─ history.js            （审核 + 历史 + 收藏）
      └─ boot.js
```

**整合原则**：
1. **不引入框架/构建**（项目既定约定，`docs/frontend/ui-audit-report.md` 明确"不重构前端技术栈"）。
2. **引入 4 个轻量基础设施文件**（`api.js`/`auth.js`/`state.js`/`router.js`），杀掉 `core.js` 集线器与散落全局变量。
3. **知识域 5 个文件收敛为 knowledge/ 目录**，与后端 `app/knowledge/` 对齐。
4. **CSS 三合一**：token 独立 + 组件统一 + 单一入口，退役 `experience.css`。

---

## 七、前端新旧映射表

| 当前文件 | 建议新路径 | 整合动作 | 依据 |
|---|---|---|---|
| core.js | 拆 core/api.js + auth.js + router.js | 拆分 | `api()`(core.js:35)、`switchTab()`(core.js:222) 混杂工具/登录/路由三职责 |
| boot.js | js/boot.js | 保留（轻） | 仅重建索引 + bootstrapAuth |
| ask.js | query/ask.js | 平移 | 取数主链路 |
| kb.js | knowledge/kb.js + governance.js | 拆分 | 跨 prompt/kb/gov 三页签 + AI录入 + 复核队列 |
| metrics.js | knowledge/kb.js 内 | 合并 | 指标是知识管理的子页 |
| dict.js | knowledge/kb.js 内 | 合并 | 字典是知识管理的子页 |
| registry.js | knowledge/registry.js | 平移 | 语义注册表 |
| gaps.js | knowledge/governance.js 内 | 合并 | 缺口扫描属治理 |
| feedback.js | js/feedback.js | 平移 | 独立功能 |
| experience.js | knowledge/experience.js | 平移 | 经验中心（`esc()` 移入 core/api.js 或 utils） |
| golden.js | knowledge/golden.js | 平移 | 标准问题 + 看板 |
| history.js | js/history.js | 保留 | 审核/历史/收藏同属"记录"域 |
| admin.js | 拆 admin/{tables,users,permission,badges}.js | 拆分 | 跨 3 页签 + 3 角标 |
| app.css | css/{tokens,components,app}.css | 拆分 | :root 34 token 仅 24 次引用、硬编码 60 处 |
| experience.css | 合并进 app.css | 合并/退役 | 0 token、独立 `.xp-*` 未走设计体系 |

---

# 第三部分 · 统一分阶段方案

## 八、分阶段重构（含优先级与回归）

| 阶段 | 动作 | 优先级 | 回归要求 |
|---|---|---|---|
| **P0** | 后端：新增 `core/exceptions.py`+`core/logging.py`，`llm.py` 加错误分类、`agent.py:761` 补超时；解 3 处循环依赖 | P0（稳定性/正确性） | `run_tests.py` 全绿 + `test_gate.py`(126) + `test_imports.py`(26) |
| **P0** | 前端：新增 `core/state.js` 统一状态 store + 刷新后从 `/api/records` 恢复上下文 | P0（状态一致性） | `test_frontend_structure.py` + 无头浏览器冒烟 |
| **P1** | 后端：拆 `config_store.py`、`server.py`；移除跨层反向依赖（gaps/sql_experience→agent） | P1（边界混乱） | `test_permission`(60)+`test_acl`+`test_knowledge_governance`(47)+迁移幂等 |
| **P1** | 前端：知识域 5 文件收敛到 `knowledge/`；CSS 三合一退役 experience.css；拆 admin.js | P1（文件错位/重复） | 13 页签切换 + 登录/改密/401 手工回归 + Playwright DOM 校验 |
| **P2** | 后端：`rag.py` 拆 Embedding/索引；物理移动到 `app/*`；收口重复实现 | P2（组织/扩展） | `evaluate.py --regression --gate` + `test_retrieve` |
| **P2** | 前端：token 化推进（60 处硬编码收敛）、统一 `.status-tag` 迁移完成、目录命名统一 | P2（一致性） | 视觉回归截图 + 组件清单核对 |

## 九、不建议迁移/整合的文件

| 文件 | 原因 |
|---|---|
| `models/`（embedding 缓存） | 运行时依赖，移动导致内网启动失败 |
| `agent_config.db` + `*.bak-*`、`.env` | 数据资产与密钥，非代码 |
| `dist/`、`zhishu-ai-code-bundle/`、`config.db`(0字节)、`check1.py`、`_probe_driver_13.py` | 打包副本/遗留文件，归档不迁移 |
| `evaluation/`、`docs/`、`design/` | 已天然分层，保持原位 |
| `index.html` | 保留骨架，仅 nav/section 与 js 目录对齐；不动内联结构与数据属性契约 |

---

*本方案为第一阶段只读审查结论，未改动任何代码。第二阶段实施时，建议严格按 P0→P1→P2 顺序推进，每阶段以对应回归全绿作为进入下一阶段的门禁。*
