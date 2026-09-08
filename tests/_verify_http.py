"""Standalone HTTP email transport verification (no Flask app, no network)."""
import sys, os, logging
import unittest.mock as m
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", "backend"))
import email_service as es

for k in ("SMTP_HOST", "EMAIL_ENABLED", "RESEND_API_KEY", "SENDGRID_API_KEY",
          "POSTMARK_SERVER_TOKEN", "BREVO_API_KEY", "MAILGUN_API_KEY",
          "MAILGUN_DOMAIN", "EMAIL_PROVIDER"):
    os.environ.pop(k, None)
os.environ["EMAIL_PROVIDER"] = "resend"
os.environ["RESEND_API_KEY"] = "mock-key-not-real"
os.environ["MAIL_FROM"] = "TI <no-reply@example.com>"

assert es.delivery_configured() is True
assert es._active_http_provider() == "resend"

class R:
    def __init__(s, c):
        s.status_code = c
        s.text = "{}"

# success -> True, correct endpoint
with m.patch("requests.post", return_value=R(200)) as p:
    assert es.send_otp_email("to@example.com", "T", "123456") is True
    assert "resend" in p.call_args[0][0]

# provider rejection -> False
with m.patch("requests.post", return_value=R(401)):
    assert es.send_otp_email("to@example.com", "T", "123456") is False

# timeout / network failure -> False, never raises
import requests
with m.patch("requests.post", side_effect=requests.exceptions.Timeout()):
    assert es.send_otp_email("to@example.com", "T", "123456") is False

# provider selection honours EMAIL_PROVIDER
os.environ["EMAIL_PROVIDER"] = "sendgrid"
os.environ["SENDGRID_API_KEY"] = "mock-key-not-real"
assert es._active_http_provider() == "sendgrid"
os.environ["EMAIL_PROVIDER"] = "brevo"
os.environ["BREVO_API_KEY"] = "mock-key-not-real"
assert es._active_http_provider() == "brevo"
os.environ["EMAIL_PROVIDER"] = "postmark"
os.environ["POSTMARK_SERVER_TOKEN"] = "mock-key-not-real"
assert es._active_http_provider() == "postmark"
os.environ["EMAIL_PROVIDER"] = "resend"

# no-secret logging
records = []
_h = logging.Handler()
_h.emit = lambda r: records.append(r.getMessage())
_lg = logging.getLogger("email_service")
_lg.addHandler(_h)
_lg.setLevel(logging.DEBUG)
with m.patch("requests.post", return_value=R(200)):
    assert es.send_otp_email("to@example.com", "T", "445566") is True
txt = " ".join(records)
assert "445566" not in txt, "OTP leaked into logs"
assert "mock-key-not-real" not in txt, "API key leaked into logs"

# force-disable wins over provider config
os.environ["EMAIL_ENABLED"] = "false"
assert es.delivery_configured() is False
os.environ.pop("EMAIL_ENABLED", None)

print("ALL EMAIL TRANSPORT CHECKS PASSED")

# Resend 400/4xx diagnostic classification check (mirrors production body)
records2 = []
_h2 = logging.Handler()
_h2.emit = lambda r: records2.append(r.getMessage())
_lg.addHandler(_h2)

class R400(R):
    def json(self):
        return {"name": "validation_error",
                "message": "You can only send testing emails to owner@resend.com"}

with m.patch("requests.post", return_value=R400(400)):
    assert es.send_otp_email("to@example.com", "T", "123456") is False
t2 = " ".join(records2)
assert "status=400" in t2 and "validation_error" in t2, t2
assert "detail=" in t2 and "testing emails" in t2, t2
assert "owner@resend.com" not in t2, "email leaked"
assert "123456" not in t2 and "mock-key-not-real" not in t2
print("CLASSIFICATION DETAIL LOGGING OK")

