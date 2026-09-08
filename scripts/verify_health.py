"""Verify /api/health and /api/model-status return the correct model state."""
import sys, os, time
_here = os.path.dirname(os.path.abspath(__file__))
_root = os.path.dirname(_here)
os.environ["MODEL_PATH"] = os.path.join(_root, "models", "yolov8n.pt")
os.environ["TESTING_DB"] = "1"
sys.path.insert(0, os.path.join(_root, "backend"))

from app import app

def now_health_state():
    with app.test_client() as c:
        h = c.get("/api/health").get_json()
        s = c.get("/api/model-status").get_json()
        return h["data"]["ai_model_status"], h["data"]["ai_model"], s["data"]["status"], s["data"]["loaded"]

# First poll triggers async load -> LOADING or READY (never blocked).
st = now_health_state()
print("immediate health/model-status:", st)

# Wait for load to settle.
import time
final = None
for _ in range(180):
    with app.test_client() as c:
        final = c.get("/api/model-status").get_json()["data"]["status"]
    if final in ("ready", "error"):
        break
    time.sleep(1)
print("settled model-status:", final)
assert final == "ready", "model did not reach ready"
print("PASS: health/model-status reach READY and do not hang on LOADING")