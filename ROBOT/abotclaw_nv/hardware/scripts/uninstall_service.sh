#!/bin/bash
#
# G1 灵巧手 Server 服务卸载脚本
# 功能：停止、禁用并删除 systemd 服务，清理 CAN 接口
#

set -e

SERVICE_NAME="g1-hand-server"
SERVICE_FILE="/etc/systemd/system/${SERVICE_NAME}.service"
SUDOERS_FILE="/etc/sudoers.d/unitree-${SERVICE_NAME}"

log() { echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"; }

if [ "$(id -u)" -ne 0 ]; then
    log "[ERROR] 请使用 sudo 运行此脚本"
    exit 1
fi

log "======================================"
log "G1 灵巧手 Server 服务卸载"
log "======================================"

# 清理 CAN 接口
log "关闭 CAN 接口..."
for iface in can0 can1; do
    sudo ip link set "$iface" down 2>/dev/null || true
done

# 停止并删除 systemd 服务
if [ ! -f "$SERVICE_FILE" ]; then
    log "[WARN] 服务文件不存在，跳过"
else
    log "停止服务: ${SERVICE_NAME}"
    systemctl stop "${SERVICE_NAME}" 2>/dev/null || true

    log "取消自启: ${SERVICE_NAME}"
    systemctl disable "${SERVICE_NAME}" 2>/dev/null || true

    log "删除服务文件: $SERVICE_FILE"
    rm -f "$SERVICE_FILE"

    log "重新加载 systemd..."
    systemctl daemon-reload
fi

# ---------- 清理 sudo NOPASSWD ----------
if [ -f "$SUDOERS_FILE" ]; then
    log "清理 sudo NOPASSWD 配置: $SUDOERS_FILE"
    rm -f "$SUDOERS_FILE"
fi

log "======================================"
log "✓ 服务卸载完成"
log "======================================"
