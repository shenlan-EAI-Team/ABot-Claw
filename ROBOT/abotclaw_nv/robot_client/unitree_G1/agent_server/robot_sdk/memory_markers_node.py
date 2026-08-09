"""常驻 ROS 节点：把 Spatial Memory 里的 place 记忆发成 RViz 可视化 Marker。

运行方式::

    source /opt/ros/noetic/setup.bash
    cd /home/xxuz/ABot-Claw/robot_client/unitree_G1/agent_server
    python -m robot_sdk.memory_markers_node

发布话题:
    /memory/places  (visualization_msgs/MarkerArray)

RViz 配置:
    - Fixed Frame 选 map（与记忆 frame_id 一致）
    - Add -> By topic -> /memory/places -> MarkerArray

可选参数（环境变量）:
    - MEMORY_MARKERS_RATE_HZ      刷新频率，默认 0.5Hz（2s）
    - MEMORY_MARKERS_TOPIC        发布话题，默认 /memory/places
    - MEMORY_MARKERS_NAMESPACE    marker namespace 前缀，默认 place
    - MEMORY_MARKERS_ARROW_LEN    箭头长度(米)，默认 0.35
"""

from __future__ import annotations

import os
import sys
from typing import Any, Dict, List

import rospy
from geometry_msgs.msg import Point, Quaternion, Vector3
from std_msgs.msg import ColorRGBA, Header
from visualization_msgs.msg import Marker, MarkerArray

try:
    from .memory_sdk import MemorySDK
except ImportError:
    from memory_sdk import MemorySDK


_TOPIC = os.environ.get("MEMORY_MARKERS_TOPIC", "/memory/places")
_RATE_HZ = float(os.environ.get("MEMORY_MARKERS_RATE_HZ", "0.5"))
_NS = os.environ.get("MEMORY_MARKERS_NAMESPACE", "place")
_ARROW_LEN = float(os.environ.get("MEMORY_MARKERS_ARROW_LEN", "0.35"))


def _color(r: float, g: float, b: float, a: float = 1.0) -> ColorRGBA:
    return ColorRGBA(r=r, g=g, b=b, a=a)


def _lifetime_rospy() -> rospy.Time:
    """Marker 生命周期 = 3 个发布周期。ROS1 中 lifetime 用 rospy.Time 表示。"""
    total = 3.0 / max(_RATE_HZ, 0.1)
    return rospy.Time(secs=int(total), nsecs=int((total % 1) * 1e9))


def _quat_from_pose(tp: Dict[str, Any]) -> Quaternion:
    qx = tp.get("qx")
    qy = tp.get("qy")
    qz = tp.get("qz")
    qw = tp.get("qw")
    if None in (qx, qy, qz, qw):
        import math

        yaw = float(tp.get("yaw", 0.0))
        qz = math.sin(yaw / 2.0)
        qw = math.cos(yaw / 2.0)
        qx, qy = 0.0, 0.0
    return Quaternion(x=float(qx), y=float(qy), z=float(qz), w=float(qw))


def _make_arrow(idx: int, place: Dict[str, Any], stamp: rospy.Time, ns: str) -> Marker:
    tp = place.get("target_pose", {}) or {}
    frame_id = tp.get("frame_id", "map")

    m = Marker()
    m.header = Header(frame_id=frame_id, stamp=stamp)
    m.ns = f"{ns}/arrow"
    m.id = idx
    m.type = Marker.ARROW
    m.action = Marker.ADD
    m.pose.position = Point(x=float(tp.get("x", 0.0)), y=float(tp.get("y", 0.0)), z=float(tp.get("z", 0.0)))
    m.pose.orientation = _quat_from_pose(tp)
    m.scale = Vector3(x=_ARROW_LEN, y=_ARROW_LEN * 0.18, z=_ARROW_LEN * 0.18)
    m.color = _color(0.1, 0.8, 0.2, 0.95)
    m.lifetime = _lifetime_rospy()
    return m


def _make_text(idx: int, place: Dict[str, Any], stamp: rospy.Time, ns: str) -> Marker:
    tp = place.get("target_pose", {}) or {}
    frame_id = tp.get("frame_id", "map")
    name = str(place.get("name", "?"))

    m = Marker()
    m.header = Header(frame_id=frame_id, stamp=stamp)
    m.ns = f"{ns}/text"
    m.id = idx
    m.type = Marker.TEXT_VIEW_FACING
    m.action = Marker.ADD
    m.pose.position = Point(
        x=float(tp.get("x", 0.0)),
        y=float(tp.get("y", 0.0)),
        z=float(tp.get("z", 0.0)) + 0.35,
    )
    m.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
    m.scale.z = 0.18
    m.color = _color(1.0, 1.0, 1.0, 1.0)
    m.text = name
    m.lifetime = _lifetime_rospy()
    return m


def _make_sphere(idx: int, place: Dict[str, Any], stamp: rospy.Time, ns: str) -> Marker:
    tp = place.get("target_pose", {}) or {}
    frame_id = tp.get("frame_id", "map")
    m = Marker()
    m.header = Header(frame_id=frame_id, stamp=stamp)
    m.ns = f"{ns}/dot"
    m.id = idx
    m.type = Marker.SPHERE
    m.action = Marker.ADD
    m.pose.position = Point(x=float(tp.get("x", 0.0)), y=float(tp.get("y", 0.0)), z=float(tp.get("z", 0.0)))
    m.pose.orientation = Quaternion(x=0.0, y=0.0, z=0.0, w=1.0)
    m.scale = Vector3(x=0.12, y=0.12, z=0.12)
    m.color = _color(0.2, 0.5, 1.0, 0.85)
    m.lifetime = _lifetime_rospy()
    return m


class MemoryMarkersNode:
    def __init__(self) -> None:
        self._pub = rospy.Publisher(_TOPIC, MarkerArray, queue_size=10)
        self._mem = MemorySDK()
        self._last_ids: List[int] = []
        period = 1.0 / max(_RATE_HZ, 0.1)
        rospy.Timer(rospy.Duration(nsecs=int(period * 1e9)), self._tick)
        rospy.loginfo(
            f"MemoryMarkersNode: publishing places to {_TOPIC} at {_RATE_HZ:.2f} Hz "
            f"(hub={self._mem._base_url})"
        )

    def _fetch_places(self) -> List[Dict[str, Any]]:
        try:
            return self._mem.query_place("", n_results=500)
        except Exception as exc:
            rospy.logwarn(f"query_place failed: {exc!r}")
            return []

    def _tick(self, _event: Any = None) -> None:
        places = self._fetch_places()
        stamp = rospy.Time.now()

        arr = MarkerArray()

        clear = Marker()
        clear.header = Header(frame_id="map", stamp=stamp)
        clear.action = Marker.DELETEALL
        arr.markers.append(clear)

        for i, p in enumerate(places):
            arr.markers.append(_make_sphere(i, p, stamp, _NS))
            arr.markers.append(_make_arrow(i, p, stamp, _NS))
            arr.markers.append(_make_text(i, p, stamp, _NS))

        self._pub.publish(arr)
        if places:
            names = ", ".join(str(p.get("name", "?") for p in places[:5]))
            more = "" if len(places) <= 5 else f" (+{len(places) - 5})"
            rospy.logdebug(f"published {len(places)} places: {names}{more}")


def main() -> int:
    rospy.init_node("memory_markers_publisher")
    node = MemoryMarkersNode()
    try:
        rospy.spin()
    except rospy.ROSInterruptException:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
