#!/usr/bin/env python3
"""
G1 速度控制节点
- 订阅 ROS1 /cmd_vel 话题
- 低通滤波平滑速度指令
- 直接通过 loco_client.Move(vx, vy, wz) 下发
"""
import sys
import rospy
from geometry_msgs.msg import Twist

from unitree_sdk2py.core.channel import ChannelFactoryInitialize
from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient


class VelFilter:
    """简单低通滤波器，避免速度突变"""

    def __init__(self, alpha: float = 0.15):
        self.alpha = alpha
        self.vx_filt = 0.0
        self.vy_filt = 0.0
        self.wz_filt = 0.0

    def update(self, vx: float, vy: float, wz: float):
        self.vx_filt = self.alpha * vx + (1 - self.alpha) * self.vx_filt
        self.vy_filt = self.alpha * vy + (1 - self.alpha) * self.vy_filt
        self.wz_filt = self.alpha * wz + (1 - self.alpha) * self.wz_filt
        return self.vx_filt, self.vy_filt, self.wz_filt

    def reset(self):
        self.vx_filt = 0.0
        self.vy_filt = 0.0
        self.wz_filt = 0.0


class G1VelController:
    def __init__(self, iface: str = "eth0", control_freq: float = 50.0,
                 filter_alpha: float = 0.15,
                 deadband_vx: float = 0.05,
                 deadband_vy: float = 0.05,
                 deadband_wz: float = 0.1):

        rospy.loginfo("========================================")
        rospy.loginfo("Initializing G1 Velocity Controller...")
        rospy.loginfo("========================================")

        # 1. 初始化 SDK 网络
        ChannelFactoryInitialize(0, iface)

        # 2. 初始化 LocoClient
        self.client = LocoClient()
        self.client.SetTimeout(10.0)
        try:
            self.client.Init()
        except Exception as e:
            rospy.logerr(f"[ERROR] Robot connection failed: {e}")
            sys.exit(-1)

        self.dt = 1.0 / control_freq
        self.deadband_vx = deadband_vx
        self.deadband_vy = deadband_vy
        self.deadband_wz = deadband_wz

        # 硬编码限幅
        self.max_vx = 0.5       # m/s
        self.max_vy = 0.2       # m/s
        self.max_wz = 0.7    # rad/s (~45 deg/s)

        # 原始指令
        self.cmd_vx = 0.0
        self.cmd_vy = 0.0
        self.cmd_wz = 0.0

        # 滤波后指令
        self.filt = VelFilter(alpha=filter_alpha)

        # 停止判定
        self.is_stopped = False
        self.stop_thresh = 0.02
        self.stop_counter = 0
        self.stop_counter_max = 5  # 连续 N 帧判定停止

        # 3. 订阅 /cmd_vel
        self.vel_sub = rospy.Subscriber("/cmd_vel", Twist, self._vel_cb)

        # 4. 启动控制循环
        self.control_timer = rospy.Timer(rospy.Duration(self.dt), self._control_loop)

        rospy.loginfo("G1 Velocity Controller started.")
        rospy.loginfo("  Filter alpha  : %.2f", filter_alpha)
        rospy.loginfo("  Control freq  : %.1f Hz", control_freq)
        rospy.loginfo("  Deadband vx/vy/wz: %.2f / %.2f / %.2f",
                      deadband_vx, deadband_vy, deadband_wz)

    # ------------------------------------------------------------------ callbacks --

    def _vel_cb(self, msg: Twist):
        self.cmd_vx = msg.linear.x
        self.cmd_vy = msg.linear.y
        self.cmd_wz = msg.angular.z

        if self.is_stopped:
            # 收到明显指令 -> 解锁
            if (abs(self.cmd_vx) >= self.deadband_vx or
                    abs(self.cmd_vy) >= self.deadband_vy or
                    abs(self.cmd_wz) >= self.deadband_wz):
                self.is_stopped = False
                self.filt.reset()
                rospy.loginfo("[G1] Unlocked: new motion command received")

    # ------------------------------------------------------------------ control --

    def _control_loop(self, event):
        # 死区裁剪
        raw_vx = self.cmd_vx if abs(self.cmd_vx) >= self.deadband_vx else 0.0
        raw_vy = self.cmd_vy if abs(self.cmd_vy) >= self.deadband_vy else 0.0
        raw_wz = self.cmd_wz if abs(self.cmd_wz) >= self.deadband_wz else 0.0

        # 全部静止 -> 进入锁定
        if abs(raw_vx) < 0.001 and abs(raw_vy) < 0.001 and abs(raw_wz) < 0.001:
            self.stop_counter += 1
        else:
            self.stop_counter = 0

        if self.stop_counter >= self.stop_counter_max:
            if not self.is_stopped:
                rospy.loginfo("[G1] Locked: stopping")
            self.is_stopped = True
            self.client.Move(0.0, 0.0, 0.0)
            return

        if self.is_stopped:
            # 锁定中等待解锁
            return

        # 低通滤波
        fvx, fvy, fwz = self.filt.update(raw_vx, raw_vy, raw_wz)

        # 限幅
        fvx = max(-self.max_vx, min(self.max_vx, fvx))
        fvy = max(-self.max_vy, min(self.max_vy, fvy))
        fwz = max(-self.max_wz, min(self.max_wz, fwz))

        # 发送指令
        self.client.Move(fvx, fvy, fwz)


# ------------------------------------------------------------------------ main --

def main():
    rospy.init_node("g1_velocity_controller", anonymous=False)

    if len(sys.argv) > 1:
        iface = sys.argv[1]
    else:
        iface = rospy.get_param("~interface", "eth0")
    freq = rospy.get_param("~control_freq", 50.0)
    alpha = rospy.get_param("~filter_alpha", 0.15)

    controller = G1VelController(
        iface=iface,
        control_freq=freq,
        filter_alpha=alpha,
    )

    try:
        rospy.spin()
    except rospy.ROSInterruptException:
        pass


if __name__ == "__main__":
    main()
