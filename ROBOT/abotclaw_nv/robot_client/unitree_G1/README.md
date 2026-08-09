# Unitree G1 Robot Client

This directory contains the agent server for Unitree G1 humanoid robot.

## Architecture

The G1 agent server follows the same pattern as `arm_piper` but uses **Unitree SDK2** instead of ROS:

```
unitree_G1/
├── agent_server/
│   ├── server.py              # FastAPI main server
│   ├── config.py              # Configuration (services, safety, timing)
│   ├── services.py            # Service manager for backend processes
│   ├── state.py               # State aggregator for robot telemetry
│   ├── lease.py               # Lease management for code execution
│   ├── code_executor.py       # Code execution in isolated subprocess
│   ├── auth.py                # API key authentication
│   ├── safety.py              # Safety checks
│   ├── robot_sdk/
│   │   ├── g1_sdk.py          # G1RobotEnv - main robot interface
│   │   ├── config.py          # G1-specific constants
│   │   └── __init__.py
│   ├── routes/
│   │   ├── sdk_docs.py        # Auto-generated SDK documentation
│   │   ├── system_guide.py    # Getting started guide
│   │   └── ...                # Other route modules
│   └── start_server.sh        # Startup script
```

## Key Differences from Piper

| Aspect | Piper | G1 |
|--------|-------|-----|
| **Middleware** | ROS Noetic + MoveIt | Unitree SDK2 |
| **Control** | Joint/cartesian commands | Whole-body + joint commands |
| **Mobility** | Fixed base | Walking, balance control |
| **Arm DOF** | 6 joints + gripper | 8 joints (4 per arm) |
| **Network** | Local ROS | Ethernet (enp4s0) |

## Quick Start

### 1. Install Dependencies

```bash
cd agent_server
pip install -r requirements.txt

# Install Unitree SDK2 (separately)
pip install unitree_sdk2py
```

### 2. Configure Network

Ensure G1 robot is connected via Ethernet and on the same subnet.

Network interface: `enp4s0`

### 3. Start Server

```bash
# Hardware mode
./start_server.sh

# Dry-run is not implemented for this G1 server; it always runs hardware mode.

# Custom port
G1_PORT=8001 ./start_server.sh
```

Or run directly:

```bash
cd /home/szm/packages/ABot-Claw-main/robot_client/unitree_G1/agent_server
source /opt/ros/humble/setup.bash   # omit if you do not use ROS2-backed features (e.g. navigation_sdk)
/usr/bin/python3 server.py        # system Python avoids conda envs missing uvicorn/fastapi
```

### 4. Verify Connection

```bash
# Check health
curl http://localhost:8888/health

# Check state
curl http://localhost:8888/state

# View SDK docs
curl http://localhost:8888/code/sdk/markdown
```

## G1RobotEnv API

### Whole-Body Control

```python
# Stand up
env.stand()

# Walk
env.walk(vx=0.3, vy=0.0, vyaw=0.0)  # m/s, m/s, rad/s
env.stop_movement()

# Body height
env.set_body_height(0.65)  # meters

# Sit down
env.sit()
```

### Arm Control

```python
# Get state
state = env.get_arm_state()
print(state.position)   # 8 joint angles
print(state.velocity)   # 8 joint velocities
print(state.effort)     # 8 joint torques

# Move joints
joint_targets = [
    0.5, 0.0, 0.0, -1.0,   # left arm
    0.5, 0.0, 0.0, -1.0    # right arm
]
env.move_arm_joints(joint_targets, duration=2.0)
```

### State Observation

```python
# Body state
body = env.get_body_state()
print(body.position)        # [x, y, z]
print(body.orientation)     # [qx, qy, qz, qw]
print(body.velocity)        # [vx, vy, vz]

# IMU
imu = env.get_imu()
print(imu['acceleration'])
print(imu['angular_velocity'])
```

### Safety

```python
# Emergency stop
env.emergency_stop()
```

## Code Execution

Submit Python code via HTTP:

```bash
curl -X POST http://localhost:8888/code/execute \
  -H "Content-Type: application/json" \
  -d '{
    "code": "env.stand(); env.walk(0.3, 0, 0); time.sleep(2); env.stop_movement()",
    "execution_id": "test_001"
  }'
```

Pre-created instances in execution context:
- `env` - G1RobotEnv
- `yolo` - YoloSDK
- `memory` - MemorySDK

## Safety Limits

| Parameter | Value |
|-----------|-------|
| Max forward velocity | 1.0 m/s |
| Max lateral velocity | 0.5 m/s |
| Max rotation rate | 1.0 rad/s |
| Body height range | 0.5 - 0.8 m |
| Arm joint velocity | 3.0 rad/s |

## Troubleshooting

### "channel factory init error"

- Check G1 is powered on
- Verify Ethernet connection
- Check network interface (enp4s0)

### "sport_ready: false"

- G1 locomotion module not ready
- Check for fault states on robot

### "arm_ready: false"

- G1 arm module not ready
- Check arm power and communication

## License

Same as parent project.
