#!/bin/bash
# 获取 AnyGrasp 机器码
# 注：本脚本使用包装器排除 docker0 接口，机器码不受 Docker 重启影响

# 获取脚本所在目录（相对路径）
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# 激活 conda 环境
source "/opt/conda/etc/profile.d/conda.sh"
conda activate anygrasp

# 切换到 license_registration 目录
cd "$SCRIPT_DIR"

# 获取机器码
echo "=========================================="
echo "你的机器码是："
cd "$SCRIPT_DIR"
python -c "from gsnet import get_feature_id; print(get_feature_id())"
echo "=========================================="
echo ""

# 打印网卡 MAC 地址（标注是否参与机器码计算）
EXCLUDED_INTERFACES="lo docker0 virbr0 veth"
echo "网卡 MAC 地址："
ip link show | grep -E '^[0-9]+:' | while read -r line; do
    iface=$(echo "$line" | cut -d':' -f2 | tr -d ' ')
    mac=$(cat /sys/class/net/$iface/address 2>/dev/null)
    if [ -n "$mac" ]; then
        if echo "$EXCLUDED_INTERFACES" | grep -qw "$iface"; then
            echo "  $iface: $mac  "
        else
            echo "  $iface: $mac  "
        fi
    fi
done
echo ""

echo "下一步："
echo "1. 访问 https://forms.gle/XVV3Eip8njTYJEBo6"
echo "2. 填入上面的机器码（结尾如果有 % 请删除）"
echo "3. 把收到的 license.zip 解压到当前目录"
echo "4. 运行验证：bash check_license.sh"