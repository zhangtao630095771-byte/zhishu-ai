# 双流水线收敛专项 · 遗留问题与风险清单（08-known-issues）

> 如实记录，不伪造。

---

## 一、需生产侧/环境补做（非本地代码可解决）

1. **结果级 A/B 对照未做**：本环境无 LLM 调用 + 无 ClickHouse，无法「同一输入双路跑数」做业务结果级比较。上线前须在隔离环境补做（用 `golden-demo` 26 条带 `expected_result` 的固定种子，及补齐 `expected_result` 的生产口径用例）。
2. **生产灰度未执行**：代码层已切换，但生产部署、灰度、负责人确认下线属人工动作（05 已列步骤与回滚条件）。
3. **integration 层 `test_gaps.py` 未连库运行**：需真实 ClickHouse。

## 二、行为变化须知（收敛的代价，需向业务方明确）

4. **瞬时故障不再有 Legacy 兜底**：方案 A 下，V2 遇超时/连接异常直接 fail-closed 返回错误，而非换弱校验路径重试。正确的缓解是 `ssh_tunnel.ensure_tunnel` 自愈 + 连接层/`_exec_sql` 重试；若业务方确认「必须保留瞬时故障降级」，需切换到方案 B（保留轻量只读兜底但补齐行级权限 + 自愈重过闸门），见 02 §三。

## 三、技术债/后续建议

5. **`degrade_reason` 语义变化**：原指「降级到 Legacy 的原因」，现在指「fail-closed 的异常原因」。字段名保留是向后兼容，但语义已变，前端/监控文案若仍写「降级」需改词（避免误读）。
6. **`zhishu-ai-code-bundle/` 历史快照**：仍含旧版 Legacy 实现，作为最终兜底参考保留，非当前代码；长期可归档到独立仓库或删除。
7. **`check1.py`**：仍在根目录，属早期 scratch 脚本（未引用退役代码），本次未清理，后续可随 `check_consistency.py` 等工具脚本统一整理。
8. **`rag._hybrid_retrieve` 等**：原被 Legacy 与 `retrieve_dicts` 共用，`retrieve_dicts` 仍用，故保留；但已无「表/指标混合检索」的对外入口，`retrieve_dicts` 是唯一消费方，未来若字典检索重构可一并审视。

## 四、上线前检查清单（给负责人）

- [ ] 隔离环境跑结果级 A/B 对照（04 §三）
- [ ] 生产灰度：观察 `query_records.degraded` 与 `degrade_reason`
- [ ] 确认 `ssh_tunnel` 自愈覆盖瞬时故障，fail-closed 比例可接受
- [ ] 确认前端/监控「降级」文案已更新为「异常中止」语义
- [ ] 负责人确认下线（06 §一 条件 8）
