#!/bin/bash
#
# G1 灵巧手 Server 服务安装脚本
# 功能：将 hand_server 注册为 systemd 服务，开机自启
#

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
HAND_SCRIPT="$SCRIPT_DIR/start_hand_server.sh"
SERVICE_NAME="g1-hand-server"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

# ---------- 检查 ----------
if [ "$(id -u)" -ne 0 ]; then
    log "[ERROR] 请使用 sudo 运行此脚本"
    exit 1
fi

if [ ! -f "$HAND_SCRIPT" ]; then
    log "[ERROR] 启动脚本不存在: $HAND_SCRIPT"
    exit 1
fi

log "======================================"
log "G1 灵巧手 Server 服务安装"
log "======================================"

# ---------- 生成 service 文件 ----------
log "创建服务文件: $SERVICE_FILE"

cat > "$SERVICE_FILE" << 'EOF'
[Unit]
Description=G1灵巧手控制Server
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
WorkingDirectory=/home/unitree/abotclaw_nv
ExecStartPre=/home/unitree/abotclaw_nv/hardware/scripts/wait_can.sh
ExecStart=/home/unitree/abotclaw_nv/hardware/scripts/start_hand_server.sh
Restart=on-failure
RestartSec=5
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

# ---------- 重新加载 systemd ----------
log "重新加载 systemd 守护进程..."
systemctl daemon-reload

# ---------- 启用并启动 ----------
log "启用服务: ${SERVICE_NAME}"
systemctl enable "${SERVICE_NAME}"

log "启动服务: ${SERVICE_NAME}"
systemctl start "${SERVICE_NAME}"

# ---------- 状态检查 ----------
sleep 2
if systemctl is-active --quiet "${SERVICE_NAME}"; then
    log "======================================"
    log "✓ 服务安装并启动成功!"
    log "======================================"
    log ""
    log "  常用命令:"
    log "    查看状态:  systemctl status ${SERVICE_NAME}"
    log "    查看日志:  journalctl -u ${SERVICE_NAME} -f"
    log "    重启服务:  systemctl restart ${SERVICE_NAME}"
    log "    停止服务:  systemctl stop ${SERVICE_NAME}"
    log "    取消自启:  systemctl disable ${SERVICE_NAME}"
else
    log "[ERROR] 服务启动失败，请检查日志:"
    log "  journalctl -u ${SERVICE_NAME} -n 30"
    exit 1
fi
