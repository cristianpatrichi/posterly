"""Authentication & authorization helpers: sessions, the allow-list, the CSRF
Origin check, and Google ID-token verification.

Sessions are stateless: a signed JWT (HS256) in an HttpOnly + Secure + SameSite
cookie. No server-side session store is needed (matches the filesystem design).
"""

from __future__ import annotations

import time

import jwt
from fastapi import HTTPException, Request, Response

from . import config, storage

SESSION_COOKIE = "session"


# --------------------------------------------------------------------------- #
# Sessions
# --------------------------------------------------------------------------- #
def create_session_token(email: str) -> str:
    now = int(time.time())
    payload = {
        "sub": email.lower(),
        "iat": now,
        "exp": now + config.session_ttl_seconds(),
    }
    return jwt.encode(payload, config.session_secret(), algorithm="HS256")


def session_payload(token: str) -> dict | None:
    """Decode + verify a session token, returning its claims (incl. iat/sub)."""
    try:
        return jwt.decode(token, config.session_secret(), algorithms=["HS256"])
    except jwt.PyJWTError:
        return None


def verify_session_token(token: str) -> str | None:
    payload = session_payload(token)
    if not payload:
        return None
    sub = payload.get("sub")
    return sub if isinstance(sub, str) and sub else None


def set_session_cookie(response: Response, email: str) -> None:
    response.set_cookie(
        SESSION_COOKIE,
        create_session_token(email),
        max_age=config.session_ttl_seconds(),
        httponly=True,
        secure=config.cookie_secure(),
        samesite="lax",
        path="/",
    )


def clear_session_cookie(response: Response) -> None:
    response.delete_cookie(SESSION_COOKIE, path="/", samesite="lax")


def refresh_session_if_stale(request: Request, response: Response) -> None:
    """Sliding sessions: if the caller has a valid session cookie that's older
    than the refresh threshold, mint a fresh one (rolls the idle-expiry window
    forward). Idle sessions are never refreshed, so they expire on schedule."""
    token = request.cookies.get(SESSION_COOKIE)
    if not token:
        return
    payload = session_payload(token)
    if not payload:
        return
    iat = payload.get("iat")
    sub = payload.get("sub")
    if not isinstance(sub, str) or not sub or not isinstance(iat, int):
        return
    if iat < storage.user_session_epoch(sub):
        return  # revoked -> don't roll it forward
    if int(time.time()) - iat > config.session_refresh_threshold_seconds():
        set_session_cookie(response, sub)


def current_user(request: Request) -> str:
    """FastAPI dependency: return the authenticated email or raise 401."""
    token = request.cookies.get(SESSION_COOKIE)
    payload = session_payload(token) if token else None
    email = payload.get("sub") if payload else None
    iat = payload.get("iat") if payload else None
    if not isinstance(email, str) or not email or not isinstance(iat, int):
        raise HTTPException(status_code=401, detail="authentication required")
    # Revoked? (logout bumps the user's epoch, killing all prior tokens.)
    if iat < storage.user_session_epoch(email):
        raise HTTPException(status_code=401, detail="session expired")
    return email


# --------------------------------------------------------------------------- #
# CSRF: reject cross-origin state-changing requests (defense beyond SameSite).
# --------------------------------------------------------------------------- #
_SAFE_METHODS = {"GET", "HEAD", "OPTIONS"}


def enforce_same_origin(request: Request) -> None:
    """Raise 403 if a mutating request carries a foreign Origin. Requests with no
    Origin header (non-browser clients / same-origin GETs) are allowed — those
    are not a CSRF vector since an attacker cannot forge a victim's cookies there.
    """
    if request.method in _SAFE_METHODS:
        return
    origin = request.headers.get("origin")
    if origin and origin.rstrip("/") not in config.allowed_origins():
        raise HTTPException(status_code=403, detail="cross-origin request blocked")


# --------------------------------------------------------------------------- #
# Allow-list (read fresh each call -> editable without a restart)
# --------------------------------------------------------------------------- #
def allowed_emails_path():
    return storage.data_root() / "allowed_emails.txt"


def is_email_allowed(email: str) -> bool:
    """True if ``email`` matches an entry in allowed_emails.txt. Supports exact
    addresses, ``*@domain.com`` wildcards, and a lone ``*`` (allow anyone)."""
    email = (email or "").strip().lower()
    if not email or "@" not in email:
        return False
    path = allowed_emails_path()
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return False
    domain = email.split("@", 1)[1]
    for raw in lines:
        entry = raw.strip().lower()
        if not entry or entry.startswith("#"):
            continue
        if entry == "*":
            return True
        if entry.startswith("*@") and domain == entry[2:]:
            return True
        if entry == email:
            return True
    return False


# --------------------------------------------------------------------------- #
# Cloudflare Turnstile (anti-bot on the login endpoints)
# --------------------------------------------------------------------------- #
def verify_turnstile(token: str | None, remote_ip: str | None = None) -> bool:
    """Validate a Cloudflare Turnstile token server-side.

    Returns True (allow) when Turnstile is NOT configured, so the app keeps
    working until a secret is set. When configured, a missing/invalid/unverified
    token returns False and the caller rejects the request."""
    secret = config.turnstile_secret()
    if not secret:
        return True
    if not token:
        return False
    try:
        import httpx

        data = {"secret": secret, "response": token}
        if remote_ip:
            data["remoteip"] = remote_ip
        resp = httpx.post(
            "https://challenges.cloudflare.com/turnstile/v0/siteverify",
            data=data,
            timeout=8.0,
        )
        return bool(resp.json().get("success"))
    except Exception:
        return False


# --------------------------------------------------------------------------- #
# Google ID-token verification
# --------------------------------------------------------------------------- #
def verify_google_credential(credential: str) -> str | None:
    """Verify a Google Identity Services ID token and return the verified email,
    or None if invalid / unverified / not for our client id."""
    client_id = config.google_client_id()
    if not client_id or not credential:
        return None
    try:
        from google.auth.transport import requests as google_requests
        from google.oauth2 import id_token

        info = id_token.verify_oauth2_token(
            credential, google_requests.Request(), client_id
        )
    except Exception:
        return None
    if info.get("iss") not in ("accounts.google.com", "https://accounts.google.com"):
        return None
    if not info.get("email_verified"):
        return None
    email = info.get("email")
    return email.lower() if isinstance(email, str) and email else None
