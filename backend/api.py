"""
Traffic Intelligence - API blueprint.

REST + SSE surface per spec:

    GET    /api/health
    POST   /api/upload                      multipart video -> {job_id}
    GET    /api/jobs/<id>/status            {status, frame, job_id}
    POST   /api/jobs/<id>/cancel
    GET    /api/sessions/<id>/live          SSE: {frame, vehicles, confidence, fps, utc}
    GET    /api/sessions/<id>/summary       runtime analytics (mirrors report page 2)
    GET    /api/sessions/<id>/vehicles      paginated + searchable
    GET    /api/sessions/<id>/violations    paginated, coalesced events
    GET    /api/sessions/<id>/heatmap
    GET    /api/sessions/<id>/report.{csv|pdf|xlsx}
    GET    /api/sessions                    list - powers the Sessions page
    GETPUT /api/config                      thresholds - powers Settings

The session id "live" resolves to the active job, else the latest completed
job in memory, else the most recent stored session.
"""

import json
import logging
import os
import time
from datetime import datetime, timezone

from flask import Blueprint, jsonify, request, Response, send_file, stream_with_context

import analytics
import generate_report
import job_manager
import session_store
from config import CONFIG
import live_feed
from process_video import data_lock

logger = logging.getLogger(__name__)

bp = Blueprint("api", __name__)

# ---------------------------------------------------------------------------
# Config overrides - persisted so Settings survives restarts.
# ---------------------------------------------------------------------------
_OVERRIDES_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "config_overrides.json")

CONFIG_KEYS = {
    "confidence_threshold": ("CONFIDENCE_THRESHOLD", float, 0.05, 0.95),
    "nms_threshold": ("NMS_THRESHOLD", float, 0.05, 0.95),
    "alert_speed_threshold_kmh": ("ALERT_SPEED_THRESHOLD", float, 1.0, 200.0),
    "pixels_per_meter": ("PIXELS_PER_METER", float, 1.0, 500.0),
    "video_fps": ("VIDEO_FPS", (int, float), 1.0, 120.0),
    "trajectory_length": ("TRAJECTORY_LENGTH", int, 20, 400),
    "trajectory_alpha": ("TRAJECTORY_ALPHA", float, 0.15, 1.0),
    "trajectory_thickness": ("TRAJECTORY_THICKNESS", int, 1, 12),
    "trajectory_smooth_window": ("TRAJECTORY_SMOOTH_WINDOW", int, 1, 12),
    "trajectory_min_alpha": ("TRAJECTORY_MIN_ALPHA", float, 0.05, 0.95),
    "heatline_glow": ("HEATLINE_GLOW", float, 0.5, 3.0),
    "heatline_visibility": ("HEATLINE_VISIBILITY", float, 0.2, 1.0),
}


def load_config_overrides():
    try:
        if os.path.exists(_OVERRIDES_PATH):
            with open(_OVERRIDES_PATH, "r", encoding="utf-8") as fh:
                saved = json.load(fh)
            for key, value in saved.items():
                if key in CONFIG_KEYS:
                    cfg_key, caster, lo, hi = CONFIG_KEYS[key]
                    try:
                        value = caster(value)
                        if lo <= value <= hi:
                            CONFIG[cfg_key] = value
                    except (TypeError, ValueError):
                        pass
    except Exception:
        logger.exception("Failed to load config overrides")


def _public_config():
    return {
        "confidence_threshold": CONFIG["CONFIDENCE_THRESHOLD"],
        "nms_threshold": CONFIG["NMS_THRESHOLD"],
        "alert_speed_threshold_kmh": CONFIG["ALERT_SPEED_THRESHOLD"],
        "pixels_per_meter": CONFIG["PIXELS_PER_METER"],
        "video_fps": CONFIG["VIDEO_FPS"],
        "trajectory_length": CONFIG["TRAJECTORY_LENGTH"],
        "trajectory_alpha": CONFIG["TRAJECTORY_ALPHA"],
        "trajectory_thickness": CONFIG["TRAJECTORY_THICKNESS"],
        "trajectory_smooth_window": CONFIG["TRAJECTORY_SMOOTH_WINDOW"],
        "trajectory_min_alpha": CONFIG["TRAJECTORY_MIN_ALPHA"],
        "heatline_glow": CONFIG["HEATLINE_GLOW"],
        "heatline_visibility": CONFIG["HEATLINE_VISIBILITY"],
        "tracked_classes": analytics.VEHICLE_CLASSES,
        # ONE shared color per vehicle type: video boxes, legend, table,
        # and charts all read the same mapping.
        "class_colors": dict(analytics.CLASS_COLORS_HEX),
        "frame_size": [CONFIG["FRAME_WIDTH"], CONFIG["FRAME_HEIGHT"]],
        "yolo_model": CONFIG["YOLO_MODEL"],
    }


@bp.route("/config", methods=["GET", "PUT"])
def api_config():
    if request.method == "GET":
        return jsonify({"success": True, "data": _public_config()})

    payload = request.get_json(silent=True) or {}
    saved = {}
    for key, (cfg_key, caster, lo, hi) in CONFIG_KEYS.items():
        if key not in payload:
            continue
        try:
            value = caster(payload[key])
        except (TypeError, ValueError):
            return jsonify({"success": False,
                            "error": f"{key} must be a number between {lo} and {hi}."}), 400
        if not (lo <= value <= hi):
            return jsonify({"success": False,
                            "error": f"{key} must be between {lo} and {hi}."}), 400
        CONFIG[cfg_key] = value
        saved[key] = value

    classes = payload.get("tracked_classes")
    if isinstance(classes, list) and classes:
        cleaned = [c for c in classes if c in analytics.VEHICLE_CLASSES]
        if cleaned:
            # Tracked-class filtering is applied at the analytics layer.
            analytics.VEHICLE_CLASSES[:] = cleaned
            saved["tracked_classes"] = cleaned

    try:
        existing = {}
        if os.path.exists(_OVERRIDES_PATH):
            with open(_OVERRIDES_PATH, "r", encoding="utf-8") as fh:
                existing = json.load(fh)
        existing.update(saved)
        with open(_OVERRIDES_PATH, "w", encoding="utf-8") as fh:
            json.dump(existing, fh, indent=2)
    except Exception:
        logger.exception("Failed to persist config overrides")

    return jsonify({"success": True, "data": _public_config(),
                    "message": "Saved. Changes apply to the next analysis run."})


# ---------------------------------------------------------------------------
# Health
# ---------------------------------------------------------------------------
@bp.route("/health")
def api_health():
    """Return health information including non-blocking AI model status.

    This endpoint must not synchronously load the model. It reports the
    status produced by process_video.ensure_model_loaded() which may be one of
    NOT_LOADED / LOADING / READY / ERROR.
    """
    pv = video_processor_module()
    # Ensure a background load is scheduled so health probes can prompt the
    # model to start loading without blocking the request.
    try:
        pv.ensure_model_loaded(async_load=True)
    except Exception:
        # ensure_model_loaded may not be present if an older module variant is
        # in use; fall back to checking model attribute.
        pass

    model_status = getattr(pv, "model_status", None) or ("READY" if getattr(pv, "model", None) else "NOT_LOADED")
    model_error = getattr(pv, "model_error", None)
    active = job_manager.active_job()
    return jsonify({
        "success": True,
        "data": {
            "backend": "Connected",
            "ai_model": model_status if model_status else "Not checked",
            "ai_model_error": model_error,
            "video_processor": "Processing" if active else "Idle",
            "camera": "Inactive",
            "database": "Connected" if job_manager.database_ok() else "Failed",
            "processing": bool(active),
            "active_job_id": active["id"] if active else None,
            "utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
        },
        "timestamp": time.time(),
    })


def video_processor_module():
    import process_video
    return process_video


# ---------------------------------------------------------------------------
# Upload + job lifecycle
# ---------------------------------------------------------------------------
@bp.route("/upload", methods=["POST"])
def api_upload():
    file = request.files.get("video")
    filename = (file.filename if file is not None else "") or ""
    if not file or filename == "":
        return jsonify({"success": False, "error": "No selected file."}), 400
    if job_manager.active_job():
        active = job_manager.active_job()
        return jsonify({"success": False,
                        "error": "Processing already in progress.",
                        "job_id": active["id"]}), 409
    extension = os.path.splitext(filename)[1].lower()
    if extension not in job_manager.ALLOWED_VIDEO_EXTENSIONS:
        return jsonify({"success": False, "error": "Unsupported video format."}), 415
    file.stream.seek(0, os.SEEK_END)
    if file.stream.tell() > job_manager.MAX_UPLOAD_BYTES:
        return jsonify({"success": False, "error": "Video exceeds the 500 MB size limit."}), 413
    file.stream.seek(0)

    video_path = job_manager.unique_video_path(filename)
    file.save(video_path)
    job = job_manager.start_job(video_path, os.path.basename(filename))
    return jsonify({"success": True, "job_id": job["id"], "status": "processing"})


@bp.route("/jobs/<job_id>/status")
def api_job_status(job_id):
    status = job_manager.job_status(job_id)
    if status is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, **status})


@bp.route("/jobs/<job_id>/cancel", methods=["POST"])
def api_job_cancel(job_id):
    result = job_manager.cancel_job(job_id)
    if result is None:
        return jsonify({"success": False, "error": "Job not found"}), 404
    return jsonify({"success": True, **result})


# ---------------------------------------------------------------------------
# Session resolution helpers
# ---------------------------------------------------------------------------
def resolve_session(session_id):
    """
    Returns (meta_dict_or_None, records_list, error_tuple_or_None).
    meta includes id/status/source_filename/started_at when known.
    """
    def _job_meta(job):
        # Normalise the internal 'done' to the public 'completed'.
        public_status = {"done": "completed"}.get(job["status"], job["status"])
        return {"id": job["id"], "status": public_status,
                "source_filename": job.get("source_filename"),
                "started_at": job.get("started_at"),
                "ended_at": job.get("ended_at")}

    if session_id == "live":
        job = job_manager.active_job() or job_manager.latest_completed_job()
        if job is not None:
            records = job_manager.job_records(job)
            return _job_meta(job), records, None
        stored = session_store.list_sessions(limit=1)
        if stored:
            meta = stored[0]
            return meta, session_store.get_session_records(meta["id"]), None
        return None, [], None

    job = job_manager.get_job(session_id)
    if job is not None:
        return _job_meta(job), job_manager.job_records(job), None

    meta = session_store.get_session_meta(session_id)
    if meta is not None:
        return meta, session_store.get_session_records(session_id), None
    return None, None, (jsonify({"success": False, "error": "Session not found"}), 404)


def _paginate(rows):
    try:
        page = max(1, int(request.args.get("page", 1)))
    except ValueError:
        page = 1
    try:
        page_size = min(500, max(10, int(request.args.get("page_size", 50))))
    except ValueError:
        page_size = 50
    total = len(rows)
    start = (page - 1) * page_size
    return {
        "page": page,
        "page_size": page_size,
        "total": total,
        "pages": max(1, -(-total // page_size)),
        "rows": rows[start:start + page_size],
    }


# ---------------------------------------------------------------------------
# Analytics endpoints
# ---------------------------------------------------------------------------
@bp.route("/sessions")
def api_sessions():
    sessions = session_store.list_sessions()
    # Include any in-memory jobs that have not been persisted yet.
    stored_ids = {s["id"] for s in sessions}
    for job in reversed(job_manager.all_jobs()):
        if job["id"] in stored_ids or job["status"] == "error":
            continue
        records = job_manager.job_records(job)
        frames = sorted({r["frame"] for r in records})
        source_fps = float((records[-1].get("source_fps") if records else None) or
                   CONFIG["VIDEO_FPS"])
        pv = job.get("processed_video") or {}
        sessions.insert(0, {
            "id": job["id"],
            "source_filename": job.get("source_filename"),
            "started_at": job.get("started_at"),
            "ended_at": job.get("ended_at"),
            "duration_s": round(((frames[-1] if frames else 0) + 1) / source_fps, 2),
            "frames": len(frames),
            "vehicles_tracked": len({r["vehicle_id"] for r in records}),
            "detections": len(records),
            "violations": sum(1 for r in records if r["speed_kmh"] > CONFIG["ALERT_SPEED_THRESHOLD"]),
            "status": {"done": "completed", "cancelled": "cancelled",
                       "processing": "processing"}.get(job["status"], job["status"]),
            "processed_video": pv.get("filename"),
            "processed_bytes": pv.get("bytes"),
            "fps": source_fps,
        })
    # Normalise processed-video availability flags for the Video Library.
    for s in sessions:
        s["has_processed"] = bool(s.get("processed_video")) and \
            job_manager.get_processed_video_path(s["id"]) is not None
        s["processed_partial"] = _session_partial_flag(s)
    return jsonify({"success": True, "data": sessions})


def _session_partial_flag(session):
    """True when a partial (cancelled / NOT VERIFIED) processed file exists."""
    if session.get("status") == "cancelled":
        return True
    job = job_manager.get_job(session["id"])
    if job and (job.get("processed_video") or {}).get("partial"):
        return True
    return False


@bp.route("/sessions/<session_id>/summary")
def api_session_summary(session_id):
    meta, records, err = resolve_session(session_id)
    if err:
        return err
    fps = float((meta or {}).get("fps") or CONFIG["VIDEO_FPS"])
    status = (meta or {}).get("status", "completed")
    safe_records = records if records is not None else []
    summary = analytics.build_summary(safe_records, fps=fps, status=status)
    summary.update({
        "session_id": (meta or {}).get("id", session_id),
        "source_filename": (meta or {}).get("source_filename"),
        "started_at": (meta or {}).get("started_at"),
        "ended_at": (meta or {}).get("ended_at"),
        "verified": status == "completed",
        "fps": fps,
        "distribution": analytics.build_distribution(safe_records),
        "trace": analytics.build_trace(safe_records, fps=fps),
        "current_frame": max((r["frame"] for r in safe_records), default=-1),
    })
    # Real measured processing FPS from the newest buffered frame.
    info = job_manager.latest_frame_info()
    if info and info.get("processing_fps"):
        summary["processing_fps"] = info["processing_fps"]
    # Processed-video metadata - powers the completed viewer + download.
    sid = (meta or {}).get("id", session_id)
    pv_path = job_manager.get_processed_video_path(sid)
    if pv_path:
        job = job_manager.get_job(sid)
        pv = (job or {}).get("processed_video") or {}
        summary["processed_video"] = {
            "filename": pv.get("filename") or os.path.basename(pv_path),
            "bytes": pv.get("bytes") or os.path.getsize(pv_path),
            "frames": pv.get("frames"),
            "codec": pv.get("codec"),
            "fps": pv.get("fps"),
            "partial": bool(pv.get("partial")) or (meta or {}).get("status") == "cancelled",
            "url": f"/api/sessions/{sid}/processed-video",
            "download_url": f"/api/sessions/{sid}/processed-video?download=1",
        }
    else:
        summary["processed_video"] = None
    return jsonify({"success": True, "data": summary})


@bp.route("/sessions/<session_id>/vehicles")
def api_session_vehicles(session_id):
    _, records, err = resolve_session(session_id)
    if err:
        return err
    vehicles = analytics.build_tracked_vehicles(records or [])

    q = (request.args.get("q") or "").strip().lower()
    if q:
        vehicles = [v for v in vehicles
                    if q in v["vehicle_id"].lower() or q in v["type"].lower()]
    vtype = request.args.get("type")
    if vtype:
        vehicles = [v for v in vehicles if v["type"] == vtype]
    violated = request.args.get("violated")
    if violated in ("true", "false"):
        want = violated == "true"
        vehicles = [v for v in vehicles if v["ever_violated"] == want]

    result = _paginate(vehicles)
    result["types_present"] = sorted({v["type"] for v in vehicles})
    return jsonify({"success": True, **result})


@bp.route("/sessions/<session_id>/violations")
def api_session_violations(session_id):
    meta, records, err = resolve_session(session_id)
    if err:
        return err
    fps = float((meta or {}).get("fps") or CONFIG["VIDEO_FPS"])
    events = analytics.coalesce_violations(records or [], fps=fps)

    severity = request.args.get("severity")
    if severity in ("warning", "severe"):
        events = [e for e in events if e["severity"] == severity]
    vtype = request.args.get("type")
    if vtype:
        events = [e for e in events if e["type"] == vtype]

    result = _paginate(events)
    result["counts"] = {
        "all": len(events),
        "warning": sum(1 for e in events if e["severity"] == "warning"),
        "severe": sum(1 for e in events if e["severity"] == "severe"),
    }
    return jsonify({"success": True, **result})


# ---------------------------------------------------------------------------
# Processed video playback / download + evidence snapshots.
# ---------------------------------------------------------------------------
@bp.route("/sessions/<session_id>/processed-video")
def api_session_processed_video(session_id):
    """Stream the SAVED processed video (Range-capable for the <video> tag).
    ?download=1 returns it as an attachment with a meaningful filename."""
    path = job_manager.get_processed_video_path(session_id)
    if not path:
        return jsonify({"success": False,
                        "error": "No processed video available for this session."}), 404
    job = job_manager.get_job(session_id)
    partial = bool((job or {}).get("processed_video", {}).get("partial"))
    if request.args.get("download"):
        download_name = f"Traffic_Intelligence_{session_id[:12]}_processed.mp4"
        if partial:
            download_name = f"Traffic_Intelligence_{session_id[:12]}_processed_PARTIAL.mp4"
        return send_file(path, as_attachment=True, download_name=download_name,
                         mimetype="video/mp4", conditional=True)
    response = send_file(path, mimetype="video/mp4", conditional=True)
    if partial:
        response.headers["X-Processed-Video-Partial"] = "1"
    return response


@bp.route("/sessions/<session_id>/video")
def api_session_source_video(session_id):
    """Original source video (in-memory jobs only)."""
    path = job_manager.get_source_video_path(session_id)
    if not path:
        return jsonify({"success": False,
                        "error": "Source video is not available for this session."}), 404
    return send_file(path, conditional=True)


@bp.route("/sessions/<session_id>/evidence")
def api_session_evidence(session_id):
    """List real evidence snapshots captured from processed frames."""
    _, _, err = resolve_session(session_id)
    if err:
        return err
    files = job_manager.evidence_files(session_id)
    for item in files:
        item["url"] = f"/api/sessions/{session_id}/evidence/{item['filename']}"
    return jsonify({"success": True, "data": files})


@bp.route("/sessions/<session_id>/evidence/<path:filename>")
def api_session_evidence_file(session_id, filename):
    """Serve one evidence snapshot image (name sanitised against traversal)."""
    safe = os.path.basename(filename)
    if safe != filename or not safe.lower().endswith(".jpg"):
        return jsonify({"success": False, "error": "Invalid evidence file."}), 400
    path = os.path.join(job_manager.evidence_dir(session_id), safe)
    if not os.path.exists(path) or os.path.getsize(path) == 0:
        return jsonify({"success": False, "error": "Evidence snapshot not found."}), 404
    return send_file(path, mimetype="image/jpeg")


@bp.route("/sessions/<session_id>/heatmap")
def api_session_heatmap(session_id):
    _, records, err = resolve_session(session_id)
    if err:
        return err
    heatmap = analytics.build_heatmap(records or [])
    heatmap["colormap"] = analytics.HEATMAP_COLORMAP
    return jsonify({"success": True, "data": heatmap})


# ---------------------------------------------------------------------------
# Reports
# ---------------------------------------------------------------------------
_REPORT_EXT = {"csv": "csv", "pdf": "pdf", "xlsx": "xlsx"}


@bp.route("/sessions/<session_id>/report.<ext>")
def api_session_report(session_id, ext):
    ext = ext.lower()
    if ext not in _REPORT_EXT:
        return jsonify({"success": False, "error": "Unsupported report format."}), 404
    meta, records, err = resolve_session(session_id)
    if err:
        return err
    path = generate_report.build_report(ext, session_id=(meta or {}).get("id", session_id),
                                        records=records,
                                        status=(meta or {}).get("status", "completed"))
    download_name = f"traffic_report_{(session_id[:12])}.{ext}"
    try:
        return send_file(path, as_attachment=True, download_name=download_name)
    except Exception:
        logger.exception("Report generation failed for %s (%s)", session_id, ext)
        return jsonify({"success": False, "error": f"Failed to generate {ext.upper()} report."}), 500


# ---------------------------------------------------------------------------
# SSE live stream - pushes one event per processed frame.
# ---------------------------------------------------------------------------
@bp.route("/sessions/<session_id>/live")
def api_session_live(session_id):
    def event(name, payload):
        return f"event: {name}\ndata: {json.dumps(payload)}\n\n"

    @stream_with_context
    def generate():
        last_frame = -1
        started = time.time()
        yield event("open", {"session_id": session_id,
                             "utc": datetime.now(timezone.utc).strftime("%H:%M:%S")})
        while time.time() - started < 1800:  # hard cap: 30 minutes per connection
            job = job_manager.active_job() if session_id == "live" else job_manager.get_job(session_id)
            frames = job_manager.live_frames_snapshot(last_frame)
            for frame in frames:
                last_frame = max(last_frame, frame.get("frame", -1))
                details = frame.get("vehicle_details", []) or []
                confidences = [float(v.get("confidence", 0)) for v in details
                               if isinstance(v.get("confidence"), (int, float))]
                violations = frame.get("violations", []) or []
                threshold = CONFIG["ALERT_SPEED_THRESHOLD"]
                detections = [{
                    "id": str(v.get("id", "")),
                    "type": str(v.get("type", "")),
                    "bbox": [round(float(x), 1) for x in (v.get("bounding_box") or [])][:4],
                    "confidence": float(v.get("confidence", 0)),
                    "speed_kmh": float(v.get("speed", 0)),
                    # Real violation flag from the configured threshold - the
                    # frontend never invents this.
                    "speeding": float(v.get("speed", 0)) > threshold,
                } for v in details]
                yield event("frame", {
                    "frame": frame.get("frame"),
                    "total_frames": (job or {}).get("total_frames"),
                    "vehicles": len(details),
                    "unique_vehicles": frame.get("unique_vehicles"),
                    "confidence": round(sum(confidences) / len(confidences), 4) if confidences else None,
                    "fps": frame.get("processing_fps"),
                    "utc": datetime.now(timezone.utc).strftime("%H:%M:%S"),
                    "avg_speed_kmh": frame.get("average_speed", 0.0),
                    "condition": ("Heavy" if len(details) >= 20 else
                                  "Moderate" if len(details) >= 10 else
                                  "Low" if details else "N/A"),
                    "active_violations": len(violations),
                    "detections": detections,
                })
            if job is not None and job["status"] != "processing":
                yield event("state", {"status": job["status"], "job_id": job["id"],
                                      "processed_video": job.get("processed_video")})
                if frames or last_frame >= 0:
                    break
            elif job is None:
                stored = session_store.list_sessions(limit=1)
                if stored:
                    yield event("state", {"status": "idle",
                                          "latest_session": stored[0]["id"]})
                else:
                    yield event("state", {"status": "empty"})
                break
            yield ": ping\n\n"
            time.sleep(0.25)

    resp = Response(generate(), mimetype="text/event-stream")
    resp.headers["Cache-Control"] = "no-cache"
    resp.headers["X-Accel-Buffering"] = "no"
    return resp


# ---------------------------------------------------------------------------
# Live camera - proven LiveCameraManager (YOLO bboxes drawn on the stream)
# ---------------------------------------------------------------------------
@bp.route("/camera/status")
def api_camera_status():
    camera_manager = live_feed.get_camera_manager()
    active_ids = list(camera_manager.active_cameras.keys())
    return jsonify({"success": True, "data": {
        "active": bool(active_ids),
        "camera_id": active_ids[0] if active_ids else None,
    }})


@bp.route("/camera/start", methods=["POST"])
def api_camera_start():
    payload = request.get_json(silent=True) or {}
    source = payload.get("source", 0)
    if isinstance(source, str):
        source = source.strip() or 0
    camera_id = "default"
    camera_manager = live_feed.get_camera_manager()
    if camera_manager.start_camera(camera_id, source):
        return jsonify({"success": True, "data": {"camera_id": camera_id,
                                                  "stream": "/api/camera/stream"}})
    return jsonify({"success": False,
                    "error": "Failed to open the camera. Check the device index or URL."}), 500


@bp.route("/camera/stop", methods=["POST"])
def api_camera_stop():
    camera_manager = live_feed.get_camera_manager()
    camera_manager.stop_camera("default")
    return jsonify({"success": True})


@bp.route("/camera/detections")
def api_camera_detections():
    camera_manager = live_feed.get_camera_manager()
    cam = camera_manager.active_cameras.get("default")
    return jsonify({"success": True,
                    "data": {"detections": (cam or {}).get("last_detections", [])}})


@bp.route("/camera/stream")
def api_camera_stream():
    camera_manager = live_feed.get_camera_manager()
    if not camera_manager.active_cameras:
        return jsonify({"success": False,
                        "error": "No camera is active. Start a camera first."}), 409
    return Response(camera_manager.generate_frames("default"),
                    mimetype="multipart/x-mixed-replace; boundary=frame")
