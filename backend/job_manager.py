"""
Traffic Intelligence - processing job manager.

Keeps the proven upload -> background-thread -> process_traffic_video pipeline,
exposed as a small registry shared by app.py (MJPEG feed) and api.py (REST/SSE).
"""

import logging
import os
import threading
import time
import uuid

import cv2

import process_video as video_processor
from process_video import process_traffic_video, data_lock, processed_data
from analytics import frames_to_records
from config import CONFIG
import session_store

logger = logging.getLogger(__name__)

MAX_UPLOAD_BYTES = int(CONFIG.get("MAX_UPLOAD_SIZE", 500 * 1024 * 1024))
ALLOWED_VIDEO_EXTENSIONS = {".mp4", ".avi", ".mov", ".mkv", ".webm"}

_jobs = []
_jobs_lock = threading.Lock()
_upload_folder = CONFIG["UPLOAD_DIR"]
_processed_folder = CONFIG["PROCESSED_DIR"]
_evidence_root = CONFIG["EVIDENCE_DIR"]
os.makedirs(_upload_folder, exist_ok=True)
os.makedirs(_processed_folder, exist_ok=True)
os.makedirs(_evidence_root, exist_ok=True)


def active_job():
    with _jobs_lock:
        return next((j for j in reversed(_jobs) if j["status"] == "processing"), None)


def get_job(job_id):
    with _jobs_lock:
        return next((j for j in _jobs if j["id"] == job_id), None)


def latest_completed_job():
    with _jobs_lock:
        for job in reversed(_jobs):
            if job["status"] in ("done", "cancelled"):
                return job
    return None


def all_jobs():
    with _jobs_lock:
        return list(_jobs)


def unique_video_path(original_filename=""):
    ext = os.path.splitext(original_filename)[1].lower() or ".mp4"
    if ext not in ALLOWED_VIDEO_EXTENSIONS:
        ext = ".mp4"
    ts = int(time.time() * 1000)
    return os.path.join(_upload_folder, f"video_{ts}_{uuid.uuid4().hex[:8]}{ext}")


def _source_total_frames(video_path):
    """Read the source frame count cheaply (no inference) for FRAME n/total."""
    try:
        cap = cv2.VideoCapture(video_path)
        total = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        cap.release()
        return max(0, total)
    except Exception:
        return 0


def _source_fps(video_path):
    """Read the source rate for live metadata without running inference."""
    try:
        cap = cv2.VideoCapture(video_path)
        fps = cap.get(cv2.CAP_PROP_FPS) if cap.isOpened() else 0
        cap.release()
        return round(float(fps), 2) if fps and fps >= 1 else None
    except Exception:
        return None


def processed_video_path(session_id):
    return os.path.join(_processed_folder, f"processed_{session_id}.mp4")


def evidence_dir(session_id):
    return os.path.join(_evidence_root, str(session_id))


def evidence_files(session_id):
    """List real evidence snapshots captured from processed frames."""
    folder = evidence_dir(session_id)
    if not os.path.isdir(folder):
        return []
    out = []
    try:
        for name in sorted(os.listdir(folder)):
            if not name.lower().endswith(".jpg"):
                continue
            path = os.path.join(folder, name)
            if os.path.getsize(path) == 0:
                continue
            # Skip variants such as _zoom.jpg and _thumb.jpg from the main evidence listing
            # to keep the evidence table compact and relevant.
            if name.endswith("_zoom.jpg") or name.endswith("_thumb.jpg"):
                continue
            stem = os.path.splitext(name)[0]
            vehicle_id, _, frame = stem.rpartition("_f")
            width = height = None
            try:
                img = cv2.imread(path, cv2.IMREAD_COLOR)
                if img is not None:
                    height, width = img.shape[:2]
            except Exception:
                pass
            out.append({
                "filename": name,
                "vehicle_id": vehicle_id or stem,
                "frame": int(frame) if frame.isdigit() else None,
                "bytes": os.path.getsize(path),
                "width": width,
                "height": height,
            })
    except OSError:
        return []
    return out


def _validate_processed_output(output_path, expected_frames):
    """Never present a corrupt file as done: verify existence, size,
    decodability, and approximate frame count."""
    if not output_path or not os.path.exists(output_path):
        return {"error": "missing"}
    size = os.path.getsize(output_path)
    if size <= 0:
        return {"error": "empty"}
    try:
        cap = cv2.VideoCapture(output_path)
        if not cap.isOpened():
            cap.release()
            return {"error": "unreadable"}
        frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT))
        fps = cap.get(cv2.CAP_PROP_FPS)
        width = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = int(cap.get(cv2.CAP_PROP_FOURCC))
        codec = "".join(chr((fourcc >> 8 * i) & 0xFF) for i in range(4)).strip()
        # Spot-check decodability: advance a few frames.
        readable = False
        for _ in range(3):
            ret, _ = cap.read()
            if ret:
                readable = True
                break
        cap.release()
        if not readable:
            return {"error": "undecodable"}
        if expected_frames and frames and frames < expected_frames * 0.5:
            return {"error": f"frame-count mismatch ({frames} vs {expected_frames})"}
        return {"ok": True, "bytes": size, "frames": frames,
                "fps": round(fps, 2) if fps else None,
                "width": width, "height": height, "codec": codec}
    except Exception as exc:
        return {"error": f"validation exception: {exc}"}


def start_job(video_path, source_filename):
    """Spawn a processing thread using the proven pipeline pattern."""
    job_id = uuid.uuid4().hex
    job = {
        "id": job_id,
        "video_path": video_path,
        "source_filename": source_filename,
        "status": "processing",
        "started_at": time.time(),
        "ended_at": None,
        "total_frames": _source_total_frames(video_path),
        "fps": _source_fps(video_path),
        "output_path": processed_video_path(job_id),
        "processed_video": None,
        "processed_data": [],
        "stop_event": threading.Event(),
    }
    with _jobs_lock:
        _jobs.append(job)
    thread = threading.Thread(target=_run_job, args=(job,), name=f"traffic-job-{job_id}")
    thread.daemon = True
    thread.start()
    return job


def _run_job(job):
    try:
        video_processor.clear_live_buffer()
        result = process_traffic_video(job["video_path"], job["stop_event"],
                                       output_path=job.get("output_path"),
                                       evidence_dir=evidence_dir(job["id"]))
        if not isinstance(result, list):
            result = list(result)
        job["processed_data"] = result
        cancelled = job["stop_event"].is_set()
        job["ended_at"] = time.time()

        # Validate the processed output - never present a corrupt file as done.
        frames_written = len({r.get("frame") for r in result})
        validation = _validate_processed_output(job.get("output_path"), frames_written)
        if validation.get("ok"):
            job["processed_video"] = {
                "filename": os.path.basename(job["output_path"]),
                "bytes": validation["bytes"],
                "frames": validation.get("frames"),
                "codec": validation.get("codec"),
                "fps": validation.get("fps"),
                "partial": cancelled,
            }
        else:
            job["processed_video"] = None
            logger.warning("Processed video invalid for job %s: %s",
                           job["id"], validation.get("error"))

        if not cancelled and not validation.get("ok"):
            raise RuntimeError("Processed video validation failed: " + validation.get("error", "unknown error"))
        job["status"] = "cancelled" if cancelled else "done"

        # Persist every finished run - cancelled runs are stored with status
        # 'cancelled' and reported as NOT VERIFIED, never silently as success.
        try:
            pv = job.get("processed_video") or {}
            processing_fps = result[-1].get("processing_fps") if result else None
            source_fps = result[-1].get("source_fps") if result else None
            session_store.save_session(
                job["id"],
                job.get("source_filename") or os.path.basename(job["video_path"]),
                job["started_at"],
                job["ended_at"],
                "cancelled" if cancelled else "completed",
                frames_to_records(result),
                processed_video=pv.get("filename"),
                processed_bytes=pv.get("bytes"),
                fps=source_fps,
                processing_fps=processing_fps,
            )
        except Exception:
            logger.exception("Failed to persist session %s", job["id"])
    except Exception as exc:
        logger.error("Error processing video: %s", exc)
        job["status"] = "error"
        job["ended_at"] = time.time()
    finally:
        # Publish results to the shared live buffer only once, after the run.
        try:
            with data_lock:
                processed_data.clear()
                processed_data.extend(job.get("processed_data", []))
        except Exception:
            logger.exception("Failed to publish processed frames")


def cancel_job(job_id):
    job = get_job(job_id)
    if job is None:
        return None
    if job["status"] == "processing":
        job["stop_event"].set()
        return {"job_id": job_id, "status": "stopping"}
    return {"job_id": job_id, "status": job["status"]}


def job_status(job_id):
    job = get_job(job_id)
    if job is None:
        return None
    frames = len(job.get("processed_data", []))
    if job["status"] == "processing":
        with data_lock:
            frames = len(processed_data)
    return {
        "job_id": job_id,
        "status": job["status"],
        "frame": max(0, frames - 1),
        "frames_processed": frames,
        "total_frames": job.get("total_frames"),
        "source_filename": job.get("source_filename"),
                "fps": job.get("fps"),
        "processed_video": job.get("processed_video"),
    }


def job_records(job):
    """Canonical raw records for an in-memory job.
    While the job is still processing, its frames live in the shared live
    buffer - read from there so analytics reflect progress in real time."""
    if job["status"] == "processing":
        with data_lock:
            return frames_to_records(list(processed_data))
    return frames_to_records(job.get("processed_data", []))


def latest_frame_info():
    """Frame number + measured processing FPS of the newest buffered frame."""
    with data_lock:
        if processed_data:
            latest = processed_data[-1]
            return {"frame": latest.get("frame"),
                    "processing_fps": latest.get("processing_fps")}
    return None


def live_frames_snapshot(since_frame=-1):
    """Return new frames appended to the shared buffer since last poll."""
    with data_lock:
        frames = [dict(f) for f in processed_data if f.get("frame", -1) > since_frame]
    return frames


def get_processed_video_path(session_id):
    """Return the stored processed-video path if it exists and is non-empty.
    Resolves in-memory jobs first, then persisted sessions."""
    path = processed_video_path(session_id)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    try:
        meta = session_store.get_session_meta(session_id)
        if meta and meta.get("processed_video"):
            candidate = os.path.join(_processed_folder,
                                     os.path.basename(meta["processed_video"]))
            if os.path.exists(candidate) and os.path.getsize(candidate) > 0:
                return candidate
    except Exception:
        pass
    return None


def get_source_video_path(session_id):
    """Source video path - only available for in-memory jobs."""
    job = get_job(session_id)
    if job and job.get("video_path") and os.path.exists(job["video_path"]):
        return job["video_path"]
    return None


def database_ok():
    try:
        conn = session_store.get_connection()
        conn.execute("SELECT 1")
        conn.close()
        return True
    except Exception:
        return False