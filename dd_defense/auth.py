"""Minimal session auth for the web app (stdlib only — hmac + secrets).

A single shared password gates the app (set DD_APP_PASSWORD). On success the
server sets a signed, expiring session cookie; protected routes check it. This is
intentionally simple — one operator / a small trusted team behind a login — not a
multi-tenant identity system. It exists so the app can be deployed or shared by
link without leaving the API key (and client data) open to the world.

Security choices:
  * Password compared with hmac.compare_digest (constant-time).
  * Cookie = base64(payload).hex(hmac_sha256(payload, secret)) — tamper-evident,
    carries an expiry; the server trusts only what it can re-sign.
  * Secret from DD_SECRET_KEY, else a per-process random key (sessions reset on
    restart, which is fine for a single small deployment).
  * Cookie flags: HttpOnly, SameSite=Lax, Path=/. Secure is added when the request
    is HTTPS (so local http dev still works).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time

COOKIE_NAME = "dd_session"
DEFAULT_TTL = 60 * 60 * 12  # 12 hours


def _secret():
    s = os.environ.get("DD_SECRET_KEY")
    if s:
        return s.encode("utf-8")
    # per-process fallback; stable within a run, resets on restart
    global _RUNTIME_SECRET
    try:
        return _RUNTIME_SECRET
    except NameError:
        _RUNTIME_SECRET = secrets.token_bytes(32)
        return _RUNTIME_SECRET


def app_password():
    """The configured gate password, or None if auth is disabled."""
    pw = os.environ.get("DD_APP_PASSWORD")
    return pw if pw else None


def auth_enabled():
    return app_password() is not None


def _sign(payload_bytes):
    return hmac.new(_secret(), payload_bytes, hashlib.sha256).hexdigest()


def make_token(subject="operator", ttl=DEFAULT_TTL):
    payload = {"sub": subject, "exp": int(time.time()) + ttl}
    raw = json.dumps(payload, separators=(",", ":")).encode("utf-8")
    b64 = base64.urlsafe_b64encode(raw).decode("ascii")
    return f"{b64}.{_sign(raw)}"


def verify_token(token):
    """Return the payload dict if the token is valid and unexpired, else None."""
    if not token or "." not in token:
        return None
    b64, sig = token.rsplit(".", 1)
    try:
        raw = base64.urlsafe_b64decode(b64.encode("ascii"))
    except (ValueError, TypeError):
        return None
    if not hmac.compare_digest(sig, _sign(raw)):
        return None
    try:
        payload = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if int(payload.get("exp", 0)) < int(time.time()):
        return None
    return payload


def check_password(candidate):
    pw = app_password()
    if pw is None:
        return False
    return hmac.compare_digest(str(candidate or ""), pw)
