"""G1 Dual-Arm IK Controller with Ruckig trajectory generation."""

import time
import math
import numpy as np
from ruckig import Ruckig, InputParameter, OutputParameter, Result

from .g1_ik_solver import G1IKSolver, RIGHT_JOINTS, LEFT_JOINTS
from robot_sdk.robotbridge import RobotBridge, name_to_index


class ArmController:
    """Single arm trajectory controller using Ruckig for smooth motion."""
    
    def __init__(self, qpos, qvel, ctrl, qpos_gripper, ctrl_gripper,
                 timestep, model, joint_names, ee_name=None):
        self.qpos = qpos
        self.qvel = qvel
        self.ctrl = ctrl
        self.qpos_gripper = qpos_gripper
        self.ctrl_gripper = ctrl_gripper
        self.model = model
        self.ee_name = ee_name

        # joints index mapping
        self.joint_ids = [self.model.joint(j).id for j in joint_names]
        self.dof = len(self.joint_ids)

        # Ruckig setup
        self.otg = Ruckig(self.dof, timestep)
        self.otg_inp = InputParameter(self.dof)
        self.otg_out = OutputParameter(self.dof)

        # limits
        self.otg_inp.max_velocity = 4 * [math.radians(80)] + 3 * [math.radians(140)]
        self.otg_inp.max_acceleration = 4 * [math.radians(240)] + 3 * [math.radians(450)]
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


def move_dual_arm_to_waypoints(
    rb: RobotBridge,
    waypoints: list,
    kp: float = 20.0,
    kd: float = 1.0,
    gravity_comp_scale: float = 1.0,
    adapt_load: bool = True,
    adapt_ki: float = 1.5,
    adapt_clamp: float = 4.0,
    enable_viewer: bool = False
):
    """
    Move dual arms through a series of waypoints using IK and Ruckig trajectory.
    
    Args:
        rb: RobotBridge instance for robot communication
        waypoints: List of (right_pos, right_quat, left_pos, left_quat) tuples
        kp: Position gain
        kd: Derivative gain  
        gravity_comp_scale: Gravity compensation scale
        adapt_load: Enable adaptive load compensation
        adapt_ki: Adaptive integral gain
        adapt_clamp: Adaptive torque clamp
        enable_viewer: Enable MuJoCo viewer (for debugging)
    """
    ik = G1IKSolver("models/g1_description/g1_29dof_rev_1_0_with_inspire_hand_FTP.xml")

    # Try to use palm_center sites, fallback to wrist_yaw_link bodies
    try:
        ee_ref_right = ("site", ik.model.site("right_palm_center").id)
        ee_ref_left = ("site", ik.model.site("left_palm_center").id)
    except Exception:
        ee_ref_right = ("body", ik.model.body("right_wrist_yaw_link").id)
        ee_ref_left = ("body", ik.model.body("left_wrist_yaw_link").id)

    try:
        qpos_home = ik.model.key("home").qpos.copy()
    except Exception:
        qpos_home = ik.data.qpos.copy()

    # Initialize from robot state
    if rb.ok:
        # Wait for DDS to be ready (critical!)
        time.sleep(2.0)
        js = None
        for _ in range(100):
            js = rb.get_joint_states()
            if js is not None:
                break
            time.sleep(0.01)

        if js is None:
            qpos0 = qpos_home.copy()
        else:
            qpos0 = qpos_home.copy()
            for jname in RIGHT_JOINTS + LEFT_JOINTS:
                jid = ik.model.joint(jname).id
                idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
                qpos0[jid] = js[idx_real]['q']
            ik.data.qpos[:] = qpos0
            import mujoco
            mujoco.mj_forward(ik.model, ik.data)
    else:
        qpos0 = qpos_home.copy()

    # Create arm controllers
    right_arm_ctrl = ArmController(
        qpos=qpos0.copy(),
        qvel=np.zeros_like(qpos0),
        ctrl=qpos0.copy(),
        qpos_gripper=np.zeros(1),
        ctrl_gripper=np.zeros(1),
        timestep=0.01,
        model=ik.model,
        joint_names=RIGHT_JOINTS,
        ee_name="ee_site_right",
    )
    left_arm_ctrl = ArmController(
        qpos=right_arm_ctrl.qpos,
        qvel=right_arm_ctrl.qvel,
        ctrl=right_arm_ctrl.ctrl,
        qpos_gripper=np.zeros(1),
        ctrl_gripper=np.zeros(1),
        timestep=0.01,
        model=ik.model,
        joint_names=LEFT_JOINTS,
        ee_name="ee_site_left",
    )

    # Adaptive load compensation
    tau_integral = {}
    for jname in RIGHT_JOINTS + LEFT_JOINTS:
        idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
        tau_integral[idx_real] = 0.0
    dt_control = 0.01

    import mujoco

    def _run_waypoints(viewer=None):
        for (r_pos, r_quat, l_pos, l_quat) in waypoints:
            qpos_goal_r = ik.solve("right", r_pos, r_quat, right_arm_ctrl.qpos)
            qpos_goal_l = ik.solve("left", l_pos, l_quat, left_arm_ctrl.qpos)

            right_arm_ctrl.move_to(qpos_goal_r)
            left_arm_ctrl.move_to(qpos_goal_l)

            while right_arm_ctrl.is_busy() or left_arm_ctrl.is_busy():
                right_arm_ctrl.step()
                left_arm_ctrl.step()

                ik.data.qpos[:] = right_arm_ctrl.qpos
                mujoco.mj_forward(ik.model, ik.data)

                if rb.ok:
                    q_real = {}
                    tau_ff = {}
                    js = rb.get_joint_states()
                    for jname in RIGHT_JOINTS + LEFT_JOINTS:
                        sim_jid = ik.model.joint(jname).id
                        q_val = ik.data.qpos[sim_jid]
                        idx_real = name_to_index(jname.replace("_joint", "").replace("_", "-"))
                        q_real[idx_real] = float(q_val)
                        dof = ik.model.jnt_dofadr[sim_jid]
                        tau_base = gravity_comp_scale * float(ik.data.qfrc_bias[dof])
                        if adapt_load and js is not None and idx_real in js:
                            err = float(q_val) - js[idx_real]["q"]
                            tau_integral[idx_real] += adapt_ki * err * dt_control
                            tau_integral[idx_real] = np.clip(tau_integral[idx_real], -adapt_clamp, adapt_clamp)
                            tau_ff[idx_real] = tau_base + tau_integral[idx_real]
                        else:
                            tau_ff[idx_real] = tau_base
                    rb.send_qpos_tau(q_real, tau_ff)

                if viewer is not None:
                    for ee_ref, color in [(ee_ref_right, [0, 1, 0, 1]), (ee_ref_left, [0, 0, 1, 1])]:
                        if ee_ref[0] == "site":
                            ee_pos = ik.data.site_xpos[ee_ref[1]].copy()
                        else:
                            ee_pos = ik.data.xpos[ee_ref[1]].copy()
                        viewer.user_scn.ngeom = min(viewer.user_scn.ngeom + 1, viewer.user_scn.maxgeom)
                        g = viewer.user_scn.geoms[viewer.user_scn.ngeom - 1]
                        mujoco.mjv_initGeom(
                            g,
                            type=mujoco.mjtGeom.mjGEOM_SPHERE,
                            size=[0.005, 0, 0],
                            pos=ee_pos,
                            mat=np.eye(3).flatten(),
                            rgba=color,
                        )
                    viewer.sync()

                time.sleep(0.01)

    if enable_viewer:
        with mujoco.viewer.launch_passive(ik.model, ik.data) as viewer:
            _run_waypoints(viewer=viewer)
            viewer.close()
    else:
        _run_waypoints(viewer=None)
