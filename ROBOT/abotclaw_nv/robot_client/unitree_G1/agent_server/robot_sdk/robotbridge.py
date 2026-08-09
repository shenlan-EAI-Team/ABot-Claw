"""G1 双臂真机控制 **唯一** 下发通道：`RobotBridge` → DDS ``rt/arm_sdk`` / ``rt/lowstate``。

本仓库 **不** 使用 HTTP「G1-adapter」或其它并行臂控服务；抓取与笛卡尔轨迹均经
``g1_grasp_sdk.grasp_target`` → ``G1IKController`` → 本模块 ``RobotBridge``。
"""

from unitree_sdk2py.comm.motion_switcher.motion_switcher_client import MotionSwitcherClient
import logging
import os
import time, math
from typing import Dict, List, Tuple, Optional
import threading
from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowState_ as LowStateMsg

logger = logging.getLogger(__name__)


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in ("1", "true", "yes", "on")


def _arm_debug() -> bool:
    return _env_truthy("G1_ARM_DEBUG")


# ----------------------------------------------------------------------
# 1) RobotBridge
# ----------------------------------------------------------------------

class RobotBridge:
    """G1 双臂：经 DDS 发布 ``LowCmd`` 至 ``rt/arm_sdk``（**本仓库臂控仅此路径**）。

    约定（与 Unitree ``g1_arm7_sdk`` 一致）：
    - 仅对手臂等 ``send_*`` 里写入的关节设置非零 ``kp/kd``；腿、腰保持 ``kp=kd=0``。
    - **不要**把 ``LowState.mode_pr`` / ``mode_machine`` 写进 ``LowCmd``（官方 arm_sdk 示例从不写，
      误同步会导致整帧指令异常、手臂不响应）。
    - 若有 ``LowState``，每帧将索引 0–14（腿+腰）写成当前反馈 ``q``（零刚度），避免长期带 ``q=0``。
    - ``motor_cmd[29].q = 1`` 为 arm_sdk 权重；``close()`` 时渐降到 0。
    """

    _LEG_WAIST_MAX = 14  # 与官方 arm7 中手臂索引 15 之前一致

    # 默认 kp/kd 与官方 g1_arm7_sdk_dds_example 一致（便于真机跟踪）
    def __init__(self, iface: str, domain: int, default_mode: int = 0, kp: float = 60.0, kd: float = 1.5):
        self._iface = iface
        self._latest_state: Optional[LowStateMsg] = None
        self._pub = None
        self._sub = None
        self._motion_switcher = None
        self._crc = None
        self._initialized = False
        
        try:
            from unitree_sdk2py.core.channel import (
                ChannelFactoryInitialize,
                ChannelPublisher,
                ChannelSubscriber
            )
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowCmd_
            from unitree_sdk2py.idl.unitree_hg.msg.dds_ import LowState_
            from unitree_sdk2py.idl.default import unitree_hg_msg_dds__LowCmd_
        except Exception:
            print("[robot] SDK-2 not present – robot output disabled")
            self.ok = False
            return

        self._default_mode = default_mode
        self._kp = float(kp)
        self._kd = float(kd)

        try:
            ChannelFactoryInitialize(domain, iface)
            self._state_lock = threading.Lock()
            self._latest_state = None
            self._lowstate_ready = threading.Event()

            self._pub = ChannelPublisher("rt/arm_sdk", LowCmd_)
            self._pub.Init()

            self._cmd = unitree_hg_msg_dds__LowCmd_()

            # 与官方 g1_arm7_sdk 一致：未在本包内主动控制的关节保持 kp=kd=0，
            # 避免对腿/腰施加指向 q=0 的刚度（仅手臂在 send_* 里写 kp/kd）。
            for mc in self._cmd.motor_cmd:
                mc.mode = self._default_mode
                mc.q = 0.0
                mc.dq = 0.0
                mc.tau = 0.0
                mc.kp = 0.0
                mc.kd = 0.0

            # Enable/weight slot
            if 29 < len(self._cmd.motor_cmd):
                self._cmd.motor_cmd[29].q = 1.0

            # --- Subscriber（队列深度与官方 g1_arm7_sdk_dds_example 一致，避免丢包）
            self._sub = ChannelSubscriber("rt/lowstate", LowState_)
            self._sub.Init(self._on_state, 10)

            try:
                from unitree_sdk2py.utils.crc import CRC
                self._crc = CRC()
            except Exception:
                self._crc = None

            # MotionSwitcherClient: 用于释放 arm_sdk 模式，使遥控器可接管
            try:
                self._motion_switcher = MotionSwitcherClient()
                self._motion_switcher.Init()
                if _env_truthy("G1_ARM_SKIP_CHECK_MODE"):
                    logger.warning(
                        "G1_ARM_SKIP_CHECK_MODE=1：已跳过 MotionSwitcher.CheckMode()，"
                        "仅用于与 main.py 行为对比排查。"
                    )
                else:
                    # Call CheckMode to wake up the control interface
                    self._motion_switcher.CheckMode()
            except Exception as e:
                print(f"[robot] MotionSwitcherClient init failed ({e})")
                self._motion_switcher = None

            # 与 G1IKController 中 G1_ARM_ALLOW_NO_LOWSTATE 一致：允许无首帧 LowState 时仍写 arm_sdk（危险，仅排查）
            self._allow_send_without_lowstate = _env_truthy("G1_ARM_ALLOW_NO_LOWSTATE")
            self._logged_skip_no_lowstate = False
            self._logged_force_send_no_lowstate = False
            self._logged_first_debug_send = False
            self._initialized = True
            self.ok = True
        except Exception as e:
            print(f"[robot] DDS init failed – robot disabled ({e})")
            self._cleanup_dds()
            self.ok = False

    def _cleanup_dds(self) -> None:
        """清理所有 DDS 资源（publisher、subscriber、motion_switcher）。"""
        # 关闭 MotionSwitcherClient
        if self._motion_switcher is not None:
            try:
                # MotionSwitcherClient 没有显式的 close 方法，置 None 即可
                self._motion_switcher = None
            except Exception:
                pass
        
        # 关闭 Publisher
        if self._pub is not None:
            try:
                self._pub.Close()
            except Exception:
                pass
            self._pub = None
        
        
        # 关闭 Subscriber
        if self._sub is not None:
            try:
                self._sub.Close()
            except Exception:
                pass
            self._sub = None
        
        
        self._initialized = False

    def _can_send_arm_sdk(self) -> bool:
        """无首帧 LowState 时不写 rt/arm_sdk，避免腿腰仍为初始化 q=0 的异常帧（与官方时序一致）。"""
        if self._lowstate_ready.is_set():
            return True
        if getattr(self, "_allow_send_without_lowstate", False):
            if not self._logged_force_send_no_lowstate:
                logger.warning(
                    "G1_ARM_ALLOW_NO_LOWSTATE=1：尚未收到 rt/lowstate 仍将写入 rt/arm_sdk（iface=%s）；"
                    "腿腰未镜像反馈，真机有风险。",
                    self._iface,
                )
                self._logged_force_send_no_lowstate = True
            return True
        if not self._logged_skip_no_lowstate:
            logger.warning(
                "跳过 arm_sdk 写入：尚未收到 rt/lowstate（iface=%s）。",
                self._iface,
            )
            self._logged_skip_no_lowstate = True
        return False

    def _mirror_leg_waist_from_state(self) -> None:
        """将下肢+腰（0–14）设为当前 LowState 关节角，kp/kd/tau=0，与官方示例中不拉腿一致且不无脑 q=0。"""
        js = self.get_joint_states()
        if js is None:
            return
        for idx in range(self._LEG_WAIST_MAX + 1):
            if idx not in js or idx >= len(self._cmd.motor_cmd):
                continue
            mc = self._cmd.motor_cmd[idx]
            mc.mode = self._default_mode
            mc.q = float(js[idx]["q"])
            mc.dq = 0.0
            mc.tau = 0.0
            mc.kp = 0.0
            mc.kd = 0.0

    # ------------------------------------------------------------------
    # Subscriber callback
    # ------------------------------------------------------------------
    def _on_state(self, msg: LowStateMsg):
        with self._state_lock:
            self._latest_state = msg
        if _arm_debug() and not self._lowstate_ready.is_set():
            logger.info("G1_ARM_DEBUG: 收到首帧 rt/lowstate（iface=%s）", self._iface)
        self._lowstate_ready.set()

    def wait_for_lowstate(self, timeout: float = 10.0) -> bool:
        """阻塞直到收到首帧 `rt/lowstate`（与官方示例在首帧后再发 `rt/arm_sdk` 一致）。"""
        if not self.ok:
            return False
        ok = self._lowstate_ready.wait(timeout=timeout)
        if _arm_debug():
            logger.info(
                "G1_ARM_DEBUG: wait_for_lowstate(timeout=%.1fs, iface=%s) -> %s",
                timeout,
                self._iface,
                ok,
            )
        if not ok:
            logger.warning(
                "wait_for_lowstate: %.1fs 内未收到 rt/lowstate（iface=%s）；"
                "arm_sdk 可能无反馈或指令被忽略。",
                timeout,
                self._iface,
            )
        else:
            logger.info("wait_for_lowstate: 已收到首帧 rt/lowstate，可开始下发 arm_sdk。")
        return ok

    def shutdown_publishers(self) -> None:
        """仅关闭 DDS Publisher/Subscriber，不发送 LowCmd。

        用于「已建连但未收到 rt/lowstate」等初始化失败场景；勿调用 ``close()``，
        后者会带刚度发一整段过渡，在无 LowState 时可能使用 q=0 等不安全占位。
        """
        self._cleanup_dds()
        self.ok = False

    def get_joint_states(self) -> Optional[Dict[int, Dict[str, float]]]:
        """
        Returns latest joint states as:
        { idx : { 'q': rad, 'dq': rad/s, 'tau': Nm } }
        """
        with self._state_lock:
            if self._latest_state is None:
                return None
            states = {}
            for i, m in enumerate(self._latest_state.motor_state):
                tau = getattr(m, "tau_est",
                              getattr(m, "tauEst",
                                      getattr(m, "tau", 0.0)))
                states[i] = {
                    "q":   float(m.q),
                    "dq":  float(m.dq),
                    "tau": float(tau),
                }
            return states

    def send_qpos(self, q: Dict[int, float]) -> None:
        """Send positions for a subset of joints (29-DoF indices → radians)."""
        if not self.ok:
            return
        if not self._can_send_arm_sdk():
            return

        self._mirror_leg_waist_from_state()

        for idx, val in q.items():
            if idx >= 29:
                continue
            if idx < len(self._cmd.motor_cmd):
                mc = self._cmd.motor_cmd[idx]
                mc.mode = self._default_mode
                mc.q = float(val)
                mc.dq = 0.0
                mc.kp = self._kp
                mc.kd = self._kd

        if 29 < len(self._cmd.motor_cmd):
            self._cmd.motor_cmd[29].q = 1.0

        if self._crc is not None:
            if hasattr(self._crc, "Crc"):
                self._cmd.crc = self._crc.Crc(self._cmd)
            elif hasattr(self._crc, "calculate_crc"):
                self._cmd.crc = self._crc.calculate_crc(self._cmd)

        self._pub.Write(self._cmd)

    def send_qpos_tau(self, q: Dict[int, float], tau: Optional[Dict[int, float]] = None) -> None:
        """
        Send desired positions and optional feedforward torques.
        q   : Dict[index, position] (radians)
        tau : Dict[index, torque]   (Nm), optional
        """
        if not self.ok:
            return
        if not self._can_send_arm_sdk():
            return

        self._mirror_leg_waist_from_state()

        for idx, val in q.items():
            if idx >= 29:
                continue
            if idx < len(self._cmd.motor_cmd):
                mc = self._cmd.motor_cmd[idx]
                mc.mode = self._default_mode
                mc.q = float(val)
                mc.dq = 0.0
                mc.kp = self._kp
                mc.kd = self._kd
                # 如果有提供 tau，就更新
                if tau and idx in tau:
                    mc.tau = float(tau[idx])

        if 29 < len(self._cmd.motor_cmd):
            self._cmd.motor_cmd[29].q = 1.0

        if self._crc is not None:
            if hasattr(self._crc, "Crc"):
                self._cmd.crc = self._crc.Crc(self._cmd)
            elif hasattr(self._crc, "calculate_crc"):
                self._cmd.crc = self._crc.calculate_crc(self._cmd)

        if _arm_debug() and not self._logged_first_debug_send:
            self._logged_first_debug_send = True
            logger.info(
                "G1_ARM_DEBUG: 首次 send_qpos_tau -> rt/arm_sdk（lowstate=%s, iface=%s）",
                self._lowstate_ready.is_set(),
                self._iface,
            )

        self._pub.Write(self._cmd)


    def send_tau(self, tau: Dict[int, float]) -> None:
        """对给定索引写入前馈力矩；其余字段不改动。写入前同步头与 CRC。"""
        if not self.ok:
            return
        if not self._can_send_arm_sdk():
            return
        self._mirror_leg_waist_from_state()
        for idx, val in tau.items():
            if idx >= 29 or idx < 0:
                continue
            if idx < len(self._cmd.motor_cmd):
                mc = self._cmd.motor_cmd[idx]
                mc.tau = float(val)
        if 29 < len(self._cmd.motor_cmd):
            self._cmd.motor_cmd[29].q = 1.0
        if self._crc is not None:
            if hasattr(self._crc, "Crc"):
                self._cmd.crc = self._crc.Crc(self._cmd)
            elif hasattr(self._crc, "calculate_crc"):
                self._cmd.crc = self._crc.calculate_crc(self._cmd)
        self._pub.Write(self._cmd)

    def send_impedance(self,
                       q_des: Dict[int, float],
                       dq_des: Optional[Dict[int, float]] = None,
                       kp: Optional[Dict[int, float]] = None,
                       kd: Optional[Dict[int, float]] = None,
                       tau_ff: Optional[Dict[int, float]] = None,
                       mirror_leg_waist: bool = True):
        """Send impedance-mode command.

        Args:
            q_des:       desired joint positions (radians)
            dq_des:      desired velocities (rad/s); default zeros
            kp:          position gains per joint; default uses self._kp
            kd:          derivative gains per joint; default uses self._kd
            tau_ff:      feedforward torques; default zeros
            mirror_leg_waist: 是否将腿部+腰部(0-14)从 LowState 镜像到 motor_cmd
                              （kp=kd=0，q 零阶保持）。设为 False 可避免覆盖
                              已在同一帧内由 send_qpos_tau 设置的关节。
        """
        if not self.ok:
            return
        if not self._can_send_arm_sdk():
            return

        if mirror_leg_waist:
            self._mirror_leg_waist_from_state()

        dq_des = dq_des or {}
        kp     = kp or {}
        kd     = kd or {}
        tau_ff = tau_ff or {}

        for idx, val in q_des.items():
            if idx >= len(self._cmd.motor_cmd):
                continue
            mc = self._cmd.motor_cmd[idx]
            mc.mode = 4                      # impedance mode
            mc.q    = float(val)
            mc.dq   = float(dq_des.get(idx, 0.0))
            mc.kp   = float(kp.get(idx, self._kp))
            mc.kd   = float(kd.get(idx, self._kd))
            mc.tau  = float(tau_ff.get(idx, 0.0))

        # enable slot
        if 29 < len(self._cmd.motor_cmd):
            self._cmd.motor_cmd[29].q = 1.0

        if self._crc is not None:
            if hasattr(self._crc, "Crc"):
                self._cmd.crc = self._crc.Crc(self._cmd)
            else:
                self._cmd.crc = self._crc.calculate_crc(self._cmd)

        self._pub.Write(self._cmd)

    def close(self, arm_pose: Optional[Dict[int, float]] = None, transition_duration: float = 2.0):
        """
        退出控制：通过平滑过渡将 motor_cmd[29].q 从 1.0 过渡到 0.0，
        使机器人从 arm_sdk 控制平滑切换到遥控器控制。

        Args:
            arm_pose: 释放时手臂的特定姿势（Dict[关节索引, 位置]），
                      如果为 None 则保持当前实际姿势。
                      关节索引范围：0-28（包含手臂和腿部关节）
            transition_duration: 过渡时间（秒），默认 2.0 秒
        """
        # 检查是否已清理或未初始化
        if not self.ok or self._pub is None:
            return

        # 保存当前控制参数
        saved_default_mode = self._default_mode
        dt = 0.02  # 控制周期 50Hz
        steps = max(1, int(transition_duration / dt))

        # 确定保持的关节位置
        if arm_pose is not None:
            q_hold = arm_pose
        else:
            js = self.get_joint_states()
            if js is not None:
                q_hold = {idx: js[idx]["q"] for idx in js if idx < 29}
            else:
                q_hold = {idx: 0.0 for idx in range(29)}

        for step in range(steps):
            # 平滑过渡比例：0 -> 1
            ratio = step / steps if steps > 0 else 1.0

            # 关节位置命令：保持当前状态（释放阶段全身用同一 kp/kd 托住当前位姿更稳）
            for idx, val in q_hold.items():
                if idx < len(self._cmd.motor_cmd):
                    mc = self._cmd.motor_cmd[idx]
                    mc.mode = saved_default_mode
                    mc.q    = float(val)
                    mc.dq   = 0.0
                    mc.tau  = 0.0
                    mc.kp   = self._kp
                    mc.kd   = self._kd

            # 关键：motor_cmd[29].q 从 1.0 平滑过渡到 0.0
            # 1.0 = arm_sdk 启用, 0.0 = arm_sdk 禁用（遥控器接管）
            if 29 < len(self._cmd.motor_cmd):
                self._cmd.motor_cmd[29].q = 1.0 - ratio  # 1.0 -> 0.0 平滑过渡

            # 计算 CRC 并发送
            if self._crc is not None:
                if hasattr(self._crc, "Crc"):
                    self._cmd.crc = self._crc.Crc(self._cmd)
                else:
                    self._cmd.crc = self._crc.calculate_crc(self._cmd)

            self._pub.Write(self._cmd)
            time.sleep(dt)

        # 过渡完成后，额外发送几帧确保切换成功
        if 29 < len(self._cmd.motor_cmd):
            self._cmd.motor_cmd[29].q = 0.0  # 确保完全设置为 0

        for _ in range(5):
            if self._crc is not None:
                if hasattr(self._crc, "Crc"):
                    self._cmd.crc = self._crc.Crc(self._cmd)
                else:
                    self._cmd.crc = self._crc.calculate_crc(self._cmd)
            self._pub.Write(self._cmd)
            time.sleep(0.01)

        # 清理所有 DDS 资源
        self._cleanup_dds()
        self.ok = False

    def __enter__(self):
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源被正确清理。"""
        if self.ok:
            self.close()
        return False

    def __del__(self):
        """析构函数，防止资源泄漏。"""
        if getattr(self, 'ok', False) and self._initialized:
            try:
                self._cleanup_dds()
            except Exception:
                pass

# ----------------------------------------------------------------------
# 2) Joint mapping
# ----------------------------------------------------------------------

JOINTS: List[Tuple[int, str, str]] = [
    # 12-14: waist (not actively controlled, mirrored from state)
    (12, "waist-yaw",   "waist_yaw"),
    (13, "waist-roll",  "waist_roll"),
    (14, "waist-pitch", "waist_pitch"),
    # 15-21: left arm
    (15, "left-shoulder-pitch", "left_shoulder_pitch"),
    (16, "left-shoulder-roll",  "left_shoulder_roll"),
    (17, "left-shoulder-yaw",   "left_shoulder_yaw"),
    (18, "left-elbow",          "left_elbow"),
    (19, "left-wrist-roll",     "left_wrist_roll"),
    (20, "left-wrist-pitch",    "left_wrist_pitch"),
    (21, "left-wrist-yaw",      "left_wrist_yaw"),
    # 22-28: right arm
    (22, "right-shoulder-pitch", "right_shoulder_pitch"),
    (23, "right-shoulder-roll",  "right_shoulder_roll"),
    (24, "right-shoulder-yaw",   "right_shoulder_yaw"),
    (25, "right-elbow",          "right_elbow"),
    (26, "right-wrist-roll",     "right_wrist_roll"),
    (27, "right-wrist-pitch",    "right_wrist_pitch"),
    (28, "right-wrist-yaw",      "right_wrist_yaw"),
]
IDX2LABEL = {idx: lbl for idx, lbl, _ in JOINTS}

IDX2MUJOCO = {
    12: "waist_yaw_joint",
    13: "waist_roll_joint",
    14: "waist_pitch_joint",
    15: "left_shoulder_pitch_joint",
    16: "left_shoulder_roll_joint",
    17: "left_shoulder_yaw_joint",
    18: "left_elbow_joint",
    19: "left_wrist_roll_joint",
    20: "left_wrist_pitch_joint",
    21: "left_wrist_yaw_joint",
    22: "right_shoulder_pitch_joint",
    23: "right_shoulder_roll_joint",
    24: "right_shoulder_yaw_joint",
    25: "right_elbow_joint",
    26: "right_wrist_roll_joint",
    27: "right_wrist_pitch_joint",
    28: "right_wrist_yaw_joint",
}










def name_to_index(name: str) -> int:
    for idx, lbl in IDX2LABEL.items():
        if lbl == name:
            return idx
    key = name.lower().replace(" ", "").replace("-", "")
    for idx, lbl in IDX2LABEL.items():
        cand = lbl.lower().replace(" ", "").replace("-", "")
        if cand == key or cand.startswith(key):
            return idx
    raise ValueError(f"Unknown joint: {name!r}")
