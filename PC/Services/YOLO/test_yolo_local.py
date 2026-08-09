import cv2
from pathlib import Path

from ultralytics import YOLO

# 加载模型
model = YOLO("yolov5l6.pt")

# 测试图片路径
TEST_IMAGE = "realsense.png"
img_path = Path(TEST_IMAGE)
if not img_path.exists():
    print(f"图片 {TEST_IMAGE} 不存在，请确认路径")
    exit(1)

print(f"图片路径: {img_path.absolute()}")
print(f"图片尺寸: {cv2.imread(str(img_path)).shape}")

# 运行检测
results = model(
    source=str(img_path),
    conf=0.25,
    iou=0.45,
    imgsz=640,
    verbose=True,
)

# 解析结果
result = results[0]
boxes = result.boxes
print(f"\n检测到 {len(boxes)} 个目标:")
for i, box in enumerate(boxes):
    cls_id = int(box.cls[0])
    conf = float(box.conf[0])
    xyxy = box.xyxy[0].cpu().numpy()
    name = result.names[cls_id]
    print(f"  [{i+1}] {name:20s} 置信度: {conf:.3f}  bbox: ({xyxy[0]:.0f}, {xyxy[1]:.0f})~({xyxy[2]:.0f}, {xyxy[3]:.0f})")

# 保存带标注的图片
save_path = "runs/detect/local_test/realsense.jpg"
result.save(save_path)
print(f"\n带标注的图片已保存到: {save_path}")
