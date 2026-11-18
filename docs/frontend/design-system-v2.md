# 知数AI Design System V2

> 版本：V2.0 ｜ 日期：2026-11-05
> 定位：企业级 AI Agent 工作台 + 企业数据知识中枢
> 风格配比：70% 企业级 SaaS 工作台 + 20% AI Agent 交互 + 10% 数据可视化
> 参考气质：ChatGPT（自然语言入口）、Linear（层级与留白）、Databricks（数据资产结构感）——**只取结构，不照搬视觉**
> 落地位置：Pixso 变量集 / 样式 / 组件；前端映射 `frontend/static/app.css` `:root`
> 配套：`design/zhishu-ai-ui-v2.html`（组件与页面的可视渲染）、`docs/frontend/ui-audit-report.md`

---

## 1. 设计原则

简洁 · 专业 · 智能 · 可信 · 易用 · 可扩展。
**信息可读性 > 美观 > 装饰**；颜色不作为唯一状态识别方式（状态标签必须带文字）。
禁止：大面积高饱和渐变、紫色 AI 光效、彩色卡片堆叠、每个按钮不同颜色、传统数据库后台观感、科技大屏观感。

---

## 2. 颜色变量（Pixso Variable Set：`Color / Light`）

### 2.1 基础色

| Token | 值 | 用途 |
|---|---|---|
| `brand/primary` | `#3478F6` | 主操作、当前选中、链接、进度 |
| `brand/primary-hover` | `#2A67DC` | 主按钮悬停 |
| `brand/primary-active` | `#2157BE` | 主按钮按下 |
| `brand/primary-subtle` | `#EDF3FF` | 选中态浅底、信息提示底 |
| `bg/page` | `#F5F7FA` | 页面背景 |
| `bg/card` | `#FFFFFF` | 卡片 / 面板 / 表格 |
| `bg/hover` | `#F8FAFC` | 行悬停、次级底 |
| `bg/subtle` | `#F0F3F8` | 分组底、代码块外的技术区底 |
| `text/primary` | `#17243A` | 主文字 / 标题 |
| `text/secondary` | `#667085` | 次级文字 / 说明 |
| `text/muted` | `#98A2B3` | 辅助 / 占位 / 禁用 |
| `border/base` | `#E7EBF0` | 分割线 / 卡片边框 |
| `border/strong` | `#D5DCE5` | 输入框边框 / 强调分隔 |

### 2.2 语义色

| Token | 主值 | 浅底 | 浅边 | 用途 |
|---|---|---|---|---|
| `semantic/success` | `#16A36A` | `#E8F7F0` | `#BCEBD4` | 成功 / 已通过 / 已启用 |
| `semantic/warning` | `#E9A23B` | `#FEF5E7` | `#F7DCAE` | 警告 / 待处理 / 待审核 |
| `semantic/danger` | `#DC5252` | `#FDEDED` | `#F5C6C6` | 错误 / 驳回 / 危险操作 |
| `semantic/info` | `#3478F6` | `#EDF3FF` | `#C7DBFF` | 执行中 / 信息 |
| `semantic/neutral` | `#667085` | `#F0F3F8` | `#E1E7EF` | 已停用 / 草稿 / 规划中 |

### 2.3 技术区专用（SQL / 日志 / 执行详情）

| Token | 值 | 用途 |
|---|---|---|
| `code/bg` | `#1B2434` | SQL 编辑器、日志深色底 |
| `code/text` | `#D9E2F0` | 深色底上的正文 |
| `code/keyword` | `#7AA2F7` | SQL 关键字 |
| `code/string` | `#9ECE6A` | 字符串字面量 |

> 深色仅用于技术区域，与主工作台形成区分，**不得**用于主导航或普通卡片。

### 2.4 使用规则

- 蓝色只用于主要操作与当前选中；绿色成功、黄色待处理、红色风险。
- 单页高饱和色面积控制在 10% 以内；状态标签一律「浅底 + 主色文字 + 文字说明」。
- 涨/跌等金融语义若后续出现：涨红跌绿（中国习惯），与本表语义色分开定义。

---

## 3. 字体规范（Pixso Text Styles）

字体族：`-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif`
等宽族：`"SF Mono", Menlo, Consolas, monospace`（仅表名 / 字段名 / SQL）

| Style | 字号 / 行高 / 字重 | 用途 |
|---|---|---|
| `Display/20` | 20 / 28 / 600 | 页面标题 |
| `Heading/16` | 16 / 24 / 600 | 模块标题、卡片组标题 |
| `Heading/14` | 14 / 22 / 600 | 卡片标题、抽屉标题 |
| `Body/14` | 14 / 22 / 400 | 正文、对话内容 |
| `Body/13` | 13 / 20 / 400 | 表格正文、表单值 |
| `Caption/12` | 12 / 18 / 400 | 辅助说明、时间戳、面包屑 |
| `Number/26` | 26 / 32 / 600 | 统计数值（**无真实数据时显示「—」**） |
| `Code/13` | 13 / 20 / 400 / mono | SQL、字段名 |

中文不加字距；数字统计用等宽数字特性（font-variant-numeric: tabular-nums）。

---

## 4. 间距 / 圆角 / 阴影 / 尺寸

| 类别 | Token | 值 | 用途 |
|---|---|---|---|
| 间距 | `space/1..6` | 4 / 8 / 12 / 16 / 24 / 32 | 原子间距；卡片内边距 16，模块间距 24，区域间距 32 |
| 圆角 | `radius/sm` | 6px | 按钮、输入框、标签 |
| 圆角 | `radius/md` | 8px | 卡片、面板 |
| 圆角 | `radius/lg` | 10px | 弹窗、抽屉 |
| 阴影 | `shadow/card` | `0 1px 2px rgba(23,36,58,.04)` | 常规卡片（极克制） |
| 阴影 | `shadow/float` | `0 8px 24px rgba(23,36,58,.10)` | 抽屉、弹窗、下拉浮层 |
| 边框 | `border/width` | 1px solid `border/base` | 统一 |
| 控件高 | `size/sm` / `md` | 32px / 40px | 小型 / 标准控件 |
| 布局 | 侧栏 232px、顶栏 56px、右依据面板 320px | — | 1440×900 / 1280×800 均不横向溢出 |

---

## 5. 组件清单（Pixso Components）

> 所有组件使用上述变量，禁止组件内硬编码色值。命名统一 `DS/` 前缀。

| 组件 | 变体 / 状态 | 规格要点 |
|---|---|---|
| `DS/Button` | `type=primary/secondary/danger/text` × `size=sm/md` × `default/hover/active/disabled/loading` | 主蓝底白字；次白底描边；危险仅用于删除等破坏性操作；文字按钮用于「查看 SQL」等次级动作 |
| `DS/Input` | `default/focus/error/disabled` + textarea + select | 高 40 / 圆角 6 / 描边 `border/strong`；focus 主色 1px + 2px 浅环；错误红边 + 下方文字说明 |
| `DS/Table` | `header/row/row-hover/empty/loading` | 表头 `bg/subtle`、13px；**单元格长文本换行**；数值右对齐；支持排序箭头、分页、筛选；无横向溢出 |
| `DS/Tag` | `pending/running/success/failed/disabled/planned` | 浅底 + 主色文字 + 中文文案；`planned`（规划中）用中性灰且配「规划中」文字 |
| `DS/Nav` | `group/item/item-active/item-admin/item-disabled` | 侧栏 232；分组标题 12px 大写间距；选中项左侧 3px 主色条 + `brand/primary-subtle` 底；管理员项带角色标识；规划中项灰显 |
| `DS/Card` | `default/hover/technical` | 白底 + 1px 边框 + 8px 圆角 + `shadow/card`；`technical` 变体用于深色 SQL 区 |
| `DS/Drawer` | `right/480px` | 遮罩 + 右侧滑入；头 / 体 / 底三段；用于知识详情、审核详情 |
| `DS/Modal` | `confirm/form` | 居中、480–640px、`shadow/float`；用于删除确认、重要配置 |
| `DS/EmptyState` | `default/planned` | 图标 + 一句说明 + 可执行操作；`planned` 明确写「该能力尚未实现，仅为架构预留」 |
| `DS/Skeleton` | `text/table/card` | 统一浅灰骨架块，加载态专用 |
| `DS/Timeline` | `step=pending/running/success/failed` | 竖线 + 状态圆点；仅展示可验证的执行步骤；失败附原因与下一步建议 |
| `DS/Breadcrumb` | — | 顶栏面包屑，12px 次级文字 |
| `DS/TrustBar` / `DS/StatusChain` | — | 取数页专用：状态链四徽章、信任条（现有能力，重设计保留） |
| `DS/Toast` | `success/error/info` | 操作反馈统一右上角，3s 自动消失 |

---

## 6. 页面布局栅格

```
┌──────────────┬───────────────────────────────────────────────┐
│              │  Topbar 56px：面包屑 · 搜索 · 数据源 · 待办 · 头像 │
│  Sidebar     ├───────────────────────────────────────────────┤
│  232px       │  Page Header：标题 + 一句说明 + 主操作           │
│  4 大分组     ├───────────────────────────────────────────────┤
│              │  Work Area（按需 2 栏 / 3 栏）                   │
│  底部身份区   │  取数页：历史 240 ｜ 对话 flex ｜ 依据 320        │
└──────────────┴───────────────────────────────────────────────┘
```

- 页面标题层级唯一：一个 H1 + 若干 H2，不出现多个同级大标题。
- 卡片之间不重复展示同一信息（例如「使用的数据表」只在依据面板出现一次）。
- 技术细节（SQL、校验报告、查询理解）**默认收纳**，不占首屏。

---

## 7. 交互规范

| 场景 | 规则 |
|---|---|
| 按钮 | 主操作每屏最多 1 个；次操作描边；危险操作需二次确认；加载中禁用并显示 loading |
| 表格 | 排序、搜索、分页、筛选按现有能力提供；长文本换行；空数据显示 EmptyState |
| 表单 | 标签在上、提示在下、错误紧贴字段；「密码」文案统一，禁用「口令」 |
| 抽屉 | 知识详情、审核详情；ESC / 遮罩关闭；底部固定操作区 |
| 弹窗 | 删除、重要配置、关键确认；不得嵌套弹窗 |
| 状态 | 待处理(黄) / 执行中(蓝) / 成功(绿) / 失败(红) / 已停用·规划中(灰)，一律带文字 |
| 空状态 | 说明原因 + 可执行操作；规划中能力明确标注未实现 |
| 加载 | 骨架屏，不用整页遮罩 |
| 错误 | 说明原因 + 下一步建议，不暴露技术异常码 |
| 反馈 | 成功 / 失败 Toast，位置与时长统一 |

---

## 8. Pixso 落地映射（拿到 `file_key` 后执行）

| 步骤 | Pixso 操作 | 内容 |
|---|---|---|
| 1 | `write_variables` | 新建变量集 `Color / Light`，写入 §2 全部颜色变量；新建 `Space / Radius / Size` 数值变量集 |
| 2 | `write_styles` | 写入 §3 的 8 个 Text Style、§2 的语义色样式、§4 的 3 个 Effect Style（卡片阴影 / 浮层阴影 / 无阴影） |
| 3 | `apply_design` / `code_to_design` | 新建「V2 / Design System」页，按 §5 组件清单逐个建组件与变体；导入 `design/zhishu-ai-ui-v2.html` 的 Design System 展示页作为可视校对 |
| 4 | `apply_design` | 新建「V2 / 页面」分组页，按 P0 → P1 → P2 顺序导入高保真页面 |
| 5 | `query_all_unique_props` + `check_layout` + `take_screenshot` | 一致性检查： stray colors / 字号 / 圆角 / 间距 / 溢出 / 组件实例一致性 |

> **不覆盖原始设计**：所有 V2 内容写入新建页面或复制页，原页面保持不动。

---

## 9. 前端迁移对照（后续开发依据，本次不改代码）

| V2 Token | 现有 `app.css` 变量 | 备注 |
|---|---|---|
| `brand/primary` | `--color-primary: #165dff` | 色值变更，需整体验证 |
| `bg/page` | `--color-bg-page: #f5f6f8` | 微调 |
| `text/primary` | `--color-text-primary: #1f2329` | 变更 |
| `border/base` | `--color-border: #e5e6eb` | 微调 |
| `semantic/*` | `--color-success/warning/danger` | 整套替换 |
| 间距 / 圆角 | `--space-* / --radius-*` | **值不变，可直接沿用** |
| 字体 | `--font-family / --font-mono` | **不变** |

落地方式：改 `:root` 一处 + 把 437 处硬编码色值逐步收敛到 token + 旧状态标签统一迁移到 `.status-tag`。保持 FastAPI 接口、数据库结构、AI 流水线不变。

---

## 10. 一致性检查清单（阶段五）

- [ ] 所有页面使用同一导航组件与同一顶栏组件
- [ ] 页面标题层级、面包屑格式一致
- [ ] 背景 / 卡片 / 边框 / 圆角 / 阴影一致，无 stray color
- [ ] 按钮、输入框、下拉、表格、状态标签均为组件实例
- [ ] 表格无文字溢出 / 无横向滚动
- [ ] 无重复信息、无无效按钮、无虚构功能
- [ ] 「规划中」项全部灰显 + 标签 + 说明
- [ ] 评测类页面无编造数值
- [ ] 1440×900 / 1280×800 布局正常
- [ ] 用户可见文案无「口令」
- [ ] 现有 13 个功能入口全部可映射到设计稿
