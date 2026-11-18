# P0 核心能力升级 · 尚未解决的问题（known-issues）

> 如实记录，不伪造。按「能否用代码解决」分类。

---

## 一、数据/业务依赖（需生产权限或人工确认，非代码可解决）

1. **生产口径评测集缺 `expected_result`**：`golden` 35 条中 0 条带结果、`regression` 14 条中 0 条。`result_accuracy`（业务结果正确率）在补齐前仍为 `null`。补齐需在真实 ClickHouse 上跑通标准 SQL 取得快照 + 业务人员确认口径。
2. **业务结果准确率暂无真实值**：同因，见 acceptance-report §二。
3. **integration 层 `test_gaps.py` 未运行**：需连生产 ClickHouse；本次环境未连库。

## 二、设计留白（代码可做，本轮未做）

4. **「业务任务完成」无自动判分维度**：六类核心评测的第 6 类目前靠 `status_chain.business_verified`（人工审核回填）。自动判定需要「需求满足度」的 LLM/规则判分器，涉及模型一致性风险，建议 P1 用「确定性规则 + LLM 解读」逐步引入。
5. **评测指标无持久化历史趋势**：`evaluation/reports/*.json` 以文件存在，无 DB 表。看板「历史趋势」只能读最新报告。P1 建议建 `eval_runs` 表。
6. **Recall@K / Context Precision 未精确实现**：`runner.aggregate` 有 `table_retrieval_recall`（Recall@1 语义），未实现 K 值扫描与 Context Precision。P1 建议在 checker 里按「召回上下文质量」评分。
7. **消融实验缺少 C（Multi-RAG 多通道逐一关闭）的完整枚举**：当前 C 用 `dictionary`/`entity_filter` 两个代表性通道代表；向量通道、关键词通道未逐一隔离。
8. **标准问题集与 `evaluation/datasets/*.jsonl` 双轨并存**：`golden_cases`（DB）与既有 JSONL 数据集尚未双向同步。P1 建议把 DB 作为唯一事实源，JSONL 仅作导出/导入格式（`/api/golden/import` 已支持导入）。

## 三、已知行为边界（非缺陷，需认知）

9. **标准 SQL 验证的列级校验会降级**：`validate_expected_sql` 在无索引上下文时，`sql_validator` 的列级存在性校验跳过（status=degraded），只做表级 + 只读 + 权限 + P0 校验。要启用列级校验需先 `/api/index/rebuild`。
10. **看板「SQL 校验通过率」无独立落库字段**：`query_records` 未记录每条的 `gate_passed`，看板以「执行成功 ⇒ 闸门必过」为下限代理，真实校验通过率依赖评测报告的 `sql_valid_pass_rate`。P1 建议给 `query_records` 补 `gate_passed`/`validation_status` 落库列。
