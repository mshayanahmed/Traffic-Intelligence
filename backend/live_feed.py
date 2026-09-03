import cv2
import numpy as np
import sqlite3
import logging
import os
from config import CONFIG
import threading
from analytics import CLASS_COLORS_HEX, hex_to_bgr
from collections import deque
# Prefer authoritative processed frames when available
try:
    from backend import process_video as proc_video
except Exception:
    # If package import fails (script run directly), try relative import
    try:
        import process_video as proc_video
    except Exception:
        proc_video = None

class LiveCameraManager:
    def __init__(self):
        self.active_cameras = {}
        self.model = None
        self.database_lock = threading.Lock()
        self.speed_window = {}
        # Treat both Rickshaw and Auto Rickshaw as "Rickshaw"
        self.allowed_vehicle_classes = {2, 3, 5, 7, 9, 10}

    def setup_database(self):
        db_path = os.path.abspath(CONFIG.get("DATABASE_PATH", "traffic_analysis.db"))
        with self.database_lock:
            conn = sqlite3.connect(db_path)
            cursor = conn.cursor()
            cursor.execute('''
                CREATE TABLE IF NOT EXISTS camera_feeds (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    camera_id TEXT UNIQUE,
                    status TEXT,
                    last_active DATETIME DEFAULT CURRENT_TIMESTAMP
                )
            ''')
            conn.commit()
            cursor.execute("UPDATE camera_feeds SET status = 'inactive'")
            conn.commit()
            conn.close()

    def start_camera(self, camera_id, source=0):
        try:
            # Support int (0, 1) or string (IP camera URL)
            if isinstance(source, str):
                if source.isdigit():
                    source = int(source)
                # else: keep as string (URL)
            cap = cv2.VideoCapture(source)
            if not cap.isOpened():
                raise Exception(f"Failed to open camera {camera_id} with source {source}")

            db_path = os.path.abspath(CONFIG.get("DATABASE_PATH", "traffic_analysis.db"))
            with self.database_lock:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    INSERT OR REPLACE INTO camera_feeds (camera_id, status)
                    VALUES (?, 'active')
                """, (camera_id,))
                conn.commit()
                conn.close()

            # Per-camera tracking history for live trajectories when no authoritative
            # processing job is active. track_history: track_id -> deque of (x,y)
            from collections import deque
            self.active_cameras[camera_id] = {
                'capture': cap,
                'prev_centers': {},
                'vehicle_count': 0,
                'last_detections': [],
                # Add "Auto Rickshaw" to distribution
                'vehicle_distribution': {"Car": 0, "Bike": 0, "Bus": 0, "Truck": 0, "Rickshaw": 0, "Auto Rickshaw": 0},
                'track_history': {},
                'track_types': {},
                'next_track_id': 0,
            }
            return True
        except Exception as e:
            logging.error(f"Error starting camera {camera_id}: {e}")
            return False

    def stop_camera(self, camera_id):
        if camera_id in self.active_cameras:
            self.active_cameras[camera_id]['capture'].release()
            del self.active_cameras[camera_id]
            db_path = os.path.abspath(CONFIG.get("DATABASE_PATH", "traffic_analysis.db"))
            with self.database_lock:
                conn = sqlite3.connect(db_path)
                cursor = conn.cursor()
                cursor.execute("""
                    UPDATE camera_feeds SET status = 'inactive'
                    WHERE camera_id = ?
                """, (camera_id,))
                conn.commit()
                conn.close()

    def process_frame(self, frame, camera_id):
        """Process a single frame to detect vehicles and calculate speeds."""
        if frame is None:
            return None, []
        try:
            if self.model is None:
                from ultralytics import YOLO
                self.model = YOLO(CONFIG["YOLO_MODEL"])
            detector = self.model
            results = detector.predict(frame, conf=CONFIG["CONFIDENCE_THRESHOLD"], iou=CONFIG["NMS_THRESHOLD"])
            boxes = getattr(results[0], 'boxes', None)
            box_data = getattr(boxes, 'data', None)
            detections = box_data.tolist() if box_data is not None else []

            vehicle_details = []
            # Only one Rickshaw type in distribution
            vehicle_distribution = {"Car": 0, "Bike": 0, "Bus": 0, "Truck": 0, "Rickshaw": 0}
            if 'vehicle_count' not in self.active_cameras[camera_id]:
                self.active_cameras[camera_id]['vehicle_count'] = 0
            ref_line_y = CONFIG["FRAME_HEIGHT"] // 2
            ref_line_thickness = 5

            for det in detections:
                x1, y1, x2, y2, conf, class_id = det
                class_id = int(class_id)
                # Map both 9 and 10 to "Rickshaw"
                vehicle_type = {
                    2: "Car",
                    3: "Bike",
                    5: "Bus",
                    7: "Truck",
                    9: "Rickshaw",
                    10: "Rickshaw"
                }.get(class_id, "Unknown")
                if class_id not in self.allowed_vehicle_classes:
                    continue
                if vehicle_type in vehicle_distribution:
                    vehicle_distribution[vehicle_type] += 1

                cx, cy = (x1 + x2) / 2, (y1 + y2) / 2
                speed = 0.0
                if class_id in self.active_cameras[camera_id]['prev_centers']:
                    prev_cx, prev_cy = self.active_cameras[camera_id]['prev_centers'][class_id]
                    dist = np.linalg.norm(np.array([cx, cy]) - np.array([prev_cx, prev_cy]))
                    speed = ((dist * CONFIG["VIDEO_FPS"]) / CONFIG["PIXELS_PER_METER"]) * 3.6
                    speed = min(speed, CONFIG["MAX_SPEED"])
                self.active_cameras[camera_id]['prev_centers'][class_id] = (cx, cy)

                if abs(cy - ref_line_y) < ref_line_thickness:
                    if 'counted_ids' not in self.active_cameras[camera_id]:
                        self.active_cameras[camera_id]['counted_ids'] = set()
                    veh_id = f"{class_id}_{int(cx)}_{int(cy)}"
                    if veh_id not in self.active_cameras[camera_id]['counted_ids']:
                        self.active_cameras[camera_id]['vehicle_count'] += 1
                        self.active_cameras[camera_id]['counted_ids'].add(veh_id)

                vehicle_details.append({
                    "id": f"{class_id}_{camera_id}",
                    "type": vehicle_type,
                    "speed": round(speed, 2),
                    "bounding_box": [x1, y1, x2, y2],
                    "confidence": round(conf, 2),
                })

            self.active_cameras[camera_id]['last_detections'] = vehicle_details
            self.active_cameras[camera_id]['vehicle_distribution'] = vehicle_distribution
            return frame, vehicle_details
        except Exception as e:
            logging.error(f"Error processing frame: {e}")
            return frame, []

    def generate_frames(self, camera_id):
        if camera_id not in self.active_cameras:
            return

        cap = self.active_cameras[camera_id]['capture']
        while True:
            # Prefer the authoritative processed frame from the pipeline when available.
            proc_frame = None
            if proc_video is not None:
                try:
                    seq, proc_frame = proc_video.latest_processed_frame()
                except Exception:
                    proc_frame = None

            if proc_frame is not None:
                # Use the processed frame (already contains overlays & trajectories)
                frame_to_send = proc_frame
                detections = []
            else:
                ret, frame = cap.read()
                if not ret:
                    break
                # Preserve camera native resolution where possible; local UI may scale it.
                frame = cv2.resize(frame, (CONFIG["FRAME_WIDTH"], CONFIG["FRAME_HEIGHT"]))
                processed_frame, detections = self.process_frame(frame, camera_id)
                if processed_frame is None:
                    continue

                # Build per-camera track assignment and draw trajectories for the live camera
                cam = self.active_cameras[camera_id]
                # ensure track_history exists
                cam.setdefault('track_history', {})
                cam.setdefault('track_types', {})
                cam.setdefault('next_track_id', 0)

                track_history = cam['track_history']
                track_types = cam['track_types']

                # Simple nearest-centroid matching to maintain track IDs across frames
                updated_tracks = set()
                for det in detections:
                    x1, y1, x2, y2 = det['bounding_box']
                    cx, cy = (x1 + x2) / 2.0, (y1 + y2) / 2.0
                    # find nearest existing track
                    best_tid = None
                    best_dist = None
                    for tid, pts in track_history.items():
                        if not pts:
                            continue
                        last = pts[-1]
                        d = np.linalg.norm(np.array([cx, cy]) - np.array(last))
                        if best_dist is None or d < best_dist:
                            best_dist = d
                            best_tid = tid
                    frame_h, frame_w = frame.shape[:2]
                    match_thresh = max(20, int(min(frame_w, frame_h) * 0.05))
                    if best_tid is None or best_dist is None or best_dist > match_thresh:
                        cam['next_track_id'] += 1
                        best_tid = f"t{cam['next_track_id']}"
                        track_history[best_tid] = deque(maxlen=CONFIG.get('TRAJECTORY_LENGTH', 60))
                    # append center
                    track_history[best_tid].append((int(round(cx)), int(round(cy))))
                    track_types[best_tid] = det['type']
                    det['id'] = best_tid
                    updated_tracks.add(best_tid)

                # draw trajectories onto overlay and blend
                try:
                    overlay = frame.copy()
                    alpha = float(CONFIG.get('TRAJECTORY_ALPHA', 1.0))
                    base_thickness = max(1, int(round(CONFIG.get('TRAJECTORY_THICKNESS', 4))))
                    smooth_w = max(1, int(CONFIG.get('TRAJECTORY_SMOOTH_WINDOW', 3)))
                    for tid, pts in track_history.items():
                        if pts and len(pts) >= 2:
                            # smoothing
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
                            base_color = hex_to_bgr(CLASS_COLORS_HEX.get(track_types.get(tid, 'Car'), '#24C0C0'))
                            for idx in range(1, n):
                                p0 = smoothed[idx - 1]
                                p1 = smoothed[idx]
                                age = (idx - 1) / max(1, n - 2)
                                seg_alpha = float(CONFIG.get('TRAJECTORY_MIN_ALPHA', 0.15)) + (1.0 - float(CONFIG.get('TRAJECTORY_MIN_ALPHA', 0.15))) * age
                                seg_color = tuple(int(max(0, min(255, c * seg_alpha))) for c in base_color)
                                try:
                                    glow_thickness = max(1, base_thickness + 2)
                                    cv2.line(overlay, tuple(map(int, p0)), tuple(map(int, p1)), (10,10,10), glow_thickness, lineType=cv2.LINE_AA)
                                except Exception:
                                    pass
                                try:
                                    cv2.line(overlay, tuple(map(int, p0)), tuple(map(int, p1)), seg_color, base_thickness, lineType=cv2.LINE_AA)
                                except Exception:
                                    pass
                            last_pt = tuple(int(x) for x in smoothed[-1])
                            try:
                                cv2.circle(overlay, last_pt, max(2, int(round(3)))+1, (10,10,10), -1, lineType=cv2.LINE_AA)
                            except Exception:
                                pass
                            cv2.circle(overlay, last_pt, max(2, int(round(3))), base_color, -1, lineType=cv2.LINE_AA)
                    # blend overlay into frame with overall alpha
                    cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)
                except Exception:
                    pass

                # Draw bounding boxes and labels
                for det in detections:
                    x1, y1, x2, y2 = det['bounding_box']
                    cv2.rectangle(frame, (int(x1), int(y1)), (int(x2), int(y2)), (0, 255, 0), 2)
                    label = f"{det['type']} ({det['confidence']:.2f})"
                    cv2.putText(frame, label, (int(x1), int(y1) - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

                frame_to_send = frame

            # If detections exist (fallback path), draw simple boxes/labels.
            if detections and proc_frame is None:
                pass

            ret, buffer = cv2.imencode('.jpg', frame_to_send)
            if not ret:
                continue

            yield (b'--frame\r\n'
                   b'Content-Type: image/jpeg\r\n\r\n' + buffer.tobytes() + b'\r\n')

    def __del__(self):
        for camera_id in list(self.active_cameras.keys()):
            self.stop_camera(camera_id)

# Global instance is created lazily to avoid heavy YOLO initialization during
# backend startup. The camera stack is only used when a real camera request
# is made, and the existing routes all go through get_camera_manager().
_camera_manager = None


def get_camera_manager():
    global _camera_manager
    if _camera_manager is None:
        _camera_manager = LiveCameraManager()
        try:
            _camera_manager.setup_database()
        except Exception:
            logging.exception("Failed to initialize camera database")
    return _camera_manager


camera_manager = None