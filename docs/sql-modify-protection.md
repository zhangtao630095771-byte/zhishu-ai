# 知数AI P0 整改交付报告 — SQL 多轮修改逻辑保护

> 日期：2026-11-03
> 案例：取数记录 #243（「将此查询修改为流动的近30天」导致业务逻辑被破坏）

---

## 1. 问题根因分析

### 现象

取数记录 #243，用户仅要求「将此查询修改为流动的近30天」，但系统重新生成了完全不同业务逻辑的 SQL：

| 维度 | 原始 SQL | 被破坏后 |
|---|---|---|
| 表 | `ods_ctc_point_record_all` + `ods_ctc_point_batch_all` | 换成了 `dwd_tu_order_lab_all` / `ods_tu_large_handcart_order_all` / `ods_valet_order_all` |
| 聚合 | `COUNT(DISTINCT biz_no)` | `COUNT(*)` |
| 业务过滤 | `channel = 1/2/3`（CASE WHEN） | `status = 6` / `state = 6` / `status = 40` |
| 指标 | `SUM(amount)`（按渠道） | `SUM(total)` / `SUM(order_amount)` |

### 根因

1. **只有软约束（Prompt），没有硬约束**：增量修改时，`_modify_prompt_block` 把上一轮 SQL 作为文本注入并「提示」模型保留，但没有对生成结果做任何**结构校验**。模型一句模糊的「流动近30天」就触发「重解释」，把表/口径/聚合整个换掉。

2. **RAG 检索重跑会换表**：增量修改时 `retrieve_semantic` 重新检索，实体/跨业务线过滤可能把原 SQL 用到的表（`ods_ctc_point_record_all` 等）过滤掉，导致新 prompt 里根本没有原表，模型「看不到」也就「保不住」。

3. **自愈无修改约束**：自愈循环只保证「语法/校验通过」，不保证「业务逻辑不变」。

### 结论

修改 SQL 时缺少一道**「结构差异校验」闸门**——只允许改用户明确要求的部分，其余必须锁定。

---

## 2. 涉及文件及代码修改说明

| 文件 | 改动 |
|---|---|
| `sql_modify.py`（新增） | 结构提取（表/聚合/业务 WHERE/SELECT/GROUP BY/ORDER BY）+ AST 差异对比 + 授权范围识别 + 越界判定 + 修复提示 |
| `agent.py` | ① 引入 `sql_modify`；② 校验循环内新增「修改结构保护」闸门（生成后 + 每次自愈后都做 diff 校验，越界则受约束修复一次，仍越界则拒绝执行）；③ `_modify_prompt_block` 注入原表列表 + 强化「禁止换表/换口径/换聚合」约束；④ 检索合并原 SQL 实际用表（P0-4） |
| `test_sql_modify.py`（新增） | #243 案例 + 正确修改 + 10 项场景的确定性回归测试 |
| `test_modify_query.py` | 新增 C10 集成测试（坏模型换表 → 拒绝执行）；修复 stub 签名 |

### 核心逻辑（`ask_v2` 校验循环内新增）

```
SQL 生成 → 规则校验（guard/validator/permission）通过
        → ★ 修改结构差异校验（仅 modify 场景）
            · diff_structure(base_sql, new_sql)
            · unauthorized_changes(diff, question)
            · 越界 → 受约束修复一次 → 重过完整闸门 → 仍越界 → 拒绝执行（fail-closed）
        → 需求审核 → 执行
```

- 每次自愈修复后也会重跑结构校验（在循环内），满足「自愈也必须遵循修改约束」。
- 结构校验不替代安全校验，两者独立、顺序执行。

---

## 3. 原始 SQL 与修改后 SQL 的 AST 差异报告

以 #243 的原始 SQL（正确基线）与被破坏后的 SQL 对比，`sql_modify.diff_structure` 输出：

| 检查项 | 原始 | 修改后 | 是否越界 |
|---|---|---|---|
| 表（FROM/JOIN） | `ods_ctc_point_batch_all`、`ods_ctc_point_record_all` | 加了 `dwd_tu_order_lab_all`、`ods_tu_large_handcart_order_all`、`ods_valet_order_all`，丢了 `ods_ctc_point_record_all` | ✅ 越界 |
| 聚合函数 | `COUNT(DISTINCT CASE WHEN channel=N THEN biz_no END)`、`SUM(CASE WHEN channel=N THEN amount ELSE 0 END)`、`SUM(point)` | `COUNT(*)`、`SUM(total)`、`SUM(order_amount)` | ✅ 越界 |
| 业务 WHERE | （无，仅时间） | `status = 6`、`state = 6`、`status = 40` | ✅ 越界 |
| SELECT 表达式 | 客户id/总积分/三类完单金额+完单量 | 少了大板/代驾完单 | ✅ 越界 |
| GROUP BY | `customer_id` | 变了 | ✅ 越界 |
| 时间 WHERE | `'2026-09-28'~'2026-10-28'` | 近30天动态 | ✅ 允许（用户要求） |

越界判定：`unauthorized_changes(diff, "将此查询修改为流动的近30天")` → `['tables', 'aggregations', 'business_where', 'select_exprs', 'group_by']`，正确识别出「时间修改」只授权 `time`，其余全部越界。

**正确「仅改时间」的 SQL**（把固定时间改为 `toStartOfDay(now()) - INTERVAL 29 DAY` / `+ INTERVAL 1 DAY`，其余一字不改）→ 结构差异为 **空**，正常放行。

---

## 4. 10 项多轮修改回归测试结果

`test_sql_modify.py`（确定性单元测试，21 项）覆盖 10 类场景的授权范围识别，全部通过：

| # | 场景 | 授权范围识别 | 结果 |
|---|---|---|---|
| 1 | 固定时间改近30天 | `time` | ✅ |
| 2 | 固定时间改近7天 | `time` | ✅ |
| 3 | 增加一个 WHERE 条件 | `filter`（+`add_filter`） | ✅ |
| 4 | 删除一个 SELECT 字段 | `remove_select` | ✅ |
| 5 | 增加一个统计字段 | `add_select` | ✅ |
| 6 | 修改 ORDER BY | `order_by` | ✅ |
| 7 | 修改 GROUP BY | `group_by` | ✅ |
| 8 | 修改指标计算方式 | `metric` | ✅ |
| 9 | 连续修改时间两次 | `time` | ✅ |
| 10 | 过滤值变更 | `filter` | ✅ |

另有：`#243 破坏性修改被识别为越界`（表/聚合/业务过滤/SELECT）、`#243 正确时间修改无差异`、`COUNT(DISTINCT)→COUNT 被识别`。

---

## 5. SQL 自愈约束验证结果

`test_modify_query.py` C10 集成测试（坏模型擅自换表/换聚合/换过滤）验证：

- ✅ 破坏性修改被拒绝（`ok=False`）
- ✅ 未执行数据库（`exec_called == 0`）
- ✅ 错误信息说明是「修改越界」（而非笼统失败）
- ✅ 返回了结构差异详情 `modify_violation` 供前端展示

自愈机制：越界时先「受约束修复一次」（把越界项回灌，要求恢复原结构），修复后**重新走完整闸门 + 重新结构校验**，仍越界则 fail-closed 拒绝执行，**绝不自动退化为全量重生成**。

---

## 6. 新旧任务路由兼容性验证

- **全新查询**（无 `base_query_id`）：`modify_context=None` → 结构校验完全跳过，行为与改造前一致。`test_pipeline.py`（17/17）、`test_gate.py`（121/121）、`test_gate_req.py`（72）、`test_pipeline_convergence.py`（26）、`test_requirement_review.py`（52）、`test_status_chain.py`（46）全部通过。
- **增量修改**（有 `base_query_id`）：走结构校验分支。`test_modify_query.py` 53/53（含原有 9 个场景 + 新增 C10）通过，原有「增指标/删维度/改过滤/改时间/失败后改/连续改/安全不可绕过/权限重判/澄清兼容」全部保持通过。

---

## 7. 尚未解决的问题和风险

1. **授权范围识别是关键词启发式**：`detect_scope` 用正则识别「时间/过滤/增删字段/排序/分组/口径」。对复杂自然语言（如「把口径调整成和上个月对齐」）可能识别不全或误判。已用「越界才拦截 + 一次受约束修复」兜底，但复杂表述仍建议用户说得更具体。
2. **时间谓词识别依赖列名关键词**：`_is_time_predicate` 靠 `create_time/ctime/...` 等列名 + 日期字面量判断「时间谓词」。若业务表用非标准时间列名（如 `stime`），可能把时间谓词误判为业务谓词，导致「只改时间」被误拦。建议后续把时间列从语义层元数据传入，替代关键词。
3. **结构提取依赖 sqlglot 解析成功**：解析失败时对应项返回 `None`（跳过该项，不误报），但也会漏检。已多方言兜底（clickhouse→mysql→postgres→无）。
4. **未做 GROUP BY/ORDER BY 的「仅增删」细分**：当前对 GROUP BY/ORDER BY 的变化按「整集合是否相同」判定，增删一个分组维度和整体替换无法区分。对本案例和常见场景已足够，后续可细化。
5. **真实模型回归待验证**：测试用「受控模型替身」验证接线与闸门逻辑，真实 DeepSeek 模型在修复提示下能否严格恢复原结构，需在真实环境跑一遍 #243 用例确认。

---

## 8. 验收原则对照

- ✅ SQL 可以修改，但不能擅自改变用户没要求修改的业务逻辑。
- ✅ 默认继承原 SQL 表/字段/聚合/过滤/分组/JOIN，仅改授权部分。
- ✅ 越界 → 记录差异 → 一次受约束修复 → 仍越界 → 终止，不自动退化全量重生成。
- ✅ 先审查、再实施、最后回归验证（本报告 §1→§2→§4/§5/§6）。
