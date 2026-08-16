#!/bin/bash
# Start Unitree G1 Agent Server

set -e

# Resolve the real path to this script so PROJECT_ROOT is correct
# regardless of how this script is invoked (absolute path, relative path,
# bash -c, or when cwd != script dir).
if [[ -L "${BASH_SOURCE[0]:-$0}" ]]; then
    _SCRIPT_REAL="$(readlink -f "${BASH_SOURCE[0]:-$0}")"
else
    _SCRIPT_REAL="${BASH_SOURCE[0]:-$0}"
fi
SCRIPT_DIR="$(cd "$(dirname "$_SCRIPT_REAL")" && pwd)"

# 加载全局配置（config.env 位于项目根目录）
# agent_server/ -> unitree_G1/ -> robot_client/ -> 项目根目录
PROJECT_ROOT="$(cd "${SCRIPT_DIR}/../../.." && pwd)"
CONFIG_ENV="${PROJECT_ROOT}/config.env"
if [[ -f "$CONFIG_ENV" ]]; then
  source "$CONFIG_ENV"
  echo "Loaded config.env: G1_NETWORK_INTERFACE=$G1_NETWORK_INTERFACE"
else
  echo "WARNING: config.env not found at $CONFIG_ENV"
fi

# Default configuration
HOST="${G1_HOST:-0.0.0.0}"
PORT="${G1_PORT:-8888}"
NETWORK_INTERFACE="${G1_NETWORK_INTERFACE:-eno1}"
# VLAC service
export VLAC_URL="${VLAC_URL:-http://192.168.31.190:8014}"
export VLAC_REQUEST_TIMEOUT="${VLAC_REQUEST_TIMEOUT:-120}"

# Keep all DDS/Unitree SDK call sites on the same robot-facing NIC.
export G1_NETWORK_INTERFACE="$NETWORK_INTERFACE"
export G1_ARM_NETWORK_IFACE="${G1_ARM_NETWORK_IFACE:-$NETWORK_INTERFACE}"
export UNITREE_IFACE="${UNITREE_IFACE:-$NETWORK_INTERFACE}"

# CycloneDDS — required for unitree_sdk2py DDS communication with the robot.
export CYCLONEDDS_HOME="${PROJECT_ROOT}/unitree_sdk2_python/cyclonedds/install"
export LD_LIBRARY_PATH="${CYCLONEDDS_HOME}/lib:${LD_LIBRARY_PATH:-}"
# The install dir ships only versioned .so files (e.g. libddsc.so.0.10.5).
# Many tools look for the unversioned name — create the symlink if it is missing.
if [[ ! -e "${CYCLONEDDS_HOME}/lib/libddsc.so" ]]; then
    ln -sf libddsc.so.0.10.5 "${CYCLONEDDS_HOME}/lib/libddsc.so"
fi

# ROS 1 master — 仅在需要与远程 ROS master 通信时启用。
# agent_server 运行在机器人本机时，若本地有 ros master 则用 localhost，
# 若不需要 ROS1 通信则注释掉这两行。
# export ROS_MASTER_URI=http://localhost:11311
# export ROS_IP=192.168.123.99

# ROS 1 Noetic：TF 查询、move_base action 等 ROS1 依赖。
# 不再 source ROS2 Humble（ROS1/ROS2 混用会导致 tf2 库冲突）。
source /opt/ros/noetic/setup.bash

echo "Starting Unitree G1 Agent Server..."
echo "  Host: $HOST"
echo "  Port: $PORT"
echo "  Network Interface: $NETWORK_INTERFACE"
echo "  G1_ARM_NETWORK_IFACE: $G1_ARM_NETWORK_IFACE"
echo "  UNITREE_IFACE: $UNITREE_IFACE"
echo "  Mode: HARDWARE"

if [[ "${G1_DRY_RUN:-0}" != "0" ]]; then
  echo ""
  echo "Warning: G1_DRY_RUN is set but this server currently runs hardware mode only."
fi

echo ""
echo "Server will be available at: http://$HOST:$PORT"
echo "  - Health check:  http://$HOST:$PORT/health"
echo "  - State:         http://$HOST:$PORT/state"
echo "  - SDK docs:      http://$HOST:$PORT/code/sdk/markdown"
echo "  - Guide:         http://$HOST:$PORT/docs/guide/html"
echo ""

# Run server with g1_agent conda environment
source /opt/miniconda3/etc/profile.d/conda.sh
conda activate g1_agent

# 让 g1_agent 能 import rospy / geometry_msgs 等 ROS1 系统包
ROS1_PYTHONPATH=$(python -c "import roslib; print(roslib.__path__[0])" 2>/dev/null || echo "/opt/ros/noetic/lib/python3/dist-packages")
# Add local robot_sdk packages so unitree_sdk2py and robot_sdk are importable.
export PYTHONPATH="${SCRIPT_DIR}:${PROJECT_ROOT}/unitree_sdk2_python:${ROS1_PYTHONPATH}:${PYTHONPATH:-}"

cd "$SCRIPT_DIR"

exec python3 server.py --host $HOST --port $PORT "$@"
