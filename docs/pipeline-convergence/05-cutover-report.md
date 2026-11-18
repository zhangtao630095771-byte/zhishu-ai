# 双流水线收敛专项 · 灰度与生产切换报告（05-cutover-report）

> 阶段五。本报告区分「**代码层切换（已完成）**」与「**生产部署切换（待人工执行）**」。
> 我只能在本地/隔离环境改代码 + 回归；生产部署、灰度、生产配置变更属于不可逆/需人工确认的操作。

---

## 一、代码层切换（已完成，有测试证据）

方案 A「fail-closed 不再降级」已落地：

| 变更 | 结果 |
|---|---|
| `agent.ask()` 只调 `ask_v2`，删除 legacy 降级分支 | ✅ 任何异常 fail-closed |
| `LEGACY_FALLBACK_MODE` 配置移除 | ✅ `config.py` 已删除 |
| `query_records` 补 `degraded`/`degrade_reason`/`trace_json` 列 | ✅ 幂等迁移，落库透传 |
| 新增 `test_pipeline_convergence.py`（26 项不变量） | ✅ 全绿 |

切换后，**生产取数请求只有一条主流水线**，不存在第二套业务编排。`pipeline` 字段恒为 `v2`。

## 二、生产切换步骤（待人工执行，附回滚条件）

任务书 §7 建议的六步，我已完成「代码层第一步（测试环境默认统一主流程）」，后续为运维/负责人动作：

| 步骤 | 状态 | 说明 |
|---|---|---|
| 1. 测试环境默认统一主流程 | ✅ 代码已就绪 | `agent.ask = ask_v2` 唯一路径 |
| 2. 内部验证 + 历史问题回放 | ⏸ 待执行 | 需 LLM + ClickHouse 隔离环境 |
| 3. 小范围灰度 | ⏸ 待执行 | 需生产部署 |
| 4. 逐步扩大覆盖 | ⏸ 待执行 | 观察 `degraded`/`degrade_reason` |
| 5. 确认无 Legacy 调用 | ⏸ 待执行 | `query_records.pipeline` 恒 v2 |
| 6. 移除 Legacy 生产分流 | ✅ 代码层已完成 | `ask_legacy`/`rag.retrieve`/`prompt.build_system_prompt` 已删除 |

## 三、灰度可观测性

- 落库 `pipeline`（恒 v2）、`degraded`（是否 fail-closed）、`degrade_reason`（异常类型+信息）、`trace_json`（各阶段耗时）。
- 上线后监控：`SELECT COUNT(*) FROM query_records WHERE degraded=1` → 若 fail-closed 比例异常升高，说明瞬时故障频发，需排查连接层（ssh_tunnel）而非回退 Legacy。

## 四、回滚方案

| 情形 | 动作 |
|---|---|
| 统一主流程明显回归 | `git revert` 本次提交，恢复 Legacy 兜底 |
| 瞬时故障频发（fail-closed 过高） | 排查 `ssh_tunnel.ensure_tunnel` 自愈 + 连接层重试，而非恢复弱校验的 Legacy |

## 五、结论

代码层切换已完成且回归通过；**生产部署与灰度需负责人确认后执行**。在未做结果级 A/B 对照（04 §三）与生产灰度（本节 §二）之前，不宣称「生产迁移成功」。
