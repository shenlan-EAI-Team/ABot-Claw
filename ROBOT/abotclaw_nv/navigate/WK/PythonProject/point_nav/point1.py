#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import rospy
import math
import tf.transformations as tft
from move_base_msgs.msg import MoveBaseActionFeedback
from geometry_msgs.msg import PoseStamped


class NavPointPlayer:
    def __init__(self, target_x, target_y, target_theta, threshold=0.5):
        self.target_x = target_x
        self.target_y = target_y
        self.target_theta = target_theta
        self.threshold = threshold
        self.reached = False

        # 发布导航目标（PoseStamped 简单模式）
        self.goal_pub = rospy.Publisher("/move_base_simple/goal", PoseStamped, queue_size=10)
        # 订阅 /move_base/feedback 获取当前位姿
        self.feedback_sub = rospy.Subscriber("/move_base/feedback", MoveBaseActionFeedback, self.feedback_callback)

        rospy.sleep(1.0)
        for _ in range(3):
            self.publish_goal()
            rospy.sleep(0.5)

    def publish_goal(self):
        pose = PoseStamped()
        pose.header.frame_id = "map"
        pose.header.stamp = rospy.Time.now()
        pose.pose.position.x = self.target_x
        pose.pose.position.y = self.target_y
        pose.pose.position.z = 0.0
        q = tft.quaternion_from_euler(0, 0, self.target_theta)
        pose.pose.orientation.x = q[0]
        pose.pose.orientation.y = q[1]
        pose.pose.orientation.z = q[2]
        pose.pose.orientation.w = q[3]

        rospy.loginfo(f"[导航目标] x={self.target_x}, y={self.target_y}, θ={self.target_theta}")
        self.goal_pub.publish(pose)

    def feedback_callback(self, msg):
        if self.reached:
            return

        current_pose = msg.feedback.base_position.pose
        dx = current_pose.position.x - self.target_x
        dy = current_pose.position.y - self.target_y
        dist = math.hypot(dx, dy)  # 欧氏距离

        rospy.loginfo_throttle(2, f"[当前位置] ({current_pose.position.x:.2f}, {current_pose.position.y:.2f}) -> 距离目标 {dist:.2f} m")

        if dist <= self.threshold:
            rospy.loginfo("[到达目标]")
            self.reached = True
            rospy.signal_shutdown("任务完成")

if __name__ == "__main__":
    rospy.init_node("nav_point_player")

    target_x = 0.0
    target_y = 0.0
    target_theta = 0.0
    threshold = 0.4

    node = NavPointPlayer(target_x, target_y, target_theta, threshold)
    rospy.spin()
