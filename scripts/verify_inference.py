"""Verify the configured YOLO model performs real inference with expected classes."""
import sys, os, time
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
os.environ["MODEL_PATH"] = os.path.join(_root, "models", "yolov8n.pt")
sys.path.insert(0, os.path.join(_root, "backend"))

import numpy as np
from process_video import get_model, get_model_status, CONFIG

print("Config YOLO_MODEL:", CONFIG["YOLO_MODEL"])
print("Status before:", get_model_status())
model = get_model()
print("Status after get_model:", get_model_status())
print("Model object:", type(model).__name__)

# Synthetic highway-ish frame at detection resolution (bias toward car color).
frame = np.zeros((360, 640, 3), dtype=np.uint8)
frame[:] = (90, 90, 95)                       # gray road
cv2_rect_l = None
frame[170:260, 150:360] = (120, 40, 40)       # dark red "car" blob
frame[160:240, 380:560] = (60, 60, 120)       # blue-gray second blob

results = model(frame, conf=0.2, verbose=False)
det = results[0]
names = det.names
boxes = det.boxes
print("Detected boxes:", 0 if boxes is None else len(boxes))
if boxes is not None and len(boxes) > 0:
    for i in range(len(boxes)):
        cls = int(boxes.cls[i])
        print("  class_id=%d label=%r conf=%.2f" % (cls, names[cls], float(boxes.conf[i])))
print("Model names sample:", {k: names[k] for k in sorted(names) if k < 5})
print("DONE")