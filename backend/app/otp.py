"""Email one-time-passcode store and verification.

Codes are stored on disk (no DB), hashed (peppered), single-use, short-lived,
with per-email send rate limiting and a verify-attempt cap to resist brute force.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import secrets
import threading
import time
from contextlib import contextmanager
from pathlib import Path

from . import config, storage

CODE_TTL_SECONDS = 600  # 10 minutes
SEND_COOLDOWN_SECONDS = 45  # min gap between code requests for one email
MAX_SENDS_PER_HOUR = 5
MAX_ATTEMPTS = 5

# Per-email lock so the verify read-modify-write (load -> attempts+1 -> save) is
# atomic. Without it, concurrent guesses all read the same attempt count and the
# last save wins, so the counter advances by 1 instead of N -- letting an
# attacker fire many more than MAX_ATTEMPTS guesses per code (brute-force cap
# bypass). Keyed by the email's sha digest; FastAPI runs these sync endpoints in
# a threadpool, so the lock is what actually serializes them.
_email_locks: dict[str, threading.Lock] = {}
_email_locks_guard = threading.Lock()


@contextmanager
def _email_lock(email: str):
    key = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    with _email_locks_guard:
        lock = _email_locks.get(key)
        if lock is None:
            lock = threading.Lock()
            _email_locks[key] = lock
    with lock:
        yield


def _otp_dir() -> Path:
    return storage.data_root() / "otp"


def _otp_path(email: str) -> Path:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return _otp_dir() / f"{digest}.json"


def _hash_code(email: str, code: str) -> str:
    # Peppered with the server secret so a leaked file is not trivially reversible.
    msg = f"{email.strip().lower()}:{code}".encode("utf-8")
    return hmac.new(config.session_secret().encode("utf-8"), msg, hashlib.sha256).hexdigest()


def _load(email: str) -> dict | None:
    try:
        return json.loads(_otp_path(email).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _save(email: str, record: dict) -> None:
    # Atomic + unique-temp write (shared with project saves) so concurrent OTP
    # writes for one email never tear the file.
    storage._atomic_write_text(_otp_path(email), json.dumps(record))


def _clear(email: str) -> None:
    try:
        _otp_path(email).unlink()
    except OSError:
        pass


def create_otp(email: str) -> tuple[str | None, str | None]:
    """Generate, store and return a code, or (None, reason) when rate-limited.

    reason is one of: "cooldown", "rate_limited".
    """
    email = email.strip().lower()
    now = int(time.time())
    with _email_lock(email):
        record = _load(email) or {}
        sends = [t for t in record.get("sends", []) if now - t < 3600]

        if sends and now - max(sends) < SEND_COOLDOWN_SECONDS:
            return None, "cooldown"
        if len(sends) >= MAX_SENDS_PER_HOUR:
            return None, "rate_limited"

        code = f"{secrets.randbelow(100_000_000):08d}"
        sends.append(now)
        _save(
            email,
            {
                "code_hash": _hash_code(email, code),
                "expires_at": now + CODE_TTL_SECONDS,
                "attempts": 0,
                "sends": sends,
            },
        )
    return code, None


def verify_otp(email: str, code: str) -> bool:
    """Verify a code: constant-time, single-use, expiry- and attempt-capped."""
    email = email.strip().lower()
    code = (code or "").strip()
    with _email_lock(email):
        record = _load(email)
        if not record:
            return False
        if int(time.time()) > int(record.get("expires_at", 0)):
            _clear(email)
            return False

        record["attempts"] = int(record.get("attempts", 0)) + 1
        if record["attempts"] > MAX_ATTEMPTS:
            _clear(email)  # too many guesses -> burn the code
            return False

        expected = str(record.get("code_hash", ""))
        if code and hmac.compare_digest(expected, _hash_code(email, code)):
            _clear(email)  # single use
            return True

        _save(email, record)  # persist the incremented attempt counter
        return False
