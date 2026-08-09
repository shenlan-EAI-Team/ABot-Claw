# ABot-Claw

An open-source humanoid robot control platform built on Unitree G1, featuring AI agent integration, whole-body motion control, navigation, and dexterous manipulation.

## Overview

ABot-Claw bridges high-level AI agent commands to physical robot execution on the Unitree G1 humanoid platform. It provides a FastAPI-based agent server that exposes a Python SDK for robot control, code execution, and real-time state observation.

The system integrates:

- **Agent Server** - FastAPI HTTP server with code execution, lease management, and robot control APIs
- **Whole-Body Control** - Unitree SDK2 with standing, walking, and body height control
- **Arm Control** - 8-DOF dual-arm manipulation with joint velocity limits and position control
- **Perception** - YOLO object detection, RealSense cameras, and spatial memory
- **Navigation** - ROS Noetic stack with FAST-LIO2 3D SLAM, TEB planner, and Livox MID360 LiDAR

## Architecture

```
abotclaw_robot_final/
├── ROBOT/                       # Robot-side runtime code
│   └── abotclaw_nv/
│       ├── robot_client/        # Agent server & robot SDK
│       │   └── unitree_G1/      # G1 humanoid client
│       ├── navigate/            # ROS navigation stack (FAST-LIO2, Nav2)
│       └── unitree_sdk2_python/ # Unitree SDK2 bindings
├── PC/                          # PC-side services (perception, reasoning)
└── Services/                    # AI service integrations
```

## Quick Start

### Prerequisites

- Unitree G1 humanoid robot
- Ubuntu 20.04 with ROS Noetic (for navigation)
- Python 3.8+
- Ethernet connection to robot

### Installation

```bash
# Install robot client dependencies
cd ROBOT/abotclaw_nv/robot_client/unitree_G1/agent_server
pip install -r requirements.txt

# Install Unitree SDK2
pip install unitree_sdk2py
```

### Configuration

Ensure the G1 robot is connected via Ethernet (`enp4s0`) and on the same subnet.

### Running the Agent Server

```bash
cd ROBOT/abotclaw_nv/robot_client/unitree_G1/agent_server
./start_server.sh
```

Or with a custom port:

```bash
G1_PORT=8001 ./start_server.sh
```

### Verify Connection

```bash
curl http://localhost:8888/health
curl http://localhost:8888/state
curl http://localhost:8888/code/sdk/markdown
```

## SDK Usage

The agent server exposes a `G1RobotEnv` interface:

```python
# Stand up
env.stand()

# Walk (forward velocity, lateral velocity, rotation)
env.walk(vx=0.3, vy=0.0, vyaw=0.0)
env.stop_movement()

# Body height control
env.set_body_height(0.65)

# Sit down
env.sit()

# Dual-arm joint control (8 joints per arm)
joint_targets = [0.5, 0.0, 0.0, -1.0, 0.5, 0.0, 0.0, -1.0]
env.move_arm_joints(joint_targets, duration=2.0)

# State observation
body = env.get_body_state()
imu = env.get_imu()
arm_state = env.get_arm_state()

# Emergency stop
env.emergency_stop()
```

### Code Execution via HTTP

```bash
curl -X POST http://localhost:8888/code/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "env.stand(); env.walk(0.3, 0, 0); time.sleep(2); env.stop_movement()",
    "execution_id": "test_001"
  }'
```

Pre-created instances available in execution context:
- `env` - G1RobotEnv
- `yolo` - YoloSDK
- `memory` - MemorySDK

## Safety Limits

| Parameter | Limit |
|-----------|-------|
| Max forward velocity | 1.0 m/s |
| Max lateral velocity | 0.5 m/s |
| Max rotation rate | 1.0 rad/s |
| Body height range | 0.5 - 0.8 m |
| Arm joint velocity | 3.0 rad/s |

## Navigation Stack

The navigation subsystem uses:

- **FAST-LIO2** - LiDAR-based 3D SLAM (Livox MID360)
- **Nav2** - ROS2 navigation framework with TEB planner
- **CycloneDDS** - DDS middleware for ROS2 communication

See `ROBOT/abotclaw_nv/navigate/` for setup details.

## Troubleshooting

| Symptom | Solution |
|---------|----------|
| `channel factory init error` | Check G1 power, Ethernet connection, and network interface |
| `sport_ready: false` | G1 locomotion module not ready; check for fault states on robot |
| `arm_ready: false` | Check arm power and communication |

## License

This project is licensed under **CC BY-NC-SA 4.0** (Creative Commons Attribution-NonCommercial-ShareAlike 4.0 International).

You are free to:
- **Share** — copy and redistribute the material in any medium or format
- **Adapt** — remix, transform, and build upon the material

Under the following terms:
- **Attribution** — You must give appropriate credit, provide a link to the license, and indicate if changes were made.
- **NonCommercial** — You may not use the material for **commercial purposes**.
- **ShareAlike** — If you remix or transform the material, you must distribute your contributions under the same license.

For the full license text, see the [LICENSE](LICENSE) file.

---

*ABot-Claw is developed for research and educational purposes. Use at your own risk.*
