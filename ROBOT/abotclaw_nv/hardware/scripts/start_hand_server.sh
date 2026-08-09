#!/bin/bash
#
# G1 灵巧手控制 Server 启动脚本
# 功能：检测 CAN 接口 → 运行 hand_server.py
#

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$(dirname "$SCRIPT_DIR")/.." && pwd)"
HAND_SERVER_SCRIPT="$PROJECT_ROOT/hardware/linkhand/hand_server.py"

log() {
    echo "[$(date '+%Y-%m-%d %H:%M:%S')] $1"
}

# 检测可用的网络工具（host 可能有 iproute2，容器镜像里没有）
if command -v ip &>/dev/null; then
    NET_TOOL="ip"
elif command -v /sbin/ip &>/dev/null; then
    NET_TOOL="/sbin/ip"
else
    NET_TOOL=""
    log "[WARN] iproute2 未安装，CAN 接口检测和配置将被跳过"
fi

log "======================================"
log "G1 灵巧手控制 Server 启动"
log "======================================"

# 尝试自动检测 conda 根目录
if [ -d "/opt/miniconda3" ]; then
    CONDA_ROOT="/opt/miniconda3"
elif [ -d "/home/unitree/miniconda3" ]; then
    CONDA_ROOT="/home/unitree/miniconda3"
else
    CONDA_ROOT=""
fi

# 检测 hand_server.py
if [ ! -f "$HAND_SERVER_SCRIPT" ]; then
    log "[ERROR] hand_server.py 不存在: $HAND_SERVER_SCRIPT"
    exit 1
fi

# CAN 检测函数
check_can_exists() {
    [ -z "$NET_TOOL" ] && return 1
    $NET_TOOL link show "$1" &>/dev/null
}

# 等待 CAN 接口
log "[0/3] 等待 CAN 接口就绪..."
CAN_READY=0
if [ -n "$NET_TOOL" ]; then
    for i in $(seq 1 30); do
        if check_can_exists can0 && check_can_exists can1; then
            CAN_READY=1
            break
        elif check_can_exists can0; then
            log "  检测到 can0（单 CAN 模式）"
            CAN_READY=1
            break
        fi
        log "  等待 CAN 接口... (${i}/30)"
        sleep 1
    done
else
    log "  [SKIP] 无法检测 CAN（iproute2 未安装）"
fi

if [ "$CAN_READY" -eq 0 ]; then
    log "[WARN] CAN 接口未完全就绪，继续尝试启动..."
fi

# 启动 CAN 接口
log "[1/3] 启动 CAN 接口 (can0, can1 @ 1Mbps)"
for iface in can0 can1; do
    if ! check_can_exists "$iface"; then
        log "  [SKIP] ${iface} 不存在"
        continue
    fi

    state=$($NET_TOOL -br link show "$iface" 2>/dev/null | awk '{print $2}')
    bitrate=$($NET_TOOL -details link show "$iface" 2>/dev/null | grep "bitrate" | awk '{print $2}' | sed 's/000$//')

    if [ "$state" = "UP" ] && [ "$bitrate" = "1000000" ]; then
        log "  ✓ ${iface} 已就绪 (UP, 1Mbps)"
        continue
    fi

    if [ "$(id -u)" -eq 0 ]; then
        $NET_TOOL link set "$iface" down 2>/dev/null
        $NET_TOOL link set "$iface" type can bitrate 1000000 2>/dev/null
        $NET_TOOL link set "$iface" up 2>/dev/null
    else
        sudo $NET_TOOL link set "$iface" down 2>/dev/null
        sudo $NET_TOOL link set "$iface" type can bitrate 1000000 2>/dev/null
        sudo $NET_TOOL link set "$iface" up 2>/dev/null
    fi

    if $NET_TOOL link show "$iface" 2>/dev/null | grep -q "state UP"; then
        log "  ✓ ${iface} 启动成功"
    else
        log "  ✗ ${iface} 启动失败（继续尝试...）"
    fi
done

# 激活 conda 环境
if [ -n "$CONDA_ROOT" ] && [ -f "$CONDA_ROOT/etc/profile.d/conda.sh" ]; then
    . "$CONDA_ROOT/etc/profile.d/conda.sh"
    if conda info --envs 2>/dev/null | grep -q linkerhand; then
        conda activate linkerhand 2>/dev/null
        log "[2/3] conda 环境 linkerhand 已激活"
    else
        log "[WARN] linkerhand 环境不存在，跳过 conda activate"
    fi
else
    log "[WARN] conda 未找到，跳过激活"
fi

# 运行 hand_server.py
log "[3/3] 启动 hand_server.py"
log "  脚本路径: $HAND_SERVER_SCRIPT"

cleanup() {
    log "关闭 CAN 接口..."
    for iface in can0 can1; do
        if ! check_can_exists "$iface"; then
            continue
        fi
        if [ "$(id -u)" -eq 0 ]; then
            $NET_TOOL link set "$iface" down 2>/dev/null || true
        else
            sudo $NET_TOOL link set "$iface" down 2>/dev/null || true
        fi
    done
    log "退出"
    exit 0
}
trap cleanup SIGINT SIGTERM

cd "$PROJECT_ROOT"
python3 "$HAND_SERVER_SCRIPT"
