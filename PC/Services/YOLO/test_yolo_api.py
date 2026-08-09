import base64
import requests
from pathlib import Path

URL = "http://127.0.0.1:8013/detect"

# 使用项目自带的测试图片
TEST_IMAGE = "realsense.png"

# 读取图片并转为 base64
image_b64 = base64.b64encode(Path(TEST_IMAGE).read_bytes()).decode("utf-8")

payload = {
    "image": image_b64,
    "conf_thres": 0.25,
    "iou_thres": 0.45,
}

print(f"正在发送图片 {TEST_IMAGE} 到 YOLO 检测服务...")
response = requests.post(URL, json=payload, timeout=60)
response.raise_for_status()
result = response.json()

print(f"\n检测到 {result['count']} 个目标：")
for det in result["detections"]:
    print(f"  - [{det['class_name']}] 置信度: {det['confidence']:.2f}  位置: ({det['x1']:.0f}, {det['y1']:.0f})~({det['x2']:.0f}, {det['y2']:.0f})")
