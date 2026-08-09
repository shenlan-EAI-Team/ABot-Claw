#!/usr/bin/env bash

set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"

# 加载全局配置
if [[ -f "${ROOT_DIR}/../config.env" ]]; then
  source "${ROOT_DIR}/../config.env"
fi

LOG_DIR="${ROOT_DIR}/logs"
PID_DIR="${ROOT_DIR}/.pids"
FACE_VENV_SITE_PACKAGES_DIR="$(find "${ROOT_DIR}/face_recognition_insightface/.venv/lib" -maxdepth 2 -type d -path '*/site-packages' | head -n 1)"
YOLO_VENV_SITE_PACKAGES_DIR="$(find "${ROOT_DIR}/YOLO/.venv/lib" -maxdepth 2 -type d -path '*/site-packages' | head -n 1)"
FACE_VENV_PYTHON="${ROOT_DIR}/face_recognition_insightface/.venv/bin/python"
YOLO_VENV_PYTHON="${ROOT_DIR}/YOLO/.venv/bin/python"
SPATIALMEMORY_VENV_PYTHON="${ROOT_DIR}/SpatialMemory/.venv/bin/python"
SYSTEM_USER_SITE="$("${FACE_VENV_PYTHON}" -c 'import site; print(site.getusersitepackages())' 2>/dev/null)"

mkdir -p "${LOG_DIR}" "${PID_DIR}"

# ---------------------------------------------------------------------------
# 网络代理（模型首次下载需要）
# ---------------------------------------------------------------------------
: "${https_proxy:=http://127.0.0.1:7897}"
: "${http_proxy:=http://127.0.0.1:7897}"
: "${all_proxy:=socks5://127.0.0.1:7897}"
export https_proxy http_proxy all_proxy

# ---------------------------------------------------------------------------
# 人脸识别 venv & CUDA 库路径（支持 venv 内部 + 系统用户 site-packages）
# ---------------------------------------------------------------------------
FACE_CUDA_LIB_PATHS=(
  "${FACE_VENV_SITE_PACKAGES_DIR}/nvidia/cuda_runtime/lib"
  "${FACE_VENV_SITE_PACKAGES_DIR}/nvidia/cublas/lib"
  "${FACE_VENV_SITE_PACKAGES_DIR}/nvidia/cufft/lib"
  "${FACE_VENV_SITE_PACKAGES_DIR}/nvidia/curand/lib"
  "${FACE_VENV_SITE_PACKAGES_DIR}/nvidia/cudnn/lib"
  "${YOLO_VENV_SITE_PACKAGES_DIR}/nvidia/cuda_runtime/lib"
  "${YOLO_VENV_SITE_PACKAGES_DIR}/nvidia/cublas/lib"
  "${YOLO_VENV_SITE_PACKAGES_DIR}/nvidia/cufft/lib"
  "${YOLO_VENV_SITE_PACKAGES_DIR}/nvidia/curand/lib"
  "${YOLO_VENV_SITE_PACKAGES_DIR}/nvidia/cudnn/lib"
  "${SYSTEM_USER_SITE}/nvidia/cuda_runtime/lib"
  "${SYSTEM_USER_SITE}/nvidia/cublas/lib"
  "${SYSTEM_USER_SITE}/nvidia/cufft/lib"
  "${SYSTEM_USER_SITE}/nvidia/curand/lib"
  "${SYSTEM_USER_SITE}/nvidia/cudnn/lib"
)
FACE_CUDA_LIBS_EXISTING=()
for candidate in "${FACE_CUDA_LIB_PATHS[@]}"; do
  if [[ -d "${candidate}" ]]; then
    FACE_CUDA_LIBS_EXISTING+=("${candidate}")
  fi
done
FACE_CUDA_LD="$(IFS=:; echo "${FACE_CUDA_LIBS_EXISTING[*]}")"

# ---------------------------------------------------------------------------
# GraspAnything 环境
# ---------------------------------------------------------------------------
: "${ANYGRASP_SDK_PATH:=${ROOT_DIR}/GraspAnything/anygrasp_sdk}"
: "${GRASP_CHECKPOINT_PATH:=${ANYGRASP_SDK_PATH}/grasp_detection/log/checkpoint_detection.tar}"
: "${ANYGRASP_PYTHON:=/home/xxuz/miniconda3/envs/anygrasp/bin/python}"
: "${ANYGRASP_SSL11_LIB_DIR:=${HOME}/.local/lib/usr/lib/x86_64-linux-gnu}"
ANYGRASP_ENV_DIR="$(cd "$(dirname "${ANYGRASP_PYTHON}")/.." 2>/dev/null && pwd || true)"
ANYGRASP_LD_PATHS=()
if [[ -n "${ANYGRASP_ENV_DIR}" && -d "${ANYGRASP_ENV_DIR}/lib" ]]; then
  ANYGRASP_LD_PATHS+=("${ANYGRASP_ENV_DIR}/lib")
fi
if [[ -d "${ANYGRASP_SSL11_LIB_DIR}" ]]; then
  ANYGRASP_LD_PATHS+=("${ANYGRASP_SSL11_LIB_DIR}")
fi
ANYGRASP_LD="$(IFS=:; echo "${ANYGRASP_LD_PATHS[*]}")"

# ---------------------------------------------------------------------------
# 工具函数
# ---------------------------------------------------------------------------
is_port_listening() {
  local port="$1"
  if ss -ltn "( sport = :${port} )" 2>/dev/null | tail -n +2 | grep -q .; then
    echo "1"
  else
    echo "0"
  fi
}

wait_for_health() {
  local name="$1"
  local url="$2"
  local logfile="$3"
  local pidfile="$4"
  local retries="${5:-30}"

  for ((i=1; i<=retries; i++)); do
    if curl -fsS "${url}" >/dev/null 2>&1; then
      echo "[OK] ${name} 已就绪: ${url}"
      return 0
    fi

    if [[ -f "${pidfile}" ]]; then
      local pid
      pid="$(<"${pidfile}")"
      if [[ -n "${pid}" ]] && ! kill -0 "${pid}" 2>/dev/null; then
        echo "[ERROR] ${name} 启动失败，进程已退出"
        if [[ -f "${logfile}" ]]; then
          echo "[ERROR] ${name} 日志尾部:"
          tail -n 20 "${logfile}"
        fi
        return 1
      fi
    fi

    sleep 1
  done

  echo "[WARN] ${name} 未在预期时间内通过健康检查: ${url}"
  if [[ -f "${logfile}" ]]; then
    echo "[WARN] ${name} 最近日志:"
    tail -n 20 "${logfile}"
  fi
  return 1
}

start_service() {
  local name="$1"
  local workdir="$2"
  local port="$3"
  local health_url="$4"
  local logfile="$5"
  local pidfile="${PID_DIR}/${name}.pid"
  local retries="${6:-60}"
  shift 6

  if [[ "$(is_port_listening "${port}")" == "1" ]]; then
    echo "[SKIP] ${name} 已在端口 ${port} 上运行"
    return 0
  fi

  echo "[START] ${name}"
  (
    cd "${workdir}"
    setsid nohup env "$@" > "${logfile}" 2>&1 &
    echo $! > "${pidfile}"
  )

  wait_for_health "${name}" "${health_url}" "${logfile}" "${pidfile}" "${retries}" || true
}

# ===================================================================
echo "服务根目录: ${ROOT_DIR}"
echo "日志目录:   ${LOG_DIR}"
echo "代理:       ${https_proxy}"
echo

# ---------------------------------------------------------------------------
# 1. YOLO  (venv python, GPU, 端口 8013)
# ---------------------------------------------------------------------------
YOLO_MODEL="${ROOT_DIR}/YOLO/yolov5l6.pt"
if [[ ! -f "${YOLO_MODEL}" ]] || [[ $(stat -c%s "${YOLO_MODEL}") -lt 50000000 ]]; then
  echo "[WARN] YOLO 模型缺失或不完整 (${YOLO_MODEL})，启动时会自动下载"
fi

if [[ ! -x "${YOLO_VENV_PYTHON}" ]]; then
  echo "[ERROR] YOLO venv 不存在: ${YOLO_VENV_PYTHON}"
else
  start_service \
    "yolo" \
    "${ROOT_DIR}/YOLO" \
    "8013" \
    "http://127.0.0.1:8013/health" \
    "${LOG_DIR}/yolo.log" \
    PORT=8013 \
    DEVICE=auto \
    YOLO_MODEL_PATH="${ROOT_DIR}/YOLO/yolov5l6.pt" \
    "${YOLO_VENV_PYTHON}" main.py
fi

# ---------------------------------------------------------------------------
# 2. 人脸识别  (venv python + CUDA, 端口 8016)
# ---------------------------------------------------------------------------
if [[ ! -x "${FACE_VENV_PYTHON}" ]]; then
  echo "[ERROR] 人脸识别 venv 不存在: ${FACE_VENV_PYTHON}"
else
  start_service \
    "face_recognition" \
    "${ROOT_DIR}/face_recognition_insightface" \
    "8016" \
    "http://127.0.0.1:8016/health" \
    "${LOG_DIR}/face_recognition.log" \
    "90" \
    PORT=8016 \
    DEVICE=auto \
    FACE_DB_PATH="${ROOT_DIR}/face_recognition_insightface/data/face_db.json" \
    LD_LIBRARY_PATH="${FACE_CUDA_LD}${LD_LIBRARY_PATH:+:$LD_LIBRARY_PATH}" \
    "${FACE_VENV_PYTHON}" main.py
fi

# ---------------------------------------------------------------------------
# 3. SpatialMemory  (venv python, 端口 8022)
# ---------------------------------------------------------------------------
if [[ ! -x "${SPATIALMEMORY_VENV_PYTHON}" ]]; then
  echo "[ERROR] SpatialMemory venv 不存在: ${SPATIALMEMORY_VENV_PYTHON}"
else
  start_service \
    "spatial_memory" \
    "${ROOT_DIR}/SpatialMemory" \
    "8022" \
    "http://127.0.0.1:8022/health" \
    "${LOG_DIR}/spatial_memory.log" \
    "60" \
    PORT=8022 \
    MEMORY_HUB_DATA_DIR="${ROOT_DIR}/SpatialMemory/data" \
    "${SPATIALMEMORY_VENV_PYTHON}" main.py
fi

# ---------------------------------------------------------------------------
# 4. GraspAnything  (anygrasp python, GPU, 端口 8015)
# ---------------------------------------------------------------------------
GRASPANYTHING_BIN="${ROOT_DIR}/GraspAnything/bin"
if [[ ! -x "${ANYGRASP_PYTHON}" ]]; then
  echo "[ERROR] GraspAnything Python 不存在: ${ANYGRASP_PYTHON}"
elif [[ ! -d "${ANYGRASP_SDK_PATH}" ]]; then
  echo "[ERROR] ANYGRASP_SDK_PATH 不存在: ${ANYGRASP_SDK_PATH}"
elif [[ ! -f "${GRASP_CHECKPOINT_PATH}" ]]; then
  echo "[ERROR] GRASP_CHECKPOINT_PATH 不存在: ${GRASP_CHECKPOINT_PATH}"
else
  start_service \
    "graspanything" \
    "${ROOT_DIR}/GraspAnything" \
    "8015" \
    "http://127.0.0.1:8015/health" \
    "${LOG_DIR}/graspanything.log" \
    PORT=8015 \
    DEVICE=auto \
    ANYGRASP_SDK_PATH="${ANYGRASP_SDK_PATH}" \
    GRASP_CHECKPOINT_PATH="${GRASP_CHECKPOINT_PATH}" \
    GRASP_YOLO_MODEL_PATH="${ROOT_DIR}/YOLO/yolov5l6.pt" \
    LD_LIBRARY_PATH="${ANYGRASP_LD}" \
    "${ANYGRASP_PYTHON}" main.py
fi

# ---------------------------------------------------------------------------
# 未默认启动的服务
# ---------------------------------------------------------------------------
echo
echo "[SKIP] VLAC 未默认启动，如需启用请手动运行 Services/VLAC/main.py"
echo
echo "启动流程结束。日志可查看:"
echo "  ${LOG_DIR}/yolo.log"
echo "  ${LOG_DIR}/face_recognition.log"
echo "  ${LOG_DIR}/spatial_memory.log"
echo "  ${LOG_DIR}/graspanything.log"
