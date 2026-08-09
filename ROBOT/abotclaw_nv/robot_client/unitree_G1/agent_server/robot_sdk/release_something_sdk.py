"""G1 通用放置/松手 SDK。

对外仅暴露 ``release_something`` 一个接口，封装以下完整流程：

1. 执行放置/松手序列（home → lift → 固定放置位 → 灵巧手松开 → 回撤）
2. 结束后回到 home 位并释放控制权
"""

from __future__ import annotations

from typing import Optional

from robot_sdk.config import get_g1_robot_ip
from robot_sdk.g1_grasp_sdk import release_object

__all__ = ["release_something"]


def release_something(
    *,
    robot_ip: Optional[str] = None,
) -> bool:
    """执行放置/松手序列。

    Args:
        robot_ip: 灵巧手所在机器人 IP。

    Returns:
        完整流程是否成功。
    """
    print("=== 开始执行 G1 放置任务 ===")

    try:
        print("\n[Step 1-7] 正在执行机械臂放置序列...")
        success = release_object(robot_ip=robot_ip or get_g1_robot_ip())

        if success:
            print("✅ 放置序列执行成功！")
        else:
            print("❌ 放置序列执行失败。")
        return success
    except Exception as e:
        print(f"❌ 任务执行出错: {e}")
        return False


if __name__ == "__main__":
    raise SystemExit(0 if release_something() else 1)
