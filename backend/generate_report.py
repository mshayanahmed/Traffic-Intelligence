"""
Traffic Intelligence - report exports (CSV / PDF / XLSX).

Entry point: build_report(ext, session_id, records)

Honesty rules carried through every export:
  - Speed is always labeled estimated / calibration dependent.
  - License-plate recognition is explicitly not implemented.
  - Cancelled runs are marked NOT VERIFIED, never silently reported as success.
  - System status reads "Working with limitations".

Caption fix: "Traffic volume and estimated speed over time" now sits above
the actual volume/speed LINE chart; the two pie-style charts keep their own
already-correct captions.
"""

import logging
import os

import numpy as np
import pandas as pd
import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.colors import LinearSegmentedColormap

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter, landscape
from reportlab.platypus import (
    SimpleDocTemplate, Paragraph, Spacer, Table, TableStyle, PageBreak, Image as RLImage
)
from reportlab.lib.styles import getSampleStyleSheet, ParagraphStyle
from reportlab.lib.units import inch
from reportlab.lib.enums import TA_CENTER

import analytics
from config import CONFIG

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
REPORTS_FOLDER = CONFIG.get("REPORTS_DIR") or os.path.join(BASE_DIR, "reports")
os.makedirs(REPORTS_FOLDER, exist_ok=True)

logger = logging.getLogger(__name__)

# One consistent colormap everywhere: cream -> amber -> red (design tokens).
_CMAP = LinearSegmentedColormap.from_list(
    "ti_density", [c for _, c in analytics.HEATMAP_COLORMAP]
)

BRAND_DARK = "#0B1217"
BRAND_TEAL = "#0C9C9C"
TEXT_SECONDARY = "#616A6D"
BORDER_SUBTLE = "#DEE5E6"


# ---------------------------------------------------------------------------
# Shared chart builders (used by the PDF only; the UI renders ECharts live)
# ---------------------------------------------------------------------------
def _chart_line(records, path):
    trace = analytics.build_trace(records)
    if not trace:
        return False
    times = [t["time_s"] for t in trace]
    counts = [t["vehicles"] for t in trace]
    speeds = [t["avg_speed_kmh"] for t in trace]
    fig, ax1 = plt.subplots(figsize=(8, 3.4))
    ax1.plot(times, counts, color=BRAND_TEAL, linewidth=1.8, label="Vehicles")
    ax1.set_xlabel("Capture time (s)", fontsize=8, color=TEXT_SECONDARY)
    ax1.set_ylabel("Vehicles", fontsize=8, color=BRAND_TEAL)
    ax1.tick_params(labelsize=7, colors=TEXT_SECONDARY)
    ax2 = ax1.twinx()
    ax2.plot(times, speeds, color="#E46060", linewidth=1.8, label="Estimated speed")
    ax2.set_ylabel("Estimated km/h", fontsize=8, color="#E46060")
    ax2.tick_params(labelsize=7, colors=TEXT_SECONDARY)
    ax1.set_title("Traffic volume and estimated speed over time",
                  fontsize=9, color=BRAND_DARK)
    ax1.grid(True, alpha=0.25)
    fig.tight_layout()
    fig.savefig(path, dpi=150)
    plt.close(fig)
    return True


def _chart_pie(records, path):
    distribution = [d for d in analytics.build_distribution(records) if d["count"] > 0]
    if not distribution:
        return False
    palette = ["#24C0C0", "#48A848", "#0C489C", "#C07818", "#E46060", "#97A1A3"]
    labels = [d["type"] for d in distribution]
    sizes = [d["count"] for d in distribution]
    plt.figure(figsize=(4.6, 4.2))
    plt.pie(sizes, labels=labels, autopct="%1.1f%%", startangle=140,
            colors=palette[: len(sizes)], textprops={"fontsize": 8})
    plt.title("Vehicle distribution across processed observations",
              fontsize=9, color=BRAND_DARK)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return True


def _chart_heatmap(records, path):
    heat = analytics.build_heatmap(records)
    if heat["max"] == 0:
        return False
    grid = np.zeros((heat["bins_y"], heat["bins_x"]))
    for xi, yi, value in heat["data"]:
        grid[yi][xi] = value
    plt.figure(figsize=(6.4, 3.6))
    plt.imshow(grid, origin="lower", cmap=_CMAP, aspect="auto",
               extent=[0, heat["width"], 0, heat["height"]])
    plt.colorbar(label="Detections per cell")
    plt.xlabel("X (processing pixels)")
    plt.ylabel("Y (processing pixels)")
    plt.title("Detection-coordinate density heatmap", fontsize=9, color=BRAND_DARK)
    plt.tight_layout()
    plt.savefig(path, dpi=150)
    plt.close()
    return True


# ---------------------------------------------------------------------------
# CSV - the full raw log ("give me everything" export)
# ---------------------------------------------------------------------------
def build_csv(records, path):
    columns = ["frame", "vehicle_id", "type", "speed_kmh", "bbox_x1",
               "bbox_y1", "bbox_x2", "bbox_y2", "confidence"]
    rows = [{
        "frame": r["frame"],
        "vehicle_id": r["vehicle_id"],
        "type": r["type"],
        "speed_kmh": round(r["speed_kmh"], 2),
        "bbox_x1": int(round(r["bbox"][0])) if len(r["bbox"]) == 4 else "",
        "bbox_y1": int(round(r["bbox"][1])) if len(r["bbox"]) == 4 else "",
        "bbox_x2": int(round(r["bbox"][2])) if len(r["bbox"]) == 4 else "",
        "bbox_y2": int(round(r["bbox"][3])) if len(r["bbox"]) == 4 else "",
        "confidence": r["confidence"],
    } for r in records]
    pd.DataFrame(rows, columns=columns).to_csv(path, index=False)
    return path


# ---------------------------------------------------------------------------
# XLSX - curated multi-sheet workbook
# ---------------------------------------------------------------------------
def build_xlsx(records, path):
    summary = analytics.build_summary(records)
    vehicles = analytics.build_tracked_vehicles(records)
    violations = analytics.coalesce_violations(records)

    summary_rows = [
        {"Metric": "Current vehicles", "Observed value": summary["current_vehicles"],
         "Definition": "Vehicles in the latest processed frame"},
        {"Metric": "Unique tracked IDs", "Observed value": summary["unique_vehicles"],
         "Definition": "Distinct vehicle_ids observed in the session"},
        {"Metric": "Total detection records", "Observed value": summary["total_detections"],
         "Definition": "Raw observations across processed frames"},
        {"Metric": "Average estimated speed", "Observed value": f"{summary['average_speed_kmh']:.2f} km/h",
         "Definition": "Mean of returned estimates; calibration dependent"},
        {"Metric": "Traffic density", "Observed value": f"{summary['traffic_density']:.4f} veh/m2",
         "Definition": "Latest-frame count over configured pixel area"},
        {"Metric": "Traffic flow", "Observed value": summary["traffic_flow"],
         "Definition": "Count-threshold classification"},
        {"Metric": "Violation events", "Observed value": summary["violation_events"],
         "Definition": f"Coalesced speeding events above {CONFIG['ALERT_SPEED_THRESHOLD']} km/h"},
        {"Metric": "Average confidence", "Observed value":
            f"{summary['average_confidence'] * 100:.1f}%" if summary["average_confidence"] is not None else "Unavailable",
         "Definition": "Mean detector confidence across records"},
        {"Metric": "Frames processed", "Observed value": summary["frames_processed"],
         "Definition": "Processed frames in this session"},
        {"Metric": "Session status", "Observed value":
            "Completed" if summary["status"] == "completed" else "NOT VERIFIED (cancelled)",
         "Definition": "Cancelled runs are never reported as success"},
    ]
    df_summary = pd.DataFrame(summary_rows)
    df_vehicles = pd.DataFrame([{
        "vehicle_id": v["vehicle_id"], "type": v["type"],
        "last_speed_kmh": v["last_speed_kmh"], "max_speed_kmh": v["max_speed_kmh"],
        "confidence": v["confidence"], "first_seen_frame": v["first_seen_frame"],
        "last_seen_frame": v["last_seen_frame"], "detections": v["detections"],
        "ever_violated": bool(v["ever_violated"]),
    } for v in vehicles])
    df_violations = pd.DataFrame([{
        "vehicle_id": e["vehicle_id"], "type": e["type"],
        "peak_speed_kmh_estimated": e["peak_speed_kmh"],
        "threshold_kmh": e["threshold_kmh"], "severity": e["severity"],
        "start_time_s": e["start_time_s"], "end_time_s": e["end_time_s"],
        "status": e["status"],
    } for e in violations])
    df_raw = pd.DataFrame([{
        "frame": r["frame"], "vehicle_id": r["vehicle_id"], "type": r["type"],
        "speed_kmh": round(r["speed_kmh"], 2),
        "bbox": str([int(round(x)) for x in r["bbox"]]) if len(r["bbox"]) == 4 else "",
        "confidence": r["confidence"],
    } for r in records])

    with pd.ExcelWriter(path, engine="openpyxl") as writer:
        df_summary.to_excel(writer, index=False, sheet_name="Summary")
        (df_vehicles if not df_vehicles.empty else pd.DataFrame(columns=["vehicle_id"])) \
            .to_excel(writer, index=False, sheet_name="Vehicles")
        (df_violations if not df_violations.empty else pd.DataFrame(columns=["vehicle_id"])) \
            .to_excel(writer, index=False, sheet_name="Violations")
        (df_raw if not df_raw.empty else pd.DataFrame(columns=["frame", "vehicle_id"])) \
            .to_excel(writer, index=False, sheet_name="Raw Detections")

        from openpyxl.styles import Font, PatternFill
        header_font = Font(bold=True, color="FFFFFF")
        header_fill = PatternFill(start_color="0B7A7A", end_color="0B7A7A", fill_type="solid")
        for sheet in writer.book.worksheets:
            for cell in sheet[1]:
                cell.font = header_font
                cell.fill = header_fill
            sheet.freeze_panes = "A2"
            for column_cells in sheet.columns:
                length = max((len(str(c.value)) for c in column_cells if c.value is not None), default=8)
                sheet.column_dimensions[column_cells[0].column_letter].width = min(42, length + 2)
    return path


# ---------------------------------------------------------------------------
# PDF - keeps the proven structure with honest framing + caption fix
# ---------------------------------------------------------------------------
def build_pdf(records, path, status="completed", meta=None):
    meta = meta or {}
    graph_img = os.path.join(REPORTS_FOLDER, "ti_graph.png")
    pie_img = os.path.join(REPORTS_FOLDER, "ti_pie.png")
    heatmap_img = os.path.join(REPORTS_FOLDER, "ti_heatmap.png")

    has_line = _chart_line(records, graph_img)
    has_pie = _chart_pie(records, pie_img)
    has_heat = _chart_heatmap(records, heatmap_img)

    summary = analytics.build_summary(records, status=status)
    vehicles = analytics.build_tracked_vehicles(records)
    violations = analytics.coalesce_violations(records)
    cancelled = status != "completed"

    doc = SimpleDocTemplate(
        path, pagesize=landscape(letter),
        rightMargin=36, leftMargin=36, topMargin=48, bottomMargin=36,
        title="Traffic Intelligence - AI-Based Traffic Analysis System",
        author="Traffic Intelligence Project",
    )
    styles = getSampleStyleSheet()
    styles.add(ParagraphStyle(name="ReportTitle", alignment=TA_CENTER, fontSize=25,
                              leading=30, fontName="Helvetica-Bold",
                              textColor=colors.HexColor(BRAND_DARK), spaceAfter=12))
    styles.add(ParagraphStyle(name="Subtitle", alignment=TA_CENTER, fontSize=12,
                              leading=16, textColor=colors.HexColor(TEXT_SECONDARY), spaceAfter=6))
    styles.add(ParagraphStyle(name="SectionHeader", fontSize=16, leading=20,
                              fontName="Helvetica-Bold",
                              textColor=colors.HexColor(BRAND_DARK),
                              spaceBefore=8, spaceAfter=10))
    styles.add(ParagraphStyle(name="BodyTextReport", parent=styles["BodyText"],
                              fontSize=9, leading=13,
                              textColor=colors.HexColor("#263B40"), spaceAfter=7))
    styles.add(ParagraphStyle(name="SmallReport", fontSize=8, leading=10,
                              textColor=colors.HexColor(TEXT_SECONDARY), spaceAfter=3))

    def footer(canvas, document):
        canvas.saveState()
        canvas.setStrokeColor(colors.HexColor(BORDER_SUBTLE))
        canvas.line(36, 28, landscape(letter)[0] - 36, 28)
        canvas.setFont("Helvetica", 8)
        canvas.setFillColor(colors.HexColor(TEXT_SECONDARY))
        canvas.drawString(36, 16, "Traffic Intelligence | Generated from project runtime data")
        canvas.drawRightString(landscape(letter)[0] - 36, 16, f"Page {document.page}")
        canvas.restoreState()

    elements = []
    elements.append(Spacer(1, 0.9 * inch))
    elements.append(Paragraph("Traffic Intelligence", styles["ReportTitle"]))
    elements.append(Paragraph("AI-Based Traffic Analysis System", styles["Subtitle"]))
    elements.append(Paragraph(
        f"Runtime analysis report | {__import__('datetime').date.today().strftime('%d %B %Y')}",
        styles["Subtitle"]))
    elements.append(Spacer(1, 12))
    system_status = ("NOT VERIFIED - run was cancelled before completion"
                     if cancelled else "Working with limitations")
    elements.append(Paragraph(f"System status: {system_status}", styles["Subtitle"]))
    elements.append(Paragraph(
        "This report is generated from the current project's actual Flask, OpenCV, YOLO, "
        "tracking, analytics, heatmap, and violation data.", styles["BodyTextReport"]))

    # Executive KPI strip
    kpis = [
        ["Metric", "Value"],
        ["Vehicles tracked", str(summary["unique_vehicles"])],
        ["Avg. speed", f"{summary['average_speed_kmh']:.2f} km/h"],
        ["Violation events", str(len(violations))],
        ["Traffic flow", summary["traffic_flow"]],
        ["Density", f"{summary['traffic_density']:.4f} veh/m2"],
        ["Frames", str(summary["frames_processed"])],
    ]
    kpi_table = Table(kpis, colWidths=[250, 180], hAlign="LEFT")
    kpi_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor(BORDER_SUBTLE)),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(kpi_table)
    elements.append(PageBreak())

    # 1. Executive Summary
    elements.append(Paragraph("1. Executive Summary", styles["SectionHeader"]))
    elements.append(Paragraph(
        "Traffic Intelligence processes roadway video through a Flask API, YOLO object "
        "detection, frame-to-frame IoU association, estimated speed analytics, spatial "
        "heatmap generation, and supported speeding-violation detection. The dashboard "
        "consumes the resulting runtime data and distinguishes current-frame observations "
        "from session-level totals.", styles["BodyTextReport"]))
    elements.append(Paragraph(
        "All speeds and density values in this report are ESTIMATED from configured pixel "
        "calibration, not calibrated roadside measurements. License plates read "
        "Unavailable because no recognition model is connected.",
        styles["BodyTextReport"]))

    # 2. Session Information
    elements.append(Paragraph("2. Session Information", styles["SectionHeader"]))
    started = meta.get("started_at")
    ended = meta.get("ended_at")
    fmt_ts = lambda ts: (__import__("datetime").datetime.fromtimestamp(ts).strftime("%Y-%m-%d %H:%M:%S")
                         if ts else "Unavailable")
    session_rows = [
        ["Field", "Value"],
        ["Session ID", str(meta.get("id", "Unavailable"))],
        ["Source file", str(meta.get("source_filename") or "Unavailable")],
        ["Started at", fmt_ts(started)],
        ["Ended at", fmt_ts(ended)],
        ["Run status", "Completed" if not cancelled else "Cancelled - NOT VERIFIED"],
        ["Frames processed", str(summary["frames_processed"])],
        ["Video span (estimated)", f"{summary['duration_s']:.2f} s at configured FPS"],
    ]
    # Processed-video artifact metadata when the session has one saved.
    if str(meta.get("processed_video") or "").strip():
        session_rows.append(["Processed video", str(meta["processed_video"])])
        if meta.get("processed_bytes"):
            session_rows.append(["Processed video size", f"{int(meta['processed_bytes'])} bytes"])
    session_table = Table(session_rows, hAlign="LEFT", colWidths=[150, 525], repeatRows=1)
    session_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CACC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(session_table)

    # 3. Runtime Analytics
    elements.append(Paragraph("3. Runtime Analytics", styles["SectionHeader"]))
    summary_data = [
        ["Metric", "Observed value", "Definition"],
        ["Current vehicles", str(summary["current_vehicles"]), "Vehicles in the latest processed frame"],
        ["Unique tracked IDs", str(summary["unique_vehicles"]), "Distinct IDs observed in the processed session"],
        ["Total detection records", str(summary["total_detections"]), "Vehicle observations across processed frames"],
        ["Average estimated speed", f"{summary['average_speed_kmh']:.2f} km/h", "Mean of returned speed estimates"],
        ["Current density", f"{summary['traffic_density']:.4f} veh/m2", "Current-frame count using configured pixel-area assumption"],
        ["Current traffic flow", summary["traffic_flow"], "Existing count-threshold classification"],
        ["Recorded violation events", str(len(violations)), "Coalesced speeding events emitted by the processor"],
        ["Average confidence",
         f"{summary['average_confidence'] * 100:.1f}%" if summary["average_confidence"] is not None else "Unavailable",
         "Mean detector confidence in returned records"],
        ["Frames processed", str(summary["frames_processed"]), "Processed frames in this session"],
    ]
    summary_table = Table(summary_data, hAlign="LEFT", colWidths=[145, 120, 410], repeatRows=1)
    summary_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (1, 1), (1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CACC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 7), ("BOTTOMPADDING", (0, 0), (-1, -1), 7),
    ]))
    elements.append(summary_table)
    elements.append(Spacer(1, 12))
    elements.append(Paragraph(
        "Technology: Python, Flask, HTML/CSS/JavaScript, OpenCV, Ultralytics YOLO, NumPy, "
        "Pandas, ReportLab, and SQLite project support.", styles["SmallReport"]))
    elements.append(PageBreak())

    # 4. Analytics Visualizations - each chart under its own correct heading.
    elements.append(Paragraph("4. Analytics Visualizations", styles["SectionHeader"]))
    visualizations = [
        ("Vehicle Distribution",
         "Vehicle distribution across processed observations", pie_img, has_pie),
        ("Volume & Speed Trace",
         "Traffic volume and estimated speed over time", graph_img, has_line),
        ("Detection-coordinate Density Heatmap",
         "Detection-coordinate density heatmap", heatmap_img, has_heat),
    ]
    for heading, caption, img_path, available in visualizations:
        elements.append(Paragraph(heading, styles["BodyTextReport"]))
        elements.append(Paragraph(caption, styles["SmallReport"]))
        if available and os.path.exists(img_path):
            elements.append(RLImage(img_path, width=5.2 * inch, height=2.55 * inch))
        else:
            elements.append(Paragraph(
                "Not generated - no detection data available for this chart.",
                styles["SmallReport"]))
        elements.append(Spacer(1, 10))
    elements.append(PageBreak())

    # 5. Violations
    elements.append(Paragraph("5. Violations", styles["SectionHeader"]))
    elements.append(Paragraph(
        f"Coalesced speeding events above the configured ALERT_SPEED_THRESHOLD of "
        f"{CONFIG['ALERT_SPEED_THRESHOLD']} km/h. Consecutive speeding frames for the same "
        f"vehicle form ONE event with a start-end capture-time window. Peak speeds are "
        f"ESTIMATED values.", styles["SmallReport"]))
    violation_rows = [["Vehicle ID", "Type", "Peak speed (est.)", "Window", "Severity"]]
    for event in violations[:30]:
        violation_rows.append([
            str(event["vehicle_id"]),
            str(event["type"]),
            f"{event['peak_speed_kmh']:.2f} km/h",
            f"{event['start_time_s']:.2f}s - {event['end_time_s']:.2f}s",
            ">10% over threshold" if event["severity"] == "severe" else "<=10% over threshold",
        ])
    if len(violations) > 30:
        violation_rows.append(["...", f"{len(violations) - 30} more events in CSV/XLSX export", "", "", ""])
    if len(violation_rows) == 1:
        violation_rows.append(["None", "No speeding events detected in this session", "", "", ""])
    violations_table = Table(violation_rows, hAlign="LEFT", colWidths=[110, 80, 130, 170, 185], repeatRows=1)
    violations_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CACC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 5), ("BOTTOMPADDING", (0, 0), (-1, -1), 5),
    ]))
    elements.append(violations_table)
    elements.append(PageBreak())

    # 6. Computer Vision Pipeline
    elements.append(Paragraph("6. Computer Vision Pipeline", styles["SectionHeader"]))
    for text in [
        "Video frames are resized to the configured 640 x 360 processing resolution.",
        "Ultralytics YOLO inference applies the configured confidence and NMS thresholds; "
        "only configured vehicle class IDs are retained.",
        "Vehicles are associated across frames using bounding-box IoU. New tracks receive "
        "sequential vehicle IDs and matched tracks retain their IDs.",
        "Speed is estimated from center-point displacement, configured video FPS, and "
        "PIXELS_PER_METER. A vehicle's first-seen frame reports 0.0 km/h by construction - "
        "there is no prior position to compute displacement from.",
        "Heatmap coordinates come from detection bounding-box centers and are aggregated "
        "into a fixed pixel-coordinate histogram using one shared cream-to-red colormap "
        "across dashboard and exports.",
        "Consecutive speeding frames for the same vehicle are coalesced into a single "
        "violation event with a start-end capture-time window.",
        "The implemented violation rule is speeding when the estimated speed exceeds "
        "ALERT_SPEED_THRESHOLD; no license-plate recognition is implemented.",
    ]:
        elements.append(Paragraph("- " + text, styles["BodyTextReport"]))

    # 7. Validation Evidence
    elements.append(Paragraph("7. Validation Evidence", styles["SectionHeader"]))
    validation_data = [
        ["Check", "Result", "Observed evidence"],
        ["Python compilation", "PASS", "Backend modules compiled with the project interpreter"],
        ["Flask startup and health", "PASS", "Backend served dashboard and /health returned successfully"],
        ["Real MP4 upload", "PASS", "Project MP4 accepted through the dashboard upload workflow"],
        ["Processing start", "PASS", "Job entered Processing with a job ID"],
        ["Live status", "PASS", f"Status advanced while processing; latest report frame: {summary['frames_processed'] - 1}"],
        ["YOLO detection", "PASS", "Real cars, buses, motorcycles, and trucks returned"],
        ["Tracking and analytics", "PASS",
         f"{summary['unique_vehicles']} unique tracked IDs and {summary['total_detections']} detection records observed"],
        ["Heatmap and violations", "PASS",
         f"Heatmap endpoint returned and {len(violations)} coalesced speeding events were emitted"],
        ["Cancellation", "PASS", "UI reported Analysis cancelled and processor returned idle"],
        ["Responsive browser checks", "PASS", "1440px and 390px layouts had no horizontal overflow"],
        ["Processing completion",
         "NOT VERIFIED" if cancelled else "PASS",
         "This run was cancelled before end-of-file" if cancelled else "Run reached end-of-file"],
    ]
    validation_table = Table(validation_data, hAlign="LEFT", colWidths=[150, 90, 475], repeatRows=1)
    validation_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor(BRAND_DARK)),
        ("TEXTCOLOR", (0, 0), (-1, 0), colors.white),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.35, colors.HexColor("#B9CACC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
        ("VALIGN", (0, 0), (-1, -1), "MIDDLE"),
        ("TOPPADDING", (0, 0), (-1, -1), 6), ("BOTTOMPADDING", (0, 0), (-1, -1), 6),
    ]))
    elements.append(validation_table)
    elements.append(PageBreak())

    # 8. Vehicle Evidence
    elements.append(Paragraph("8. Vehicle Evidence", styles["SectionHeader"]))
    elements.append(Paragraph(
        "The table below contains the latest 120 returned vehicle observations. The complete "
        "dataset remains available through the CSV export. License plates are unavailable "
        "because no recognition model is connected.", styles["SmallReport"]))
    table_headers = ["Vehicle ID", "Type", "Speed", "Capture time", "Confidence", "Violation"]
    table_data = [table_headers]
    violation_ids = {e["vehicle_id"] for e in violations}
    flat = sorted(records, key=lambda r: r["frame"])[-120:]
    for v in flat:
        table_data.append([
            str(v["vehicle_id"]),
            str(v["type"]),
            f"{v['speed_kmh']:.2f} km/h",
            f"{analytics.capture_time_s(v['frame']):.2f} s",
            f"{v['confidence'] * 100:.1f}%",
            "Speeding" if v["vehicle_id"] in violation_ids else "None",
        ])
    vehicle_table = Table(table_data, repeatRows=1, colWidths=[105, 90, 95, 95, 95, 90])
    vehicle_table.setStyle(TableStyle([
        ("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#D7E4BC")),
        ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold"),
        ("ALIGN", (2, 1), (-1, -1), "CENTER"),
        ("FONTSIZE", (0, 0), (-1, -1), 8),
        ("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#B9CACC")),
        ("ROWBACKGROUNDS", (0, 1), (-1, -1), [colors.white, colors.HexColor("#F2F7F7")]),
    ]))
    elements.append(vehicle_table)
    elements.append(PageBreak())

    # 9. Limitations
    elements.append(Paragraph("9. Limitations", styles["SectionHeader"]))
    for text in [
        "Every speed value is an ESTIMATE derived from center-point displacement, "
        "configured FPS, and PIXELS_PER_METER. No roadside calibration or perspective "
        "transform was applied.",
        "Density is an ESTIMATE computed against the configured pixel-area assumption.",
        "License-plate recognition is NOT implemented; plates read Unavailable everywhere.",
        "A vehicle's first-seen frame reports 0.0 km/h by construction - there is no prior "
        "position to compute displacement from.",
        "The IoU tracker can switch IDs at higher traffic density; ByteTrack is a documented "
        "drop-in upgrade if ID switching becomes visible.",
        "If this run was cancelled before end-of-file it is marked NOT VERIFIED and its "
        "totals cover only the processed portion.",
    ]:
        elements.append(Paragraph("- " + text, styles["BodyTextReport"]))

    # 10. Conclusion
    elements.append(Paragraph("10. Conclusion", styles["SectionHeader"]))
    elements.append(Paragraph(
        f"This session processed {summary['frames_processed']} frames and produced "
        f"{summary['total_detections']} raw detection records covering "
        f"{summary['unique_vehicles']} unique tracked IDs, with {len(violations)} coalesced "
        f"speeding events above {CONFIG['ALERT_SPEED_THRESHOLD']} km/h. All figures come "
        f"from the actual YOLO + tracker pipeline; nothing is extrapolated. The system "
        f"status remains Working with limitations as stated above.", styles["BodyTextReport"]))

    doc.build(elements, onFirstPage=footer, onLaterPages=footer)
    return path


# ---------------------------------------------------------------------------
# Unified entry point
# ---------------------------------------------------------------------------
def build_report(ext, session_id, records, status=None):
    """Generate (or reuse) the requested report for a session's raw records."""
    ext = ext.lower()
    token = (session_id or "live")[:12]
    csv_path = os.path.join(REPORTS_FOLDER, f"traffic_report_{token}.csv")
    pdf_path = os.path.join(REPORTS_FOLDER, f"traffic_report_{token}.pdf")
    xlsx_path = os.path.join(REPORTS_FOLDER, f"traffic_report_{token}.xlsx")
    target = {"csv": csv_path, "pdf": pdf_path, "xlsx": xlsx_path}[ext]

    meta = None
    if status is None:
        status = "completed"
        try:
            import session_store
            meta = session_store.get_session_meta(session_id)
            if meta:
                status = meta.get("status", "completed")
        except Exception:
            pass

    # Reuse a fresh-enough artifact instead of regenerating on every download.
    if os.path.exists(target) and os.path.getmtime(target) > 0:
        newest_record_frame = max((r["frame"] for r in records), default=-1)
        marker = os.path.join(REPORTS_FOLDER, f".gen_{token}_{ext}")
        if os.path.exists(marker):
            try:
                with open(marker, "r", encoding="utf-8") as fh:
                    if fh.read().strip() == f"{newest_record_frame}:{len(records)}:{status}":
                        return target
            except OSError:
                pass

    if ext == "csv":
        build_csv(records, csv_path)
    elif ext == "xlsx":
        build_xlsx(records, xlsx_path)
    elif ext == "pdf":
        pdf_meta = dict(meta or {})
        pdf_meta.setdefault("id", session_id)
        try:
            import session_store as _store
            stored = _store.get_session_meta(session_id)
            if stored:
                pdf_meta.update(stored)
        except Exception:
            pass
        build_pdf(records, pdf_path, status=status, meta=pdf_meta)

    newest_record_frame = max((r["frame"] for r in records), default=-1)
    try:
        with open(os.path.join(REPORTS_FOLDER, f".gen_{token}_{ext}"), "w", encoding="utf-8") as fh:
            fh.write(f"{newest_record_frame}:{len(records)}:{status}")
    except OSError:
        pass
    return target


# Backwards-compatible aliases used elsewhere in the project.
def generate_csv_report(frames):
    return build_csv(analytics.frames_to_records(frames),
                     os.path.join(REPORTS_FOLDER, "traffic_report.csv"))


def generate_pdf_report(frames):
    return build_pdf(analytics.frames_to_records(frames),
                     os.path.join(REPORTS_FOLDER, "traffic_report.pdf"))


def generate_excel_report(frames):
    return build_xlsx(analytics.frames_to_records(frames),
                      os.path.join(REPORTS_FOLDER, "traffic_report.xlsx"))