"""G1 TTS (Text-to-Speech) SDK.

Provides simple interface for G1 robot text-to-speech functionality.
"""

from __future__ import annotations

import os
from typing import Optional

__all__ = ["TTSClient", "tts_speak"]

# Default network interface for DDS communication
_DEFAULT_IFACE = os.environ.get(
    "G1_ARM_NETWORK_IFACE",
    os.environ.get("UNITREE_IFACE", os.environ.get("G1_NETWORK_INTERFACE", "enp4s0")),
)


class TTSClient:
    """G1 Text-to-Speech client wrapper.
    
    **使用示例**::
    
        from robot_sdk.tts_sdk import TTSClient
        
        # 初始化（必须先调用）
        tts = TTSClient()
        tts.initialize()  # enable_loco=True 默认会激活 G1 服务
        
        # 播报语音
        tts.speak("你好，我是 G1 机器人")
        
        # 调节音量
        tts.set_volume(80)
        volume = tts.get_volume()
        print(f"当前音量：{volume}")
        
        # 清理资源
        tts.close()
    """

    def __init__(self, iface: str = _DEFAULT_IFACE, timeout: float = 10.0):
        """Initialize TTS client.

        Args:
            iface: Network interface for DDS communication.
            timeout: Timeout in seconds for operations.
        """
        self._iface = iface
        self._timeout = timeout
        self._client = None
        self._loco_client = None
        self._initialized = False
        self._dds_initialized = False

    def initialize(self, enable_loco: bool = True) -> bool:
        """Initialize the audio client. Must be called before other methods.

        Args:
            enable_loco: Also initialize LocoClient to activate G1 services.
        """
        try:
            from unitree_sdk2py.core.channel import ChannelFactoryInitialize
            from unitree_sdk2py.g1.audio.g1_audio_client import AudioClient

            ChannelFactoryInitialize(0, self._iface)
            self._dds_initialized = True

            # Initialize LocoClient first to activate G1 services (required for TTS)
            if enable_loco:
                try:
                    from unitree_sdk2py.g1.loco.g1_loco_client import LocoClient
                    self._loco_client = LocoClient()
                    self._loco_client.SetTimeout(self._timeout)
                    self._loco_client.Init()
                    print("[G1TTSClient] LocoClient initialized to activate G1 services")
                except Exception as e:
                    print(f"[G1TTSClient] LocoClient init warning: {e}")
                    self._loco_client = None

            self._client = AudioClient()
            self._client.SetTimeout(self._timeout)
            self._client.Init()
            self._initialized = True
            return True
        except Exception as e:
            print(f"[G1TTSClient] Initialization failed: {e}")
            self._cleanup()
            return False

    def _check_init(self) -> bool:
        if not self._initialized:
            print("[G1TTSClient] Not initialized. Call initialize() first.")
        return self._initialized

    def _cleanup(self) -> None:
        """清理所有 DDS 资源。"""
        # AudioClient 和 LocoClient 没有 Close 方法，置 None 即可
        self._client = None
        self._loco_client = None
        self._initialized = False
        # DDS ChannelFactory 是全局单例，无法主动释放

    def close(self) -> None:
        """释放 TTS 客户端资源。"""
        self._cleanup()

    def __enter__(self):
        """上下文管理器入口。"""
        return self

    def __exit__(self, exc_type, exc_val, exc_tb):
        """上下文管理器出口，确保资源被正确清理。"""
        self.close()
        return False

    def __del__(self):
        """析构函数，防止资源泄漏。"""
        if self._initialized:
            try:
                self._cleanup()
            except Exception:
                pass


    def speak(self, text: str, speaker_id: int = 0) -> bool:
        """Speak the given text using TTS.

        Args:
            text: Text to speak (Chinese supported).
            speaker_id: Speaker ID (default 0).

        Returns:
            True if successful, False otherwise.
        """
        if not self._check_init():
            return False
        try:
            self._client.TtsMaker(text, speaker_id)
            return True
        except Exception as e:
            print(f"[G1TTSClient] TTS failed: {e}")
            return False

    def set_volume(self, volume: int) -> bool:
        """Set speaker volume.

        Args:
            volume: Volume level (0-100).

        Returns:
            True if successful, False otherwise.
        """
        if not self._check_init():
            return False
        try:
            self._client.SetVolume(volume)
            return True
        except Exception as e:
            print(f"[G1TTSClient] Set volume failed: {e}")
            return False

    def get_volume(self) -> Optional[int]:
        """Get current speaker volume.

        Returns:
            Volume level (0-100) or None on failure.
        """
        if not self._check_init():
            return None
        try:
            result = self._client.GetVolume()
            # GetVolume returns (status_code, {'volume': value})
            if isinstance(result, tuple) and len(result) >= 2:
                return result[1].get('volume', None)
            return result
        except Exception as e:
            print(f"[G1TTSClient] Get volume failed: {e}")
            return None


def tts_speak(text: str, iface: str = _DEFAULT_IFACE, speaker_id: int = 0) -> bool:
    """Convenience function: speak text and release resources.

    Args:
        text: Text to speak.
        iface: Network interface.
        speaker_id: Speaker ID.

    Returns:
        True if successful.
    """
    with TTSClient(iface=iface) as client:
        if not client.initialize():
            return False
        return client.speak(text, speaker_id)
