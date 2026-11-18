# 知数AI · 发版流程（基于 GitHub）

## 一、版本约定

- 版本号存于根目录 `VERSION` 文件，格式 `MAJOR.MINOR.PATCH`（如 `1.0.0`）。
- Git tag 格式：`v` + 版本号，如 `v1.0.0`。tag 是发版的唯一锚点。
- 每次发版必须更新 `CHANGELOG.md`。

## 二、日常开发（CI 自动守护）

```
本地改代码 → git push / PR → GitHub Actions 自动跑单元测试
```
- 测试不过 → 合并/推送会被标记为失败，不许发版。
- 配置文件在 `.github/workflows/ci.yml`。

## 三、发版步骤（每次上线走一遍）

### 1. 准备
```bash
# 确保 main 是最新且测试通过
git checkout main
git pull origin main
.venv/bin/python run_tests.py unit       # 本地先跑一遍

# 更新版本号与 CHANGELOG（手动编辑两个文件）
echo "1.0.1" > VERSION                    # 示例：PATCH 版本
# 编辑 CHANGELOG.md，加上 [1.0.1] 段落
```

### 2. 提交并打 tag
```bash
git add VERSION CHANGELOG.md
git commit -m "chore: release v1.0.1"
git tag -a v1.0.1 -m "Release v1.0.1"
git push origin main
git push origin v1.0.1                    # ★ tag 必须推，Release 依赖它
```
> 也可以用 `./release.sh 1.0.1` 一键完成打 tag + push（见下）。

### 3. 在 GitHub 上创建 Release
- 打开 <https://github.com/zhangtao630095771-byte/zhishu-ai/releases>
- 点「Draft a new release」→ 选刚才推上去的 tag → 标题填 `v1.0.1` → 正文粘贴 CHANGELOG 对应段落 → 发布。

### 4. 服务器更新（内网部署机）
```bash
cd /path/to/zhishu-ai
git fetch --tags
git checkout v1.0.1                       # ★ 切到 tag，比跟 main 更稳，回滚只需换 tag

# 重建并重启服务（Docker 方式）
docker compose down
docker compose up -d --build

# 或裸机方式：拉代码后重启
./restart.sh

# 验证
curl http://127.0.0.1:8000/api/ping
```

## 四、回滚

```bash
git fetch --tags
git checkout v1.0.0                       # 切回上一个稳定 tag
docker compose up -d --build              # 重建镜像
```
> 知识库在持久化卷 `/app/data`，切 tag 不会丢数据（代码与数据分离）。

## 五、首次发版特别说明

当前仓库刚建立、远程为空，本地有历史改动未提交。首次完成「本地提交 + 推 tag + 建 Release」后，后续即可按上面流程走。用 `./release.sh 1.0.0` 可一键处理首次提交与打 tag。

## 六、安全红线

- `DEEPSEEK_API_KEY`、`CK_PASSWORD`、`SSH_PASSWORD`、`ADMIN_INITIAL_PASSWORD` 一律只走环境变量，绝不提交。
- `agent_config.db`（知识库+账号）绝不入库，靠挂载卷 + `deploy/backup.sh` 备份。
- 仓库设为 **Private**（公司内部项目），Public 会让代码结构暴露。
