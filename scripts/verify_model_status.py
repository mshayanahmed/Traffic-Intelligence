import sys, os, time
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
os.environ["MODEL_PATH"] = os.path.join(_root, "models", "yolov8n.pt")
sys.path.insert(0, os.path.join(_root, "backend"))

import process_video as pv

print("initial:", pv.get_model_status())
pv.ensure_model_loaded(async_load=True)

final = None
for _ in range(180):
    final = pv.get_model_status()
    if final["status"] in ("ready", "error"):
        break
    time.sleep(1)

print("final:", final)
print("model object loaded:", pv.model is not None)
print("model_status var:", pv.model_status)

# Verify ERROR is terminal (a subsequent ensure call must NOT flip back to LOADING).
if final and final["status"] == "error":
    pv.ensure_model_loaded(async_load=True)
    time.sleep(0.5)
    after = pv.get_model_status()
    print("after ensure (must stay error):", after)
    assert after["status"] == "error", "ensure_model_loaded re-armed ERROR to LOADING"
    print("PASS: ERROR is terminal, no LOADING loop")