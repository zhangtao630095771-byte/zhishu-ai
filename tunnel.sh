#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════════
#  ClickHouse SSH 隧道
#
#  ClickHouse 跑在公司内网（地址见 .env 的 SSH_REMOTE_HOST），Mac 直连不到，
#  必须经跳板机（地址见 .env 的 SSH_HOST）转发：
#
#     Mac 127.0.0.1:8123 ──SSH──> 跳板机 ──内网──> ClickHouse:8123
#
#  用法：
#     ./tunnel.sh          建立隧道（已在跑则提示）
#     ./tunnel.sh status   查看状态
#     ./tunnel.sh stop     关闭隧道
# ═══════════════════════════════════════════════════════════════
set -uo pipefail
cd "$(dirname "$0")"

ENV_FILE=".env"

# ── 从 .env 读一个键（不依赖 python）─────────────────────────
get_env() {
    grep -E "^[[:space:]]*$1=" "$ENV_FILE" 2>/dev/null | head -1 | cut -d= -f2- \
        | sed 's/^[[:space:]]*//;s/[[:space:]]*$//'
}

SSH_HOST=$(get_env SSH_HOST)
SSH_PORT=$(get_env SSH_PORT);  SSH_PORT=${SSH_PORT:-22}
SSH_USER=$(get_env SSH_USER)
SSH_PASSWORD=$(get_env SSH_PASSWORD)
REMOTE_PORT=$(get_env CK_PORT); REMOTE_PORT=${REMOTE_PORT:-8123}
LOCAL_PORT="$REMOTE_PORT"
# ★ 入库安全（GitHub）：内网 CK 地址不再硬编码默认值，必须由 .env 提供。
#   本机迁移：确认 .env 里有 SSH_REMOTE_HOST=（老版本默认值），否则此处会报错提示。
REMOTE_HOST=$(get_env SSH_REMOTE_HOST); REMOTE_HOST=${REMOTE_HOST:?"❌ 请在 .env 设置 SSH_REMOTE_HOST（ClickHouse 内网地址）"}   # ClickHouse 内网地址

if [ -z "$SSH_HOST" ] || [ -z "$SSH_USER" ] || [ -z "$SSH_PASSWORD" ]; then
    echo "❌ .env 里缺少 SSH_HOST / SSH_USER / SSH_PASSWORD，无法建隧道"
    exit 1
fi

is_up() { lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN >/dev/null 2>&1; }

stop_tunnel() {
    local pids
    pids=$(lsof -nP -iTCP:"$LOCAL_PORT" -sTCP:LISTEN -t 2>/dev/null)
    if [ -n "$pids" ]; then
        # shellcheck disable=SC2086
        kill $pids 2>/dev/null
        echo "🛑 已关闭隧道 (pid: $(echo $pids | tr '\n' ' '))"
    else
        echo "ℹ️  隧道本来就没在跑"
    fi
}

case "${1:-start}" in
    stop)   stop_tunnel; exit 0 ;;
    status)
        if is_up; then
            echo "✅ 隧道运行中：127.0.0.1:${LOCAL_PORT} -> ${REMOTE_HOST}:${REMOTE_PORT} (经 ${SSH_USER}@${SSH_HOST})"
            curl -s -m 8 "http://127.0.0.1:${LOCAL_PORT}/ping" || echo "⚠️  端口在监听但 ClickHouse 无响应"
        else
            echo "❌ 隧道未运行"
        fi
        exit 0
        ;;
esac

if is_up; then
    echo "ℹ️  隧道已在运行（127.0.0.1:${LOCAL_PORT}），无需重复建立"
    echo "   要重来请先执行：./tunnel.sh stop"
    exit 0
fi

echo "🔌 正在建立隧道 ${SSH_USER}@${SSH_HOST}:${SSH_PORT} ..."

export SSHPASS_WRAP_HOST="$SSH_HOST"
export SSHPASS_WRAP_PORT="$SSH_PORT"
export SSHPASS_WRAP_USER="$SSH_USER"
export SSHPASS_WRAP_PW="$SSH_PASSWORD"
export SSHPASS_WRAP_REMOTE="$REMOTE_HOST:$REMOTE_PORT"
export SSHPASS_WRAP_LOCAL="$LOCAL_PORT"

expect <<'EOF' 2>&1 | grep -vE 'spawn |assword:'
set timeout 30
spawn ssh -f -N \
    -o StrictHostKeyChecking=no \
    -o UserKnownHostsFile=/dev/null \
    -o LogLevel=ERROR \
    -o PubkeyAuthentication=no \
    -o PreferredAuthentications=password \
    -o NumberOfPasswordPrompts=1 \
    -o ExitOnForwardFailure=yes \
    -o ServerAliveInterval=30 \
    -o ServerAliveCountMax=3 \
    -L $env(SSHPASS_WRAP_LOCAL):$env(SSHPASS_WRAP_REMOTE) \
    $env(SSHPASS_WRAP_USER)@$env(SSHPASS_WRAP_HOST) -p $env(SSHPASS_WRAP_PORT)
expect {
    "assword:"          { send "$env(SSHPASS_WRAP_PW)\r"; exp_continue }
    "Permission denied" { puts ">>>FAIL_WRONG_PASSWORD"; exit 1 }
    "Connection closed" { puts ">>>FAIL_CLOSED"; exit 1 }
    "Address already in use" { puts ">>>FAIL_PORT_BUSY"; exit 1 }
    timeout             { puts ">>>FAIL_TIMEOUT"; exit 1 }
    eof                 {}
}
EOF

sleep 1
if is_up; then
    echo "✅ 隧道已建立：127.0.0.1:${LOCAL_PORT} -> ${REMOTE_HOST}:${REMOTE_PORT}"
    echo -n "   ClickHouse 响应："
    curl -s -m 8 "http://127.0.0.1:${LOCAL_PORT}/ping" || echo "(无响应)"
else
    echo "❌ 隧道建立失败（多为 SSH 密码错误或端口被占）"
    exit 1
fi
