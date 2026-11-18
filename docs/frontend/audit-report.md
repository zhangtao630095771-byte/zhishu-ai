# 知数AI 统一前端 UI — 现状审计报告

> 阶段：第一阶段（现状审计，未改代码）
> 日期：2026-11-03
> 范围：`frontend/` 全量

---

## 0. 结论速览

1. **技术栈**：原生 HTML + CSS + 原生 JS，**无框架、无构建工具、无组件库**。单页应用 `index.html`（1469 行）+ 13 个 JS 文件（约 5400 行）+ 2 个 CSS 文件。
2. **无设计 Token**：全站无任何 `:root` CSS 变量，约 30 种颜色全部硬编码（Arco 风格色板）。
3. **两套表格体系并存**：旧的 `table.data`（`td` 默认 `white-space: nowrap`，正是长文本溢出的根因）与较新的 `.u-table`（已修复该问题）。
4. **两套 CSS 文件有重复**：`experience.css` 的 `.xp-*` 重复定义颜色与卡片样式。
5. **已有可复用的类**：`.stat-card`、`.badge-*`、`.drawer`、`.kv-*`、`.u-table`、`.panel`、`pre.code` 等，但命名风格不一、未 token 化。

---

## 1. 技术栈与项目结构

| 项 | 现状 |
|---|---|
| 框架 | 无（原生 HTML/CSS/JS） |
| 构建 | 无（直接静态文件服务） |
| 路由 | 单页 tab 切换（`switchTab`，14 个 tab） |
| 组件库 | 无（公共能力以 CSS 类 + JS 函数形式散落） |
| 状态管理 | 无（全局变量 + DOM 操作） |

### 文件清单

```
frontend/index.html          # 单页骨架：14 个 <section class="tab">
frontend/static/app.css      # 全局样式（1035 行，色板/布局/组件/各页签骨架）
frontend/static/experience.css # SQL 经验中心专用样式（.xp-*，与 app.css 有重复）
frontend/static/js/*.js      # 13 个文件，按业务域拆分
```

### 页面（tab）清单

| tab | 标题 | 对应 JS |
|---|---|---|
| ask | 智能取数 | ask.js |
| fav | 收藏取数 | history.js |
| review | 取数审核 | history.js |
| prompt | Prompt 配置 | admin.js |
| kb | 知识管理 | kb.js |
| gov | 知识治理 | gaps.js |
| feedback | 反馈处理 | feedback.js |
| exp | SQL 经验 | experience.js |
| tables | 表与权限 | registry.js |
| users | 账号管理 | admin.js |
| perm | 数据权限 | admin.js |
| golden | 标准问题（标准问题库） | golden.js |
| dash | 评测看板 | golden.js |

---

## 2. 现有色板（硬编码，Arco 风格）

统计自 `app.css` + `experience.css`（按使用次数）：

| 语义 | 现值 | 次数 |
|---|---|---|
| 主色蓝 | `#165dff` | 38 |
| 次要文本 | `#86909c` | 37 |
| 正文次级 | `#4e5969` | 33 |
| 边框 | `#e5e6eb` | 31 |
| 主文本 | `#1f2329` / `#1d2129` | 39 |
| 危险红 | `#f53f3f` | 19 |
| 页面背景 | `#f7f8fa` | 17 |
| 警告橙 | `#ff7d00` | 14 |
| 成功绿 | `#00b42a` | 12 |
| 各色浅底/浅边 | `#fff7e8/#ffece8/#e8ffea/#e8f3ff` 等 | 各 5~10 |

**结论**：现有色板本身是自洽的（Arco Design 色系），只是**硬编码、未 token 化**，且 `experience.css` 里重复定义了一小撮近似值（`#eef0f4`、`#d4380d`、`#0f8a5f` 等）。

---

## 3. 现有可复用类（组件雏形）

| 能力 | 现状类名 | 位置 |
|---|---|---|
| 统计卡片 | `.stat-grid` / `.stat-card`(+`.blue/.green/.orange/.gray`) / `.stat-val` / `.stat-label` | app.css:917 |
| 状态标签 | `.badge-green/.orange/.red/.gray/.blue` | app.css:937 |
| 抽屉 | `.drawer-mask` / `.drawer` / `.drawer-head/body/foot` | app.css:951 |
| 键值对 | `.kv` / `.kv-k` / `.kv-v` / `.kv-row` | app.css:979 |
| 表格 | `table.data`（旧）、`.u-table`（新） | app.css:80 / 528 |
| 代码块 | `pre.code` | app.css:70 |
| 按钮 | `button` / `.ghost` / `.danger` / `a.btn` | app.css:60 |
| 面板 | `.panel` / `.panel-head` / `.panel-title` | 各 tab 骨架 |
| 布局 | `.row` / `.spacer` / `.filters` / `.muted` | 散落 |

**结论**：公共组件已有雏形，但：
- 命名风格不一（`.badge-green` 用后缀，`.stat-card.green` 用修饰符）。
- 未 token 化，颜色硬编码。
- `table.data` 与 `.u-table` 两套并存。

---

## 4. 问题清单（对照规范书）

### 4.1 无设计 Token（规范 §2.2，P0）
全站 0 个 `:root` 变量，颜色硬编码。任何全局换肤/统一都要改几十处。

### 4.2 表格长文本溢出（规范 §4.3，P0）
`table.data td { white-space: nowrap }`（app.css:84）——**所有单元格拒绝换行**，长文本（问题描述、标准问题、业务说明）会撑破表格。仅 `.acl-table`（app.css:105）和部分 `.kb-cols`（app.css:231）做了局部覆盖。较新的 `.u-table`（app.css:528）本身没问题，但未推广到所有列表。

### 4.3 两套表格体系（规范 §4，P0）
`table.data`（旧，nowrap + 等宽字体）与 `.u-table`（新，正常换行 + 悬停高亮）并存，风格不一致。

### 4.4 两份 CSS 文件重复（规范 §2，P0）
`experience.css` 重复定义颜色与卡片样式，与 `app.css` 有重叠，维护成本高。

### 4.5 状态标签命名不一致（规范 §8，P1）
`.badge-*` 与 `.stat-card.*` 两套命名；且状态映射分散在各 JS 里（英文状态中文化此前已部分处理）。

### 4.6 部分核心内容被省略号截断（规范 §9.3，P1）
`.tcmt-txt`、`.fb-q` 等处对业务内容用 `text-overflow: ellipsis`，对"次要信息"可接受，但对核心知识（标准问题、指标说明）不符合规范。

### 4.7 布局问题（规范 §12，P0）
- `table.data` nowrap 导致表格撑破卡片（历史已知问题，部分页面用 `.table-scroll` 兜底）。
- 各 tab 页面头部结构不一（部分有标题+说明，部分只有标题）。

---

## 5. 改造影响分析（保守方案）

| 事项 | 建议 | 风险 |
|---|---|---|
| 建立 `:root` 设计 Token | **用现有色板映射为变量**（不换色，视觉不变，只做集中） | 低 |
| 统一表格 | 推广 `.u-table`，对长文本列显式换行 | 低（局部覆盖） |
| 合并 experience.css | 迁移 `.xp-*` 到 app.css 或改引用变量 | 中（需回归 SQL 经验页） |
| 统一状态标签 | 收口到 `.status-tag` + 修饰符 | 中（涉及多 JS 渲染） |
| 统一页面头部 | 收敛到统一 `PageHeader` 结构 | 中 |

---

## 6. 审计清单（对应规范 §14 第一阶段）

1. ✅ 技术栈：原生 HTML/CSS/JS，无框架、无构建、无组件库。
2. ✅ 路由与页面结构：单页 14 tab。
3. ✅ 列表/详情/表单页已梳理（见 §1）。
4. ✅ 重复组件：两套表格、两份 CSS、两套状态标签命名。
5. ✅ 布局问题：表格 nowrap 溢出、页面头部不一致。
6. ✅ 本报告即审计输出。
