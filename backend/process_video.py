"""
Traffic Intelligence - THE authoritative processing pipeline.

Exactly ONE inference pass per uploaded video:

    INPUT VIDEO -> FRAME READ -> YOLO DETECTION -> TRACKING -> ANALYTICS
    -> DRAW OVERLAYS (once) -> STORE/LIVE-PUBLISH PROCESSED FRAME
    -> ENCODE OUTPUT VIDEO

The SAME overlaid frame feeds:
  1. the live MJPEG stream        (latest_processed_frame / seq counter)
  2. the saved processed video    (progressively encoded output MP4)
  3. heatmap data                 (detection centers in frame_data)
  4. analytics / vehicle table    (frame_data -> canonical records)
  5. violations                   (speed events from real telemetry)

YOLO is never re-run for the live view, heatmap, reports, or completed video.
"""

import logging
import os
import threading
import time

import cv2
import numpy as np
from collections import deque

import analytics
from analytics import CLASS_COLORS_HEX, hex_to_bgr
from config import CONFIG

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Model - loaded lazily exactly once, with status tracking to avoid
# reloading on health checks and to allow the health endpoint to report
# READY / LOADING / NOT_LOADED / ERROR without side effects.
# ---------------------------------------------------------------------------
model = None
model_status = "NOT_LOADED"  # one of: NOT_LOADED, LOADING, READY, ERROR
model_error = None
_model_lock = threading.Lock()


def _load_model_blocking():
    """Blocking model load. Sets model, model_status and model_error.
    Call under _model_lock or from a background thread."""
    global model, model_status, model_error
    try:
        model_status = "LOADING"
        from ultralytics import YOLO
        model = YOLO(CONFIG["YOLO_MODEL"])
        model_status = "READY"
        model_error = None
        logger.info("YOLO model loaded: %s", CONFIG["YOLO_MODEL"])
    except Exception as exc:
        model = None
        model_status = "ERROR"
        model_error = str(exc)
        logger.exception("Failed to load YOLO model: %s", exc)


def ensure_model_loaded(async_load=True):
    """Ensure model load is scheduled or completed.
    If async_load is True, schedule a background thread to load the model
    if not already loading or loaded. If async_load is False, perform a
    blocking load (useful for startup paths that must guarantee readiness).
    Returns the current model_status."""
    global model_status
    with _model_lock:
        if model_status == "READY":
            return model_status
        if model_status == "LOADING":
            return model_status
        # NOT_LOADED or ERROR -> attempt load
        if async_load:
            model_status = "LOADING"
            t = threading.Thread(target=_load_model_blocking, daemon=True)
            t.start()
            return model_status
        else:
            _load_model_blocking()
            return model_status


def get_model():
    """Return the loaded model or raise if not READY.
    This does not trigger async loads; callers should call ensure_model_loaded
    to schedule a load if desired."""
    global model, model_status
    if model_status != "READY":
        raise RuntimeError(f"Model not ready: status={model_status}")
    return model

# Track history for trajectories: veh_id -> deque of (x,y)
track_history = {}
# Map vehicle id -> last known vehicle type (for color selection)
track_types = {}

# ---------------------------------------------------------------------------
# Shared processed-frame buffer + latest annotated frame for the live stream.
# ---------------------------------------------------------------------------
processed_data = []                          # canonical per-frame dicts
data_lock = threading.Lock()

latest_annotated_frame = None                # newest overlay-drawn frame (copy)
latest_annotated_frame_lock = threading.Lock()
_frame_seq = 0                               # increments once per NEW frame
_seq_lock = threading.Lock()


def _publish_annotated_frame(frame):
    """Store the newest processed frame and bump its sequence number."""
    global latest_annotated_frame, _frame_seq
    with latest_annotated_frame_lock:
        latest_annotated_frame = frame.copy()
    with _seq_lock:
        _frame_seq += 1


def latest_frame_seq():
    with _seq_lock:
        return _frame_seq


def latest_processed_frame():
    """Return (seq, frame_copy_or_None) for MJPEG consumers."""
    with latest_annotated_frame_lock:
        frame = None if latest_annotated_frame is None else latest_annotated_frame.copy()
    with _seq_lock:
        return _frame_seq, frame


def clear_live_buffer():
    global latest_annotated_frame
    with data_lock:
        processed_data.clear()
    with latest_annotated_frame_lock:
        latest_annotated_frame = None

# ---------------------------------------------------------------------------
# Output writer - codec chosen by actually opening it locally, never silently.
# ---------------------------------------------------------------------------
_WRITER_CODEC_CANDIDATES = ("avc1", "mp4v", "XVID")


def _open_writer(output_path, width, height, fps):
    """Open a VideoWriter with the first codec that actually works locally.
    Returns (writer, fourcc_name) or (None, None) - never a silent failure."""
    if not output_path:
        return None, None
    fps = float(fps) if fps and fps >= 1 else float(CONFIG["VIDEO_FPS"])
    size = (int(width), int(height))
    for fourcc_name in _WRITER_CODEC_CANDIDATES:
        try:
            fourcc = cv2.VideoWriter_fourcc(*fourcc_name)
            writer = cv2.VideoWriter(output_path, fourcc, fps, size)
            if writer.isOpened():
                logger.info("Processed-video writer opened: %s (%s @ %.1f fps)",
                            output_path, fourcc_name, fps)
                return writer, fourcc_name
            writer.release()
        except Exception as exc:
            logger.warning("VideoWriter codec %s failed: %s", fourcc_name, exc)
    logger.error("No working VideoWriter codec found for %s", output_path)
    return None, None


def _inspect_source(video_path):
    """Read resolution/fps/frame-count cheaply (no inference)."""
    info = {"width": CONFIG["FRAME_WIDTH"], "height": CONFIG["FRAME_HEIGHT"],
            "fps": float(CONFIG["VIDEO_FPS"]), "total_frames": 0}
    try:
        cap = cv2.VideoCapture(video_path)
        if cap.isOpened():
            w = int(cap.get(cv2.CAP_PROP_FRAME_WIDTH))
            h = int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT))
            fps = cap.get(cv2.CAP_PROP_FPS)
            info.update({
                "width": w or CONFIG["FRAME_WIDTH"],
                "height": h or CONFIG["FRAME_HEIGHT"],
                "fps": round(float(fps), 2) if fps and fps >= 1 else float(CONFIG["VIDEO_FPS"]),
                "total_frames": max(0, int(cap.get(cv2.CAP_PROP_FRAME_COUNT))),
            })
        cap.release()
    except Exception:
        pass
    return info

def iou(boxA, boxB):
    # Compute intersection over union for two bounding boxes
    xA = max(boxA[0], boxB[0])
    yA = max(boxA[1], boxB[1])
    xB = min(boxA[2], boxB[2])
    yB = min(boxA[3], boxB[3])
    interArea = max(0, xB - xA) * max(0, yB - yA)
    boxAArea = (boxA[2] - boxA[0]) * (boxA[3] - boxA[1])
    boxBArea = (boxB[2] - boxB[0]) * (boxB[3] - boxB[1])
    unionArea = boxAArea + boxBArea - interArea
    if unionArea == 0:
        return 0
    return interArea / unionArea

def match_vehicles(prev_positions, curr_boxes, iou_threshold=0.3):
    # prev_positions: {vehicle_id: (x1, y1, x2, y2)}
    # curr_boxes: list of [x1, y1, x2, y2, conf, class_id]
    assignments = {}
    used_prev = set()
    for idx, det in enumerate(curr_boxes):
        x1, y1, x2, y2, conf, class_id = det
        best_iou = 0
        best_id = None
        for vehicle_id, prev_box in prev_positions.items():
            iou_score = iou([x1, y1, x2, y2], prev_box)
            if iou_score > best_iou and iou_score > iou_threshold and vehicle_id not in used_prev:
                best_iou = iou_score
                best_id = vehicle_id
        if best_id is not None:
            assignments[idx] = best_id
            used_prev.add(best_id)
    return assignments

# ---------------------------------------------------------------------------
# Overlay drawing - professional boxes + labels, drawn ONCE per frame.
# The same style appears live AND in the saved processed video.
# ---------------------------------------------------------------------------
_VIOLATION_BGR = hex_to_bgr("#E46060")
_TEXT_BG_BGR = (16, 16, 16)


def draw_overlays(frame, detections, track_history=None):
    """
    Draw bounding boxes + labels onto the processed frame and plot trajectories.

    detections: list of dicts with bbox [x1,y1,x2,y2], type, id, confidence,
    speed_kmh, speeding. All values come straight from the tracker - nothing
    here invents data.

    track_history: optional dict mapping vehicle_id -> iterable of (x,y) points
    representing recent center positions in image coordinates.
    """
    height, width = frame.shape[:2]
    scale = max(1.0, width / 640.0)
    thickness = max(1, int(round(1.4 * scale)))
    font_scale = 0.42 * scale
    font_thickness = 1
    pad_x, pad_y, line_h = int(5 * scale), int(3 * scale), int(14 * scale)
    font = cv2.FONT_HERSHEY_SIMPLEX

    # First draw trajectories (so they appear beneath boxes/labels).
    if track_history:
        # draw trajectories onto an overlay and alpha-blend for visibility
        overlay = frame.copy()
        for det in detections:
            vid = det.get("id")
            pts = track_history.get(vid) if vid and vid in track_history else None
            if pts and len(pts) >= 2:
                # optional smoothing (moving average)
                try:
                    smooth_w = max(1, int(CONFIG.get("TRAJECTORY_SMOOTH_WINDOW", 3)))
                except Exception:
                    smooth_w = 3
                if smooth_w > 1 and len(pts) >= smooth_w:
                    smoothed = []
                    for i in range(len(pts)):
                        start = max(0, i - smooth_w + 1)
                        window = pts[start:i+1]
                        avg_x = int(round(sum(p[0] for p in window) / len(window)))
                        avg_y = int(round(sum(p[1] for p in window) / len(window)))
                        smoothed.append((avg_x, avg_y))
                else:
                    smoothed = list(pts)

                n = len(smoothed)
                color = hex_to_bgr(CLASS_COLORS_HEX.get(det["type"], "#24C0C0"))
                base_thickness = max(1, int(round(CONFIG.get("TRAJECTORY_THICKNESS", 2) * scale)))

                # draw each segment with age-based alpha so older segments are fainter
                for idx in range(1, n):
                    p0 = smoothed[idx - 1]
                    p1 = smoothed[idx]
                    age = (idx - 1) / max(1, n - 2)
                    seg_alpha = float(CONFIG.get("TRAJECTORY_MIN_ALPHA", 0.15)) + (1.0 - float(CONFIG.get("TRAJECTORY_MIN_ALPHA", 0.15))) * age
                    # compute color scaled by alpha
                    seg_color = tuple(int(max(0, min(255, c * seg_alpha))) for c in color)
                    # glow underlay
                    try:
                        glow_thickness = max(1, base_thickness + 2)
                        cv2.line(overlay, tuple(map(int, p0)), tuple(map(int, p1)), (10, 10, 10), glow_thickness, lineType=cv2.LINE_AA)
                    except Exception:
                        pass
                    # draw the colored segment
                    try:
                        cv2.line(overlay, tuple(map(int, p0)), tuple(map(int, p1)), seg_color, base_thickness, lineType=cv2.LINE_AA)
                    except Exception:
                        pass

                # draw latest point highlight (bright)
                last_pt = tuple(int(x) for x in smoothed[-1])
                try:
                    cv2.circle(overlay, last_pt, max(2, int(round(3 * scale)))+1, (10,10,10), -1, lineType=cv2.LINE_AA)
                except Exception:
                    pass
                cv2.circle(overlay, last_pt, max(2, int(round(3 * scale))), color, -1, lineType=cv2.LINE_AA)

                # highlight recent segments if speeding
                if det.get("speeding") and n >= 2:
                    # emphasize last 3 segments
                    start_idx = max(1, n - 4)
                    for idx in range(start_idx, n):
                        p0 = smoothed[idx - 1]
                        p1 = smoothed[idx]
                        try:
                            cv2.line(overlay, tuple(map(int, p0)), tuple(map(int, p1)), _VIOLATION_BGR, base_thickness + 2, lineType=cv2.LINE_AA)
                        except Exception:
                            pass
        try:
            alpha = float(CONFIG.get("TRAJECTORY_ALPHA", 0.85))
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
        except Exception:
            # if blending fails, fall back to in-place overlay
            frame[:] = overlay

    # Then draw boxes and labels on top
    for det in detections:
        x1, y1, x2, y2 = [int(round(v)) for v in det["bbox"]]
        x1, y1 = max(0, x1), max(0, y1)
        x2, y2 = min(width - 1, x2), min(height - 1, y2)
        if x2 <= x1 or y2 <= y1:
            continue

        color = hex_to_bgr(CLASS_COLORS_HEX.get(det["type"], "#24C0C0"))
        box_color = _VIOLATION_BGR if det["speeding"] else color
        cv2.rectangle(frame, (x1, y1), (x2, y2), box_color, thickness)

        # Multi-line label built ONLY from real pipeline values.
        conf_pct = int(round(float(det.get("confidence", 0)) * 100))
        lines = ["%s \u2022 %s %d%%" % (det["type"], det["id"], conf_pct)]
        speed = float(det.get("speed_kmh") or 0)
        if speed > 0:
            lines.append("%.1f km/h" % speed)
        if det["speeding"]:
            lines.append("SPEEDING")

        widths = [cv2.getTextSize(t, font, font_scale, font_thickness)[0][0] for t in lines]
        label_w = max(widths) + pad_x * 2
        label_h = len(lines) * line_h + pad_y * 2
        # Above the box top edge; flip below when it would clip the frame.
        lx = min(max(1, x1), max(1, width - label_w - 1))
        ly = y1 - label_h - 2
        if ly < 1:
            ly = min(y1 + 2, max(1, height - label_h - 1))

        # Semi-transparent label background - never an opaque panel over the car.
        roi = frame[ly:ly + label_h, lx:lx + label_w]
        if roi.size:
            blended = cv2.addWeighted(roi, 0.35, np.full_like(roi, _TEXT_BG_BGR), 0.65, 0)
            frame[ly:ly + label_h, lx:lx + label_w] = blended

        ty = ly + pad_y + int(line_h * 0.82)
        for i, text in enumerate(lines):
            is_speeding_line = det["speeding"] and i == len(lines) - 1
            text_color = (_VIOLATION_BGR if is_speeding_line
                          else (color if i == 0 else (235, 235, 235)))
            cv2.putText(frame, text, (lx + pad_x, ty), font,
                        font_scale, text_color, font_thickness, cv2.LINE_AA)
            ty += line_h
    return frame


def process_traffic_video(video_path, stop_event=None, output_path=None, evidence_dir=None):
    """Run the single authoritative pipeline over a video file.

    Returns the per-frame processed data list (also appended to the shared
    live buffer). The same overlaid frames are published for the live stream
    and progressively encoded into output_path.
    """
    detector = get_model()
    cap = cv2.VideoCapture(video_path)
    if not cap.isOpened():
        logger.error("Failed to open video file: %s", video_path)
        return []

    # Inspect the source (resolution/fps/frame-count) - no inference here.
    source = _inspect_source(video_path)
    logger.info("Source video: %sx%s @ %.2f fps, ~%s frames",
                source["width"], source["height"], source["fps"],
                source["total_frames"] or "unknown")

    # Output writer uses the SOURCE fps so playback speed matches the
    # original footage; frames are written progressively as they are
    # processed - never re-encoded in a second pass at the end.
    writer = None
    writer_codec = None
    if output_path:
        # Use the source video resolution for output encoding so saved MP4
        # preserves original quality instead of forcing CONFIG size.
        writer, writer_codec = _open_writer(output_path, source["width"],
                                            source["height"], source["fps"])
        if writer is None:
            cap.release()
            raise RuntimeError("No compatible codec is available for the processed video.")

    if evidence_dir:
        try:
            os.makedirs(evidence_dir, exist_ok=True)
        except OSError:
            evidence_dir = None

    processed_data_local = []
    frame_count = 0
    processing_started_at = time.perf_counter()
    prev_positions = {}
    prev_centers = {}
    next_vehicle_id = 0
    violation_events = []
    active_violation_events = {}
    seen_vehicle_ids = set()
    source_fps = source["fps"]
    violation_cooldown_frames = max(1, int(source_fps * 1.5))
    # Only one Rickshaw type in mapping
    vehicle_names = {2: "Car", 3: "Bike", 5: "Bus", 7: "Truck", 9: "Rickshaw", 10: "Rickshaw"}

    try:
        while cap.isOpened():
            if stop_event is not None and stop_event.is_set():
                logger.info("Video processing cancelled: %s", video_path)
                break
            ret, frame = cap.read()
            if not ret:
                break

                    # Preserve source resolution — do not force downscale/upscale here.
            # The detector can handle the input frame size; resizing causes quality loss.
            results = detector.predict(frame, conf=CONFIG["CONFIDENCE_THRESHOLD"], iou=CONFIG["NMS_THRESHOLD"])
            detections = results[0].boxes.data.tolist() if hasattr(results[0], 'boxes') else []

            vehicle_details = []
            vehicle_speeds = {}
            current_boxes = []
            for det in detections:
                x1, y1, x2, y2, conf, class_id = det
                class_id = int(class_id)
                if (class_id not in vehicle_names or
                    vehicle_names[class_id] not in analytics.VEHICLE_CLASSES):
                    continue
                current_boxes.append([x1, y1, x2, y2, conf, class_id])

            assignments = match_vehicles(prev_positions, current_boxes)
            current_positions = {}
            index_to_veh_id = {}  # Map detection index to vehicle ID
            for index, (x1, y1, x2, y2, conf, class_id) in enumerate(current_boxes):
                veh_id = assignments.get(index)
                if veh_id is None:
                    next_vehicle_id += 1
                    veh_id = f"vehicle_{next_vehicle_id}"
                index_to_veh_id[index] = veh_id
                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                speed = 0.0
                previous = prev_centers.get(veh_id)
                if previous is not None:
                    prev_cx, prev_cy = previous[:2]
                    dist = np.linalg.norm(np.array([cx, cy]) - np.array([prev_cx, prev_cy]))
                    speed = ((dist * source_fps) / CONFIG["PIXELS_PER_METER"]) * 3.6
                    speed = min(speed, CONFIG["MAX_SPEED"])
                current_positions[veh_id] = (x1, y1, x2, y2)
                prev_centers[veh_id] = (cx, cy)
                vehicle_speeds[veh_id] = round(speed, 2)

                # Update trajectory history (bounded deque of recent centers)
                try:
                    if veh_id not in track_history:
                        track_history[veh_id] = deque(maxlen=CONFIG.get("TRAJECTORY_LENGTH", 60))
                    track_history[veh_id].append((int(round(cx)), int(round(cy))))
                    track_types[veh_id] = vehicle_names[class_id]
                except Exception:
                    # Defensive: do not break processing for any history error
                    pass

                vehicle_details.append({
                    "id": veh_id,
                    "type": vehicle_names[class_id],
                    "speed": vehicle_speeds[veh_id],
                    "capture_time": round(frame_count / source_fps, 2),
                    "bounding_box": [x1, y1, x2, y2],
                    "center": [int(round(cx)), int(round(cy))],
                    "confidence": round(conf, 2),
                })
            seen_vehicle_ids.update(vehicle_speeds.keys())

            # ONE overlay pass - the SAME frame feeds the live stream AND
            # the saved processed video. No second rendering implementation.
            overlay_dets = []
            for index, (x1, y1, x2, y2, conf, class_id) in enumerate(current_boxes):
                veh_id = index_to_veh_id.get(index)
                if veh_id is None:
                    continue
                speed_val = vehicle_speeds.get(veh_id, 0.0)
                overlay_dets.append({
                    "bbox": [x1, y1, x2, y2],
                    "type": vehicle_names[int(class_id)],
                    "id": veh_id,
                    "confidence": conf,
                    "speed_kmh": speed_val,
                    "speeding": speed_val > CONFIG["ALERT_SPEED_THRESHOLD"],
                })
            frame = draw_overlays(frame, overlay_dets, track_history)

            # Publish the processed frame for the live MJPEG stream...
            _publish_annotated_frame(frame)
            # ...and progressively encode the SAME frame into the output file.
            if writer is not None:
                writer.write(frame)

            prev_positions = current_positions
            current_time = round(frame_count / source_fps, 2)
            for vehicle_id, speed in vehicle_speeds.items():
                if speed <= CONFIG["ALERT_SPEED_THRESHOLD"]:
                    continue
                event = active_violation_events.get(vehicle_id)
                if event is None or frame_count - event["last_frame"] > violation_cooldown_frames:
                    event = {
                        "vehicle_id": vehicle_id,
                        "violation": "Speeding",
                        "first_detected_at": current_time,
                        "last_detected_at": current_time,
                        "max_speed": speed,
                        "duration": 0.0,
                        "severity": "detected",
                        "last_frame": frame_count,
                    }
                    violation_events.append(event)
                    active_violation_events[vehicle_id] = event
                    # Real evidence snapshot: the actual processed frame with
                    # the real bounding box, captured at violation onset.
                    if evidence_dir:
                        try:
                            # Prefer cropping the original/high-quality processed frame
                            bbox = current_positions.get(vehicle_id)
                            h, w = frame.shape[:2]
                            if bbox:
                                x1, y1, x2, y2 = [int(round(v)) for v in bbox]
                                # include recent trajectory points so the crop contains the path
                                pts = list(track_history.get(vehicle_id, [])) if track_history else []
                                # Compute combined extents (bbox U trajectory bounds)
                                min_x, min_y = x1, y1
                                max_x, max_y = x2, y2
                                for (px, py) in pts[-CONFIG.get("TRAJECTORY_LENGTH", 60):]:
                                    min_x = min(min_x, px)
                                    min_y = min(min_y, py)
                                    max_x = max(max_x, px)
                                    max_y = max(max_y, py)
                                # add a small proportional margin (5% of max dimension)
                                margin = int(round(0.05 * max(w, h)))
                                nx1 = max(0, min_x - margin)
                                ny1 = max(0, min_y - margin)
                                nx2 = min(w - 1, max_x + margin)
                                ny2 = min(h - 1, max_y + margin)
                                # Ensure valid crop
                                if nx2 <= nx1 or ny2 <= ny1:
                                    crop_full = frame.copy()
                                else:
                                    crop_full = frame[ny1:ny2, nx1:nx2].copy()
                                # Save full-resolution evidence crop (high quality)
                                try:
                                    snapshot_full = os.path.join(evidence_dir, f"{vehicle_id}_f{frame_count}.jpg")
                                    cv2.imwrite(snapshot_full, crop_full, [int(cv2.IMWRITE_JPEG_QUALITY), 95])
                                except Exception:
                                    # fallback to writing the whole frame
                                    try:
                                        snapshot_full = os.path.join(evidence_dir, f"{vehicle_id}_f{frame_count}.jpg")
                                        cv2.imwrite(snapshot_full, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                                    except Exception:
                                        pass

                                # Save a zoomed crop centered on the vehicle bounding box for clarity
                                try:
                                    bx1 = max(0, x1 - margin)
                                    by1 = max(0, y1 - margin)
                                    bx2 = min(w - 1, x2 + margin)
                                    by2 = min(h - 1, y2 + margin)
                                    if bx2 > bx1 and by2 > by1:
                                        crop_box = frame[by1:by2, bx1:bx2].copy()
                                    else:
                                        crop_box = crop_full
                                    # Upscale to a reasonable size for evidence viewing (target longest side = 800px)
                                    bh, bw = crop_box.shape[:2]
                                    max_side = max(bh, bw) if (bh and bw) else 0
                                    if max_side and max_side < 800:
                                        scale = 800.0 / float(max_side)
                                        zoom = cv2.resize(crop_box, (int(bw * scale), int(bh * scale)), interpolation=cv2.INTER_CUBIC)
                                    else:
                                        zoom = crop_box
                                    snapshot_zoom = os.path.join(evidence_dir, f"{vehicle_id}_f{frame_count}_zoom.jpg")
                                    cv2.imwrite(snapshot_zoom, zoom, [int(cv2.IMWRITE_JPEG_QUALITY), 92])
                                except Exception:
                                    pass

                                # Save a small thumbnail for index pages
                                try:
                                    thumb_w = 256
                                    if crop_full.shape[1] > 0:
                                        thumb_h = int(thumb_w * crop_full.shape[0] / crop_full.shape[1])
                                    else:
                                        thumb_h = 256
                                    thumb = cv2.resize(crop_full, (thumb_w, max(1, thumb_h)), interpolation=cv2.INTER_AREA)
                                    snapshot_thumb = os.path.join(evidence_dir, f"{vehicle_id}_f{frame_count}_thumb.jpg")
                                    cv2.imwrite(snapshot_thumb, thumb, [int(cv2.IMWRITE_JPEG_QUALITY), 85])
                                except Exception:
                                    pass
                            else:
                                # As a last resort save the full processed frame
                                try:
                                    snapshot_full = os.path.join(evidence_dir, f"{vehicle_id}_f{frame_count}.jpg")
                                    cv2.imwrite(snapshot_full, frame, [int(cv2.IMWRITE_JPEG_QUALITY), 90])
                                except Exception:
                                    pass
                        except Exception:
                            pass
                else:
                    event["last_detected_at"] = current_time
                    event["max_speed"] = max(event["max_speed"], speed)
                    event["duration"] = round(
                        event["last_detected_at"] - event["first_detected_at"], 2
                    )
                    event["last_frame"] = frame_count

            avg_speed = round(sum(vehicle_speeds.values()) / len(vehicle_speeds), 2) if vehicle_speeds else 0.0
            elapsed = max(time.perf_counter() - processing_started_at, 1e-6)
            frame_data = {
                "frame": frame_count,
                "vehicle_count": len(vehicle_details),
                "average_speed": avg_speed,
                "unique_vehicles": len(seen_vehicle_ids),
                "vehicle_details": vehicle_details,
                "processing_fps": round((frame_count + 1) / elapsed, 2),
                "source_fps": source_fps,
                "violations": [
                    {key: value for key, value in event.items() if key != "last_frame"}
                    for event in violation_events
                ],
            }
            processed_data_local.append(frame_data)
            with data_lock:
                processed_data.append(frame_data)

            frame_count += 1
        cap.release()
        if writer is not None:
            writer.release()
            logger.info("Processed video written (%s): %s", writer_codec, output_path)
        return processed_data_local
    except Exception as e:
        logger.exception("Error processing video: %s", e)
        cap.release()
        if writer is not None:
            writer.release()
        return []
