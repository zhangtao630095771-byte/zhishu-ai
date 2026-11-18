# 针对性优化报告（2026-10-29）

> 依据：`docs/architecture_review_2026-10-29.md` 中列出的缺点清单
> 原则：只做**成本小、收益确定、可验证**的三项；大重构不硬上，给出分阶段方案并说明为什么现在不动。

---

## 0. 一句话结论

修掉了 1 个真实安全缺口（第二条执行路径），拿到了 1 个数量级的性能收益（85.6s → 0.03s），
消除了 1 类"顺序写错也不报错"的隐性风险（Prompt 组装）。

**全部改动零回归**：unit 17 个文件 + config 3 个文件全绿，`test_gate` 从 122 项增加到 126 项。

---

## 1. 改了什么（三项）

| # | 缺点 | 动作 | 效果 | 风险 |
|---|---|---|---|---|
| 1 | 存在第二条执行路径，绕过闸门 | `gaps.py` 两条直连收口为 `_system_query` | 全仓再无裸 `db.execute_sql` | 极低 |
| 2 | 无结果级缓存，重复问题重复烧钱 | 新增 `result_cache.py` + server 接线 | 重复问题 **85.6s → 0.03s** | 中（已用 key 设计挡住） |
| 3 | Prompt 组装分散 4 处，顺序靠手工 | 收敛为 `_build_system_prompt()` + 落盘开关 | 顺序可测、可 diff | 低（有等价性测试） |

---

## 2. 优化一：收口 `gaps.py` 的裸执行路径

### 2.1 问题

`gaps.py:92` 和 `gaps.py:115` 直接调用 `db.execute_sql(sql)`：

```python
sql = f"SELECT {exprs} FROM {config.CK_DATABASE}.{table}"
_, rows = db.execute_sql(sql)      # ← 既不过 guard，也不过表白名单
```

这是全仓**唯一**一条不经过 `_exec_sql`、也不经过 `_pre_execute_check` 的执行通道。
虽然 SQL 由系统拼装、表名来自建表清单，风险低于 2026-10-19 那次自愈路径事故，
但"相信上游拼得对"正是那个事故的根因假设。

**更值得注意的是：为什么它一直没被发现？**
`test_gate.py` 里确实有一条"执行必须只走 `_exec_sql`"的静态契约测试——
但它只扫 `agent.py` 一个文件。**只守一个文件的契约，等于没守**：新模块天然不受约束。

### 2.2 改法

新增统一出口，两步校验后走唯一执行出口：

```python
def _system_query(sql, table):
    ok, why = guard.sql_guard(sql)              # ① 只读校验
    if not ok:
        raise RuntimeError(f"缺口扫描 SQL 未通过只读校验：{why}")
    t = (table or "").strip().strip("`")
    allowed = _system_tables()                  # ② 真实表白名单
    if t and allowed and t not in allowed:
        raise RuntimeError(f"缺口扫描拒绝了非业务表：{t}")
    return agent._exec_sql(sql)                 # ③ 唯一执行出口
```

两个设计取舍写在了代码注释里：

- **白名单用"全部真实表"而不是"ACL 可见表"**：缺口扫描是管理员侧能力，要能扫还没授权出去的表；这里要挡的是"表名拼错/被注入"，不是"该不该开放"。终端用户的 ACL 约束在取数主链路上。
- **不复用 `_pre_execute_check`**：那道闸门需要 ctx/resolved 才能跑五维校验，而这里跑的是 `uniqExact` / `groupUniqArray` 这类元数据聚合，没有业务口径可校验。**只做它该做的那部分，不假装跑了完整校验。**

`_system_tables()` 带 5 分钟缓存——扫描 200+ 字段时会被反复调用，而 `db.list_tables()` 是一次 ClickHouse 元数据查询。

### 2.3 把契约从"守一个文件"改成"守全仓"

`test_gate.py` 新增 `test_no_bare_exec_anywhere`：扫描根目录所有 `*.py`，
除 7 个白名单文件（`agent.py` / `db*.py` / `check1.py` / `ck_check.py` / `main.py`）外，
出现 `db.execute_sql` 即失败，并打印违规行号。

再加 `test_gaps_system_query_rejects` 验证这个出口**真的会拒**（不是换了个名字的转发）：
非 SELECT 被拒、非真实表被拒、合规 SQL 不被误拦。

### 2.4 结果

```
test_gate.py：122 → 126 项，全部通过
全仓裸 db.execute_sql：2 处（gaps.py） → 0 处
```

---

## 3. 优化二：结果级缓存

### 3.1 问题

同一道题问两遍，会完整重跑：语义检索 → LLM 生成 → ClickHouse 执行 → **列中文名解析**。
后者被忽略但代价惊人——实测**单这一步就要 27 秒**（要查 ClickHouse 的字段注释）。

### 3.2 三个容易做错的地方

**① 缓存不能藏在 agent 里。**
`evaluation/runner.py` 直接调 `agent.ask`。缓存若在 agent 内，第二次跑同一道题直接命中，
测出来的是缓存不是模型——**所有 A/B/C/D 对照实验都会失真**。所以缓存放在 server 层，
只作用于真实用户链路。

**② key 必须区分用户，否则缓存就是权限旁路。**
取数结果里有行级权限过滤后的行、有字段级 MASK 脱敏后的值。
按问题缓存而不区分用户，A 的脱敏结果会给到 B，或 B 的越权查询直接命中 A 的合法结果。

key 包含 8 项：

```
问题（仅压空白/大小写） + 用户名 + 角色 + 可见表指纹 + 知识指纹 + 方言 + 模型 + 经验开关
```

知识指纹 = 6 张知识表各自的「条数 : 最大 updated_at」串联。
改口径、补注释、发经验 → 指纹变 → 缓存自动失效。

**③ 只缓存成功结果。**
失败（拒答/拦截/报错）不缓存——否则一次偶发故障被固化成稳定故障。
降级（走 legacy 兜底）也不缓存——否则"V2 恢复了"这件事永远体现不出来。

### 3.3 一个必须说明的返工

第一版只缓存了 `agent.ask` 的返回结果，实测第二次仍要 **27.7 秒**
（省了 LLM 的 55 秒，但列中文名解析照样跑）。
这说明**缓存边界选错了**——缓存必须覆盖"agent 取数 + 列名解析 + payload 组装"整段。

于是重构：把 payload 组装抽成 `_build_ask_payload()`，缓存整段 payload。

第二个副作用：命中缓存时 `result` 为 None，权限审计报 `'NoneType' object has no attribute 'get'`。
如果简单地把审计跳过，就会出现**缓存命中不留审计记录**的合规空洞（脱敏查询尤其不能缺）。
最终方案是缓存"信封"：

```python
{"payload": ..., "perm": 权限判定}   # perm 不进响应体，只留给审计
```

命中时还原 `perm_decision`，审计照常落库，且不扩大接口暴露面。

### 3.4 结果

| 指标 | 改造前 | 改造后 |
|---|---|---|
| 首次取数 | 85.6s | 85.6s（不变） |
| **重复同一问题** | 85.6s | **0.03 ~ 0.18s** |
| 提速 | — | **约 490 倍** |
| 数据一致性 | — | SQL / 行数据 / 列中文名三次完全一致 |
| 历史完整性 | — | 每次仍落新 `record_id`（不因缓存丢历史） |

新增接口：

- `GET /api/cache/stats` —— 命中率、容量、TTL（运维判断 TTL 是否合适）
- `POST /api/cache/clear` —— 手工清空（对账、验证口径改动）
- `/api/index/rebuild` 会自动清空缓存（重建索引 = 明确的"我改了东西"信号）

测试 `test_result_cache.py` 23 项，重点全在 **"key 是否会区分"** 而不是"缓存是否更快"：

```
不同用户 → 不同 key（防脱敏串号）        ✅
可见表变了 → 不同 key（ACL 变更失效）    ✅
知识指纹变了 → 不同 key（改口径失效）    ✅
经验开关变了 → 不同 key（对照不互相污染）✅
指纹算不出来 → 拒绝缓存（不猜 key）      ✅
失败/降级结果 → 不缓存                  ✅
```

---

## 4. 优化三：Prompt 组装收敛

### 4.1 问题

`ask_v2` 里有 4 处手工拼接：

```python
system_prompt = semantic_layer.build_prompt_parts(...)
system_prompt = system_prompt + "\n\n# " + dict_block + "\n"
system_prompt = system_prompt + "\n\n" + hard_block + "\n"
system_prompt = system_prompt + "\n\n" + exp_match.prompt_block + "\n"
```

顺序全靠这几十行代码的**书写顺序**保证。没有任何地方能看出"最终 prompt 长什么样"。
顺序一旦被后来的改动插错位置（比如把经验块插到指标口径前面），系统不会报错，**只会答错**。

### 4.2 改法

收敛为 `_build_system_prompt(sidx, ctx, resolved, question, exp_block="")`，
返回 `(text, dict_names, dict_reason, parts)`，`parts` 记录每个段落的名字与字数。

配套加了 `_dump_prompt()`：`PROMPT_DUMP=1` 时把最终 prompt 落盘到
`logs/prompt/<时间>-<问题哈希>.txt`，可逐段查看、可 diff 两次改动。默认关闭（一次取数一个文件）。

### 4.3 怎么证明"没改坏"

不靠人眼比对，靠**逐字节等价性测试**（`test_prompt_assembly.py` 23 项）：

在测试里把改造前的逻辑原样抄一遍（`_legacy_assemble`），对 5 组打桩输入
分别跑新旧两条路径，断言输出字符串**完全相等**：

| 场景 | 结果 |
|---|---|
| 全段落（基础+字典+硬约束+经验） | 逐字节相同 ✅ |
| 无经验块 | 逐字节相同 ✅ |
| 字典召回但拼不出块（接线断开） | 逐字节相同 ✅ |
| 完全无字典 | 逐字节相同 ✅ |
| 只有基础段 | 逐字节相同 ✅ |

另加顺序断言：`base(P0~P4) → dicts → hard_constraints → experience`，
且 `text.rstrip().endswith(exp_block)` —— **经验块必须物理上在最后**，
这是"经验不得覆盖口径"的结构保证，而不是靠一句 Prompt 叮嘱。

### 4.4 顺序变化说明

顺带把经验检索从"Prompt 组装之后"移到"组装之前"——因为经验块要作为最后一段传给组装函数。
检索本身不读 prompt，功能等价；真实链路已验证（`ok=True, rows=183, validation_status=passed, dicts=['订单状态']`）。

---

## 5. 回归结果

```
unit  层（17 个文件）
  test_imports 26 · test_pipeline 19 · test_prompt_constraints 52 · test_gate 126(+4)
  test_join_suggest 10 · test_guard ✓ · test_labels 33 · test_retrieve 22
  test_permission ✓ · test_legacy_validation 9 · test_join_cardinality 15
  test_audit_safety 9 · test_result_check 16 · test_result_cache 23(新)
  test_prompt_assembly 23(新) · test_golden_demo_dataset 9 · test_calendar ✓

config 层（3 个文件）
  test_acl 88 · test_knowledge_governance 47 · test_sql_experience 62
```

**全部通过，零回归。**

### 改动规模

| 文件 | 变化 | 说明 |
|---|---|---|
| `result_cache.py` | 新增 226 行 | 缓存模块 |
| `test_result_cache.py` | 新增 186 行 | 23 项 |
| `test_prompt_assembly.py` | 新增 191 行 | 23 项，含等价性对照 |
| `gaps.py` | +77 | `_system_query` + 表白名单 |
| `agent.py` | +211（累计，含上一轮经验中心 +99） | 本轮约 +112：`_build_system_prompt` / `_dump_prompt` |
| `server.py` | +429（累计，含上一轮经验接口 +340） | 本轮约 +89：缓存接线 / `_build_ask_payload` 抽取 / 3 个接口 |
| `config.py` | +98（累计） | 本轮 +29：缓存与 Prompt 落盘开关 |
| `test_gate.py` | +81 | 全仓静态契约 + 拒绝路径验证 |
| `.env.example` | +43 | 缓存与经验中心配置说明 |

---

## 6. 没有做的，以及为什么

缺点清单里还有 6 项，都不是"多做几小时"能解决的，硬上反而会制造新风险。

| 缺点 | 为什么这轮不做 | 建议路径 |
|---|---|---|
| 前端 7114 行单文件 | 拆分需要构建工具、路由改造、回归全部 11 个页签。中途状态（一半模块化）比现在更难维护 | 分 3 期：① 先只抽 CSS 到独立文件；② 按页签拆 JS 文件（仍无构建，用 `<script>` 引入）；③ 引入 Vite 后才做组件化 |
| `config_store.py` 3918 行 | 33 张表的 CRUD，拆错会引发大面积回归，且当前测试对这一层的覆盖不足以担保 | 先按域拆只读查询（metrics / permission / docs），写操作暂不动；每拆一块跑一次 config 层 |
| 两条流水线并存 | 下线 legacy 是产品决策，不是技术决策 | 先量化：统计 `degrade_rate`，若长期为 0 再定下线时间表 |
| 同步阻塞、无异步队列 | 引入 Celery/Redis 是架构级变更，且当前并发压力未量化 | 先加并发压测拿到基线（QPS / 峰值并发），再决定要不要上队列 |
| 评测集缺 `expected_sql` / `expected_result` | 需要人工标注，是数据工作不是代码工作 | 建议先标 20 条（覆盖最高频的指标），就能解锁 `result_accuracy` |
| `ClickHouse` 单点 | 涉及基础设施 | 只读副本或物化视图层，属运维范畴 |

**排序建议**：`expected_sql 标注`（20 条即见效）→ `前端拆 CSS`（低风险第一步）→ `legacy 量化` → 其余。

---

## 6.5 第二轮（同日）：解锁 `result_accuracy` 的标注器

上一轮把「评测集缺 ground truth」列为**最该先做**，但它本质是"人工确认口径"。
这轮把人工部分降到最低：新增 `evaluation/annotate_expected.py` + `test_annotate_expected.py`(17 项)。

### 6.5.1 交付了什么

| 命令 | 作用 |
|---|---|
| `annotate_expected.py status` | 看当前数据集有几条缺 `expected_sql` / `expected_result` |
| `annotate_expected.py annotate --limit N` | 逐条跑模型 → 展示 SQL 和结果 → 标注人 `y`(确认) / `e`(手改SQL) / `s`(跳过) → 回写 |
| `annotate_expected.py seed-from-experience` | 把 SQL 经验中心里**已验证成功**的黄金案例，按"问题归一化后完全相同"匹配到数据集，直接种子 `expected_sql`（实现经验→评测闭环） |

写回前自动备份（`.bak-<时间戳>`），原子替换，只改目标行不污染其余行（有测试断言）。

### 6.5.2 两个诚实的护栏（这是本工具最有价值的部分）

1. **`--accept` 模式绝不快照 `expected_result`**。
   若把模型自己的输出当 `expected_result` 再去比对，就是**拿模型比对模型**，
   `result_accuracy` 会恒等于 1 —— 循环论证。所以结果快照只在**人工确认**（`y`）后才写，
   `--accept` 只写 `expected_sql` 并标注 `auto-accept(待复核)`。

2. **`expected_result` 是结构快照，不是永久真理**。数仓每天都在变，
   隔几天的快照会过时。所以快照只适合"评测当天跑、当天比"，
   长期应把"重跑 expected_sql"做成评测的一部分（下一步）。

### 6.5.3 效果验证（未污染真实数据集）

对 golden 的 `g001「查询订单量」` 做了端到端验证：

```
标注前：checks.check_result → None   （= result_accuracy 暂不可测）
人工确认并快照后：checks.check_result → True  （= 可计算）
```

即：**一旦人工标够 N 条，`result_accuracy` 从"永远算不出"变成"能算出"**。

### 6.5.4 顺带量化的另一条：legacy 降级率

`query_records.pipeline` 字段本就记录了降级（降级时 = `legacy`）。实测：

```
174 条记录：v2 = 166，legacy = 0，None(早期) = 8
```

**降级率 = 0%**。这条不需要改任何代码 —— "legacy 要不要下线"的量化依据已经拿到：
生产上从未实际降级过，legacy 是纯兜底代码，可以进入"定下线时间表"的讨论。

---

## 6.6 第三轮（同日）：前端结构拆分

### 6.6.1 问题量化

前端原来是一个文件：

```
frontend/index.html   7118 行 / 357 KB
  ├─ 1–24      head
  ├─ 24–799    <style> 内联样式      805 行
  ├─ 800–2115  body 标记            1282 行
  └─ 2116–7116 <script> 内联脚本    4999 行
```

改一个按钮要滚动上千行；两个人同时改必然冲突；前端测试 **0 个**（后端 19 个）。
这是上一轮梳理里排第 1 的工程债，也是唯一"投入产出比最高但最难下决心"的一块。

### 6.6.2 做了什么：只搬家，不重写

| 之后 | 行数 | 内容 |
|---|---|---|
| `frontend/index.html` | **1330** | 只留 DOM 骨架 + 2 个 CSS 引用 + 12 个 script 引用 |
| `frontend/static/app.css` | 774 | 全局样式（变量/布局/组件/页签骨架） |
| `frontend/static/experience.css` | 31 | SQL 经验中心专用（`.xp-*`） |
| `frontend/static/js/core.js` | 246 | 登录态、工具、启动 |
| `static/js/experience.js` | 483 | SQL 经验中心 |
| `static/js/ask.js` | 442 | 取数页 + 结果渲染 + 反馈 |
| `static/js/kb.js` | 391 | Prompt 配置 / RAG 知识库 / 复核队列 |
| `static/js/registry.js` | 853 | 语义注册表 + 模糊推荐 |
| `static/js/dict.js` | 350 | 数据字典 |
| `static/js/gaps.js` | 428 | 知识缺口 |
| `static/js/feedback.js` | 250 | 问题反馈 + 优化建议 |
| `static/js/metrics.js` | 94 | 指标口径 |
| `static/js/admin.js` | 714 | 表与权限 / 账号 / 字段级权限 |
| `static/js/history.js` | 720 | 取数审核 + 个人历史 |
| `static/js/boot.js` | 28 | 重建索引 + 启动入口 |

**为什么还是不上 Vue/React？**
上框架要同时引入构建链、依赖管理、跨域配置三类新问题；而当下最痛的是"找不到、改不动、测不了"，
这三件事靠切文件就能解决。**一次只动一个变量**——这是原注释里就写下的原则，这次继续遵守。

**为什么用普通 `<script src>` 而不是 ES Module？**
现有代码有 78 处 `onclick="fn()"`，靠全局函数调用；转成 module 后所有内联处理器全部失效。
这不是风格选择，是硬约束。

### 6.6.3 怎么证明"没改坏"

不靠人眼比对，靠四条可执行的断言：

| 断言 | 结果 |
|---|---|
| JS 12 个文件按序拼回去，与原文逐字节相同 | ✅ 4999 行一致 |
| CSS 拼回去与原文逐字节相同 | ✅ 805 行一致 |
| HTML 正文标记逐行一致 | ✅ 1282 行一致 |
| 全局声明的标识符一个不少、无重复 | ✅ 296 → 296，重复 0 |

然后是真浏览器验证（Chrome Headless + CDP，不是只有语法检查）：

```
首屏登录框可见： True        样式生效（卡片 max-width）：386px
登录后主界面显示： True      当前用户：admin
跨文件函数抽查 13 个，缺失：无
11 个页签逐个切换：全部成功
经验中心真实拉数：统计卡 8 张、列表 100 条
未捕获异常 0 条；网络/日志错误 0 条
```

截图确认：登录页、取数主页、SQL 经验页三处布局与拆分前一致。

### 6.6.4 过程中真实踩到的一个坑（已修）

重写文件头注释时，把 `<!DOCTYPE html>`、`<html>`、`<head>` 三行弄丢了。
**浏览器照样渲染**（进入怪异模式），接口测试全绿，语法检查全过 —— 只有截图才看得出来。
这正是"结构测试必须存在"的理由，于是补了一条 `test_document_skeleton`：
DOCTYPE 存在、html/head/body 顺序正确、div/section/main/table/button 开闭配对。

### 6.6.5 后端配套

| 改动 | 原因 |
|---|---|
| `app.mount("/static", StaticFiles(...))` | 12 个 JS 会持续增加，手写白名单迟早起漏；StaticFiles 已处理穿越/404/ETag/304 |
| `_PUBLIC_PREFIXES = ("/static/",)` | 登录页本身要用 CSS/JS，否则用户看到白板 |
| `Cache-Control: no-cache` | 没有构建步骤、文件名不带哈希；盲缓存会让"改了没生效"变成常态。配合 ETag，没变时仍是 304 |

目录穿越实测：`/static/../server.py`、`/static/%2e%2e/server.py`、`/static/../agent_config.db`
全部拿不到内容（401/404），无源码泄漏。

### 6.6.6 新增守护测试

`test_frontend_structure.py`（23 项，已接入 config 层）：

1. 引用的静态文件全部存在，且没有"没人引用"的孤儿文件；
2. 文档骨架完整（DOCTYPE / 标签顺序 / 开闭配对）；
3. 不再有内联 `<style>` / `<script>`；CSS、JS 总行数不得因拆分缩水；
4. **78 个 `onclick` 处理器全部有定义** —— 这是"少写一个 `<script src>`"的探测器：
   那种情况下代码仍语法正确、页面仍能打开，只有那个按钮静默失灵；
5. 每个 JS 通过 `node --check`；
6. `/static` 免登录可取、Cache-Control 正确、目录穿越被拦截。

---

## 7. 回滚方式

三项改动互相独立，可单独回滚：

| 改动 | 回滚方式 |
|---|---|
| 缓存 | `RESULT_CACHE_TTL=0`（无需改代码、无需重启数据） |
| Prompt 收敛 | `git revert` 对应提交；`_build_system_prompt` 是纯函数，不影响数据 |
| gaps 收口 | 恢复两条 `db.execute_sql` 即可；同时需放宽 `test_no_bare_exec_anywhere` 的白名单 |
| 前端拆分 | 建议把拆分**单独提交**（不带其他改动），回滚即 `git revert` 该提交，`index.html` 原样复原、`static/` 一并移除。后端 `/static` 挂载可以留着（目录不存在时会跳过挂载） |

---

## 8. 一句话总结

前两轮的共同点是：**都在补"看起来没问题、但换个角度就是漏洞"的地方**。

- 第二条执行路径——功能上没问题（SQL 是系统拼的），但结构上就是缺口；
- 缓存——功能上加不加都能跑，但加了之后"key 少一项"就是权限旁路；
- Prompt 顺序——现在是对的，但没有任何机制保证它以后还对。

第三轮（前端拆分）的收益不是"更快"，而是**把"改得动"这件事从不可能变成可能**：
7118 行 → 1330 行骨架 + 14 个各管一件事的文件，且首次有了前端结构测试（23 项）。
拆分过程本身也验证了一件事：**搬家比重写安全得多，但前提是你有办法证明"只是搬家"**——
逐字节回拼 + 真浏览器验证，这两条才是敢动 357 KB 单文件的底气。

**优化的价值往往不在"让它更快"，而在"让错误更不容易发生"。**
