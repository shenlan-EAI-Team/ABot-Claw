"""宇树 G1 语音 SDK — 基于 DDS RPC 的完整语音控制封装。

通信链路拆解
============
1. **DDS 网卡绑定** — ``ChannelFactoryInitialize(domain, iface)`` 绑定到本机 ``enp4s0``
   网卡，CycloneDDS 在该网卡上进行参与者发现，自动找到同网段内机器人 DDS 节点。
2. **RPC 客户端创建** — ``AudioClient`` → ``ClientStub`` 内部创建：
   - 发送 Channel（DDS DataWriter）: ``rt/api/voice/request``
   - 接收 Channel（DDS DataReader）: ``rt/api/voice/response``
3. **TTS 请求发送** — ``TtsMaker`` 把文字和参数打包成 JSON，封装成 DDS ``Request_``
   消息，通过 DataWriter 发出。
4. **机器人端执行** — 机器人板载 voice 服务进程的 DataReader 收到 Request，
   将文字送入板载 TTS 语音合成引擎播报。

使用示例
========
::

    import os
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

    from robot_sdk.voice_sdk import VoiceSDK

    voice = VoiceSDK()
    voice.initialize()

    voice.speak("你好，我是深蓝机器人")
    voice.set_volume(100)
    print(f"当前音量: {voice.get_volume()}")
    voice.set_led(0, 255, 0)
    voice.close()

也可以用上下文管理器::

    with VoiceSDK() as voice:
        voice.speak("开始巡检")
"""

from __future__ import annotations

import json
import logging
import os
import types
import threading
import time
from typing import Optional

__all__ = [
    "VoiceSDK",
    "quick_speak",
    "VOICE_API",
]

logger = logging.getLogger("voice_sdk")

_DEFAULT_IFACE = os.environ.get(
    "G1_NETWORK_INTERFACE",
    os.environ.get("G1_ARM_NETWORK_IFACE", os.environ.get("UNITREE_IFACE", "enp4s0")),
)

class VOICE_API:
    """机器人端 voice 服务的 API ID 常量。"""
    SERVICE_NAME = "voice"
    TTS         = 1001
    ASR         = 1002
    PLAY_START  = 1003
    PLAY_STOP   = 1004
    GET_VOLUME  = 1005
    SET_VOLUME  = 1006
    SET_RGB_LED = 1010


class _DDSContext:
    """DDS 全局初始化守卫（进程级单例）。"""
    _lock = threading.Lock()
    _initialized = False

    @classmethod
    def ensure_init(cls, domain_id: int, iface: str) -> None:
        if cls._initialized:
            return
        with cls._lock:
            if cls._initialized:
                return
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            ChannelFactoryInitialize(domain_id, iface)
            cls._initialized = True
            logger.info("DDS ChannelFactory 已绑定网卡 %s (domain=%d)", iface, domain_id)


class VoiceSDK:
    """宇树 G1 语音控制 SDK。

    封装 DDS RPC 通信全流程：网卡绑定 → RPC 客户端 → 语音指令收发。

    Parameters
    ----------
    iface : str
        DDS 通信绑定的网卡名，默认 ``enp4s0``。
    domain_id : int
        DDS Domain ID，默认 0。
    timeout : float
        RPC 调用超时（秒），默认 10。
    enable_loco : bool
        是否同时初始化 LocoClient 以激活 G1 服务（TTS 依赖此前置）。
    """

    def __init__(
        self,
        iface: str = _DEFAULT_IFACE,
        domain_id: int = 0,
        timeout: float = 10.0,
        enable_loco: bool = True,
    ):
        self._iface = iface
        self._domain_id = domain_id
        self._timeout = timeout
        self._enable_loco = enable_loco

        self._audio_client = None
        self._loco_client = None
        self._initialized = False

    # ---- 生命周期 ----

    def initialize(self) -> bool:
        """完整初始化：绑定网卡 → 激活服务 → 创建 AudioClient。"""
        if self._initialized:
            return True
        try:
            _DDSContext.ensure_init(self._domain_id, self._iface)
            if self._enable_loco:
                self._init_loco()
            self._init_audio()
            self._initialized = True
            logger.info("VoiceSDK 初始化完成")
            return True
        except Exception:
            logger.exception("VoiceSDK 初始化失败")
            self._cleanup()
            return False

    def _init_loco(self) -> None:
        from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
        self._loco_client = LocoClient()
        self._loco_client.SetTimeout(self._timeout)
        self._loco_client.Init()
        logger.info("LocoClient 已激活 G1 服务")

    def _init_audio(self) -> None:
        from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient
        self._audio_client = AudioClient()
        self._patch_tts_index(self._audio_client)
        self._audio_client.SetTimeout(self._timeout)
        self._audio_client.Init()
        logger.info("AudioClient RPC 通道就绪")

    @staticmethod
    def _patch_tts_index(audio_client) -> None:
        """Keep Python AudioClient behavior aligned with the upstream C++ SDK."""
        def tts_maker(client, text: str, speaker_id: int) -> int:
            client.tts_index = getattr(client, "tts_index", 0) + 1
            payload = {
                "index": client.tts_index,
                "text": text,
                "speaker_id": speaker_id,
            }
            code, _ = client._Call(VOICE_API.TTS, json.dumps(payload, ensure_ascii=False))
            return code

        audio_client.TtsMaker = types.MethodType(tts_maker, audio_client)

    def _cleanup(self) -> None:
        self._audio_client = None
        self._loco_client = None
        self._initialized = False

    def close(self) -> None:
        """释放资源。"""
        self._cleanup()

    def __enter__(self):
        self.initialize()
        return self

    def __exit__(self, *exc):
        self.close()
        return False

    def __del__(self):
        if self._initialized:
            try:
                self._cleanup()
            except Exception:
                pass

    def _require_init(self) -> None:
        if not self._initialized:
            raise RuntimeError("VoiceSDK 未初始化，请先调用 initialize()")

    # ---- TTS 语音播报 ----

    def speak(self, text: str, speaker_id: int = 0) -> int:
        """文字转语音播报。

        将文字和参数打包成 JSON，封装为 DDS Request 发送到机器人
        板载 voice 服务，由 TTS 引擎合成并播放。

        Returns: 0 成功，非 0 为错误码。
        """
        self._require_init()
        code = self._audio_client.TtsMaker(text, speaker_id)
        if code != 0:
            logger.warning("TTS 播报失败 code=%d text=%r", code, text[:50])
        return code

    # ---- 音量控制 ----

    def get_volume(self) -> Optional[int]:
        """获取当前音量 (0-100)，失败返回 None。"""
        self._require_init()
        code, data = self._audio_client.GetVolume()
        if code == 0 and isinstance(data, dict):
            return data.get("volume")
        logger.warning("获取音量失败 code=%d", code)
        return None

    def set_volume(self, volume: int) -> int:
        """设置音量 (0-100)。Returns: 0 成功。"""
        self._require_init()
        volume = max(0, min(100, volume))
        code = self._audio_client.SetVolume(volume)
        if code != 0:
            logger.warning("设置音量失败 code=%d", code)
        return code

    # ---- LED 控制 ----

    def set_led(self, r: int, g: int, b: int) -> int:
        """设置头部 RGB LED 颜色 (0-255)。Returns: 0 成功。"""
        self._require_init()
        code = self._audio_client.LedControl(r, g, b)
        if code != 0:
            logger.warning("LED 设置失败 code=%d", code)
        return code

    # ---- PCM 音频流 ----

    def play_stream(self, pcm_data: bytes, app_name: str = "voice_sdk", stream_id: str = "0") -> int:
        """向机器人推送 PCM 音频流。Returns: 0 成功。"""
        self._require_init()
        code, _ = self._audio_client.PlayStream(app_name, stream_id, pcm_data)
        return code

    def play_stop(self, app_name: str = "voice_sdk") -> int:
        """停止音频流播放。"""
        self._require_init()
        return self._audio_client.PlayStop(app_name)


def quick_speak(text: str, iface: str = _DEFAULT_IFACE, speaker_id: int = 0) -> int:
    """一行调用：初始化 → 播报 → 释放。"""
    with VoiceSDK(iface=iface) as v:
        return v.speak(text, speaker_id)
