"""
Traffic Intelligence - Flask application.

Slim orchestrator: registers the /api blueprint, serves the frontend, and
keeps the proven MJPEG video feed (annotated frames while processing,
raw source frames otherwise). Job lifecycle lives in job_manager;
derived analytics live in analytics; persistence lives in session_store.
"""

import logging
import os
import time

import cv2
from flask import Flask, Response, jsonify, redirect, request, send_from_directory, make_response

import api as api_module
import auth as auth_module
import job_manager
import process_video as video_processor
import session_store
from config import CONFIG

logging.basicConfig(level=logging.INFO, format="%(asctime)s - %(message)s")
logger = logging.getLogger(__name__)

app = Flask(__name__, static_folder="../frontend", static_url_path="/static")

secret_key = os.environ.get("TI_SECRET_KEY")
if not secret_key and (os.environ.get("FLASK_ENV") == "production" or os.environ.get("RENDER") == "true"):
    raise RuntimeError("TI_SECRET_KEY is required in production. Set it in the Render environment.")
app.secret_key = secret_key or "traffic-intelligence-local-key"

cors_origins = CONFIG.get("CORS_ORIGINS", [])
if cors_origins:
    try:
        from flask_cors import CORS
        CORS(app, resources={r"/api/*": {"origins": cors_origins}}, supports_credentials=True)
        app.config["SESSION_COOKIE_SAMESITE"] = "None" if any(origin.startswith("https://") for origin in cors_origins) else "Lax"
        app.config["SESSION_COOKIE_SECURE"] = any(origin.startswith("https://") for origin in cors_origins)
    except ImportError:
        logger.warning("flask-cors not installed; same-origin serving still works.")
else:
    try:
        from flask_cors import CORS
        CORS(app, resources={r"/api/*": {"origins": ["http://localhost:5000", "http://127.0.0.1:5000"]}}, supports_credentials=True)
    except ImportError:
        logger.warning("flask-cors not installed; same-origin serving still works.")

UPLOAD_FOLDER = CONFIG["UPLOAD_DIR"]
PROCESSED_FOLDER = CONFIG["PROCESSED_DIR"]
REPORTS_FOLDER = CONFIG["REPORTS_DIR"]
app.config["UPLOAD_FOLDER"] = UPLOAD_FOLDER
app.config["PROCESSED_FOLDER"] = PROCESSED_FOLDER
app.config["REPORTS_FOLDER"] = REPORTS_FOLDER
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(PROCESSED_FOLDER, exist_ok=True)
os.makedirs(REPORTS_FOLDER, exist_ok=True)

FRONTEND_FOLDER = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "frontend"))

# Register the API blueprint and apply any persisted Settings overrides.
api_module.load_config_overrides()
app.register_blueprint(api_module.bp, url_prefix="/api")
app.register_blueprint(auth_module.bp, url_prefix="/api/auth")
session_store.init_db()


# ---------------------------------------------------------------------------
# Authentication gate - everything except the login page, static assets,
# and auth endpoints requires a signed-in session.
# ---------------------------------------------------------------------------
PUBLIC_PREFIXES = ("/login.html", "/css/", "/js/", "/images/", "/favicon.ico",
                   "/api/auth/")


@app.before_request
def require_login():
    path = request.path
    if any(path == p or path.startswith(p) for p in PUBLIC_PREFIXES):
        return None
    if auth_module.current_username():
        return None
    if path.startswith("/api/"):
        return jsonify({"success": False, "error": "Sign in required."}), 401
    return redirect("/login.html")


# ---------------------------------------------------------------------------
# Frontend routes
# ---------------------------------------------------------------------------
@app.route("/")
def index():
    return app.send_static_file("index.html")


@app.route("/login.html")
def login_page():
    return send_from_directory(FRONTEND_FOLDER, "login.html")


@app.route("/report.html")
def report_page():
    return send_from_directory(FRONTEND_FOLDER, "report.html")


@app.route("/results.html")
def results_page():
    return send_from_directory(FRONTEND_FOLDER, "results.html")


@app.route("/css/<path:filename>")
def frontend_css(filename):
    return send_from_directory(os.path.join(FRONTEND_FOLDER, "css"), filename)


@app.route("/js/<path:filename>")
def frontend_javascript(filename):
    return send_from_directory(os.path.join(FRONTEND_FOLDER, "js"), filename)


@app.route("/images/<path:filename>")
def frontend_images(filename):
    return send_from_directory(os.path.join(FRONTEND_FOLDER, "images"), filename)


# ---------------------------------------------------------------------------
# MJPEG feed - streams ONLY real processed frames from the shared buffer.
# These are the exact frames the pipeline drew overlays on and wrote to the
# saved processed video. Raw source frames are never sent to the frontend.
# ---------------------------------------------------------------------------
def generate_processed_stream():
    last_seq = -1
    empty_ticks = 0
    while True:
        seq, frame = video_processor.latest_processed_frame()
        if frame is None or seq == last_seq:
            # No new processed frame yet. If no job is running and the buffer
            # has stayed empty for a while, end the stream (client shows the
            # empty-state panel instead of raw footage).
            empty_ticks += 1
            if job_manager.active_job() is None and empty_ticks > 100:
                return
            time.sleep(0.04)
            continue
        empty_ticks = 0
        last_seq = seq
        encoded, buffer = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 82])
        if encoded:
            yield (b"--frame\r\nContent-Type: image/jpeg\r\n\r\n"
                   + buffer.tobytes() + b"\r\n")
        else:
            return
        # After the active job finishes, keep the tail brief so the UI can
        # switch to the saved completed video.
        if job_manager.active_job() is None:
            idle_ticks = 0
            while idle_ticks < 25:
                seq2, frame2 = video_processor.latest_processed_frame()
                if frame2 is not None and seq2 != last_seq:
                    break
                time.sleep(0.04)
                idle_ticks += 1
            else:
                return


@app.route("/video_feed")
def video_feed():
    has_frames = video_processor.latest_frame_seq() > 0
    if job_manager.active_job() is None and not has_frames:
        return jsonify({"success": False,
                        "error": "No processed frames available. Upload a video to start detection."}), 409
    try:
        return Response(generate_processed_stream(),
                        mimetype="multipart/x-mixed-replace; boundary=frame")
    except Exception as exc:
        logger.error("Error in video_feed: %s", exc)
        return Response(status=500)


# ---------------------------------------------------------------------------
# Misc
# ---------------------------------------------------------------------------
@app.route("/health")
def health_alias():
    """Backwards-compatible alias for /api/health."""
    with app.test_request_context("/api/health"):
        response = app.full_dispatch_request()
    return response


@app.route("/favicon.ico")
def favicon():
    return make_response("", 204)


@app.errorhandler(404)
def not_found(_error):
    if request_wants_json():
        return jsonify({"success": False, "error": "Not found"}), 404
    return app.send_static_file("index.html")


def request_wants_json():
    from flask import request
    best = request.accept_mimetypes.best_match(["application/json", "text/html"])
    return best == "application/json" and \
        request.accept_mimetypes[best] > request.accept_mimetypes["text/html"]


if __name__ == "__main__":
    camera_manager_setup = getattr(job_manager, "camera_manager", None)  # noqa: F841
    try:
        from live_feed import camera_manager
        camera_manager.setup_database()
    except Exception:
        pass
    host = os.environ.get("BACKEND_HOST", "0.0.0.0")
    port = int(os.environ.get("PORT") or os.environ.get("BACKEND_PORT") or "5000")
    app.run(host=host, port=port, debug=False, use_reloader=False)