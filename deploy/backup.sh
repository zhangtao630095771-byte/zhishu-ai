#!/usr/bin/env bash
# ═══════════════════════════════════════════════════════════
# 知数AI · 知识库在线备份（agent_config.db = 全系统最值钱资产）
#
# 用 SQLite 的 backup API（在线备份，不锁表、不阻塞查询）。
#
# 用法：
#   # 容器部署（在宿主机上跑）
#   docker exec zhishu-ai /bin/sh -c 'DATA_DIR=/app/data /app/deploy/backup.sh'
#
#   # 裸机部署（宿主机直接跑）
#   DATA_DIR=/data/zhishu ./deploy/backup.sh
#
# 挂 cron（每天 02:00，容器部署示例）：
#   0 2 * * * docker exec zhishu-ai /bin/sh -c 'DATA_DIR=/app/data /app/deploy/backup.sh'
#
# 建议再把备份目录同步到 NAS / 对象存储，避免单机故障连备份一起丢。
# ═══════════════════════════════════════════════════════════
set -euo pipefail

DATA_DIR="${DATA_DIR:-/app/data}"          # 数据目录（含 agent_config.db）
BACKUP_DIR="${BACKUP_DIR:-$DATA_DIR/backups}"
KEEP="${KEEP:-14}"                          # 保留最近 N 份
DB_FILE="${DB_FILE:-$DATA_DIR/agent_config.db}"

if [ ! -f "$DB_FILE" ]; then
    echo "❌ 找不到 $DB_FILE，请确认 DATA_DIR / DB_FILE 指向正确"
    exit 1
fi

mkdir -p "$BACKUP_DIR"
STAMP="$(date +%Y%m%d-%H%M%S)"
OUT="$BACKUP_DIR/agent_config-$STAMP.db"

# 优先 sqlite3 CLI 的 .backup；没有则退回 python 的 sqlite3.backup()
if command -v sqlite3 >/dev/null 2>&1; then
    sqlite3 "$DB_FILE" ".backup '$OUT'"
else
    python3 - "$DB_FILE" "$OUT" <<'PY'
import sqlite3, sys
src = sqlite3.connect(sys.argv[1])
dst = sqlite3.connect(sys.argv[2])
src.backup(dst)
dst.close(); src.close()
PY
fi

echo "✅ 备份完成：$OUT（$(du -h "$OUT" | cut -f1)）"

# 只保留最近 KEEP 份，其余删除
ls -1t "$BACKUP_DIR"/agent_config-*.db 2>/dev/null | tail -n +$((KEEP + 1)) | xargs -r rm -f
echo "   已清理旧备份，保留最近 $KEEP 份"
