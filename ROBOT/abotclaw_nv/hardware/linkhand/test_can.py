#!/usr/bin/env python3
"""CAN诊断工具 - 测试与灵巧手的通信"""
import can
import time
import sys

def test_can_communication(channel='can0', can_id=0x27):
    """测试CAN通信"""
    print(f"=== CAN 通信诊断 ===")
    print(f"通道: {channel}")
    print(f"CAN ID: {hex(can_id)} (右手)")
    
    try:
        bus = can.interface.Bus(channel=channel, interface='socketcan', bitrate=1000000)
        print("✓ CAN总线连接成功")
    except Exception as e:
        print(f"✗ CAN总线连接失败: {e}")
        return False
    
    # 发送一个查询帧 (0x01 = 查询位置)
    test_data = [0x01, 0, 0, 0, 0, 0, 0, 0]
    msg = can.Message(arbitration_id=can_id, data=test_data, is_extended_id=False)
    
    print(f"\n发送测试帧: ID={hex(can_id)} Data={[hex(x) for x in test_data]}")
    try:
        bus.send(msg)
        print("✓ 发送成功")
    except Exception as e:
        print(f"✗ 发送失败: {e}")
        return False
    
    # 等待响应
    print("\n等待响应 (5秒)...")
    for i in range(50):  # 5秒内最多50次尝试
        try:
            msg = bus.recv(timeout=0.1)
            if msg:
                print(f"✓ 收到响应: ID={hex(msg.arbitration_id)} Data={[hex(x) for x in msg.data]}")
                return True
        except Exception as e:
            print(f"✗ 接收错误: {e}")
            return False
    
    print("✗ 未收到响应")
    return False

if __name__ == "__main__":
    channel = sys.argv[1] if len(sys.argv) > 1 else 'can0'
    can_id = int(sys.argv[2], 16) if len(sys.argv) > 2 else 0x27
    
    success = test_can_communication(channel, can_id)
    sys.exit(0 if success else 1)
