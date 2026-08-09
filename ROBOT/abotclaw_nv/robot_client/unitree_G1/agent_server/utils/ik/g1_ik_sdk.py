"""G1 IK — MuJoCo IK + Ruckig；真机双臂 **仅** 经 ``RobotBridge`` 下发（``rt/arm_sdk``），无 adapter 旁路。"""

from __future__ import annotations

import logging
import os
import time
import numpy as np
import mujoco
from dataclasses import dataclass, field
from typing import Optional, Dict

from ruckig import Ruckig, InputParameter, OutputParameter, Result

from .g1_ik_solver import G1IKSolver, RIGHT_JOINTS, LEFT_JOINTS, WAIST_JOINTS

logger = logging.getLogger(__name__)


def _default_iface() -> str:
    return os.environ.get(
        "G1_ARM_NETWORK_IFACE",
        os.environ.get("UNITREE_IFACE", os.environ.get("G1_NETWORK_INTERFACE", "enp4s0")),
    )


def _pd_from_env(kp: float, kd: float) -> tuple[float, float]:
    """可选与 main.py 对齐：``G1_ARM_PD_KP`` / ``G1_ARM_PD_KD`` 覆盖 IKConfig 中的 PD。"""
    out_kp, out_kd = float(kp), float(kd)
    raw = os.environ.get("G1_ARM_PD_KP", "").strip()
    if raw:
        try:
            out_kp = float(raw)
        except ValueError:
            pass
    raw = os.environ.get("G1_ARM_PD_KD", "").strip()
    if raw:
        try:
            out_kd = float(raw)
        except ValueError:
            pass
    return out_kp, out_kd


try:
    from robot_sdk.robotbridge import RobotBridge, name_to_index
except ImportError:
    from robotbridge import RobotBridge, name_to_index


@dataclass
class IKConfig:
    """Configuration for G1 IK controller."""
    xml_path: str = "models/g1_description/g1_29dof_rev_1_0_with_inspire_hand_FTP.xml"
    iface: str = field(default_factory=_default_iface)
    domain: int = 0
    # 双臂 PD（与官方 g1_arm7_sdk_dds_example 一致）
    kp: float = 60.0
    kd: float = 1.5
    # 腰部 PD：惯性更大，需更高增益才能在双臂运动时保持不动
    waist_kp: float = 120.0
    waist_kd: float = 8.0
    gravity_comp_scale: float = 1.0
    adapt_ki: float = 1.5
    adapt_clamp: float = 4.0
    timestep: float = 0.01


class IkArmTrajectoryController:
    """单臂 Ruckig 轨迹（仅供 G1IKController 内部使用）。"""

    def __init__(self, qpos, qvel, ctrl, timestep, model, joint_names):
        self.qpos = qpos
        self.qvel = qvel
        self.ctrl = ctrl
        self.model = model

        self.joint_ids = [self.model.joint(j).id for j in joint_names]
        self.dof = len(self.joint_ids)

        self.otg = Ruckig(self.dof, timestep)
        self.otg_inp = InputParameter(self.dof)
        self.otg_out = OutputParameter(self.dof)

        self.otg_inp.max_velocity = 4 * [np.deg2rad(80)] + 3 * [np.deg2rad(140)]
        self.otg_inp.max_acceleration = 4 * [np.deg2rad(240)] + 3 * [np.deg2rad(450)]
        self.otg_res = Result.Finished

    def move_to(self, target_qpos):
        target = target_qpos[self.joint_ids]
        self.otg_inp.current_position = self.qpos[self.joint_ids].copy()
        self.otg_inp.current_velocity = self.qvel[self.joint_ids].copy()
        self.otg_inp.target_position = target.copy()
        self.otg_res = Result.Working

    def step(self):
        if self.otg_res == Result.Working:
            self.otg_res = self.otg.update(self.otg_inp, self.otg_out)
            self.otg_out.pass_to_input(self.otg_inp)
            self.qpos[self.joint_ids] = self.otg_out.new_position
            self.qvel[self.joint_ids] = self.otg_out.new_velocity
            self.ctrl[self.joint_ids] = self.otg_out.new_position
        elif self.otg_res == Result.Finished:
            self.ctrl[self.joint_ids] = self.otg_out.new_position

    def is_busy(self):
        return self.otg_res == Result.Working


class G1IKController:
    """G1 双臂笛卡尔 IK 高层控制器。"""

    def __init__(self, config: Optional[IKConfig] = None, enable_robot: bool = True):
        self.config = config or IKConfig()
        self.enable_robot = enable_robot
        self.rb: Optional[RobotBridge] = None
        self.ik: Optional[G1IKSolver] = None
        self.right_arm_ctrl: Optional[IkArmTrajectoryController] = None
        self.left_arm_ctrl: Optional[IkArmTrajectoryController] = None
        self.tau_integral: Dict[int, float] = {}
        # 腰部：位置跟踪（零刚度 → 跟随初始位置，保持刚度）
        self._waist_qpos: Optional[Dict[int, float]] = None
        self._initialized = False

    def initialize(self) -> bool:
        try:
            if self.enable_robot:
                kp_eff, kd_eff = _pd_from_env(self.config.kp, self.config.kd)
                if (kp_eff, kd_eff) != (float(self.config.kp), float(self.config.kd)):
                    print(
                        f"[G1IKController] 使用环境变量 PD 覆盖: kp={kp_eff}, kd={kd_eff} "
                        f"(IKConfig 原为 kp={self.config.kp}, kd={self.config.kd})"
                    )
                self.rb = RobotBridge(
                    iface=self.config.iface,
                    domain=self.config.domain,
                    default_mode=0,
                    kp=kp_eff,
                    kd=kd_eff,
                )
                if not self.rb.ok:
                    print("[G1IKController] RobotBridge initialization failed")
                    return False
                # 与官方 arm7 示例一致：首帧 LowState 到达后再发 arm_sdk，避免腿腰仍为 q=0 的异常帧
                try:
                    lowstate_timeout = float(os.environ.get("G1_ARM_LOWSTATE_TIMEOUT", "10.0"))
                except ValueError:
                    lowstate_timeout = 10.0
                lowstate_timeout = max(1.0, lowstate_timeout)
                got_lowstate = self.rb.wait_for_lowstate(timeout=lowstate_timeout)
                allow_skip = os.environ.get("G1_ARM_ALLOW_NO_LOWSTATE", "").strip().lower() in (
                    "1",
                    "true",
                    "yes",
                    "on",
                )
                if not got_lowstate and allow_skip:
                    print(
                        "[G1IKController] WARNING: 未收到 rt/lowstate，但 G1_ARM_ALLOW_NO_LOWSTATE=1，"
                        "继续初始化；RobotBridge 将仍向 rt/arm_sdk 写入（腿腰未镜像，真机有风险，仅排查）。"
                    )
                elif not got_lowstate:
                    print(
                        "[G1IKController] 初始化失败：在 "
                        f"{lowstate_timeout:.1f}s 内未收到 rt/lowstate。"
                        "请检查 G1_ARM_NETWORK_IFACE / UNITREE_IFACE、机器人上电与二层网络。"
                        "可适当增大环境变量 G1_ARM_LOWSTATE_TIMEOUT。"
                        "仅离线调试可设 G1_ARM_ALLOW_NO_LOWSTATE=1（真机勿用）。"
                    )
                    try:
                        self.rb.shutdown_publishers()
                    except Exception:
                        pass
                    self.rb = None
                    return False
                time.sleep(0.05)

            xml_path = self.config.xml_path
            if not os.path.isabs(xml_path):
                # 本文件在 utils/ik/，相对路径默认解析到 utils/models/
                xml_path = os.path.normpath(
                    os.path.join(os.path.dirname(__file__), "..", xml_path)
                )
            self.ik = G1IKSolver(xml_path)

            qpos0 = self._get_initial_qpos()

            self.right_arm_ctrl = IkArmTrajectoryController(
                qpos=qpos0.copy(),
                qvel=np.zeros_like(qpos0),
                ctrl=qpos0.copy(),
                timestep=self.config.timestep,
                model=self.ik.model,
                joint_names=RIGHT_JOINTS,
            )
            self.left_arm_ctrl = IkArmTrajectoryController(
                qpos=self.right_arm_ctrl.qpos,
                qvel=self.right_arm_ctrl.qvel,
                ctrl=self.right_arm_ctrl.ctrl,
                timestep=self.config.timestep,
                model=self.ik.model,
                joint_names=LEFT_JOINTS,
            )

            for jname in RIGHT_JOINTS + LEFT_JOINTS:
                idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
                self.tau_integral[idx_real] = 0.0

            # 记录腰部初始位置（用于跟踪）
            self._waist_qpos = {}
            for jname in WAIST_JOINTS:
                idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
                if self.rb and self.rb.ok:
                    js = self.rb.get_joint_states()
                    if js is not None and idx_real in js:
                        self._waist_qpos[idx_real] = float(js[idx_real]["q"])
                    else:
                        sim_jid = self.ik.model.joint(jname).id
                        self._waist_qpos[idx_real] = float(self.ik.data.qpos[sim_jid])
                else:
                    sim_jid = self.ik.model.joint(jname).id
                    self._waist_qpos[idx_real] = float(self.ik.data.qpos[sim_jid])

            self._initialized = True
            return True

        except Exception as e:
            print(f"[G1IKController] Initialization failed: {e}")
            return False

    def _get_initial_qpos(self) -> np.ndarray:
        try:
            qpos_home = self.ik.model.key("home").qpos.copy()
        except Exception:
            qpos_home = self.ik.data.qpos.copy()

        if self.rb and self.rb.ok:
            js = None
            for _ in range(100):
                js = self.rb.get_joint_states()
                if js is not None:
                    break
                time.sleep(0.01)

            if js is not None:
                qpos0 = qpos_home.copy()
                for jname in RIGHT_JOINTS + LEFT_JOINTS:
                    jid = self.ik.model.joint(jname).id
                    idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
                    if idx_real in js:
                        qpos0[jid] = js[idx_real]["q"]
                self.ik.data.qpos[:] = qpos0
                mujoco.mj_forward(self.ik.model, self.ik.data)
                return qpos0

            print(
                "[G1IKController] WARNING: 未收到 rt/lowstate（get_joint_states 超时）。"
                "请检查网卡 iface（当前 IKConfig.iface="
                f"{self.config.iface}）、机器人上电与二层网络；"
                "部分机型在无 LowState 时 arm_sdk 指令可能被忽略。"
            )
        return qpos_home

    def move_to_waypoint(self, r_pos, r_quat, l_pos, l_quat) -> bool:
        if not self._initialized:
            print("[G1IKController] Not initialized")
            return False

        rb_ok = bool(self.rb and self.rb.ok)
        logger.info(
            "move_to_waypoint: enable_robot=%s, rb is None=%s, rb.ok=%s (will send LowCmd=%s)",
            self.enable_robot,
            self.rb is None,
            (self.rb.ok if self.rb is not None else None),
            rb_ok,
        )
        if self.enable_robot and not rb_ok:
            logger.warning(
                "move_to_waypoint: RobotBridge 不可用，轨迹仅在仿真中推进，不会下发到真机；"
                "函数仍会在轨迹结束后返回 True。"
            )

        qpos_goal_r = self.ik.solve("right", r_pos, r_quat, self.right_arm_ctrl.qpos)
        qpos_goal_l = self.ik.solve("left", l_pos, l_quat, self.left_arm_ctrl.qpos)

        self.right_arm_ctrl.move_to(qpos_goal_r)
        self.left_arm_ctrl.move_to(qpos_goal_l)

        trajectory_steps = 0
        send_calls = 0
        while self.right_arm_ctrl.is_busy() or self.left_arm_ctrl.is_busy():
            self.right_arm_ctrl.step()
            self.left_arm_ctrl.step()

            self.ik.data.qpos[:] = self.right_arm_ctrl.qpos
            mujoco.mj_forward(self.ik.model, self.ik.data)

            trajectory_steps += 1
            if self.rb and self.rb.ok:
                self._send_control_command()
                send_calls += 1

            time.sleep(self.config.timestep)

        logger.info(
            "move_to_waypoint done: trajectory_steps=%d, _send_control_command calls=%d, return True",
            trajectory_steps,
            send_calls,
        )
        if self.enable_robot and send_calls == 0 and trajectory_steps > 0:
            logger.warning(
                "move_to_waypoint: 有 %d 步仿真步进但从未调用 _send_control_command（检查 rb / rb.ok）",
                trajectory_steps,
            )

        return True

    def _send_control_command(self):
        q_real = {}
        tau_ff = {}
        js = self.rb.get_joint_states()

        for jname in RIGHT_JOINTS + LEFT_JOINTS:
            sim_jid = self.ik.model.joint(jname).id
            q_val = self.ik.data.qpos[sim_jid]
            idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
            q_real[idx_real] = float(q_val)

            dof = self.ik.model.jnt_dofadr[sim_jid]
            tau_base = self.config.gravity_comp_scale * float(self.ik.data.qfrc_bias[dof])

            if js is not None and idx_real in js:
                err = float(q_val) - js[idx_real]["q"]
                self.tau_integral[idx_real] += self.config.adapt_ki * err * self.config.timestep
                self.tau_integral[idx_real] = np.clip(
                    self.tau_integral[idx_real],
                    -self.config.adapt_clamp,
                    self.config.adapt_clamp,
                )
                tau_ff[idx_real] = tau_base + self.tau_integral[idx_real]
            else:
                tau_ff[idx_real] = tau_base

        # 腰部：使用 send_impedance 施加更高 PD 增益（waist_kp / waist_kd）固定在初始位
        for jname in WAIST_JOINTS:
            idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
            if idx_real not in self._waist_qpos:
                sim_jid = self.ik.model.joint(jname).id
                self._waist_qpos[idx_real] = float(self.ik.data.qpos[sim_jid])

        self.rb.send_qpos_tau(q_real, tau_ff)

        waist_q = {idx: self._waist_qpos[idx] for idx in self._waist_qpos}
        waist_kp = {idx: self.config.waist_kp for idx in self._waist_qpos}
        waist_kd = {idx: self.config.waist_kd for idx in self._waist_qpos}
        # mirror_leg_waist=False: 避免覆盖 send_qpos_tau 已设置的双臂 kp/kd
        self.rb.send_impedance(waist_q, kp=waist_kp, kd=waist_kd, mirror_leg_waist=False)

    def get_joint_states(self):
        if self.rb and self.rb.ok:
            return self.rb.get_joint_states()
        return None

    def close(self):
        if self.rb:
            self.rb.close()
            self.rb = None
        self._initialized = False
