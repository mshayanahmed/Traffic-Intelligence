"""
Traffic Intelligence - derived analytics.

Everything here is computed at the API layer from the canonical raw detection
record (the exact shape of the CSV/XLSX export):

    {"frame": 0, "vehicle_id": "vehicle_1", "type": "Car",
     "speed_kmh": 0.0, "bbox": [414, 269, 485, 326], "confidence": 0.75}

Nothing in this module mutates its inputs. All values are plain Python types
so they are JSON-safe without extra sanitisation.
"""

import math
from collections import Counter, defaultdict

from config import CONFIG

# ---------------------------------------------------------------------------
# Shared colormap for detection-coordinate density.
# Used by the live heatmap card AND the PDF/XLSX exports so a session never
# looks like two different systems depending on where it is viewed.
# Palette stays closed: canvas -> amber -> red tokens only.
# ---------------------------------------------------------------------------
HEATMAP_COLORMAP = [
    [0.0, "#EAF0F1"],
    [0.35, "#F7EADA"],
    [0.65, "#C07818"],
    [1.0, "#C04848"],
]

VEHICLE_CLASSES = ["Car", "Bike", "Bus", "Truck", "Rickshaw"]

# ---------------------------------------------------------------------------
# ONE deterministic color per vehicle type, shared by every surface:
# server-drawn bounding boxes (converted to BGR), the donut chart, the
# legend swatches, and the evidence tables. Never re-derived per frame.
# ---------------------------------------------------------------------------
CLASS_COLORS_HEX = {
    "Car": "#24C0C0",
    "Bike": "#48A848",
    "Bus": "#0C489C",
    "Truck": "#C07818",
    "Rickshaw": "#E46060",
}


def hex_to_bgr(hex_color):
    """'#RRGGBB' -> OpenCV (B, G, R) tuple."""
    hex_color = (hex_color or "#FFFFFF").lstrip("#")
    r, g, b = int(hex_color[0:2], 16), int(hex_color[2:4], 16), int(hex_color[4:6], 16)
    return (b, g, r)


def frames_to_records(frames):
    """Flatten legacy in-memory frame dicts into canonical raw records."""
    records = []
    for frame in frames or []:
        frame_no = frame.get("frame", 0)
        for v in frame.get("vehicle_details", []) or []:
            bbox = v.get("bounding_box") or v.get("bbox") or []
            try:
                bbox = [float(x) for x in bbox][:4]
            except (TypeError, ValueError):
                bbox = []
            records.append({
                "frame": int(frame_no),
                "vehicle_id": str(v.get("id") or v.get("vehicle_id") or ""),
                "type": str(v.get("type", "")),
                "speed_kmh": float(v.get("speed", v.get("speed_kmh", 0.0)) or 0.0),
                "bbox": bbox,
                "confidence": float(v.get("confidence", 0.0) or 0.0),
            })
    return records


def _mean(values):
    return sum(values) / len(values) if values else 0.0


def capture_time_s(frame_no, fps=None):
    fps = fps or CONFIG["VIDEO_FPS"]
    return round(frame_no / float(fps), 2)


def build_summary(records, fps=None, status="completed"):
    """Runtime analytics summary - mirrors page 2 of the PDF report."""
    fps = fps or CONFIG["VIDEO_FPS"]
    if not records:
        return {
            "frames_processed": 0,
            "duration_s": 0.0,
            "current_vehicles": 0,
            "unique_vehicles": 0,
            "total_detections": 0,
            "average_speed_kmh": 0.0,
            "traffic_density": 0.0,
            "traffic_flow": "N/A",
            "violation_events": 0,
            "average_confidence": None,
            "processing_fps": None,
            "status": status,
        }

    frames = sorted({r["frame"] for r in records})
    last_frame = frames[-1]
    current = [r for r in records if r["frame"] == last_frame]
    speeds = [r["speed_kmh"] for r in records]
    positive_speeds = [s for s in speeds if s > 0]
    confidences = [r["confidence"] for r in records if r["confidence"] > 0]
    violations = coalesce_violations(records, fps=fps)

    area = (CONFIG["FRAME_WIDTH"] / CONFIG["PIXELS_PER_METER"]) * \
           (CONFIG["FRAME_HEIGHT"] / CONFIG["PIXELS_PER_METER"])
    density = round(len(current) / area, 4) if area > 0 else 0.0
    count = len(current)
    flow = "Heavy" if count >= 20 else "Moderate" if count >= 10 else "Low" if count > 0 else "N/A"

    duration_s = round((last_frame + 1) / float(fps), 2)

    return {
        "frames_processed": last_frame + 1,
        "duration_s": duration_s,
        "current_vehicles": count,
        "unique_vehicles": len({r["vehicle_id"] for r in records}),
        "total_detections": len(records),
        "average_speed_kmh": round(_mean(positive_speeds), 2),
        "traffic_density": density,
        "traffic_flow": flow,
        "violation_events": len(violations),
        "average_confidence": round(_mean(confidences), 4) if confidences else None,
        "processing_fps": None,
        "status": status,
    }


def build_tracked_vehicles(records):
    """One row per unique vehicle_id - powers the Vehicle Evidence table."""
    grouped = defaultdict(list)
    for r in records:
        grouped[r["vehicle_id"]].append(r)

    vehicles = []
    for vehicle_id, rows in grouped.items():
        rows.sort(key=lambda r: r["frame"])
        types = Counter(r["type"] for r in rows)
        # Most common type wins; ties resolve alphabetically for determinism.
        vehicle_type = sorted(types.items(), key=lambda kv: (-kv[1], kv[0]))[0][0]
        confidences = [r["confidence"] for r in rows if r["confidence"] > 0]
        threshold = CONFIG["ALERT_SPEED_THRESHOLD"]
        vehicles.append({
            "vehicle_id": vehicle_id,
            "type": vehicle_type,
            "last_speed_kmh": round(rows[-1]["speed_kmh"], 2),
            "max_speed_kmh": round(max(r["speed_kmh"] for r in rows), 2),
            "confidence": round(_mean(confidences), 4) if confidences else None,
            "first_seen_frame": rows[0]["frame"],
            "last_seen_frame": rows[-1]["frame"],
            "detections": len(rows),
            "ever_violated": any(r["speed_kmh"] > threshold for r in rows),
        })
    vehicles.sort(key=lambda v: _natural_key(v["vehicle_id"]))
    return vehicles


def _natural_key(text):
    """Sort 'vehicle_2' before 'vehicle_10'."""
    parts = []
    for chunk in str(text).split("_"):
        parts.append(int(chunk) if chunk.isdigit() else chunk.lower())
    return parts


def coalesce_violations(records, fps=None):
    """
    Coalesced violation events - powers the Violations Center.

    Consecutive speeding frames for the same vehicle_id are grouped into ONE
    event with a start-end capture-time window. A gap of more than one frame
    ends the run.
    """
    fps = fps or CONFIG["VIDEO_FPS"]
    threshold = CONFIG["ALERT_SPEED_THRESHOLD"]

    runs = defaultdict(list)
    for r in sorted(records, key=lambda x: x["frame"]):
        if r["speed_kmh"] <= threshold:
            continue
        key = r["vehicle_id"]
        run = runs.get(key)
        if run and r["frame"] - run[-1]["frame"] <= 1:
            run.append(r)
        else:
            runs[key] = [r]
            run = runs[key]
            # keep reference to the latest list for this vehicle
            _ = run

    events = []
    for vehicle_id, frames in runs.items():
        # Split into consecutive runs (gap > 1 frame starts a new event).
        split = [[frames[0]]]
        for prev, cur in zip(frames, frames[1:]):
            if cur["frame"] - prev["frame"] <= 1:
                split[-1].append(cur)
            else:
                split.append([cur])
        for run in split:
            peak = max(r["speed_kmh"] for r in run)
            ratio = peak / threshold if threshold > 0 else 0
            events.append({
                "vehicle_id": vehicle_id,
                "type": run[0]["type"],
                "peak_speed_kmh": round(peak, 2),
                "threshold_kmh": threshold,
                "start_frame": run[0]["frame"],
                "end_frame": run[-1]["frame"],
                "start_time_s": capture_time_s(run[0]["frame"], fps),
                "end_time_s": capture_time_s(run[-1]["frame"], fps),
                "duration_s": round(capture_time_s(run[-1]["frame"], fps)
                                    - capture_time_s(run[0]["frame"], fps), 2),
                "severity": "severe" if ratio > 1.10 else "warning",
                "status": "Detected",
            })
    events.sort(key=lambda e: (e["start_time_s"], _natural_key(e["vehicle_id"])))
    return events


def build_trace(records, fps=None):
    """Per-frame volume and estimated-speed series for the dual-line chart."""
    fps = fps or CONFIG["VIDEO_FPS"]
    by_frame = defaultdict(list)
    for r in records:
        by_frame[r["frame"]].append(r)

    trace = []
    for frame in sorted(by_frame):
        rows = by_frame[frame]
        positive = [r["speed_kmh"] for r in rows if r["speed_kmh"] > 0]
        trace.append({
            "time_s": capture_time_s(frame, fps),
            "vehicles": len(rows),
            "avg_speed_kmh": round(_mean(positive), 2) if positive else 0.0,
        })
    return trace


def build_distribution(records):
    """Vehicle-type distribution across ALL processed observations."""
    counts = Counter(r["type"] for r in records)
    ordered = {cls: counts.get(cls, 0) for cls in VEHICLE_CLASSES}
    for extra, n in counts.items():
        if extra not in ordered:
            ordered[extra] = n
    total = sum(ordered.values())
    distribution = []
    for label, count in ordered.items():
        distribution.append({
            "type": label,
            "count": count,
            "percent": round(count * 100.0 / total, 1) if total else 0.0,
        })
    return distribution


def build_heatmap(records, bins_x=32, bins_y=18):
    """
    Detection-coordinate density matrix for the ECharts heatmap component.

    Returns {x_labels, y_labels, data: [[x_idx, y_idx, value], ...], max}
    using one consistent cream-to-red colormap (see HEATMAP_COLORMAP).
    """
    width, height = CONFIG["FRAME_WIDTH"], CONFIG["FRAME_HEIGHT"]
    step_x = width / bins_x
    step_y = height / bins_y

    grid = defaultdict(int)
    max_value = 0
    for r in records:
        bbox = r.get("bbox") or []
        if len(bbox) != 4:
            continue
        cx = (bbox[0] + bbox[2]) / 2.0
        cy = (bbox[1] + bbox[3]) / 2.0
        if not (math.isfinite(cx) and math.isfinite(cy)):
            continue
        xi = min(bins_x - 1, max(0, int(cx / step_x)))
        yi = min(bins_y - 1, max(0, int(cy / step_y)))
        grid[(xi, yi)] += 1
        max_value = max(max_value, grid[(xi, yi)])

    data = [[xi, yi, count] for (xi, yi), count in grid.items()]
    return {
        "width": width,
        "height": height,
        "bins_x": bins_x,
        "bins_y": bins_y,
        "x_labels": [int((i + 0.5) * step_x) for i in range(bins_x)],
        "y_labels": [int((i + 0.5) * step_y) for i in range(bins_y)],
        "data": data,
        "max": max_value,
        "colormap": HEATMAP_COLORMAP,
    }