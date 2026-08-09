#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
单独控制灵巧手每个关节的位置，便于手动观察对应关系。

示例：
  python manual_joint_control.py --hand_type left --hand_joint O6 --pose 0 255 255 255 255 255
  python manual_joint_control.py --hand_type left --hand_joint L10 --pose 255 0 255 255 255 255 255 255 255 255
"""

import argparse
import time

from LinkerHand.linker_hand_api import LinkerHandApi


def parse_args():
    parser = argparse.ArgumentParser(description="手动控制灵巧手每个关节的位置")
    parser.add_argument("--hand_type", choices=["left", "right"], default="left", help="灵巧手类型")
    parser.add_argument("--hand_joint", default="O6", help="灵巧手型号，例如 O6 或 L10")
    parser.add_argument("--can", default="can0", help="CAN 接口")
    parser.add_argument("--speed", type=int, nargs="*", default=None, help="速度参数，按手型长度填写")
    parser.add_argument("--pose", type=int, nargs="+", required=True, help="关节位置值，范围 0~255")
    parser.add_argument("--hold", type=float, default=2.0, help="保持时间（秒）")
    parser.add_argument("--return_pose", type=int, nargs="*", default=None, help="退出前恢复的位置")
    return parser.parse_args()


def main():
    args = parse_args()
    hand = LinkerHandApi(hand_type=args.hand_type, hand_joint=args.hand_joint, can=args.can)

    try:
        if args.speed is not None:
            hand.set_speed(args.speed)

        print(f"发送 pose: {args.pose}")
        hand.finger_move(pose=args.pose)
        time.sleep(args.hold)

    finally:
        if args.return_pose is not None:
            print(f"恢复 pose: {args.return_pose}")
            hand.finger_move(pose=args.return_pose)
            time.sleep(1)
        hand.close_can()


if __name__ == "__main__":
    main()
