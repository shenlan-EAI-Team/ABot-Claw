# 完整使用示例（G1 机器人）
import os
import sys
from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient  # ← 改成 G1 的
import time


def main(iface: str = "eno1") -> None:
    """G1 机器人运动演示。

    Args:
        iface: 机器人网络接口名，默认为 "enp4s0"，
               可通过环境变量 ROBOT_NETWORK_IFACE 覆盖。
    """
    iface = os.environ.get("ROBOT_NETWORK_IFACE", iface)

    ChannelFactoryInitialize(0, iface)

    loco_client = LocoClient()           # ← 改成 LocoClient
    loco_client.SetTimeout(10.0)
    loco_client.Init()

    # G1 需要先用 Damp 再 Squat2StandUp，再 Start 进入运动模式
    # loco_client.Damp()
    # time.sleep(0.5)
    # 前后、左右移动的死区为0.2，旋转死区为0.3    # 前进 / 后退 / 左移 / 右移
    loco_client.Move(0.0, 0.0, 0.25)    # 前进 0.3 m/s
    time.sleep(1)

    loco_client.StopMove()              # 停止


if __name__ == "__main__":
    main()