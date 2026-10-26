# ═══════════════════════════════════════════════════════════
# 知数AI · Dockerfile（生产部署）
#
# ★ 前提：容器需与 ClickHouse 处于同一内网/VPC，直接用环境变量 CK_HOST 直连，
#   不再依赖 SSH 隧道（隧道只适合开发机，不适合生产）。
# ★ embedding 模型已缓存在项目 models/ 目录（EMBED_LOCAL_ONLY=1 只读本地），
#   随镜像一起打包，内网环境启动零联网。
# ═══════════════════════════════════════════════════════════
FROM python:3.13-slim

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# ★ 知识库 / 账号都写在 SQLite（agent_config.db）。默认会落在工作目录 /app，
#   那是【容器可写层】—— 容器一重建就全丢（知识库、账号、反馈全没）。
#   这里显式指到挂载卷，配套运行时：-v zhishu-data:/app/data
ENV CONFIG_DB=/app/data/agent_config.db
RUN mkdir -p /app/data

# 先装依赖（利用 Docker 层缓存，代码变更不必重装）
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 再拷代码与本地模型
COPY . .

EXPOSE 8000

# ★ 探活必须用【公开】接口 /api/ping。
#   原来写的是 /api/status —— 加了登录鉴权后该接口需要身份，不带 token 返回 401，
#   urlopen 抛 HTTPError → 容器永远 unhealthy，被平台反复重启。
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/ping', timeout=5)" || exit 1

# 单 worker：索引与 embedding 模型是进程内懒加载缓存，多 worker 会重复加载、内存翻倍。
# 水平扩展用「多实例 + 反向代理」，而不是 --workers。
CMD ["python", "-m", "uvicorn", "server:app", "--host", "0.0.0.0", "--port", "8000"]
