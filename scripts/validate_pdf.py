"""One-off validation: regenerate the session PDF and verify its sections."""
import glob
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "backend"))

import api as api_module
import generate_report
import session_store

api_module.load_config_overrides()
session_store.init_db()

sessions = session_store.list_sessions(limit=1)
assert sessions, "No stored sessions found."
meta = sessions[0]
records = session_store.get_session_records(meta["id"])
print(f"session {meta['id'][:12]} status={meta['status']} records={len(records)}")

path = generate_report.build_report("pdf", meta["id"], records, status=meta["status"])
print("pdf:", path, os.path.getsize(path), "bytes")

# Extract text using pypdf if available, else raw stream scan.
text = ""
try:
    from pypdf import PdfReader
    reader = PdfReader(path)
    text = "\n".join((page.extract_text() or "") for page in reader.pages)
    print("extracted via pypdf,", len(reader.pages), "pages")
except ImportError:
    import re
    import zlib
    data = open(path, "rb").read()
    chunks = []
    for m in re.findall(rb"stream\r?\n(.*?)endstream", data, re.S):
        try:
            chunks.append(zlib.decompress(m))
        except Exception:
            pass
    text = b"\n".join(chunks).decode("latin-1", errors="ignore")
    print("extracted via raw zlib scan")

required = [
    "Executive Summary", "Session Information", "Runtime Analytics",
    "Vehicle Distribution", "Volume & Speed Trace", "Density Heatmap",
    "Violations", "Vehicle Evidence", "Computer Vision Pipeline",
    "Validation Evidence", "Limitations", "Conclusion",
    "Working with limitations", "ESTIMATED", "Unavailable",
]
missing = [s for s in required if s not in text]
print("sections present:", len(required) - len(missing), "/", len(required))
if missing:
    print("MISSING:", missing)
else:
    print("ALL SECTIONS PRESENT")