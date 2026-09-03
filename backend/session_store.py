"""
Traffic Intelligence - session persistence (SQLite).

Stores one row per analysis run plus its raw detection records so the
Sessions page, evidence tables, and exports keep working across restarts.
The Postgres swap-in path is a driver + connection-string change; every
access goes through this module.
"""

import json
import os
import sqlite3
import threading

from config import CONFIG

_DB_PATH = CONFIG.get("DATABASE_PATH", "traffic_analysis.db")
# Resolve relative to the backend folder so cwd does not matter.
if not os.path.isabs(_DB_PATH):
    _DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), _DB_PATH)

_write_lock = threading.Lock()

_SCHEMA = """
CREATE TABLE IF NOT EXISTS ti_sessions (
    id               TEXT PRIMARY KEY,
    source_filename  TEXT,
    started_at       REAL,
    ended_at         REAL,
    duration_s       REAL,
    frames           INTEGER DEFAULT 0,
    vehicles_tracked INTEGER DEFAULT 0,
    detections       INTEGER DEFAULT 0,
    violations       INTEGER DEFAULT 0,
    status           TEXT DEFAULT 'completed'
);
CREATE TABLE IF NOT EXISTS ti_detections (
    session_id TEXT NOT NULL,
    frame      INTEGER NOT NULL,
    vehicle_id TEXT NOT NULL,
    type       TEXT,
    speed_kmh  REAL DEFAULT 0.0,
    bbox       TEXT,
    confidence REAL DEFAULT 0.0
);
CREATE INDEX IF NOT EXISTS idx_ti_det_session ON ti_detections(session_id, frame);
"""


def get_connection():
    conn = sqlite3.connect(_DB_PATH, timeout=15)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    with _write_lock:
        conn = get_connection()
        try:
            conn.executescript(_SCHEMA)
            # Migration-safe: add processed-video/metadata columns to pre-existing DBs.
            for alter in (
                "ALTER TABLE ti_sessions ADD COLUMN processed_video TEXT",
                "ALTER TABLE ti_sessions ADD COLUMN processed_bytes INTEGER",
                "ALTER TABLE ti_sessions ADD COLUMN fps REAL",
                "ALTER TABLE ti_sessions ADD COLUMN avg_confidence REAL",
                "ALTER TABLE ti_sessions ADD COLUMN avg_speed_kmh REAL",
                "ALTER TABLE ti_sessions ADD COLUMN processing_fps REAL",
                "ALTER TABLE ti_sessions ADD COLUMN created_at REAL",
            ):
                try:
                    conn.execute(alter)
                except sqlite3.OperationalError:
                    pass  # column already exists
            conn.commit()
        finally:
            conn.close()


def save_session(session_id, source_filename, started_at, ended_at, status, records,
                 processed_video=None, processed_bytes=None, fps=None,
                 avg_confidence=None, avg_speed_kmh=None, processing_fps=None):
    """Persist session metadata + raw detection records in one transaction."""
    frames = sorted({r["frame"] for r in records})
    fps = float(fps or CONFIG["VIDEO_FPS"])
    duration_s = round(((frames[-1] if frames else 0) + 1) / fps, 2) if records else 0.0
    payload = [
        (
            session_id,
            int(r["frame"]),
            r["vehicle_id"],
            r["type"],
            float(r["speed_kmh"] or 0.0),
            json.dumps([round(x, 1) for x in (r.get("bbox") or [])]),
            float(r["confidence"] or 0.0),
        )
        for r in records
    ]
    if avg_confidence is None:
        confidences = [r["confidence"] for r in records if r["confidence"] > 0]
        avg_confidence = round(sum(confidences) / len(confidences), 4) if confidences else None
    if avg_speed_kmh is None:
        speeds = [r["speed_kmh"] for r in records if r["speed_kmh"] > 0]
        avg_speed_kmh = round(sum(speeds) / len(speeds), 2) if speeds else None
    with _write_lock:
        conn = get_connection()
        try:
            conn.execute("BEGIN")
            conn.execute(
                """INSERT OR REPLACE INTO ti_sessions
                   (id, source_filename, started_at, ended_at, duration_s,
                    frames, vehicles_tracked, detections, violations, status,
                    processed_video, processed_bytes, fps, avg_confidence,
                    avg_speed_kmh, processing_fps, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    session_id,
                    source_filename,
                    started_at,
                    ended_at,
                    duration_s,
                    len(frames),
                    len({r["vehicle_id"] for r in records}),
                    len(records),
                    sum(1 for r in records if r["speed_kmh"] > CONFIG["ALERT_SPEED_THRESHOLD"]),
                    status,
                    processed_video,
                    processed_bytes,
                    fps,
                    avg_confidence,
                    avg_speed_kmh,
                    processing_fps,
                    started_at,
                ),
            )
            conn.execute("DELETE FROM ti_detections WHERE session_id = ?", (session_id,))
            conn.executemany(
                """INSERT INTO ti_detections
                   (session_id, frame, vehicle_id, type, speed_kmh, bbox, confidence)
                   VALUES (?, ?, ?, ?, ?, ?, ?)""",
                payload,
            )
            conn.commit()
        except sqlite3.Error:
            conn.rollback()
            raise
        finally:
            conn.close()


def list_sessions(limit=100):
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT id, source_filename, started_at, ended_at, duration_s,
                      frames, vehicles_tracked, detections, violations, status,
                      processed_video, processed_bytes
               FROM ti_sessions ORDER BY started_at DESC LIMIT ?""",
            (limit,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def get_session_meta(session_id):
    conn = get_connection()
    try:
        row = conn.execute(
            "SELECT * FROM ti_sessions WHERE id = ?", (session_id,)
        ).fetchone()
        return dict(row) if row else None
    finally:
        conn.close()


def get_session_records(session_id):
    """Return canonical raw detection records for a stored session."""
    conn = get_connection()
    try:
        rows = conn.execute(
            """SELECT frame, vehicle_id, type, speed_kmh, bbox, confidence
               FROM ti_detections WHERE session_id = ? ORDER BY frame ASC""",
            (session_id,),
        ).fetchall()
        records = []
        for r in rows:
            try:
                bbox = json.loads(r["bbox"]) if r["bbox"] else []
            except (ValueError, TypeError):
                bbox = []
            records.append({
                "frame": int(r["frame"]),
                "vehicle_id": r["vehicle_id"],
                "type": r["type"] or "",
                "speed_kmh": float(r["speed_kmh"] or 0.0),
                "bbox": bbox,
                "confidence": float(r["confidence"] or 0.0),
            })
        return records
    finally:
        conn.close()