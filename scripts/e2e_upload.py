import http.client, json, time, os, sys
import cv2
import numpy as np

HOST = "127.0.0.1"; PORT = 5000

# --- 1. Generate a small synthetic traffic video ---------------------------
video_path = os.path.join(os.path.dirname(__file__), "fixture_traffic.mp4")
cap = cv2.VideoCapture(video_path)
if not cap.isOpened():
    w, h, fps, n = 640, 360, 10, 60
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    out = cv2.VideoWriter(video_path, fourcc, fps, (w, h))
    for i in range(n):
        frame = np.zeros((h, w, 3), dtype=np.uint8)
        x = int((i * 6) % w)
        cv2.rectangle(frame, (x, 120), (x + 60, 200), (40, 120, 200), -1)
        cv2.rectangle(frame, (w - x - 70, 200), (w - x, 280), (200, 160, 40), -1)
        cv2.putText(frame, "TRAFFIC FIXTURE", (20, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (255,255,255), 2)
        out.write(frame)
    out.release()
    print("generated fixture:", video_path)
else:
    cap.release()
    print("fixture exists:", video_path)

def raw(method, path, body=None, cookie=None, content_type=None, timeout=30):
    conn = http.client.HTTPConnection(HOST, PORT, timeout=timeout)
    headers = {}
    if content_type: headers["Content-Type"] = content_type
    if cookie: headers["Cookie"] = cookie
    conn.request(method, path, body=body, headers=headers)
    r = conn.getresponse()
    data = r.read()
    sc = r.getheader("Set-Cookie")
    conn.close()
    return r.status, data, sc

def cookie(sc): return sc.split(";")[0] if sc else None

# --- 2. login as admin -----------------------------------------------------
s, body, sc = raw("POST", "/api/auth/login",
                  json.dumps({"username": "admin", "password": "admin123"}).encode(),
                  content_type="application/json")
print("\nlogin:", s, body.decode())
ck = cookie(sc)

# --- 3. POST /api/upload with multipart ------------------------------------
boundary = "----AuditBoundary123"
file_size = os.path.getsize(video_path)
with open(video_path, "rb") as fh: fbytes = fh.read()
body = (
    ("--" + boundary + "\r\n"
     'Content-Disposition: form-data; name="video"; filename="fixture_traffic.mp4"\r\n'
     "Content-Type: video/mp4\r\n\r\n").encode() + fbytes +
    ("\r\n--" + boundary + "--\r\n").encode()
)
s, data, _ = raw("POST", "/api/upload", body=body, cookie=ck,
                 content_type="multipart/form-data; boundary=" + boundary, timeout=60)
print("\nupload status:", s)
up = json.loads(data.decode())
print("upload:", json.dumps(up)[:400])

if s != 200 or not up.get("job_id"):
    print("UPLOAD FAILED - aborting")
    sys.exit(1)
job_id = up["job_id"]

# --- 4. poll job status ----------------------------------------------------
sess_id = None
for poll_i in range(240):
    s, data, _ = raw("GET", "/api/jobs/%s/status" % job_id, cookie=ck)
    st = json.loads(data.decode())
    status = st.get("status")
    if status in ("done", "error", "cancelled"):
        print("\njob final:", json.dumps(st)[:600])
        break
    if poll_i % 15 == 0:
        print("  job status:", status, "frame", st.get("frame"), "/", st.get("total_frames"))
    time.sleep(1)
else:
    print("JOB TIMED OUT")
    sys.exit(1)

# --- 5. resolve session + summary ------------------------------------------
s, data, _ = raw("GET", "/api/sessions", cookie=ck)
sessions = json.loads(data.decode()).get("data", [])
if sessions:
    sess_id = sessions[0]["id"]
    print("\nlatest session:", sess_id, "has_processed:", sessions[0].get("has_processed"))
    s, data, _ = raw("GET", "/api/sessions/%s/summary" % sess_id, cookie=ck)
    print("summary:", data.decode()[:500])

# --- 6. Inspect processed-video codec ---------------------------------------
s, data, _ = raw("GET", "/api/sessions/%s/processed-video?download=1" % sess_id, cookie=ck, timeout=30)
pv_path = os.path.join(os.path.dirname(__file__), "downloaded_processed.mp4")
with open(pv_path, "wb") as fh: fh.write(data)
print("\nprocessed-video HTTP:", s, "bytes:", len(data))
src = cv2.VideoCapture(pv_path)
if src.isOpened():
    codec = int(src.get(cv2.CAP_PROP_FOURCC))
    codec_str = "".join(chr((codec >> 8*i) & 0xFF) for i in range(4)).strip()
    fps = src.get(cv2.CAP_PROP_FPS)
    frames = src.get(cv2.CAP_PROP_FRAME_COUNT)
    ok, frame = src.read()
    # non-black check: mean pixel brightness of first frame
    brightness = float(np.mean(frame)) if ok else -1.0
    print("  input codec:", codec_str, "fps", round(fps,1), "frames", int(frames), "brightness", round(brightness,1))
    print("  PLAYABLE(H264):", codec_str.lower() in ("avc1", "h264"))
    src.release()
else:
    print("  COULD NOT OPEN processed video (unplayable)")

print("\nDONE")