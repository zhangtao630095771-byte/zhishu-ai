# 知数AI 统一前端 UI — 改造实施报告

> 版本：V1.0 ｜ 日期：2026-11-03
> 配套：`audit-report.md`（审计）、`design-system.md`（设计规范）、`component-guidelines.md`（组件规范）、`page-guidelines.md`（页面规范）

---

## 1. 本次实施内容（已完成）

### 1.1 全局设计 Token（`frontend/static/app.css`）
在文件顶部新增 `:root`，集中定义 **59 个设计变量**：颜色（主色/语义色/文本/边框/背景/状态浅底浅边）、字体、圆角、间距。`body` 字体与背景已改为引用变量。

- **零视觉变化**：色值沿用现有 Arco 色板，全站外观不变，仅实现"集中管理"。
- **可一键换肤**：后续切换色板只需改 `:root`。

### 1.2 公共组件（`frontend/static/app.css` 底部）
新增「统一设计体系」区块：

- `.status-tag` + 修饰符（`success/warning/danger/info/gray`）— 统一状态标签，含 `.dot` 圆点。
- `.text-wrap` / `.text-nowrap` / `.text-ellipsis` — 文本换行工具类（表格长文本规范）。
- `.empty-state` / `.loading-state` / `.error-state` — 空/加载/错误状态。

### 1.3 规范文档
- `design-system.md` — 设计 Token 全表。
- `component-guidelines.md` — StatusTag/DataTable/StatCard/PageHeader/Drawer/SqlViewer 等组件用法。
- `page-guidelines.md` — 页面结构/头部/筛选/表单/详情/专属内容/响应式/质量红线。
- `audit-report.md` — 现状审计。

---

## 2. 示范页面（标准问题库）

标准问题库（`tab-golden` + `golden.js`）在上一轮产品化改造中已具备规范结构：页面标题+说明、统计卡片（`.stat-grid/.stat-card`）、多条件筛选、`.u-table` 表格（已正确换行）、详情抽屉。**它已是可复用页面模板的参照**，本次新增的设计 Token 与状态标签可直接套用。

---

## 3. 已识别但未在本次完成的改造（后续分批）

按规范 §13 实施计划，以下为剩余工作（Phase 3 逐步推广 + Phase 4 测试）：

| 项 | 优先级 | 说明 |
|---|---|---|
| 全站状态标签迁移到 `.status-tag` | P1 | 各 JS 当前用 `.badge-*`（兼容保留），逐页替换 |
| 表格 `table.data`（结果表 nowrap）审计 | P1 | 结果表 nowrap 为数值对齐有意为之，需逐页确认列表页是否误用 |
| `experience.css` 合并到 app.css / 改用 token | P2 | 消除两套 CSS 重复 |
| 各 tab 页面头部统一为 PageHeader 结构 | P2 | 逐页收敛 |
| 响应式 / 键盘交互 / 大数据量性能 | P3 | 后续体验优化 |
| 测试验收（逐页检查） | P4 | 见 §4 |

---

## 3.1 批次记录（Phase 3 逐批推广）

### 批次 1（已完成，2026-11-03）：标准问题库 + 评测看板 + 反馈处理

| 文件 | 改动 |
|---|---|
| `frontend/static/js/golden.js` | 状态标签 `.badge-*` → `.status-tag`（8 处）；「标准问题」列去掉 60 字截断，改为完整换行 |
| `frontend/static/js/feedback.js` | 状态标签 `.tag on/off` → `.status-tag`；「待处理」由红色纠正为橙色（warning） |
| `frontend/static/app.css` | `.td-question/.td-metric` 补 `word-break/overflow-wrap` |

验证：JS 语法通过；golden.js 残留 `badge-` 为 0、feedback.js 残留 `tag on/off` 为 0；服务端已加载新文件，页面 200。

> 注意：内容类标签 `.tag.layer`（指标/字典小标签）与 `.tag.warn`（原因标签）属于「内容 chip」而非状态，暂保留。

### 待续批次
- 批次 2（P1）：知识管理（kb.js）、取数审核/收藏（history.js）状态标签迁移 + 长文本换行。
- 批次 3（P2）：experience.css 合并、页面头部 PageHeader 收敛。
- 批次 4（P3）：响应式 / 键盘 / 大数据量。

---

## 4. 测试与回滚

- **可回滚**：本次改动均为**新增**（`:root` 变量 + 底部组件类 + 文档），未删除任何既有规则，CSS 括号平衡校验通过（490/490）。
- **回归**：前端页面静态服务不受影响；后端零改动。
- **待验收**：逐页检查（表格溢出/长文本换行/按钮挤压/表单/弹窗/筛选/分页/详情/状态/响应式），随 Phase 3 逐批进行。

---

## 5. 最终执行原则（遵循）

1. 不为了统一而强行重构所有页面 —— 采用「token 先行 + 组件先行 + 逐批迁移」。
2. 不重复建设已有公共组件 —— 复用 `.stat-card`/`.drawer`/`.kv`/`pre.code`/`.u-table`。
3. 不牺牲信息可读性 —— 长文本换行工具类，核心知识不省略。
4. 不改后端接口与数据库结构 —— 本次后端零改动。
5. 不引入第三方依赖 —— 纯 CSS 变量 + 类。
6. 可验证、可回滚。
