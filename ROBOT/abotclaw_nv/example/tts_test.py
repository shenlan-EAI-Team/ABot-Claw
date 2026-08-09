import os
import sys

# robot_sdk 在 agent_server/robot_sdk/，需要把 agent_server 加入路径
# example/tts_test.py -> abotclaw_nv/ -> robot_client/unitree_G1/agent_server
_repo_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
_agent_server = os.path.join(_repo_root, "robot_client", "unitree_G1", "agent_server")
sys.path.insert(0, _agent_server)

from robot_sdk.voice_sdk import VoiceSDK

voice = VoiceSDK()
voice.initialize()

voice.speak("你好，我是深蓝机器人，欢迎来到深蓝学院")
voice.set_volume(100)
print(f"当前音量: {voice.get_volume()}")
voice.set_led(0, 255, 0)
voice.close()