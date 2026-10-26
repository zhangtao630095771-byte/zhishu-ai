# 知数AI · 上线部署指南

把知数AI从「开发机自己用」变成「同事都能用」，要解决三件事：
**① 服务得监听对外地址（不然同事连不上）② 机器得长期在线 ③ 知识库不能丢**。

---

## 零、先看清两个硬约束

**约束一：ClickHouse 在内网 `<内网CK_HOST>`。**
开发机连不到，所以现在靠跳板机 SSH 隧道（`tunnel.sh`）。生产机必须能**直连**它
（同内网 / 同 VPC），用 `CK_HOST` 指过去。代码逻辑：`ssh_tunnel.ensure_tunnel()`
在没配 `SSH_HOST` 时直接返回 —— 所以生产环境**不要**设 `SSH_HOST`，就是直连模式。

**约束二：这个系统是「单进程 + 本地 SQLite + 进程内缓存」。**
embedding 模型和检索索引都在进程内存里懒加载。所以：
- ❌ 不要用 `uvicorn --workers N`（每个 worker 都会再加载一遍模型，内存翻倍）
- ✅ 要扩容就起**多个容器/实例**，前面挂 nginx 轮询
- ✅ 小团队（十几个人）单实例完全够用

---

## 一、三种形态，按投入选

| 形态 | 适合 | 同事能访问 | 主要风险 |
|---|---|---|---|
| **A. 本机常驻**（`serve.sh -d`） | 先给 2~3 个人试用、内部演示 | ✅ 需绑 `0.0.0.0` | Mac 合盖/休眠/断网就挂；你的电脑成了服务器；无 HTTPS |
| **B. 内网服务器 + Docker + nginx**（推荐） | 正式提供给全公司 | ✅ | 需要一台能连 CK 的 Linux 机器 |
| **C. K8s / 内部 PaaS** | 公司已有平台 | ✅ | 要配持久卷（PV），否则知识库随 Pod 重建丢失 |

---

## 二、形态 A：今天就让同事用上（5 分钟）

```bash
cd /path/to/zhishu-ai
HOST=0.0.0.0 ./serve.sh -d          # ★ 默认绑 127.0.0.1，那样只有本机能访问
./serve.sh status                    # 确认监听地址
```

同事浏览器打开 `http://<你的内网IP>:8000`。

> `serve.sh` 的 `HOST` 默认是 `127.0.0.1`（开发最安全）。绑 `0.0.0.0` 才是对外。
> 用 launchd 常驻的话，`~/Library/LaunchAgents/com.zhishuai.server.plist` 里跑的是 `serve.sh`，
> 但 launchd **不会**自动带上你的环境变量，需要把 `HOST` 写进 plist 的 `EnvironmentVariables`
> 或写进 `serve.sh`（改成 `HOST="${HOST:-0.0.0.0}"`）。

⚠️ 形态 A 只适合试用：Mac 一休眠服务就断，而且没 HTTPS。

---

## 三、形态 B：正式部署（推荐）

### 1) 准备机器

一台 Linux 服务器（或公司内网 VM），要求：
- 能直连 ClickHouse（`curl http://<CK_HOST>:8123/ping` 返回 `Ok`）
- 装了 Docker
- 能访问 `api.deepseek.com`（或被允许出网）

### 2) 构建镜像

```bash
cd /path/to/zhishu-ai
docker build -t zhishu-ai:latest .
```

- `.dockerignore` 已排除 `.env`（密钥）与 `*.db`（数据），不会把密钥和你的本地知识库打进镜像。
- `models/`（embedding 模型，约 91MB）**会**打进镜像，配合 `EMBED_LOCAL_ONLY=1` 实现内网零联网启动。

### 3) 启动容器

```bash
docker run -d --name zhishu-ai -p 8000:8000 --restart unless-stopped \
  -e DB_TYPE=clickhouse \
  -e CK_HOST=<内网CK_HOST> \
  -e CK_PORT=8123 \
  -e CK_USER=<只读账号名> \
  -e CK_PASSWORD='只读账号密码' \
  -e CK_DATABASE=ctc_data_warehouse \
  -e DEEPSEEK_API_KEY='sk-...' \
  -e ACL_DEFAULT_VISIBLE=false \
  -e EMBED_LOCAL_ONLY=1 \
  -e CONFIG_DB=/app/data/agent_config.db \
  -v zhishu-data:/app/data \
  zhishu-ai:latest
```

> ★ `CONFIG_DB` + `-v zhishu-data:/app/data` 这两个必须成对出现，见下一节。

### 4) ★ 迁移知识库（漏了这步 = 白部署）

**这是最容易忽略、后果最严重的一步。**

镜像里只有「出厂知识」—— `config_store.py` 里的 `DEFAULT_PROMPTS / DEFAULT_METRICS /
DEFAULT_TABLE_COMMENTS / DEFAULT_TABLE_ACL`。而你**实打实积累下来的**知识全在本地
`agent_config.db` 这个文件里，`.dockerignore` 把它排除了，所以它**不在镜像里**：

| 内容 | 当前条数 | 没了会怎样 |
|---|---|---|
| `column_comments` 字段注释 | 94 | 模型读不懂字段含义，开始瞎猜口径 |
| `metrics` 指标口径 | 28 | 指标口径失效（出厂 28 条在，但线上改过的会丢） |
| `table_comments` 表注释 | 16 | 表语义丢失 |
| `table_acl` 表权限 | 13 | 表可见范围重置 |
| `entities` / `relationships` | 9 / 2 | 实体识别与 JOIN 规则丢失 |
| `dict_entries` 数据字典 | 7 | 编码值又变成裸数字 |
| `prompts`（在线改过的规则） | 2 | **规则 19/20 等线上新增的规则全丢** |
| `users` 账号 | 2 | 账号全丢，只剩默认 admin |

迁移做法（**先停掉开发机的服务**，避免复制到一半数据库还在写）：

```bash
# 开发机：停服务 → 用 sqlite 自己的 backup 命令导出（比直接 cp 安全）
./serve.sh stop
sqlite3 agent_config.db ".backup '/tmp/agent_config.db'"

# 拷到服务器
scp /tmp/agent_config.db user@server:/tmp/

# 服务器：塞进卷里（容器名按实际改）
docker cp /tmp/agent_config.db zhishu-ai:/app/data/agent_config.db
docker restart zhishu-ai
```

> 只想搬**知识**、不搬账号？也可以在开发机的「知识治理 → 导出备份」里导出 JSON。
> 注意：目前**只有导出、没有导入接口**，JSON 只能人工核对，全量迁移请用上面的文件复制。

验证迁移成功：登录后「知识管理」页顶部应显示注释覆盖率（94 条字段注释），
「表与权限」应显示 13 张已登记的表。

### 5) nginx + HTTPS

```nginx
server {
    listen 443 ssl;
    server_name zhishu.your-company.com;

    ssl_certificate     /etc/nginx/certs/zhishu.crt;
    ssl_certificate_key /etc/nginx/certs/zhishu.key;

    location / {
        proxy_pass http://127.0.0.1:8000;
        proxy_set_header Host $host;
        proxy_set_header X-Real-IP $remote_addr;

        # ★★ 这两行必须有：单个复杂取数要跑 40~120 秒，
        #    nginx 默认 60 秒就掐断 → 用户看到 504，还以为系统坏了。
        proxy_read_timeout    300s;
        proxy_send_timeout    300s;

        # 导出大文件（/api/export）别被缓冲卡住
        proxy_buffering off;
    }
}
```

内网如果暂时不上 HTTPS，也请至少：
- 不要把 8000 直接暴露到公网（只在内网 / 走 VPN）
- 知道 token 是放在浏览器 localStorage 的，纯 HTTP 下同网段可被嗅探

### 6) 账号与安全（上线第一天必做）

1. **首次登录会强制改密**：初始密码取 `ADMIN_INITIAL_PASSWORD`；未配置时由系统随机生成，
   只在首次初始化日志里打印一次。登录后必须先设置新密码，才能使用取数等功能。
   （不再有公开的 `admin/admin123` 默认口令。）
2. 给同事**逐个建普通用户**账号，不要共用 admin。
3. **每位管理员改完自己的密码后**，把 `users` 表当敏感数据对待（备份文件也要控权限）。
4. `CK_USER` 必须是**只读账号**——这是第一道防线；代码层 `table_acl` 是第二道防线。

### 7) 备份（知识库是全系统最值钱的东西）

```bash
# 每天备一次配置库（SQLite 在线备份，不锁表）
docker exec zhishu-ai python -c "
import sqlite3, shutil, datetime
src = sqlite3.connect('/app/data/agent_config.db')
dst = f\"/app/data/backup-{datetime.date.today()}.db\"
src.backup(sqlite3.connect(dst))
print('backed up ->', dst)
"
# 再把这个文件同步到对象存储/NAS
```

### 8) 健康检查与日志

- **健康检查请打 `/api/ping`**（公开接口）。不要用 `/api/status` —— 它需要登录，不带 token 返回 401，
  探活会一直失败、容器被反复重启（Dockerfile 里已内置正确的 HEALTHCHECK）。
- 日志走 stdout，交给容器平台采集：`docker logs -f zhishu-ai`
- 索引重建（改完知识点「重建索引」）耗时 5~15 秒，期间其他人仍可查询。

---

## 四、上线检查清单

- [ ] 生产机能 `curl http://<CK_HOST>:8123/ping` 通
- [ ] `HOST` 绑到 `0.0.0.0`（或前面有 nginx 转发），同事能打开页面
- [ ] `CONFIG_DB` 指向挂载卷 `-v zhishu-data:/app/data`
- [ ] **本地 `agent_config.db` 已迁移过去**，注释覆盖率/表数量对得上
- [ ] `CK_USER` 是只读账号
- [ ] `DEEPSEEK_API_KEY` 走环境变量，没写进镜像/仓库
- [ ] 默认管理员密码已修改，同事账号已建
- [ ] nginx `proxy_read_timeout 300s`
- [ ] 配置库备份任务已挂上
- [ ] 健康检查打 `/api/ping`

---

## 五、常见问题

**Q：同事打不开页面？**
A：① 确认服务绑的是 `0.0.0.0` 不是 `127.0.0.1`（`lsof -nP -iTCP:8000 -sTCP:LISTEN` 看监听地址）；
② 服务器防火墙 / 安全组是否放行 8000（或 443）；③ 是否在同一个内网。

**Q：容器起来了但取不到数？**
A：先 `docker exec zhishu-ai python -c "import db; print(db.execute_sql('SELECT 1'))"`，
再确认 `CK_HOST` 从容器里能连通、只读账号对目标表有 SELECT 权限。

**Q：容器重建后知识库/账号全没了？**
A：`CONFIG_DB` 没指到挂载卷，数据写进容器可写层了。见第三节第 4 步。

**Q：容器一直 unhealthy 被重启？**
A：健康检查打了需要登录的接口（401）。改打 `/api/ping`。

**Q：用户报「一直转圈」或 504？**
A：反代读超时太短。复杂取数要 40~120 秒，`proxy_read_timeout` 给到 300s。

**Q：内网下载不了 embedding 模型？**
A：在有网的机器先跑一次把 `models/` 下好（`EMBED_LOCAL_ONLY=0`），再整个打镜像；
或构建时用 `HF_ENDPOINT=https://hf-mirror.com` 走国内镜像。

**Q：要支持更多人怎么办？**
A：不要加 `--workers`。起多个容器实例（共享同一个挂载卷），前面用 nginx 轮询。

---

## 六、本机常驻（开发机，供参考）

```bash
cp com.zhishuai.server.plist ~/Library/LaunchAgents/
launchctl load ~/Library/LaunchAgents/com.zhishuai.server.plist     # 启动并常驻
launchctl unload ~/Library/LaunchAgents/com.zhishuai.server.plist   # 停止并卸载
tail -f /tmp/zhishu-ai.out.log /tmp/zhishu-ai.err.log               # 看日志
```

plist 里项目路径是绝对路径，换机器要改；它也会拉起 ClickHouse 隧道（`tunnel.sh`）。
这只是「开发机顺手常驻」，**不是生产方案**。
