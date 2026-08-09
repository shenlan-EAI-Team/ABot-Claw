#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_DIR="${ROOT_DIR}/.pids"
LOG_DIR="${ROOT_DIR}/logs"

SERVICES=("yolo" "face_recognition" "spatial_memory" "graspanything")

echo "正在停止服务..."
echo

for name in "${SERVICES[@]}"; do
  pidfile="${PID_DIR}/${name}.pid"
  if [[ -f "${pidfile}" ]]; then
    pid="$(<"${pidfile}")"
    if [[ -n "${pid}" ]] && kill -0 "${pid}" 2>/dev/null; then
      echo "[STOP] ${name} (PID: ${pid})"
      kill "${pid}" 2>/dev/null || true
      sleep 1
      if kill -0 "${pid}" 2>/dev/null; then
        echo "      进程仍在运行，发送 SIGKILL..."
        kill -9 "${pid}" 2>/dev/null || true
      fi
    else
      echo "[SKIP] ${name} 进程不存在或已停止"
    fi
    rm -f "${pidfile}"
  else
    echo "[SKIP] ${name} PID 文件不存在: ${pidfile}"
  fi
done

echo
echo "所有服务已停止。"
