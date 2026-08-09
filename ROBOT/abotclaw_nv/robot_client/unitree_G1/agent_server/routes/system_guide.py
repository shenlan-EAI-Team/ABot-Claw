"""System guide endpoint for G1 robot."""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import HTMLResponse

router = APIRouter(prefix="/docs", tags=["docs"])


GUIDE_HTML = """<!DOCTYPE html>
<html>
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Unitree G1 Getting Started Guide</title>
<style>
  body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #24292e; }
  h1 { border-bottom: 2px solid #e1e4e8; padding-bottom: 0.3em; }
  h2 { border-bottom: 1px solid #e1e4e8; padding-bottom: 0.3em; margin-top: 2em; }
  h3 { margin-top: 1.5em; }
  pre { background: #f6f8fa; border-radius: 6px; padding: 16px; overflow-x: auto; }
  code { font-family: SFMono-Regular, Consolas, "Liberation Mono", Menlo, monospace; font-size: 0.9em; }
  p > code, li > code { background: #f0f0f0; padding: 0.2em 0.4em; border-radius: 3px; }
  ul { padding-left: 1.5em; }
  li { margin: 0.25em 0; }
  .warning { background: #fff3cd; border-left: 4px solid #ffc107; padding: 1rem; margin: 1rem 0; }
  .info { background: #d1ecf1; border-left: 4px solid #17a2b8; padding: 1rem; margin: 1rem 0; }
</style>
</head>
<body>
<h1>Unitree G1 Getting Started Guide</h1>

<div class="warning">
<strong>Safety First:</strong> G1 is a humanoid robot with significant mass and power.
Always ensure adequate clearance, use the emergency stop when needed, and start with
dry-run mode to test code before running on hardware.
</div>

<h2>Overview</h2>

<p>The Unitree G1 is a humanoid robot with:</p>
<ul>
  <li><strong>Whole-body control:</strong> Walking, balancing, body height adjustment</li>
  <li><strong>Dual arms:</strong> 4 DOF per arm (shoulder pitch/roll/yaw, elbow pitch)</li>
  <li><strong>Sensors:</strong> IMU, joint encoders, cameras (via perception services)</li>
</ul>

<h2>Quick Start</h2>

<h3>1. Check Robot Connection</h3>

<pre><code># Check health endpoint
curl http://localhost:8888/health

# Expected response:
{
  "status": "ok"
}</code></pre>

<h3>2. Fixed Grasp</h3>

<pre><code># Execute fixed grasp sequence to target positions
result = grasp_target(
    right_pos=[0.471, -0.0074, 0.022],
    left_pos=[-0.003, 0.212, -0.004]
)
print(f"Grasp result: {result}")</code></pre>

<h3>3. Object Detection</h3>

<pre><code># Set YOLO_URL before starting agent (e.g. G1_Yolo Ultralytics service):
# export YOLO_URL=http://127.0.0.1:8013/detect

# Detect objects in environment
labels = yolo.detect_env()
print(f"Detected: {labels}")

# Save one frame with boxes to ~/d435i_yolo_*.jpg
path = yolo.save_detection_image()
print("Saved:", path)

# Get 3D position of specific object
detections = yolo.segment_3d("bottle")
for d in detections:
    print(f"{d['label']}: {d['position_base']}")</code></pre>

<h2>Coordinate Frames</h2>

<ul>
  <li><strong>World frame:</strong> Origin at robot start position, Z up</li>
  <li><strong>Body frame:</strong> Origin at robot pelvis, follows robot orientation</li>
  <li><strong>Arm frames:</strong> Defined relative to shoulder joints</li>
</ul>

<h2>Available Services</h2>

<table>
<tr><td><code>env</code></td><td><code>G1RobotEnv</code> — aggregated runtime; e.g. <code>env.read_cameras()</code> (Piper-shaped)</td></tr>
<tr><td><code>grasp_target</code></td><td>Fixed grasp to target positions (wraps IK)</td></tr>
<tr><td><code>camera</code></td><td>D455 RGB-D via ZMQ (same as <code>env.camera</code>)</td></tr>
<tr><td><code>camera_d435i</code></td><td>D435i RGB-D via TCP (see <code>g1.d435i_port</code> in config.yaml)</td></tr>
<tr><td><code>yolo</code></td><td>Object detection via YOLO_URL (e.g. G1_Yolo :8013)</td></tr>
<tr><td><code>memory</code></td><td>Spatial memory upsert/query (HTTP API)</td></tr>
<tr><td><code>face</code></td><td>Face SDK (HTTP + D455)</td></tr>
<tr><td><code>tts</code></td><td>Text-to-speech (DDS)</td></tr>
</table>
<p><em>Note:</em> Piper-style HTTP <code>grasp</code> (AnyGrasp) is not pre-created on G1; use <code>grasp_target</code> for fixed trajectories.</p>

<h2>SDK Reference</h2>

<p>See <a href="/code/sdk/markdown">/code/sdk/markdown</a> for complete API documentation.</p>

<h2>Troubleshooting</h2>

<h3>"channel factory init error"</h3>
<p>The G1 robot is not reachable. Check:</p>
<ul>
  <li>Robot is powered on</li>
  <li>Network cable is connected</li>
  <li>Network interface is correct (<code>enp4s0</code> by default; override via <code>UNITREE_IFACE</code>)</li>
</ul>

<h3>"sport_ready: false"</h3>
<p>The sport (locomotion) module is not ready. The robot may be in a fault state.
Try restarting the robot or checking for error messages.</p>

<h3>"arm_ready: false"</h3>
<p>The arm control module is not ready. Check arm power and communication.</p>

</body>
</html>
"""


@router.get("/guide/html", response_class=HTMLResponse)
async def get_system_guide():
    """Get the getting-started guide as HTML.

    No lease required.
    """
    return GUIDE_HTML
