#!/usr/bin/env bash
#============================================================
# 路径适配脚本
# 作用：自动将 /home/xxuz/Services 替换为目标机器的用户路径
# 使用：bash adapt_path.sh
#============================================================

set -euo pipefail

CURRENT_USER="xxuz"
TARGET_USER=$(whoami)
SERVICES_DIR="/home/${TARGET_USER}/Services"

echo "========================================"
echo "  路径适配工具"
echo "========================================"
echo "  当前用户:   ${CURRENT_USER}"
echo "  目标用户:   ${TARGET_USER}"
echo "  服务目录:   ${SERVICES_DIR}"
echo ""

if [[ "${CURRENT_USER}" == "${TARGET_USER}" ]]; then
  echo "[INFO] 用户名相同，无需适配。"
  exit 0
fi

# 检查目标目录是否存在
if [[ ! -d "${SERVICES_DIR}" ]]; then
  echo "[ERROR] 目标目录不存在: ${SERVICES_DIR}"
  echo "        请先解压 code.tar.gz："
  echo "        tar -xzf code.tar.gz"
  exit 1
fi

SED_CMD="sed -i 's|/home/${CURRENT_USER}|/home/${TARGET_USER}|g'"

echo "[STEP 1] 适配 docker-compose.yml ..."
${SED_CMD} "${SERVICES_DIR}/docker-compose.yml"
echo "        -> docker-compose.yml 已更新"

echo "[STEP 2] 适配 start_services.sh 中的 miniconda 路径 ..."
${SED_CMD} "${SERVICES_DIR}/start_services.sh"
echo "        -> start_services.sh 已更新"

echo ""
echo "[DONE] 路径适配完成！"
echo ""
echo "接下来启动服务："
echo ""
echo "  # 方式 A：直接启动（需要本机已配置环境）"
echo "  cd ${SERVICES_DIR}"
echo "  ./start_services.sh"
echo ""
echo "  # 方式 B：Docker 方式"
echo "  cd ${SERVICES_DIR}"
echo "  sudo docker-compose up -d"
echo ""
