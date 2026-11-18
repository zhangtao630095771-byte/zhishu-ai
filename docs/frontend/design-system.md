# 知数AI 全局设计规范（Design System）

> 版本：V1.0 ｜ 适用范围：知数AI 全站 ｜ 落地位置：`frontend/static/app.css` 顶部 `:root`

本规范把全站颜色、字体、间距、圆角统一为**全局 CSS 变量（设计 Token）**，禁止在各页面硬编码颜色。色值沿用现有 Arco 色板（视觉不变、零回归），后续如需切换色板，只需改 `:root` 一处。

---

## 1. 颜色 Token

| 变量 | 值 | 用途 |
|---|---|---|
| `--color-primary` | `#165dff` | 主交互色（按钮、链接、激活态） |
| `--color-primary-hover` | `#4080ff` | 主色悬停 |
| `--color-success` | `#00b42a` | 成功 / 已验证 / 正常 |
| `--color-warning` | `#ff7d00` | 待处理 / 待验证 / 警告 |
| `--color-danger` | `#f53f3f` | 失败 / 异常 / 危险操作 |
| `--color-info` | `#165dff` | 信息 / 处理中 |
| `--color-text-primary` | `#1f2329` | 主文本 |
| `--color-text-secondary` | `#4e5969` | 次级文本 |
| `--color-text-muted` | `#86909c` | 辅助 / 占位文本 |
| `--color-border` | `#e5e6eb` | 常规边框 |
| `--color-border-light` | `#f2f3f5` | 浅边框 / 分隔 |
| `--color-bg-page` | `#f5f6f8` | 页面背景 |
| `--color-bg-card` | `#ffffff` | 内容卡片背景 |
| `--color-bg-hover` | `#f7f8fa` | 悬停背景 |

状态浅底 / 浅边（标签、提示条）：

| 状态 | 背景变量 | 边框变量 |
|---|---|---|
| 成功 | `--color-success-bg` `#e8ffea` | `--color-success-border` `#aff0b5` |
| 警告 | `--color-warning-bg` `#fff7e8` | `--color-warning-border` `#ffcf8b` |
| 危险 | `--color-danger-bg` `#ffece8` | `--color-danger-border` `#fdcdc5` |
| 信息 | `--color-info-bg` `#e8f3ff` | `--color-info-border` `#94bfff` |
| 灰 | `--color-gray-bg` `#f2f3f5` | `--color-gray-border` `#e5e6eb` |

---

## 2. 字体 Token

| 变量 | 值 | 用途 |
|---|---|---|
| `--font-family` | `-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif` | 正文 |
| `--font-mono` | `"SF Mono", Menlo, Consolas, monospace` | 代码 / 表名 / 字段名 |

### 字号

| 场景 | 字号 |
|---|---|
| 页面标题 | 20px |
| 模块标题 | 16px |
| 卡片标题 | 14px |
| 正文 / 表格正文 | 13–14px |
| 辅助信息 | 12px |
| 统计数字 | 26–30px |
| 代码 | 13px |

正文行高 1.5–1.7。

---

## 3. 间距 Token

| 变量 | 值 | 用途 |
|---|---|---|
| `--space-xs` | 4px | 超小间距 |
| `--space-sm` | 8px | 小间距 |
| `--space-md` | 12px | 标准间距 |
| `--space-lg` | 16px | 模块间距 |
| `--space-xl` | 24px | 区域间距 |
| `--space-2xl` | 32px | 大区块间距 |

---

## 4. 圆角 Token

| 变量 | 值 | 用途 |
|---|---|---|
| `--radius-sm` | 6px | 按钮 / 输入框 |
| `--radius-md` | 8px | 卡片 |
| `--radius-lg` | 10px | 弹窗 / 抽屉 |

---

## 5. 控件高度

| 类型 | 高度 |
|---|---|
| 小型控件 | 32px |
| 标准控件 | 40px |
| 大型操作控件 | 44px |

---

## 6. 使用规范

- **禁止**在页面 JS/HTML 内联样式中硬编码颜色，一律引用 `var(--color-*)`。
- **禁止**大面积高饱和渐变、过多彩色卡片、不必要的阴影、大量圆角胶囊按钮。
- **颜色不作为唯一状态识别方式**（状态标签须配文字）。
- 视觉优先级：**信息可读性 > 美观 > 装饰**。
