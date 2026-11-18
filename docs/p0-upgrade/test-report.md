# P0 核心能力升级 · 测试结果（test-report）

> 执行时间：2026-11-02　命令：`.venv/bin/python run_tests.py unit` / `config`
> 环境：macOS，Python 3.13，未连生产 ClickHouse（integration 层不在本次范围）。

---

## 一、结果总览

| 层级 | 文件数 | 结果 |
|---|---|---|
| unit（纯逻辑单测） | 21 | ✅ 全部通过 |
| config（需本地配置库） | 9 | ✅ 全部通过 |
| integration（需真实 ClickHouse） | 1 | ⏸ 未连库（`test_gaps.py`，见 known-issues） |

## 二、本次新增测试

| 文件 | 断言数 | 覆盖 |
|---|---|---|
| `test_result_compare.py` | 22 | 六类判定 + 差异定位 + NULL/精度/旧格式兼容 |
| `test_golden_cases.py` | 19 | 标准 SQL 安全验证 / CRUD / 版本留痕 / 审核重验 / 验证独立 / 归档 / 批量导入 / 概览 |

## 三、关键回归（未破坏红线）

- `test_gate.py` 126 项：唯一执行出口 + 唯一闸门 + 自愈重过闸门 —— **全绿**，证明本次改动未新增裸执行、未破坏安全闸门。
- `test_join_cardinality.py` 15 项：JOIN 一对多放大专项 —— 全绿。
- `test_status_chain.py` 46 项：状态链七态 + 前端接线 —— 全绿，`exec_ok` 与 `business_verified` 严格分离。
- `test_result_check.py` 16 项：结构化结果比较 —— 全绿。
- `test_frontend_structure.py` 23 项：新增 golden.js / 两个页签 / 事件处理器 / node --check —— 全绿。

## 四、标准 SQL 安全验证实测（`validate_expected_sql`）

| 输入 | 结果 |
|---|---|
| `SELECT COUNT(*) AS c FROM dwd_order_all`（授权表） | ✅ ok（degraded：列级校验因无索引跳过） |
| `DROP TABLE dwd_order_all` | ❌ blocked（安全护栏：只允许 SELECT/WITH） |
| `SELECT * FROM secret_table` | ❌ blocked（未授权表） |
| 空 SQL | ❌ 拦截 |

## 五、端到端接口验证（TestClient + 临时库）

- `GET /api/dashboard/p0` → 200，返回 business/sql/knowledge/feedback/evaluation 五组。
- `GET /api/golden/cases` / `/api/golden/overview` → 200。
- `POST /api/golden/cases`（expected_sql 引用未授权表）→ 400，`expected_sql 未通过安全验证`。
- 未登录访问 `/api/dashboard/p0`、`/api/golden/*` → 401（默认 admin 中间件生效）。

---

## 结论

**本次新增与既有全部测试通过（30 个测试文件，unit 21 + config 9），未通过修改任何预期值制造通过。**
integration 层（`test_gaps.py`）因需要生产 ClickHouse 未运行，属环境限制，如实标注。
