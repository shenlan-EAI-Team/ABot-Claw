"""G1 ``/code/execute`` 子进程运行时：与 Piper 对齐，把硬件注入放在 ``robot_sdk``。

由 ``code_executor`` 生成的临时脚本在设置 ``sys.path`` 后调用：

  ``install_executor_runtime(globals())``
  … 用户代码 …
  ``shutdown_executor_runtime()``

注入内容见 ``G1RobotEnv.install_into_globals``：含 ``env``（``from_config()``）、``Nav2Anywhere``（ROS2 导航）及 ``camera`` / ``yolo`` 等兼容别名。
"""

from __future__ import annotations

import json
from typing import Any, Dict

# 单进程单次执行
_runtime: Dict[str, Any] = {}


def install_executor_runtime(g: Dict[str, Any]) -> None:
    """构造 ``G1RobotEnv.from_config()`` 并写入 ``g``（一般为 ``globals()``）。

    网络参数来自 ``robot_sdk/config.yaml`` 的 ``g1`` 段（及 ``ROBOT_SDK_CONFIG``），
    与 Piper 子进程读 yaml 的方式一致，不在生成脚本里硬编码 IP。
    """
    from .g1_robot_env import G1RobotEnv

    _runtime.clear()
    env = G1RobotEnv.from_config()
    env.install_into_globals(g)
    _runtime["env"] = env


def print_exec_result_if_any() -> None:
    """若存在全局 ``result``，按与历史 wrapper 相同格式打印。"""
    import __main__ as _m

    if "result" not in dir(_m):
        return
    try:
        r = getattr(_m, "result", None)
        print(f"__EXEC_RESULT__: {json.dumps(r, default=str)}")
    except Exception as e:
        print(f"__EXEC_RESULT_ERROR__: Failed to serialize result: {e}")


def shutdown_executor_runtime() -> None:
    """关闭 ``G1RobotEnv`` 持有的资源。"""
    env = _runtime.pop("env", None)
    if env is not None:
        try:
            env.shutdown()
        except Exception:
            pass
    _runtime.clear()
