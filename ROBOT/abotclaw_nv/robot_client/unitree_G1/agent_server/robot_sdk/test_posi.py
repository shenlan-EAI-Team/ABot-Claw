"""获取机器人当前坐标和目标点坐标"""

import math
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import rclpy

from robot_sdk import MemorySDK, Nav2Anywhere


def _yaw(ori) -> float:
    x, y, z, w = ori.x, ori.y, ori.z, ori.w
    return math.atan2(2 * (w * z), w * w - x * x - y * y + z * z)


def main():
    rclpy.init()
    nav = Nav2Anywhere()
    mem = MemorySDK()

    # 列出所有已记录的地点
    all_places = mem.query_place("", n_results=50)
    if not all_places:
        print("Memory 中暂无任何地点记录")
        rclpy.shutdown()
        return

    print(f"\n已记录 {len(all_places)} 个地点：")
    for p in all_places:
        tp = p["target_pose"]
        print(f"  {p['name']:15s}  x={tp['x']:7.3f}  y={tp['y']:7.3f}  yaw={tp['yaw']:7.3f} rad ({math.degrees(tp['yaw']):6.1f} deg)")

    # 取第一个地点作为目标
    target_pose = all_places[0]["target_pose"]
    place_name = all_places[0]["name"]
    tx, ty, tyaw = target_pose["x"], target_pose["y"], target_pose["yaw"]
    print(f"\n目标点 [{place_name}]: x={tx:.3f}  y={ty:.3f}  yaw={tyaw:.3f} rad ({math.degrees(tyaw):.1f} deg)")

    # spin 直到收到 /state_estimation（等待最多 3 秒）
    deadline = rclpy.clock.Clock().now() + rclpy.duration.Duration(seconds=3)
    while rclpy.ok() and rclpy.clock.Clock().now() < deadline:
        rclpy.spin_once(nav, timeout_sec=0.1)
        if nav.get_current_pose() is not None:
            break

    current = nav.get_current_pose()
    if current is None:
        print("未收到 /state_estimation，请确认 lightning loc_play.launch 已启动")
        rclpy.shutdown()
        return

    cx, cy = current.pose.position.x, current.pose.position.y
    cyaw = _yaw(current.pose.orientation)
    dist = math.hypot(cx - tx, cy - ty)
    yaw_diff = math.degrees(math.atan2(math.sin(cyaw - tyaw), math.cos(cyaw - tyaw)))
    print(f"当前坐标:         x={cx:.3f}  y={cy:.3f}  yaw={cyaw:.3f} rad ({math.degrees(cyaw):.1f} deg)")
    print(f"距离目标: {dist:.3f} m  朝向偏差: {yaw_diff:.1f} deg")

    rclpy.shutdown()


if __name__ == "__main__":
    main()
