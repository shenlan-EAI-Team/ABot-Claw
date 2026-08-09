#!/usr/bin/env bash

set -euo pipefail

SERVICE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV_DIR="${SERVICE_DIR}/.venv"

# 支持 .venv 为 conda 环境的符号链接或标准 venv
if [[ -L "${VENV_DIR}" ]]; then
  VENV_TARGET="$(readlink -f "${VENV_DIR}")"
  if [[ -d "${VENV_TARGET}/bin" ]]; then
    VENV_PYTHON="${VENV_DIR}/bin/python"
  elif [[ -d "${VENV_TARGET}" ]]; then
    # conda 环境结构
    VENV_PYTHON="${VENV_DIR}/bin/python"
  fi
else
  VENV_PYTHON="${VENV_DIR}/bin/python"
fi

if [[ ! -x "${VENV_PYTHON}" ]]; then
  echo "未找到人脸识别虚拟环境: ${VENV_PYTHON}"
  exit 1
fi

SITE_PACKAGES_DIR="$(find "${VENV_DIR}/lib" -maxdepth 2 -type d -path '*/site-packages' 2>/dev/null | head -n 1)"
SYSTEM_PYTHON_SITE="$(python3 -c 'import site; print(site.getusersitepackages())' 2>/dev/null)"

CUDA_LIB_PATHS=(
  "${SITE_PACKAGES_DIR}/nvidia/cuda_runtime/lib"
  "${SITE_PACKAGES_DIR}/nvidia/cublas/lib"
  "${SITE_PACKAGES_DIR}/nvidia/cufft/lib"
  "${SITE_PACKAGES_DIR}/nvidia/curand/lib"
  "${SITE_PACKAGES_DIR}/nvidia/cudnn/lib"
  "${SYSTEM_PYTHON_SITE}/nvidia/cuda_runtime/lib"
  "${SYSTEM_PYTHON_SITE}/nvidia/cublas/lib"
  "${SYSTEM_PYTHON_SITE}/nvidia/cufft/lib"
  "${SYSTEM_PYTHON_SITE}/nvidia/curand/lib"
  "${SYSTEM_PYTHON_SITE}/nvidia/cudnn/lib"
)

CUDA_LIBS_EXISTING=()
for candidate in "${CUDA_LIB_PATHS[@]}"; do
  if [[ -d "${candidate}" ]]; then
    CUDA_LIBS_EXISTING+=("${candidate}")
  fi
done

CUDA_LD_LIBRARY_PATH="$(IFS=:; echo "${CUDA_LIBS_EXISTING[*]}")"
export LD_LIBRARY_PATH="${CUDA_LD_LIBRARY_PATH}${LD_LIBRARY_PATH:+:${LD_LIBRARY_PATH}}"

export PORT="${PORT:-8016}"
export DEVICE="${DEVICE:-auto}"
export FACE_DB_PATH="${FACE_DB_PATH:-${SERVICE_DIR}/data/face_db.json}"

cd "${SERVICE_DIR}"
exec "${VENV_PYTHON}" "${SERVICE_DIR}/main.py" "$@"
