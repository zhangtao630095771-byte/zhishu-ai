#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  restart.sh —— 改完 .env 后一键重启知数AI服务
#
#  为什么需要它：
#    config.py 用 load_dotenv() 只在【进程启动时】读一次 .env，
#    改完 DEEPSEEK_API_KEY 后必须重启进程才生效。
#    而 launchd 的 --reload 只监听 .py 文件，改 .env 不会触发。
#
#  它会做四件事：
#    ① 校验 .env 里 key 不为空
#    ② 停掉旧服务（launchd job + 残留 uvicorn 进程）
#    ③ 重新注册 launchd；不行就退回 load；再不行用 serve.sh -d 常驻
#    ④ 轮询端口直到返回 200，确认真的起来了
#
#  用法：
#    ./restart.sh
#
#  ★ 请在【你自己的终端】里执行。某些受限环境（沙箱/IDE 内置执行器）
#    没有向 launchd 注册任务的权限，会报 Input/output error。
# ═══════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")"                       # 切到脚本所在目录，避免相对路径出错

PORT=8000                                   # 服务端口，和 serve.sh 保持一致
LABEL="com.zhishuai.server"                 # launchd 的 job 标识
PLIST="$HOME/Library/LaunchAgents/$LABEL.plist"
UID_NUM="$(id -u)"                          # 当前用户 uid，gui 域需要它
PY=".venv/bin/python"

# ── ① 前置检查 ────────────────────────────────────────────────
if [ ! -f .env ]; then
    echo "❌ 找不到 .env（当前目录：$(pwd)）"
    exit 1
fi
# grep -E 是扩展正则：. + 表示"至少要有一个字符"，过滤掉空值
if ! grep -qE '^DEEPSEEK_API_KEY=.+$' .env; then
    echo "❌ .env 里的 DEEPSEEK_API_KEY 为空或不存在，请先填好 key"
    exit 1
fi
if [ ! -x "$PY" ]; then
    echo "❌ 找不到虚拟环境 $PY，请先创建 .venv 并安装依赖"
    exit 1
fi
echo "① 配置检查通过（.env 中 DEEPSEEK_API_KEY 非空）"

# ── ② 停掉旧服务 ──────────────────────────────────────────────
echo "② 停止旧服务…"
# bootout = 停止并把 job 从 launchd 移除；job 本来不存在时返回非 0，忽略即可
launchctl bootout "gui/$UID_NUM/$LABEL" 2>/dev/null || true
# 兜底：杀掉可能残留的 uvicorn（比如之前用 serve.sh 或手动启动的）
pkill -f "uvicorn server:app" 2>/dev/null || true

# 等端口真正释放，否则新进程会报 "address already in use"
for _ in $(seq 1 20); do
    lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1 || break
    sleep 0.5
done
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "   ⚠️  端口 $PORT 仍被占用，继续尝试启动（可能失败）"
else
    echo "   ✅ 旧进程已停止，端口 $PORT 已释放"
fi

# ── ③ 重新拉起服务 ────────────────────────────────────────────
echo "③ 重新启动服务…"
STARTED=""
if launchctl bootstrap "gui/$UID_NUM" "$PLIST" 2>/dev/null; then
    STARTED="launchd(bootstrap)"
else
    # 老办法：unload + load，兼容旧版 macOS
    launchctl unload "$PLIST" 2>/dev/null || true
    if launchctl load "$PLIST" 2>/dev/null; then
        STARTED="launchd(load)"
    else
        # 最后的退路：项目自带的 serve.sh 后台常驻（自带 pidfile）
        if ./serve.sh -d >/dev/null 2>&1; then
            STARTED="serve.sh -d"
        fi
    fi
fi

if [ -z "$STARTED" ]; then
    echo "   ❌ 三种方式都启动失败"
    echo "   请查看日志：tail -50 /tmp/zhishu-ai.out.log /tmp/zhishu-ai.err.log"
    exit 1
fi
echo "   ✅ 已通过 $STARTED 启动"

# ── ④ 验证服务真的可用 ────────────────────────────────────────
# 首次启动要加载 embedding 模型，可能比较慢，最多等 60 秒
echo "④ 等待服务就绪（最多 60 秒）…"
CODE="000"
for _ in $(seq 1 60); do
    CODE="$(curl -s -o /dev/null -w '%{http_code}' -m 2 "http://127.0.0.1:$PORT/" 2>/dev/null)"
    if [ "$CODE" = "200" ]; then
        echo "   ✅ 服务已就绪：http://127.0.0.1:$PORT  (HTTP 200)"
        echo ""
        echo "   现在可以发一句真实查询验证新 key 是否生效。"
        exit 0
    fi
    sleep 1
done

echo "   ❌ 60 秒内未就绪（最后 HTTP $CODE）"
echo "   日志："
echo "     tail -50 /tmp/zhishu-ai.out.log"
echo "     tail -50 /tmp/zhishu-ai.err.log"
echo "   若报 401 / Invalid API key，检查 .env 里 key 是否多了空格或少了字符。"
exit 1
