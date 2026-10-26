#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  一键启动 Web 服务
#
#  它会做两件事：
#    1. 确认 ClickHouse SSH 隧道已建立（没建就自动建）
#    2. 启动 FastAPI 服务，浏览器打开 http://127.0.0.1:8000
#
#  用法：
#     ./serve.sh            前台启动（推荐，能看到日志，Ctrl+C 停止）
#     ./serve.sh -d         后台启动（关掉终端也不停）
#     ./serve.sh stop       停止后台服务
#     ./serve.sh status     查看运行状态
#     ./serve.sh -d --reload   开发模式：改代码自动重载（★ 生产不要开）
#     RELOAD=1 ./serve.sh -d   同上（环境变量方式）
# ═══════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")"

PORT=8000
# ★ 监听地址：默认 127.0.0.1（只有本机能访问，最安全，开发用）。
#   要让【同事从别的机器访问】，必须绑 0.0.0.0：
#       HOST=0.0.0.0 ./serve.sh -d
#   绑 0.0.0.0 后所有网卡都监听，同内网的同事用 http://<本机内网IP>:8000 访问。
HOST="${HOST:-127.0.0.1}"
PY=".venv/bin/python"
LOG="/tmp/zhishu-ai.log"
PIDFILE="/tmp/zhishu-ai.pid"

# ── 开发热重载开关（默认关闭；--reload 或 RELOAD=1 开启）─────────────
#   ★ 仅开发/调试用：改 .py 后 uvicorn 自动重启子进程，省去手动重启。
#   ★ 生产必须关闭（--reload 会多一个 reloader 进程、有性能/稳定性开销）。
RELOAD_FLAG=""
case " $* " in *" --reload "*) RELOAD_FLAG="--reload" ;; esac
if [ "${RELOAD:-}" = "1" ]; then RELOAD_FLAG="--reload"; fi
if [ -n "$RELOAD_FLAG" ]; then
    echo "⚠️  已开启热重载（--reload）：仅开发用，生产请勿开启"
fi

# ── 找解释器 ──────────────────────────────────────────────────
if [ ! -x "$PY" ]; then
    echo "❌ 找不到 $PY（项目虚拟环境）。请先在项目目录执行 python3 -m venv .venv && .venv/bin/pip install -r requirements.txt"
    exit 1
fi

# ── 子命令：停止 ──────────────────────────────────────────────
if [ "${1:-}" = "stop" ]; then
    if [ -f "$PIDFILE" ] && kill -0 "$(cat "$PIDFILE")" 2>/dev/null; then
        kill "$(cat "$PIDFILE")" && echo "🛑 已停止服务 (pid: $(cat "$PIDFILE"))"
        rm -f "$PIDFILE"
    else
        pkill -f "uvicorn server:app" 2>/dev/null && echo "🛑 已停止服务" || echo "服务本来就没在跑"
    fi
    exit 0
fi

# ── 子命令：状态 ──────────────────────────────────────────────
if [ "${1:-}" = "status" ]; then
    if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
        echo "✅ 服务运行中：http://$HOST:$PORT"
    else
        echo "❌ 服务未运行"
    fi
    ./tunnel.sh status 2>/dev/null | sed 's/^/   /'
    exit 0
fi

# ── 1) 确保隧道 ───────────────────────────────────────────────
echo "① 检查 ClickHouse 隧道…"
if ! curl -s -m 5 "http://127.0.0.1:8123/ping" 2>/dev/null | grep -q "Ok"; then
    echo "   隧道不通，正在建立…"
    ./tunnel.sh start || { echo "❌ 隧道建立失败，请先解决 SSH 连接"; exit 1; }
    sleep 2
fi
if curl -s -m 5 "http://127.0.0.1:8123/ping" 2>/dev/null | grep -q "Ok"; then
    echo "   ✅ 隧道就绪 (127.0.0.1:8123 → ClickHouse)"
else
    echo "   ⚠️  隧道端口在，但 ClickHouse 无响应，继续启动（页面可能取不到数）"
fi

# ── 2) 端口占用检查 ───────────────────────────────────────────
if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
    echo "② 端口 $PORT 已被占用 —— 服务可能已在运行"
    echo "   访问：http://$HOST:$PORT"
    echo "   要重启请先执行：./serve.sh stop"
    exit 0
fi

# ── 3) 启动服务 ───────────────────────────────────────────────
echo "② 启动 Web 服务…"
if [ "${1:-}" = "-d" ] || [ "${1:-}" = "--daemon" ]; then
    # 后台：nohup + 进程完全脱离终端
    nohup "$PY" -m uvicorn server:app --host "$HOST" --port $PORT $RELOAD_FLAG >"$LOG" 2>&1 &
    echo $! > "$PIDFILE"
    disown 2>/dev/null || true
    sleep 5
    if lsof -nP -iTCP:$PORT -sTCP:LISTEN >/dev/null 2>&1; then
        echo "   ✅ 已在后台启动 (pid: $(cat "$PIDFILE")) 监听 $HOST:$PORT"
        echo "   日志：tail -f $LOG"
        echo "   停止：./serve.sh stop"
    else
        echo "   ❌ 启动失败，日志尾部："
        tail -20 "$LOG"
        rm -f "$PIDFILE"
        exit 1
    fi
else
    # 前台：直接跑，Ctrl+C 停止，日志实时可见
    echo "   ✅ 即将启动，浏览器打开：http://127.0.0.1:$PORT"
    echo "   （按 Ctrl+C 停止服务）"
    echo ""
    exec "$PY" -m uvicorn server:app --host "$HOST" --port $PORT $RELOAD_FLAG
fi
