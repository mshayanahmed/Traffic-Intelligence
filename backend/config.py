import os
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATABASE_DIR = PROJECT_ROOT / "database"
MODELS_DIR = PROJECT_ROOT / "models"
UPLOAD_DIR = PROJECT_ROOT / "backend" / "uploads"
PROCESSED_DIR = PROJECT_ROOT / "backend" / "processed"
EVIDENCE_DIR = PROJECT_ROOT / "backend" / "evidence"
REPORTS_DIR = PROJECT_ROOT / "backend" / "reports"
LOG_DIR = PROJECT_ROOT / "logs"

for directory in (DATABASE_DIR, MODELS_DIR, UPLOAD_DIR, PROCESSED_DIR, EVIDENCE_DIR, REPORTS_DIR, LOG_DIR):
    directory.mkdir(parents=True, exist_ok=True)


def _env_path(name, default):
    value = os.environ.get(name)
    if not value:
        return str(default)
    path = Path(value).expanduser()
    return str(path if path.is_absolute() else (PROJECT_ROOT / path).resolve())


def _env_list(name, default):
    value = os.environ.get(name)
    if not value:
        return list(default)
    return [item.strip() for item in value.split(",") if item.strip()]


DEFAULT_MODEL = MODELS_DIR / "yolov8n.pt"
DEFAULT_DB = DATABASE_DIR / "traffic_analysis.db"

CONFIG = {
    "FRAME_WIDTH": 640,
    "FRAME_HEIGHT": 360,
    "PIXELS_PER_METER": 20,
    "YOLO_MODEL": _env_path("MODEL_PATH", DEFAULT_MODEL),
    "CONFIDENCE_THRESHOLD": float(os.environ.get("CONFIDENCE_THRESHOLD", 0.2)),
    "NMS_THRESHOLD": float(os.environ.get("NMS_THRESHOLD", 0.3)),
    "VIDEO_FPS": float(os.environ.get("VIDEO_FPS", 30)),
    "ALERT_SPEED_THRESHOLD": float(os.environ.get("ALERT_SPEED_THRESHOLD", 10)),
    "SPEED_WINDOW_SIZE": int(os.environ.get("SPEED_WINDOW_SIZE", 5)),
    "MAX_SPEED": float(os.environ.get("MAX_SPEED", 120.0)),
    "MAX_DENSITY": float(os.environ.get("MAX_DENSITY", 10.0)),
    "DATABASE_PATH": _env_path("DATABASE_PATH", DEFAULT_DB),
    "UPLOAD_DIR": _env_path("UPLOAD_DIR", UPLOAD_DIR),
    "PROCESSED_DIR": _env_path("PROCESSED_DIR", PROCESSED_DIR),
    "EVIDENCE_DIR": _env_path("EVIDENCE_DIR", EVIDENCE_DIR),
    "REPORTS_DIR": _env_path("REPORTS_DIR", REPORTS_DIR),
    "LOG_DIR": _env_path("LOG_DIR", LOG_DIR),
    "CORS_ORIGINS": _env_list(
        "CORS_ORIGINS",
        [
            "https://traffic-intelligence-web.vercel.app",
            "http://localhost:5000",
            "http://127.0.0.1:5000",
        ],
    ),
    "MAX_UPLOAD_SIZE": int(os.environ.get("MAX_UPLOAD_SIZE", 524288000)),
    "MONITORED_AREA": (640 / 20) * (360 / 20),
    "TRAJECTORY_LENGTH": int(os.environ.get("TRAJECTORY_LENGTH", 200)),
    "TRAJECTORY_ALPHA": float(os.environ.get("TRAJECTORY_ALPHA", 1.0)),
    "TRAJECTORY_THICKNESS": int(os.environ.get("TRAJECTORY_THICKNESS", 4)),
    "TRAJECTORY_SMOOTH_WINDOW": int(os.environ.get("TRAJECTORY_SMOOTH_WINDOW", 3)),
    "TRAJECTORY_MIN_ALPHA": float(os.environ.get("TRAJECTORY_MIN_ALPHA", 0.15)),
    "HEATLINE_GLOW": float(os.environ.get("HEATLINE_GLOW", 1.5)),
    "HEATLINE_VISIBILITY": float(os.environ.get("HEATLINE_VISIBILITY", 0.9)),
}
