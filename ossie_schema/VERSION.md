# Apache Ossie Schema 版本锁定记录

本目录内的 schema 文件是知数AI 内置的**固定版本副本**，用于 OSSIE 语义模型的导入/导出/校验，不追踪上游 main 分支。

## 锁定信息

| 项 | 值 |
|---|---|
| 上游仓库 | https://github.com/apache/ossie |
| 锁定 commit | e6ad94ee87a53fd8f8b6dbf0de33fab59e406c0c |
| 规范版本 | 0.3.0.dev0（DRAFT） |
| 锁定日期 | 2026-11-12 |
| 锁定原因 | 任务书要求「固定 Schema 版本或 Git Commit」；main 为 0.3.0.dev0（含 nonportable_extensions 能力），不追 main 避免上游破坏性变更 |

## 内置文件

- `ossie-schema.json` — 机器可读 JSON Schema（draft 2020-12），校验单个语义模型文档
- `spec.yaml` — 规范 YAML 定义
- `spec.md` — 人类可读规范全文

## 使用约定

- `ossie_adapter.py` 只从本目录加载 schema，不联网、不追踪上游。
- 未来如需升级规范版本：重新下载指定 commit 的三个文件覆盖本目录，并更新上表与 `ossie_adapter.py` 顶部的 `OSSIE_VERSION` / `SCHEMA_COMMIT` 常量。
- 升级后必须重跑往返一致性测试（`test_ossie_adapter.py`）。
