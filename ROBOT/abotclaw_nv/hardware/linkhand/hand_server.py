"""
G1机器人端灵巧手控制Server - 增强版
用法：python hand_server.py [--port PORT] [--log LOG_FILE]
主机端通过TCP发送数字指令,本服务端执行对应动作。
"""
# 查询状态
# echo "s" | nc localhost 5678

# # 测试左手张开
# echo "1" | nc localhost 5678

# # 测试右手握拳
# echo "8" | nc localhost 5678
import socket
import threading
import sys
import time
import argparse
import json
import logging
import os
from datetime import datetime
from io import StringIO


current_dir = os.path.dirname(os.path.abspath(__file__))
project_root = os.path.abspath(os.path.join(current_dir, ".."))
sys.path.insert(0, project_root)

from LinkerHand.linker_hand_api import LinkerHandApi

JOINT_COUNT = 6
OPEN  = [255] * JOINT_COUNT
CLOSE = [0]   * JOINT_COUNT
HALF  = [128] * JOINT_COUNT

SAFETY_MARGIN = 0.85
O6_MAX_DEG = {
    "thumb_flex": 55,
    "finger_flex": 90,
    "thumb_abd": 88,
}

def _deg_to_cmd(deg, deg_max):
    if deg_max <= 0:
        return 255
    d = max(0.0, min(float(deg), float(deg_max)))
    return int(round(255.0 * (1.0 - d / float(deg_max))))

def _pose(thumb_flex_deg, thumb_abd_deg, f_flex_deg):
    return [
        _deg_to_cmd(thumb_flex_deg * SAFETY_MARGIN, O6_MAX_DEG["thumb_flex"]),
        _deg_to_cmd(thumb_abd_deg * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),
        _deg_to_cmd(f_flex_deg * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
        _deg_to_cmd(f_flex_deg * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
        _deg_to_cmd(f_flex_deg * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
        _deg_to_cmd(thumb_abd_deg * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),
    ]

PINCH = [
    _deg_to_cmd(35 * SAFETY_MARGIN, O6_MAX_DEG["thumb_flex"]),
    _deg_to_cmd(45 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),
    _deg_to_cmd(70 * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),
]

GRASP_BOTTLE_PRE = [
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["thumb_flex"]),     # 拇指弯曲
    _deg_to_cmd(85 * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),  # 拇指侧摆
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 食指弯曲
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 中指弯曲
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 无名指弯曲
    _deg_to_cmd(10 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),   # 小指弯曲
]
# 人类抓握瓶子(圆柱)：四指包裹、拇指参与对握、侧摆中等
GRASP_BOTTLE = [
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["thumb_flex"]),     # 拇指弯曲
    _deg_to_cmd(85 * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),  # 拇指侧摆
    _deg_to_cmd(75 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 食指弯曲
    _deg_to_cmd(70 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 中指弯曲
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 无名指弯曲
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),    # 小指弯曲
]

# 比耶手势 (Peace Sign)：大拇指弯曲+侧摆，食指/中指张开，无名指/小指微弯曲
PEACE = [
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["thumb_flex"]),     # 拇指弯曲 ~35°
    _deg_to_cmd(80 * SAFETY_MARGIN, O6_MAX_DEG["thumb_abd"]),  # 拇指侧摆 ~50°
    255,  # 食指弯曲 - 伸直
    255,  # 中指弯曲 - 伸直
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),  # 无名指弯曲 ~30°
    _deg_to_cmd(65 * SAFETY_MARGIN, O6_MAX_DEG["finger_flex"]),   # 小指弯曲 ~30°
]


TORQUE_OPEN_MOVE = [60, 70, 70, 70, 70, 50]
GRASP_TORQUE_HIGH_VEC = [60, 80, 80, 80, 80, 55]
GRASP_TORQUE_KEEP_VEC = [30, 45, 45, 45, 45, 25]

GRASP_CHECK_DELAY = 1.0
GRASP_TOLERANCE = 10
GRASP_SAMPLE_PERIOD = 0.2
GRASP_STILL_TOLERANCE = 2
GRASP_STILL_DURATION = 1.0
GRASP_MAX_WAIT = 3.0


def _check_can(iface):
    """检查 CAN 接口是否存在且 UP"""
    import subprocess
    try:
        result = subprocess.run(["ip", "link", "show", iface],
                                capture_output=True, text=True)
        return result.returncode == 0 and iface in result.stdout
    except Exception:
        return False


def setup_hands(left_can_iface=None, right_can_iface=None):
    """
    固定 CAN 接口绑定：can0=右手, can1=左手
    参数可覆盖默认绑定，支持单 CAN 接口场景。
    返回: (left_hand, right_hand, left_can_iface, right_can_iface)
    """
    global left_hand, right_hand

    left_can  = left_can_iface  or "can1"
    right_can = right_can_iface or "can0"

    log_info(f"CAN 接口绑定: can0={right_can} → 右手, can1={left_can} → 左手")

    left_ok  = _check_can(left_can)
    right_ok = _check_can(right_can)

    if not left_ok and not right_ok:
        log_error(f"CAN 接口均不可用 (left={left_can}, right={right_can})，请检查接线")
        return None, None, None, None

    # 只插了单 CAN，默认当左手用
    if left_can == right_can or (not right_ok and left_ok):
        log_info(f"单 CAN 模式，仅初始化左手 ({left_can})...")
        try:
            left_hand = LinkerHandApi(hand_type="left", hand_joint="O6", can=left_can)
            left_hand.set_speed([100] * JOINT_COUNT)
            left_hand.set_torque(TORQUE_OPEN_MOVE)
            log_info(f"  左手在 {left_can} 上初始化成功!")
            return left_hand, None, left_can, None
        except Exception as e:
            log_error(f"  左手在 {left_can} 上初始化失败: {e}")
            return None, None, None, None

    # 只插了左手 CAN
    if not right_ok:
        log_info(f"仅左手 CAN 可用 ({left_can})，跳过右手...")
        try:
            left_hand = LinkerHandApi(hand_type="left", hand_joint="O6", can=left_can)
            left_hand.set_speed([100] * JOINT_COUNT)
            left_hand.set_torque(TORQUE_OPEN_MOVE)
            log_info(f"  左手在 {left_can} 上初始化成功!")
            return left_hand, None, left_can, None
        except Exception as e:
            log_error(f"  左手在 {left_can} 上初始化失败: {e}")
            return None, None, None, None

    # 双 CAN，固定 can0=右手, can1=左手
    log_info("双 CAN 模式，初始化双手...")
    try:
        left_hand = LinkerHandApi(hand_type="left", hand_joint="O6", can=left_can)
        left_hand.set_speed([100] * JOINT_COUNT)
        left_hand.set_torque(TORQUE_OPEN_MOVE)
        log_info(f"  左手在 {left_can} 上初始化成功!")
    except Exception as e:
        log_error(f"  左手在 {left_can} 上初始化失败: {e}")
        left_hand = None

    try:
        right_hand = LinkerHandApi(hand_type="right", hand_joint="O6", can=right_can)
        right_hand.set_speed([100] * JOINT_COUNT)
        right_hand.set_torque(TORQUE_OPEN_MOVE)
        log_info(f"  右手在 {right_can} 上初始化成功!")
    except Exception as e:
        log_error(f"  右手在 {right_can} 上初始化失败: {e}")
        right_hand = None

    if left_hand is None and right_hand is None:
        log_error("双手初始化均失败，请检查 CAN 接线")
        return None, None, None, None

    return left_hand, right_hand, left_can, right_can


def init_hand(side, can):
    log_info(f"初始化 {side} 手 ({can})...")
    hand = LinkerHandApi(hand_type=side, hand_joint="O6", can=can)
    hand.set_speed([100] * JOINT_COUNT)
    hand.set_torque(TORQUE_OPEN_MOVE)
    log_info(f"  {side} 手初始化完成")
    return hand


def move(hand, name, positions, label):
    log_info(f"  [{name}] {label} -> {positions}")
    hand.finger_move(positions)
    time.sleep(1.5)


def soft_grasp(hand, name, target_positions, label="柔性抓取"):
    log_info(f"  [{name}] {label} -> {target_positions}")

    log_info(f"    [1/3] 初始抓取 (扭矩={GRASP_TORQUE_HIGH_VEC})")
    hand.set_torque(GRASP_TORQUE_HIGH_VEC)
    hand.finger_move(target_positions)

    def _is_reached(cur):
        return all(abs(cur[i] - target_positions[i]) <= GRASP_TOLERANCE for i in range(len(target_positions)))

    def _is_still(prev, cur):
        return all(abs(cur[i] - prev[i]) <= GRASP_STILL_TOLERANCE for i in range(len(target_positions)))

    log_info(f"    [2/3] 进入判定窗口 (初始延迟{GRASP_CHECK_DELAY}秒)...")
    time.sleep(GRASP_CHECK_DELAY)

    log_info(f"    [3/3] 判定到位/卡住：max_wait={GRASP_MAX_WAIT}s still={GRASP_STILL_DURATION}s")
    try:
        prev = hand.get_state()
        still_for = 0.0
        waited = 0.0

        while waited < GRASP_MAX_WAIT:
            time.sleep(GRASP_SAMPLE_PERIOD)
            waited += GRASP_SAMPLE_PERIOD

            cur = hand.get_state()

            if _is_reached(cur):
                log_info(f"      已到达目标,降低扭矩保持 (扭矩={GRASP_TORQUE_KEEP_VEC})")
                hand.set_torque(GRASP_TORQUE_KEEP_VEC)
                log_info(f"  [{name}] {label} 完成 (到位后柔性)")
                return

            if _is_still(prev, cur):
                still_for += GRASP_SAMPLE_PERIOD
            else:
                still_for = 0.0

            if still_for >= GRASP_STILL_DURATION:
                log_info(f"      未到位但已卡住不动 {still_for:.1f}s,启动柔性控制 (扭矩={GRASP_TORQUE_KEEP_VEC})")
                hand.set_torque(GRASP_TORQUE_KEEP_VEC)
                log_info(f"  [{name}] {label} 完成 (卡住后柔性)")
                return

            prev = cur

        log_info("      超时仍未到位/未卡住,保持初始抓取扭矩")
        log_info(f"  [{name}] {label} 完成")
    except Exception as e:
        log_info(f"  [{name}] 状态读取失败: {e},保持初始抓取扭矩")
        log_info(f"  [{name}] {label} 完成")


# 全局配置
HOST = '0.0.0.0'
DEFAULT_PORT = 5678
HEARTBEAT_TIMEOUT = 30  # 心跳超时(秒)
BUFFER_SIZE = 4096

# 全局状态
server_running = True
connected_clients = []
clients_lock = threading.Lock()
left_hand = None
right_hand = None
start_time = None


def setup_logging(log_file=None, console=True):
    """配置日志系统"""
    log_format = "%(asctime)s [%(levelname)s] %(message)s"
    handlers = []

    if console:
        handlers.append(logging.StreamHandler(sys.stdout))
    if log_file:
        handlers.append(logging.FileHandler(log_file, encoding='utf-8'))

    logging.basicConfig(
        level=logging.INFO,
        format=log_format,
        handlers=handlers
    )
    return logging.getLogger(__name__)


def log_info(msg):
    logging.info(msg)
    print(f"[Server] {msg}")


def log_error(msg):
    logging.error(msg)
    print(f"[ERROR] {msg}", file=sys.stderr)


def log_client(addr, msg):
    log_info(f"[{addr[0]}:{addr[1]}] {msg}")


def get_hand_state_json(hand, name):
    """获取手的JSON格式状态"""
    if hand is None:
        return {"hand": name, "status": "not_available"}
    try:
        state = hand.get_state()
        return {
            "hand": name,
            "status": "connected",
            "positions": state if isinstance(state, list) else list(state) if hasattr(state, '__iter__') else [state],
            "joint_count": JOINT_COUNT
        }
    except Exception as e:
        return {"hand": name, "status": "error", "message": str(e)}


def get_status_json():
    """获取完整状态JSON"""
    return {
        "timestamp": datetime.now().isoformat(),
        "uptime_seconds": int(time.time() - start_time) if start_time else 0,
        "connected_clients": len(connected_clients),
        "left_hand": get_hand_state_json(left_hand, "left"),
        "right_hand": get_hand_state_json(right_hand, "right"),
        "available_poses": {
            "open": OPEN,
            "close": CLOSE,
            "half": HALF,
            "pinch": PINCH,
            "grasp_bottle": GRASP_BOTTLE,
            "peace": PEACE
        }
    }


def execute_command(cmd, client_addr):
    """执行命令并返回响应"""
    cmd = cmd.strip().lower()

    # 左手控制命令 (1-6)
    if cmd == '1':
        if left_hand:
            move(left_hand, "左手", OPEN, "张开")
            return "OK: 左手张开"
        return "ERROR: 左手不可用"

    elif cmd == '2':
        if left_hand:
            move(left_hand, "左手", CLOSE, "握拳")
            return "OK: 左手握拳"
        return "ERROR: 左手不可用"

    elif cmd == '3':
        if left_hand:
            move(left_hand, "左手", HALF, "半握")
            return "OK: 左手半握"
        return "ERROR: 左手不可用"

    elif cmd == '4':
        if left_hand:
            move(left_hand, "左手", PINCH, "对指抓取")
            return "OK: 左手对指抓取"
        return "ERROR: 左手不可用"

    elif cmd == '5':
        if left_hand:
            move(left_hand, "左手", GRASP_BOTTLE, "抓握瓶子")
            return "OK: 左手抓握瓶子"
        return "ERROR: 左手不可用"

    elif cmd == '6':
        if left_hand:
            move(left_hand, "左手", PEACE, "比耶")
            return "OK: 左手比耶"
        return "ERROR: 左手不可用"

    # 右手控制命令 (7-12)
    elif cmd == '7':
        if right_hand:
            move(right_hand, "右手", OPEN, "张开")
            return "OK: 右手张开"
        return "ERROR: 右手不可用"

    elif cmd == '8':
        if right_hand:
            move(right_hand, "右手", CLOSE, "握拳")
            return "OK: 右手握拳"
        return "ERROR: 右手不可用"

    elif cmd == '9':
        if right_hand:
            move(right_hand, "右手", HALF, "半握")
            return "OK: 右手半握"
        return "ERROR: 右手不可用"

    elif cmd == '10':
        if right_hand:
            move(right_hand, "右手", PINCH, "对指抓取")
            return "OK: 右手对指抓取"
        return "ERROR: 右手不可用"

    elif cmd == '11':
        if right_hand:
            move(right_hand, "右手", GRASP_BOTTLE_PRE, "拇指外展")
            time.sleep(0.5)  # 等待到位
            move(right_hand, "右手", GRASP_BOTTLE, "抓握瓶子")
            return "OK: 右手抓握瓶子"
        return "ERROR: 右手不可用"

    elif cmd == '12':
        if right_hand:
            move(right_hand, "右手", PEACE, "比耶")
            return "OK: 右手比耶"
        return "ERROR: 右手不可用"

    # 双手控制命令 (13-18)
    elif cmd == '13':
        if left_hand: move(left_hand, "左手", OPEN, "张开")
        if right_hand: move(right_hand, "右手", OPEN, "张开")
        return "OK: 双手张开"

    elif cmd == '14':
        if left_hand: move(left_hand, "左手", CLOSE, "握拳")
        if right_hand: move(right_hand, "右手", CLOSE, "握拳")
        return "OK: 双手握拳"

    elif cmd == '15':
        if left_hand: move(left_hand, "左手", HALF, "半握")
        if right_hand: move(right_hand, "右手", HALF, "半握")
        return "OK: 双手半握"

    elif cmd == '16':
        if left_hand: move(left_hand, "左手", PINCH, "对指抓取")
        if right_hand: move(right_hand, "右手", PINCH, "对指抓取")
        return "OK: 双手对指抓取"

    elif cmd == '17':
        if left_hand: move(left_hand, "左手", GRASP_BOTTLE, "抓握瓶子")
        if right_hand: move(right_hand, "右手", GRASP_BOTTLE, "抓握瓶子")
        return "OK: 双手抓握瓶子"

    elif cmd == '18':
        if left_hand: move(left_hand, "左手", PEACE, "比耶")
        if right_hand: move(right_hand, "右手", PEACE, "比耶")
        return "OK: 双手比耶"

    # 特殊命令
    elif cmd == '19':
        alternate_open_close()
        return "OK: 双手交替开合完成"

    # 状态查询命令
    elif cmd == 's' or cmd == 'status':
        return json.dumps(get_status_json(), indent=2, ensure_ascii=False)

    elif cmd == 'q' or cmd == 'quit':
        global server_running
        server_running = False
        return "OK: 服务器关闭"

    # 帮助命令
    elif cmd == 'h' or cmd == 'help':
        return get_help_text()

    # 心跳
    elif cmd == 'ping':
        return "PONG"

    # 未知命令
    else:
        return f"ERROR: 未知命令 '{cmd}'"


def alternate_open_close():
    """双手交替开合"""
    log_info("开始交替开合测试...")
    for i in range(3):
        if left_hand: left_hand.finger_move(CLOSE)
        if right_hand: right_hand.finger_move(OPEN)
        time.sleep(1.0)
        if left_hand: left_hand.finger_move(OPEN)
        if right_hand: right_hand.finger_move(CLOSE)
        time.sleep(1.0)
    if left_hand: left_hand.finger_move(OPEN)
    if right_hand: right_hand.finger_move(OPEN)
    log_info("交替开合测试完成")


def get_help_text():
    """获取帮助文本"""
    return """\
=== 灵巧手控制Server帮助 ===

[控制命令]
1-6:   左手动作
       1-张开 2-握拳 3-半握 4-对指抓取 5-抓握瓶子 6-比耶
7-12:  右手动作
       7-张开 8-握拳 9-半握 10-对指抓取 11-抓握瓶子 12-比耶
13-18: 双手动作
       13-张开 14-握拳 15-半握 16-对指抓取 17-抓握瓶子 18-比耶

[特殊命令]
s:     查询状态(JSON格式)
h:     显示帮助
ping:  心跳测试
q:     关闭服务器

[示例]
  echo "1" | nc <robot_ip> 5678
  python -c "import socket; s=socket.socket(); s.connect(('192.168.1.100',5678)); s.send(b'1'); print(s.recv(1024)); s.close()"
"""


def handle_client(conn, addr, client_id):
    """处理客户端连接"""
    global connected_clients

    conn.settimeout(HEARTBEAT_TIMEOUT)

    with clients_lock:
        connected_clients.append(addr)

    log_client(addr, f"客户端#{client_id}连接")

    try:
        while server_running:
            try:
                data = conn.recv(BUFFER_SIZE)
                if not data:
                    break

                cmd = data.decode('utf-8').strip()
                log_client(addr, f"收到指令: {cmd}")

                response = execute_command(cmd, addr)
                conn.sendall(f"{response}\n".encode('utf-8'))
                log_client(addr, f"响应: {response[:50]}...")

                if cmd.lower() in ['q', 'quit']:
                    break

            except socket.timeout:
                log_client(addr, "心跳超时")
                break
            except ConnectionResetError:
                break
            except Exception as e:
                log_client(addr, f"处理异常: {e}")
                conn.sendall(f"ERROR: {str(e)}\n".encode('utf-8'))

    except Exception as e:
        log_client(addr, f"连接错误: {e}")
    finally:
        conn.close()
        with clients_lock:
            if addr in connected_clients:
                connected_clients.remove(addr)
        log_client(addr, f"客户端#{client_id}断开")


def accept_connections(server_socket):
    """接受客户端连接"""
    global server_running
    client_counter = 0

    log_info(f"TCP服务已启动,监听 {HOST}:{PORT}")
    log_info("等待客户端连接...")

    while server_running:
        try:
            server_socket.settimeout(1.0)
            try:
                conn, addr = server_socket.accept()
                client_counter += 1
                thread = threading.Thread(
                    target=handle_client,
                    args=(conn, addr, client_counter),
                    daemon=True
                )
                thread.start()
            except socket.timeout:
                continue
        except Exception as e:
            if server_running:
                log_error(f"接受连接失败: {e}")
            break

    log_info("停止接受新连接")


def signal_handler(signum, frame):
    """信号处理器"""
    global server_running
    log_info("收到退出信号,正在关闭...")
    server_running = False


def cleanup():
    """清理资源"""
    log_info("正在清理资源...")

    # 张开双手
    try:
        if left_hand:
            left_hand.finger_move(OPEN)
        if right_hand:
            right_hand.finger_move(OPEN)
        time.sleep(0.5)
    except Exception as e:
        log_error(f"清理手部状态失败: {e}")

    # 关闭CAN连接
    try:
        if left_hand:
            left_hand.close_can()
        if right_hand:
            right_hand.close_can()
    except Exception as e:
        log_error(f"清理CAN连接失败: {e}")

    log_info("清理完成,服务器退出")


def parse_args():
    """解析命令行参数"""
    parser = argparse.ArgumentParser(
        description='G1机器人端灵巧手控制Server',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=get_help_text()
    )
    parser.add_argument(
        '-p', '--port',
        type=int,
        default=DEFAULT_PORT,
        help=f'监听端口 (默认: {DEFAULT_PORT})'
    )
    parser.add_argument(
        '-l', '--log',
        type=str,
        default=None,
        help='日志文件路径'
    )
    parser.add_argument(
        '--no-console',
        action='store_true',
        help='禁用控制台输出'
    )
    parser.add_argument(
        '--left-can',
        type=str,
        default=None,
        help='左手 CAN 接口 (默认: can1)'
    )
    parser.add_argument(
        '--right-can',
        type=str,
        default=None,
        help='右手 CAN 接口 (默认: can0)'
    )
    return parser.parse_args()


def main():
    global left_hand, right_hand, start_time, PORT, server_running

    args = parse_args()
    PORT = args.port

    # 配置日志
    setup_logging(log_file=args.log, console=not args.no_console)

    log_info("=" * 50)
    log_info("G1灵巧手控制Server - 增强版")
    log_info("=" * 50)

    # 设置信号处理
    import signal
    signal.signal(signal.SIGINT, signal_handler)
    signal.signal(signal.SIGTERM, signal_handler)

    # 初始化灵巧手，固定 can0=左手, can1=右手
    log_info("初始化灵巧手 (固定: can0=左手, can1=右手)...")
    left_hand, right_hand, left_can, right_can = setup_hands(
        left_can_iface=args.left_can,
        right_can_iface=args.right_can
    )

    if left_hand is None and right_hand is None:
        log_error("灵巧手初始化失败")
        sys.exit(1)

    if left_hand:
        log_info(f"左手初始化成功 (CAN: {left_can})")
    if right_hand:
        log_info(f"右手初始化成功 (CAN: {right_can})")

    start_time = time.time()

    # 启动TCP服务
    try:
        server_socket = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        server_socket.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        server_socket.bind((HOST, PORT))
        server_socket.listen(5)

        accept_connections(server_socket)

    except OSError as e:
        log_error(f"无法绑定端口 {PORT}: {e}")
        sys.exit(1)
    finally:
        server_socket.close()
        cleanup()


if __name__ == "__main__":
    main()
