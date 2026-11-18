# 知数AI 公共组件规范（Component Guidelines）

> 版本：V1.0 ｜ 落地：`frontend/static/app.css` 底部「统一设计体系」区块 + 各业务 JS

新模块**优先复用以下公共组件**，不允许硬编码颜色、不允许各页面自定义同名类。组件均可用 CSS 类 + 少量配置实现，**不引入第三方框架**。

---

## 1. StatusTag 状态标签（已落地）

统一状态映射组件，禁止各页面自行设计相同业务状态。

```html
<span class="status-tag success">已启用</span>
<span class="status-tag warning">待审核</span>
<span class="status-tag danger">验证失败</span>
<span class="status-tag info">处理中</span>
<span class="status-tag gray">已归档</span>
<span class="status-tag success"><i class="dot"></i>已验证</span>  <!-- 圆点 + 文字 -->
```

修饰符：`success` / `warning` / `danger` / `info` / `gray`。

状态 → 颜色映射：

| 状态 | 修饰符 | 颜色 |
|---|---|---|
| 已启用 / 已验证 / 已完成 / 通过 | `success` | 绿 |
| 待验证 / 待审核 / 待处理 | `warning` | 橙 |
| 处理中 | `info` | 蓝 |
| 草稿 / 已停用 / 已归档 | `gray` | 灰 |
| 验证失败 / 异常 / 失败 | `danger` | 红 |

**注意**：业务状态、审核状态、验证状态是三个独立维度，各用独立标签，不混为同一字段。

> 兼容：旧 `.badge-green/.orange/.red/.gray/.blue` 类继续可用，后续逐步迁移到 `.status-tag`。

---

## 2. DataTable 数据表格（优先建设）

统一列表表格。推荐使用 `.u-table`（已正确换行），长文本列加 `.text-wrap`。

```html
<div class="table-scroll">
  <table class="u-table">
    <thead><tr><th>名称</th><th>说明</th><th>状态</th><th>操作</th></tr></thead>
    <tbody>…</tbody>
  </table>
</div>
```

列宽规则：
- 操作列固定宽度；状态列适当固定宽度。
- 长文本列（描述 / 标准问题 / 业务说明）优先分配剩余空间，加 `.text-wrap`。
- 禁止整表 `white-space: nowrap`；禁止固定高度截断文字。
- 表格宽度不足时用 `.table-scroll` 横向滚动，不强行压缩所有列。

文本工具类：
- `.text-wrap` — 自动换行（长文本默认）。
- `.text-nowrap` — 单行（ID/时间/操作按钮）。
- `.text-ellipsis` — 单行截断 + 省略号（仅用于次要信息，核心知识必须有详情入口）。

交互要求：行悬停高亮（`.u-table tr:hover` 已具备）、排序态、分页、搜索筛选、空状态、加载态、错误态、危险操作二次确认。

---

## 3. EmptyState / LoadingState / ErrorState（已落地）

```html
<div class="empty-state"><div class="empty-icon">📭</div>暂无数据</div>
<div class="loading-state">加载中…</div>
<div class="error-state">加载失败，<button class="ghost">重试</button></div>
```

---

## 4. StatCard 统计卡片（已有）

```html
<div class="stat-grid">
  <div class="stat-card blue"><div class="stat-val">128</div><div class="stat-label">标准问题总数</div></div>
  <div class="stat-card green">…</div>
</div>
```

修饰符：`blue` / `green` / `orange` / `gray`。

---

## 5. PageHeader 页面头部

统一结构（页面标题 + 说明 + 主要操作按钮，主按钮不超过一个）：

```html
<div class="page-head">
  <div>
    <div class="page-title">标准问题库</div>
    <div class="page-desc">沉淀标准业务问题与正确取数案例，支持知识复用与 AI 评测</div>
  </div>
  <div class="page-actions">
    <button class="btn">＋ 新增标准问题</button>
    <button class="ghost">批量导入</button>
  </div>
</div>
```

---

## 6. Drawer 抽屉（已有）

复杂详情/编辑用右侧抽屉（`.drawer` / `.drawer-head` / `.drawer-body` / `.drawer-foot`），不在列表页堆叠字段。

---

## 7. SqlViewer / JsonViewer（知数AI 专属）

- SQL：等宽字体、语法高亮、一键复制、折叠展开，长 SQL 不撑破页面。
- JSON：格式化、支持折叠。
- 见 `pre.code`（已有）作为代码块基础。

---

## 8. 组件建设优先级

优先：**DataTable → StatusTag → SqlViewer → KnowledgeContent**（对应规范 §11）。

其余：FilterPanel、Pagination、Form、Dialog、MetricReference、AuditStatus、VerificationStatus 按需复用/补充。
