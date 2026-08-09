"""Navigation SDK for G1: read current pose, send navigation goal.

ROS1 Noetic 实现：
  - 发布目标：/move_base_simple/goal（fire-and-forget，立即返回）
  - 监听状态：通过 TF 查询当前位姿，自己计算欧氏距离判定到达

核心设计原则：
  - move_base 只接收"干净"的四元数（qx=0, qy=0，仅 Z 轴旋转）
  - 传入含 X/Y 分量的四元数时，SDK 会自动提取 yaw 再转成干净四元数
  - G1 旋转机构有物理限位（约 ±75° / ±1.3 rad），使用朝向容差建议 >= 0.5 rad

对外接口：
    nav = Nav2Anywhere()
    nav.publish_simple_goal(x=2.0, y=1.5, yaw=1.5708)   # 推荐：传 yaw 角
    nav.publish_simple_goal(x=2.0, y=1.5, qx=q, qy=q, qz=q, qw=q)  # 传四元数也行，SDK 自动清理
    nav.nav_to_pose(pose_stamped)                        # PoseStamped 方式，同样自动清理四元数
    nav.wait_until_reached(timeout_sec=120.0)            # 等待到达（位置+朝向双检）

使用示例：
    nav = Nav2Anywhere()

    # 推荐方式：传 yaw 角，move_base 规划通畅
    nav.publish_simple_goal(x=2.0, y=1.5, yaw=1.5708)
    if nav.wait_until_reached(timeout_sec=120.0):
        print("到达")

    # 也可以传四元数，SDK 内部自动提取 yaw 再转干净四元数
    nav.publish_simple_goal(
        x=1.0, y=2.0,
        qx=0.307, qy=0.951, qz=0.011, qw=0.024,
    )


    # nav_to_pose 方式，等待到达同样自动清理四元数
    from geometry_msgs.msg import PoseStamped
    goal = PoseStamped()
    goal.header.frame_id = "map"
    goal.pose.position.x = 1.0
    goal.pose.position.y = 2.0
    goal.pose.orientation.z = -0.354
    goal.pose.orientation.w = 0.935
    nav.nav_to_pose(goal)
    nav.wait_until_reached(timeout_sec=180.0)
"""

from __future__ import annotations

import math
import time
from typing import Any

import rospy
import tf
import tf.transformations as tft
from geometry_msgs.msg import PoseStamped, Twist
from move_base_msgs.msg import MoveBaseActionFeedback
from actionlib_msgs.msg import GoalID
from nav_msgs.msg import Odometry

# 旧兼容：暴露给 routes/navigation_routes.py
def _yaw_from_quaternion(ori) -> float:
    """从 geometry_msgs/Quaternion 提取 yaw（弧度）。"""
    x, y, z, w = float(ori.x), float(ori.y), float(ori.z), float(ori.w)
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


def _quaternion_from_yaw(yaw: float) -> tuple[float, float, float, float]:
    """从 yaw 角构建干净的四元数（仅 Z 轴旋转，无 X/Y 分量）。"""
    return tft.quaternion_from_euler(0.0, 0.0, yaw)


def yaw_from_quaternion_xyzw(x: float, y: float, z: float, w: float) -> float:
    """从四元数 (x,y,z,w) 提取 yaw（弧度）。用于从 memory pose_data 提取目标朝向。

    注意：memory.yaw 字段可能不准确，强烈建议用此函数从四元数提取。
    G1 旋转机构有物理限位，实际可达范围约 -1.3 ~ 1.3 rad (-75° ~ 75°)，
    使用时朝向容差建议不小于 0.5 rad (~28°)。
    """
    siny_cosp = 2.0 * (w * z + x * y)
    cosy_cosp = 1.0 - 2.0 * (y * y + z * z)
    return math.atan2(siny_cosp, cosy_cosp)


# 统一容差/速度常量
REACH_THRESHOLD: float = 0.2          # 位置容差（米）
YAW_THRESHOLD_DEFAULT: float = 0.2    # 位置到达阶段允许的朝向容差（弧度）
FINE_YAW_THRESHOLD: float = 0.15      # 精细朝向容差（弧度）
ROTATE_SPEED_DEFAULT: float = 0.4133   # 精细旋转角速度（弧度/秒），衰减后最低约 0.31
XY_FINE_SPEED: float = 0.21            # XY 微调线速度（米/秒）
XY_FINE_THRESHOLD: float = 0.1       # XY 微调容差（米） 0.15可用


class Nav2Anywhere:
    """
    导航 SDK：基于 point1.py 的实现。
    通过 /move_base_simple/goal 发布目标，通过 TF 查询当前位姿自行计算距离判断到达。

    对外接口：
        nav = Nav2Anywhere()
        nav.get_current_pose()            -> PoseStamped | None
        nav.nav_to_pose(pose_stamped)     -> bool
        nav.wait_until_reached(timeout)   -> bool
        nav.nav_to_with_yaw(x, y, target_yaw) -> bool   # 导航+朝向控制
        nav.rotate_to_yaw(target_yaw)     -> bool        # 原地旋转控制朝向

    ROS1 依赖（由 navigate 工作空间提供）：
        - /tf                    (tf/tfMessage)          <- TF 树（查询 map→body）
        - /move_base_simple/goal (PoseStamped)           <- 发布目标
        - /move_base/feedback     (MoveBaseActionFeedback) <- 监听距离
        - slam_odom               (Odometry)              <- 备用调试
    """

    _ros_inited: bool = False

    # 类常量引用模块级常量（方便外部访问 Nav2Anywhere.REACH_THRESHOLD 等）
    REACH_THRESHOLD = REACH_THRESHOLD
    YAW_THRESHOLD_DEFAULT = YAW_THRESHOLD_DEFAULT
    FINE_YAW_THRESHOLD = FINE_YAW_THRESHOLD
    ROTATE_SPEED_DEFAULT = ROTATE_SPEED_DEFAULT
    XY_FINE_SPEED = XY_FINE_SPEED
    XY_FINE_THRESHOLD = XY_FINE_THRESHOLD

    def __init__(
        self,
        *,
        parent_frame: str = "map",
        child_frame: str = "body",
        move_base_ns: str = "/move_base",
        reach_threshold: float = REACH_THRESHOLD,
        yaw_threshold_default: float = YAW_THRESHOLD_DEFAULT,
    ):
        if not Nav2Anywhere._ros_inited:
            try:
                rospy.get_rostime()
            except Exception:
                rospy.init_node("nav2anywhere", anonymous=True)
            Nav2Anywhere._ros_inited = True

        self._parent_frame = parent_frame
        self._child_frame = child_frame
        self._reach_threshold = reach_threshold

        self._goal_active = False
        self._goal_reached = False
        self._goal_pose: PoseStamped | None = None
        self._feedback_dist: float | None = None
        self._near_goal_since: float | None = None
        self._odom: Odometry | None = None

        self._tf_listener = tf.TransformListener()

        self._odom_sub = rospy.Subscriber(
            "slam_odom", Odometry, self._odom_cb, queue_size=10
        )

        self._simple_pub = rospy.Publisher(
            "/move_base_simple/goal", PoseStamped, queue_size=1
        )

        self._feedback_sub = rospy.Subscriber(
            f"{move_base_ns}/feedback",
            MoveBaseActionFeedback,
            self._feedback_cb,
            queue_size=10,
        )

        self._vel_pub = rospy.Publisher("/cmd_vel", Twist, queue_size=1)

        self._cancel_pub = rospy.Publisher(
            "/move_base/cancel", GoalID, queue_size=1
        )

        rospy.sleep(1.0)

    def _odom_cb(self, msg: Odometry) -> None:
        self._odom = msg

    def _feedback_cb(self, msg: MoveBaseActionFeedback) -> None:
        """move_base 过程反馈：当前到目标的欧氏距离。"""
        if self._goal_pose is None or self._goal_reached:
            return
        gx = self._goal_pose.pose.position.x
        gy = self._goal_pose.pose.position.y
        px = msg.feedback.base_position.pose.position.x
        py = msg.feedback.base_position.pose.position.y
        self._feedback_dist = math.hypot(px - gx, py - gy)

    def publish_simple_goal(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        yaw: float = 0.0,
        pitch: float = 0.0,
        roll: float = 0.0,
        frame_id: str = "map",
        wait_before_pub: float = 0.5,
        *,
        qx: float | None = None,
        qy: float | None = None,
        qz: float | None = None,
        qw: float | None = None,
    ) -> bool:
        """
        发布导航目标点到 /move_base_simple/goal（fire-and-forget，立即返回）。

        内部自动处理四元数：
        - 如果传了 qx/qy/qz/qw（含 X/Y 分量的四元数），会自动提取 yaw 再转成干净四元数，
          避免 move_base 规划时被 X/Y 倾斜分量干扰。
        - 如果传了 yaw/pitch/roll，直接转四元数（qx=0, qy=0）。

        Args:
            x, y, z:       目标坐标（map 坐标系）
            yaw/pitch/roll: 目标朝向（弧度），当未指定四元数时使用
            frame_id:       坐标系，默认 map
            wait_before_pub: 发布前等待 publisher 建立连接的时间（秒）
            qx/qy/qz/qw:    四元数朝向（优先级高于 yaw/pitch/roll）；SDK 会自动清理

        Returns:
            bool: 是否成功发布

        使用示例（传 yaw 角，推荐）：
            nav.publish_simple_goal(x=3.0, y=2.0, yaw=1.5708)

        使用示例（传四元数，SDK 自动清理 X/Y 分量）：
            nav.publish_simple_goal(
                x=0.0, y=0.0,
                qx=0.307, qy=0.951, qz=0.011, qw=0.024,
            )
        """
        if qw is not None and qx is not None:
            # 四元数 → 提取 yaw → 转回干净四元数（仅 Z 轴），避免 X/Y 分量干扰 move_base 规划
            yaw_extracted = yaw_from_quaternion_xyzw(qx, qy, qz, qw)
            ox, oy, oz, ow = _quaternion_from_yaw(yaw_extracted)
        else:
            ox, oy, oz, ow = _quaternion_from_yaw(yaw)

        goal = PoseStamped()
        goal.header.frame_id = frame_id
        goal.header.stamp = rospy.Time.now()
        goal.pose.position.x = x
        goal.pose.position.y = y
        goal.pose.position.z = z
        goal.pose.orientation.x = ox
        goal.pose.orientation.y = oy
        goal.pose.orientation.z = oz
        goal.pose.orientation.w = ow

        # Set goal state so wait_until_reached() knows the target
        self._goal_active = True
        self._goal_reached = False
        self._goal_pose = PoseStamped()
        self._goal_pose.header = goal.header
        self._goal_pose.pose = goal.pose
        self._feedback_dist = None
        self._near_goal_since = None

        rospy.sleep(wait_before_pub)
        self._simple_pub.publish(goal)
        rospy.loginfo(
            f"[publish_simple_goal] ({x:.3f}, {y:.3f}) q=({ox:.4f},{oy:.4f},{oz:.4f},{ow:.4f}) → /move_base_simple/goal"
        )
        return True

    def get_current_pose(self) -> "PoseStamped | None":
        """通过 TF 查询 map → body 的位姿，返回当前机器人 PoseStamped。"""
        try:
            now = rospy.Time(0)
            self._tf_listener.waitForTransform(
                self._parent_frame,
                self._child_frame,
                now,
                rospy.Duration(10.0),
            )
            (trans, rot) = self._tf_listener.lookupTransform(
                self._parent_frame, self._child_frame, now
            )
        except (
            tf.Exception,
            tf.LookupException,
            tf.ConnectivityException,
            tf.ExtrapolationException,
        ) as e:
            rospy.logwarn_throttle(2.0, f"[get_current_pose] TF lookup failed: {e}")
            return None

        pose = PoseStamped()
        pose.header.frame_id = self._parent_frame
        pose.header.stamp = rospy.Time.now()
        pose.pose.position.x = trans[0]
        pose.pose.position.y = trans[1]
        pose.pose.position.z = trans[2]
        pose.pose.orientation.x = rot[0]
        pose.pose.orientation.y = rot[1]
        pose.pose.orientation.z = rot[2]
        pose.pose.orientation.w = rot[3]
        return pose

    def nav_to_pose(self, pose: "PoseStamped") -> bool:
        """
        发布导航目标点，连续发布 3 次到 /move_base_simple/goal，然后通过 wait_until_reached 监听到达。

        如果传入的 PoseStamped 含四元数且 X/Y 分量不为 0，
        会自动提取 yaw 再转成干净四元数（qx=0, qy=0），
        避免 move_base 规划时被 X/Y 倾斜分量干扰。

        Returns:
            bool: 是否成功发布
        """
        self._goal_active = True
        self._goal_reached = False
        self._feedback_dist = None
        self._near_goal_since = None

        # 提取 yaw → 构建干净四元数（仅 Z 轴）
        raw_ori = pose.pose.orientation
        yaw_extracted = _yaw_from_quaternion(raw_ori)
        q_clean = _quaternion_from_yaw(yaw_extracted)
        clean_ox, clean_oy, clean_oz, clean_ow = q_clean

        stamped_pose = PoseStamped()
        stamped_pose.header.frame_id = pose.header.frame_id
        stamped_pose.header.stamp = (
            pose.header.stamp
            if not pose.header.stamp.is_zero()
            else rospy.Time.now()
        )
        stamped_pose.pose.position = pose.pose.position
        stamped_pose.pose.orientation.x = clean_ox
        stamped_pose.pose.orientation.y = clean_oy
        stamped_pose.pose.orientation.z = clean_oz
        stamped_pose.pose.orientation.w = clean_ow

        self._goal_pose = stamped_pose

        rospy.loginfo(
            f"[nav_to_pose] ({pose.pose.position.x:.3f}, {pose.pose.position.y:.3f}) "
            f"yaw={yaw_extracted:.3f} rad ({math.degrees(yaw_extracted):.1f} deg) "
            f"frame={pose.header.frame_id}"
        )

        for i in range(3):
            self._simple_pub.publish(stamped_pose)
            if i < 2:
                rospy.sleep(0.1)

        return True

    def nav_to_with_yaw(
        self,
        x: float,
        y: float,
        z: float = 0.0,
        target_yaw: float = 0.0,
        frame_id: str = "map",
        reach_threshold: float = REACH_THRESHOLD,
        reach_timeout: float = 120.0,
        yaw_threshold: float = FINE_YAW_THRESHOLD,
        rotate_timeout: float = 30.0,
    ) -> bool:
        """
        导航到目标点，到达后精细控制朝向。

        分两阶段：
        1. publish_simple_goal 导航到位置（忽略初始朝向，宽松等待）
        2. 取消 move_base 目标，用 /cmd_vel 原地旋转对齐目标朝向

        Args:
            x, y, z:        目标坐标（map 坐标系）
            target_yaw:      目标朝向（弧度）
            frame_id:        坐标系，默认 map
            reach_threshold: 位置容差（米），默认 0.2m
            reach_timeout:   位置导航超时（秒），默认 120s
            yaw_threshold:   朝向容差（弧度），默认 0.15 rad (~8.6°)
            rotate_timeout:  旋转控制超时（秒），默认 30s

        Returns:
            bool: 位置到达且朝向达标返回 True，否则 False
        """
        rospy.loginfo(
            f"[nav_to_with_yaw] 目标: ({x:.3f}, {y:.3f}) yaw={target_yaw:.3f} rad"
        )

        self.publish_simple_goal(x=x, y=y, z=z, yaw=0.0, frame_id=frame_id)
        self._reach_threshold = reach_threshold

        if not self.wait_until_reached(
            timeout_sec=reach_timeout,
            yaw_threshold=math.pi,
        ):
            rospy.logwarn(
                f"[nav_to_with_yaw] 位置导航超时 reach_timeout={reach_timeout}s"
            )
            return False

        rospy.loginfo("[nav_to_with_yaw] 位置到达，开始精细旋转控制朝向...")
        return self.rotate_to_yaw(
            target_yaw, threshold=yaw_threshold, timeout_sec=rotate_timeout
        )

    def rotate_to_yaw(
        self,
        target_yaw: float,
        threshold: float = FINE_YAW_THRESHOLD,
        timeout_sec: float = 30.0,
        angular_speed: float = ROTATE_SPEED_DEFAULT,
    ) -> bool:
        """
        原地旋转，控制机器人朝向达到目标角度。

        先取消 move_base 目标，再通过 /cmd_vel 发布角速度。
        当旋转受阻（物理限位或振荡）时，自动尝试反方向旋转。

        Args:
            target_yaw:     目标朝向（弧度）
            threshold:       朝向容差（弧度），默认 FINE_YAW_THRESHOLD=0.15 rad (~8.6°)
            timeout_sec:     超时（秒），默认 30s
            angular_speed:   旋转角速度（弧度/秒），默认 ROTATE_SPEED_DEFAULT=0.4

        Returns:
            bool: 朝向达标返回 True，超时返回 False
        """
        # 取消 move_base 目标，避免它覆盖 cmd_vel
        self._cancel_pub.publish(GoalID())
        rospy.loginfo("[rotate_to_yaw] 已取消 move_base 目标")
        rospy.sleep(0.5)
        self._stop()

        start = time.monotonic()
        vel = Twist()
        vel.linear.x = 0.0
        vel.linear.y = 0.0
        vel.linear.z = 0.0
        vel.angular.x = 0.0
        vel.angular.y = 0.0

        rospy.loginfo(
            f"[rotate_to_yaw] 目标 yaw={target_yaw:.3f} rad ("
            f"{math.degrees(target_yaw):.1f} deg), 容差={threshold:.3f} rad"
        )

        def normalize_diff(diff: float) -> float:
            while diff > math.pi:
                diff -= 2.0 * math.pi
            while diff < -math.pi:
                diff += 2.0 * math.pi
            return diff

        def shortest_omega(current_yaw: float, tgt: float) -> tuple[float, bool]:
            """返回 (omega, flipped)。flipped=True 表示走了反方向（绕远路）。"""
            raw = normalize_diff(current_yaw - tgt)
            if abs(raw) <= threshold:
                return 0.0, False
            omega = angular_speed if raw > 0 else -angular_speed
            return omega, False

        flipped = False
        prev_yaw = None
        stuck_count = 0
        last_log_time = 0.0

        while not rospy.is_shutdown():
            elapsed = time.monotonic() - start
            if elapsed > timeout_sec:
                current = self.get_current_pose()
                cy = (
                    _yaw_from_quaternion(current.pose.orientation)
                    if current
                    else None
                )
                rospy.logwarn(
                    f"[rotate_to_yaw] 超时 after {timeout_sec}s, "
                    f"当前 yaw={cy:.3f} rad" if cy is not None else "无法获取当前朝向"
                )
                self._stop()
                return False

            current = self.get_current_pose()
            if current is None:
                time.sleep(0.1)
                continue

            current_yaw = _yaw_from_quaternion(current.pose.orientation)

            if prev_yaw is not None:
                delta = abs(normalize_diff(current_yaw - prev_yaw))
                if delta < 0.01:
                    stuck_count += 1
                else:
                    stuck_count = 0

            raw_diff = normalize_diff(current_yaw - target_yaw)
            yaw_diff = abs(raw_diff)

            if yaw_diff <= threshold:
                rospy.loginfo(
                    f"[rotate_to_yaw] >>> 朝向达标! 当前={current_yaw:.3f} rad "
                    f"目标={target_yaw:.3f} rad 误差={yaw_diff:.3f} rad, "
                    f"用时={elapsed:.1f}s"
                )
                self._stop()
                return True

            if stuck_count >= 5 and not flipped:
                rospy.logwarn(
                    f"[rotate_to_yaw] 检测到旋转受阻 (yaw={current_yaw:.3f} 连续不动), "
                    f"切换方向尝试 (raw_diff={raw_diff:.3f})"
                )
                flipped = True
                stuck_count = 0
                rospy.sleep(0.3)
                self._stop()
                rospy.sleep(0.2)

            if flipped:
                omega = -angular_speed if raw_diff >= 0 else angular_speed
            else:
                omega = angular_speed if raw_diff > 0 else -angular_speed

            if yaw_diff < 0.3:
                omega *= 0.75  # 接近目标时适当减速，但不小于 0.31 rad/s

            vel.angular.z = omega
            self._vel_pub.publish(vel)

            if elapsed - last_log_time >= 1.0:
                rospy.loginfo(
                    f"[rotate_to_yaw] yaw={current_yaw:.3f} rad "
                    f"({math.degrees(current_yaw):.1f} deg), "
                    f"误差={yaw_diff:.3f} rad, omega={omega:.2f}"
                    + (" [反方向]" if flipped else "")
                )
                last_log_time = elapsed

            prev_yaw = current_yaw
            time.sleep(0.05)

        self._stop()
        return False

    def _stop(self) -> None:
        """停止机器人所有运动。"""
        vel = Twist()
        self._vel_pub.publish(vel)

    def _adjust_xy_to_goal(
        self,
        target_x: float,
        target_y: float,
        linear_speed: float = XY_FINE_SPEED,
        threshold: float = XY_FINE_THRESHOLD,
        timeout_sec: float = 30.0,
    ) -> bool:
        """
        Phase 3：精细 XY 位置微调。

        取消 move_base 目标，通过 /cmd_vel 以固定线速度驱动 x/y 轴，
        使机器人精确到达目标 XY 坐标（精度 threshold 米以内）。

        Args:
            target_x, target_y: 目标 XY 坐标（map 坐标系）
            linear_speed:       线速度（米/秒），默认 XY_FINE_SPEED=0.2
            threshold:          XY 容差（米），默认 XY_FINE_THRESHOLD=0.15
            timeout_sec:        超时（秒），默认 30s

        Returns:
            bool: 到达达标返回 True，超时返回 False
        """
        self._cancel_pub.publish(GoalID())
        rospy.loginfo("[_adjust_xy] 已取消 move_base 目标，开始 XY 微调")
        rospy.sleep(0.5)
        self._stop()

        start = time.monotonic()
        vel = Twist()
        vel.angular.x = 0.0
        vel.angular.y = 0.0
        vel.angular.z = 0.0

        rospy.loginfo(
            f"[_adjust_xy] 目标=({target_x:.3f}, {target_y:.3f})m, "
            f"线速度={linear_speed}m/s, 容差={threshold}m"
        )

        while not rospy.is_shutdown():
            elapsed = time.monotonic() - start
            if elapsed > timeout_sec:
                current = self.get_current_pose()
                cx = current.pose.position.x if current else None
                cy = current.pose.position.y if current else None
                dist = math.hypot(cx - target_x, cy - target_y) if cx is not None else None
                rospy.logwarn(
                    f"[_adjust_xy] 超时 after {timeout_sec}s, "
                    + (f"当前=({cx:.3f},{cy:.3f}) 距离={dist:.3f}m" if dist is not None else "无法获取位置")
                )
                self._stop()
                return False

            current = self.get_current_pose()
            if current is None:
                time.sleep(0.1)
                continue

            cx = current.pose.position.x
            cy = current.pose.position.y
            current_yaw = _yaw_from_quaternion(current.pose.orientation)
            dx = target_x - cx
            dy = target_y - cy
            dist = math.hypot(dx, dy)
            theta = math.degrees(math.atan2(dy, dx) - current_yaw)
            dx = math.cos(math.radians(theta)) * dist
            dy = math.sin(math.radians(theta)) * dist
            if abs(dist) < threshold:
                rospy.loginfo(
                    f"[_adjust_xy] >>> XY 达标! 当前=({cx:.3f},{cy:.3f}) "
                    f"目标=({target_x:.3f},{target_y:.3f}) dx={dx:.3f}m dy={dy:.3f}m 用时={elapsed:.1f}s"
                )
                self._stop()
                return True
            if abs(dx) > 0.05:
                vx = linear_speed if dx > 0 else -linear_speed
            else:
                vx=0
            if abs(dy) > 0.05:
                vy = linear_speed if dy > 0 else -linear_speed
            else:
                vy=0

            vel.linear.x = vx
            vel.linear.y = vy
            vel.linear.z = 0.0
            self._vel_pub.publish(vel)

            if int(elapsed) > (int(elapsed) // 2) * 2:
                rospy.loginfo_throttle(
                    2.0,
                    f"[_adjust_xy] 当前=({cx:.3f},{cy:.3f}) dx={dx:.3f}m dy={dy:.3f}m → ({vx:.2f},{vy:.2f})",
                )

            time.sleep(0.05)

        self._stop()
        return False

    def wait_until_reached(
        self,
        timeout_sec: float | None = None,
        yaw_threshold: float = YAW_THRESHOLD_DEFAULT,
        fine_yaw_threshold: float = FINE_YAW_THRESHOLD,
        rotate_timeout: float = 30.0,
        angular_speed: float = ROTATE_SPEED_DEFAULT,
        xy_fine_threshold: float = XY_FINE_THRESHOLD,
    ) -> bool:
        """
        阻塞等待 move_base 导航到达，自动做精细朝向与 XY 位置微调。

        三阶段策略：
        1. 等位置到达（允许宽松朝向容差 yaw_threshold）
        2. 位置到达后，若朝向误差 > fine_yaw_threshold，调用 rotate_to_yaw 精细旋转
        3. 精细旋转完成后，若 XY 距离 > xy_fine_threshold，调用 _adjust_xy_to_goal 微调 XY

        同时满足以下三个条件才视为到达：
            1. 位置误差 <= reach_threshold
            2. 朝向误差 <= fine_yaw_threshold
            3. XY 距离误差 <= xy_fine_threshold

        Args:
            timeout_sec:       最大等待时间（秒），默认 300s
            yaw_threshold:     位置到达判断时允许的朝向容差（弧度），默认 YAW_THRESHOLD_DEFAULT=0.2 rad
            fine_yaw_threshold: 精细朝向容差（弧度），默认 FINE_YAW_THRESHOLD=0.15 rad (~8.6°)
            rotate_timeout:    精细旋转超时（秒），默认 30s
            angular_speed:     精细旋转角速度（弧度/秒），默认 ROTATE_SPEED_DEFAULT=0.4
            xy_fine_threshold: 精细 XY 位置容差（米），默认 XY_FINE_THRESHOLD=0.15 m

        Returns:
            bool: 是否成功到达
        """
        timeout = timeout_sec if timeout_sec is not None else 300.0
        start = time.monotonic()
        threshold = self._reach_threshold

        target_yaw = None
        if self._goal_pose is not None:
            target_yaw = _yaw_from_quaternion(self._goal_pose.pose.orientation)

        target_x = self._goal_pose.pose.position.x if self._goal_pose else 0.0
        target_y = self._goal_pose.pose.position.y if self._goal_pose else 0.0
        _ty_str = f"{target_yaw:.3f}rad" if target_yaw is not None else "N/A"
        rospy.loginfo(
            f"[wait_until_reached] ==> 等待到达目标: x={target_x:.3f} y={target_y:.3f} "
            f"yaw={_ty_str} (位置容差={threshold}m, 位置阶段朝向容差={yaw_threshold:.3f}rad, "
            f"精细朝向容差={fine_yaw_threshold:.3f}rad)"
        )

        pos_reached = False
        last_px = last_py = last_dist = last_yd = None

        # ── Phase 1: 等待位置到达 ──────────────────────────────────────────────
        while not rospy.is_shutdown():
            elapsed = time.monotonic() - start
            if elapsed > timeout:
                rospy.logwarn(
                    f"[wait_until_reached] 超时 after {timeout}s | "
                    + (
                        f"当前位置: x={last_px:.3f} y={last_py:.3f} | 距离目标: {last_dist:.3f}m"
                        if last_dist is not None else "TF查询失败，无法获取位置"
                    )
                )
                self._goal_active = False
                return False

            current = self.get_current_pose()
            if current is not None and self._goal_pose is not None:
                gx = self._goal_pose.pose.position.x
                gy = self._goal_pose.pose.position.y
                px = current.pose.position.x
                py = current.pose.position.y
                dist = math.hypot(px - gx, py - gy)

                current_yaw = _yaw_from_quaternion(current.pose.orientation)
                yaw_diff = None
                if target_yaw is not None:
                    raw_diff = current_yaw - target_yaw
                    while raw_diff > math.pi:
                        raw_diff -= 2.0 * math.pi
                    while raw_diff < -math.pi:
                        raw_diff += 2.0 * math.pi
                    yaw_diff = abs(raw_diff)

                last_px, last_py, last_dist, last_yd = px, py, dist, yaw_diff
                pos_reached = dist <= threshold

                _yd_str = f"{yaw_diff:.3f}rad" if yaw_diff is not None else "N/A"
                status = (f"[wait_until_reached] 当前位置: x={px:.3f} y={py:.3f} yaw={current_yaw:.3f}rad "
                          f"| 到目标: 距离={dist:.3f}m 朝向差={_yd_str} | "
                          f"位置达标={pos_reached}")
                rospy.loginfo_throttle(2.0, status)

                if pos_reached:
                    # Phase 1：位置已到达（容差 threshold），进入精细调整阶段
                    break

                self._near_goal_since = None
            else:
                rospy.loginfo_throttle(2.0, "[wait_until_reached] Waiting for TF...")

            time.sleep(0.1)

        # ── Phase 2: 精细旋转朝向 ───────────────────────────────────────────────
        yaw_ok = True  # 若无需旋转，默认达标
        yaw_adjust_needed = (
            target_yaw is not None
            and not (last_yd is not None and last_yd <= fine_yaw_threshold)
        )

        if yaw_adjust_needed:
            elapsed = time.monotonic() - start
            remaining = timeout - elapsed
            phase2_timeout = min(rotate_timeout, remaining) if remaining > 0 else rotate_timeout
            rospy.loginfo(
                f"[wait_until_reached] 位置已到达 (距离={last_dist:.3f}m)，"
                f"朝向误差 {last_yd:.3f}rad > {fine_yaw_threshold:.3f}rad，进入精细旋转..."
                f"(剩余总时间={remaining:.1f}s，Phase2限时={phase2_timeout:.1f}s)"
            )
            yaw_ok = self.rotate_to_yaw(
                target_yaw,
                threshold=fine_yaw_threshold,
                timeout_sec=phase2_timeout,
                angular_speed=angular_speed,
            )
            if not yaw_ok:
                rospy.logwarn(
                    f"[wait_until_reached] 精细旋转未达标，剩余朝向误差约 {last_yd:.3f}rad，退出"
                )
                self._goal_active = False
                return False

        # ── Phase 3: 精细 XY 微调 ───────────────────────────────────────────────
        xy_ok = True  # 若无需 XY 微调，默认达标
        if yaw_ok and target_x is not None and target_y is not None:
            elapsed = time.monotonic() - start
            remaining = timeout - elapsed
            phase3_timeout = min(30.0, remaining) if remaining > 0 else 30.0
            current = self.get_current_pose()
            cur_dist = last_dist if current is None else math.hypot(
                current.pose.position.x - target_x,
                current.pose.position.y - target_y,
            )

            if cur_dist > xy_fine_threshold:
                rospy.loginfo(
                    f"[wait_until_reached] Phase 2 完成，进入 Phase 3 XY 微调 "
                    f"(距离={cur_dist:.3f}m > {xy_fine_threshold}m，限时={phase3_timeout:.1f}s)"
                )
                xy_ok = self._adjust_xy_to_goal(
                    target_x,
                    target_y,
                    linear_speed=XY_FINE_SPEED,
                    threshold=xy_fine_threshold,
                    timeout_sec=phase3_timeout,
                )
                if not xy_ok:
                    rospy.logwarn("[wait_until_reached] Phase 3 XY 微调未达标，退出")
                    self._goal_active = False
                    return False

        # ── 到达：打印最终误差 ──────────────────────────────────────────────────
        elapsed = time.monotonic() - start
        current = self.get_current_pose()
        if current is not None and target_x is not None and target_y is not None:
            final_dist = math.hypot(
                current.pose.position.x - target_x,
                current.pose.position.y - target_y,
            )
            raw_diff = _yaw_from_quaternion(current.pose.orientation) - target_yaw
            while raw_diff > math.pi:
                raw_diff -= 2.0 * math.pi
            while raw_diff < -math.pi:
                raw_diff += 2.0 * math.pi
            final_yaw_err = abs(raw_diff) if target_yaw is not None else 0.0
        else:
            final_dist = last_dist
            final_yaw_err = last_yd if last_yd is not None else 0.0

        rospy.loginfo(
            f"[wait_until_reached] >>> 到达! 用时={elapsed:.1f}s "
            f"| 最终距离误差={final_dist:.3f}m 最终朝向误差={final_yaw_err:.3f}rad"
        )
        self._goal_active = False
        self._goal_reached = True
        return True


def main(args=None):
    rospy.init_node("nav2anywhere_main")
    node = Nav2Anywhere()
    try:
        rospy.spin()
    except rospy.ROSInterruptException:
        pass


if __name__ == "__main__":
    main()
