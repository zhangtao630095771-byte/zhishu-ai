# 更新日志（CHANGELOG）

本项目版本号遵循 [语义化版本 Semantic Versioning](https://semver.org/lang/zh-CN/)：
- **MAJOR**：不兼容的架构/口径变更
- **MINOR**：新增能力，向下兼容
- **PATCH**：修复缺陷，向下兼容

---

## [1.0.0] — 2026-11-18

首个正式发版。从「开发机自用」到「公司内网可部署」的完整能力基线。

### 新增
- **确定性取数流水线**：查询理解 → P0~P4 分层检索 → SQL 生成 → 五维校验 → 唯一执行出口，全程 fail-closed。
- **多层安全护栏**：只读校验、表白名单（sqlglot AST）、字段级 ALLOW/MASK/DENY、行级权限 AST 改写、结果脱敏、审计留痕。
- **业务口径硬约束**：指标元数据声明 `required_predicates` / `forbidden_predicates`，一对多 JOIN 强制预聚合。
- **业务域语义体系**：业务域 → 业务主题 → 语义模型三层组织，OSSIE 标准对象动态映射（派生视图不落库）。
- **会话式分析**：Query State 结构化 + 多轮增量修改 + 需求一致性审核（灰度期只告警）。
- **SQL 经验中心**：成功/失败经验检索、归纳规则注入、高相似复用。
- **完整评测门禁**：8 类数据集 + Release/Regression Gate + 线上反馈回流。
- **Web 服务**：FastAPI 60+ API、字段级权限前端、业务域管理页。
- **内网部署体系**：Docker + nginx + 生产环境变量模板 + 知识库在线备份 + 上线检查清单。

### 安全
- 去除默认管理员口令，改为 `ADMIN_INITIAL_PASSWORD` 环境变量 + 首次强制改密。
- `.env` / `*.db` / `*.bak-*` / 前端备份目录全部排除，密钥与知识库不入库。

### 部署
- 修复 `CK_JFYF` / `CK_KPCIFOXK` 失效变量名（正确为 `CK_PORT` / `CK_DATABASE`）。
- 新增 `prod.env.example`、`docker-compose.yml`、`deploy/nginx.conf`、`deploy/backup.sh`、`上线准备清单.md`。

[1.0.0]: https://github.com/zhangtao630095771-byte/zhishu-ai/releases/tag/v1.0.0
