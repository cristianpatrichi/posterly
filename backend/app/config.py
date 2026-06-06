"""Runtime configuration, read from the environment at CALL TIME.

Lazy getters (like ``storage.data_root``) so tests can set env vars before the
app is imported and so values can be overridden per process without import-order
surprises. Nothing here touches the network or filesystem.
"""

from __future__ import annotations

import os
import secrets
from urllib.parse import urlparse

# Ephemeral per-process fallback so local dev works without configuration.
# In production SESSION_SECRET MUST be set, otherwise sessions are invalidated on
# every restart (and would differ across workers).
_FALLBACK_SECRET = secrets.token_urlsafe(48)


def _int_env(name: str, default: int) -> int:
    """Read an int env var, falling back to default when unset, empty, or
    non-numeric. (Compose forwards unset vars as "" via ${VAR:-}, so empty must
    be treated as 'use the default' rather than crashing on int("").)"""
    raw = os.environ.get(name)
    if raw is None or raw.strip() == "":
        return default
    try:
        return int(raw)
    except ValueError:
        return default


def app_origin() -> str:
    """The canonical public origin, e.g. https://collage.example.com."""
    return os.environ.get("APP_ORIGIN", "http://localhost:5173").rstrip("/")


def _is_local_origin() -> bool:
    parsed = urlparse(app_origin())
    return parsed.scheme == "http" and parsed.hostname in {
        "localhost",
        "127.0.0.1",
        "::1",
    }


def include_dev_origins() -> bool:
    """Whether localhost origins are trusted for CORS/CSRF. On by default only
    when APP_ORIGIN is itself localhost (dev); a public deployment excludes them
    unless INCLUDE_DEV_ORIGINS is explicitly set."""
    value = os.environ.get("INCLUDE_DEV_ORIGINS")
    if value is not None and value.strip() != "":
        return value.strip().lower() in ("1", "true", "yes", "on")
    return _is_local_origin()


def allowed_origins() -> list[str]:
    """Origins accepted for CORS and the CSRF Origin check: the public origin,
    plus local dev origins only when include_dev_origins() is true, plus any
    comma-separated EXTRA_ORIGINS."""
    origins = {app_origin()}
    if include_dev_origins():
        origins.update(
            {
                "http://localhost:5173",
                "http://127.0.0.1:5173",
                "http://localhost:8787",
                "http://127.0.0.1:8787",
            }
        )
    for extra in os.environ.get("EXTRA_ORIGINS", "").split(","):
        extra = extra.strip().rstrip("/")
        if extra:
            origins.add(extra)
    return sorted(origins)


def trusted_hosts() -> list[str]:
    """Hostnames accepted by TrustedHostMiddleware (S-004). Always allows
    localhost/127.0.0.1 (dev + Docker healthcheck), the APP_ORIGIN host, and any
    comma-separated EXTRA_TRUSTED_HOSTS."""
    hosts = {"localhost", "127.0.0.1"}
    parsed = urlparse(app_origin())
    if parsed.hostname:
        hosts.add(parsed.hostname)
    for extra in os.environ.get("EXTRA_TRUSTED_HOSTS", "").split(","):
        extra = extra.strip()
        if extra:
            hosts.add(extra)
    return sorted(hosts)


def session_secret() -> str:
    value = os.environ.get("SESSION_SECRET", "")
    if len(value) >= 32:
        return value
    if _is_local_origin():
        return value or _FALLBACK_SECRET
    raise RuntimeError(
        "SESSION_SECRET must be set to at least 32 characters for a public APP_ORIGIN."
    )


def session_ttl_seconds() -> int:
    # 7 days. Sessions are "sliding" (see auth.refresh_session_if_stale): an
    # active user's cookie is rolled forward, so this is effectively an idle
    # timeout; an unused session expires after this long.
    return _int_env("SESSION_TTL_SECONDS", 7 * 24 * 3600)


def session_refresh_threshold_seconds() -> int:
    """Re-issue the session cookie once it's older than this (default 1 day), so
    we roll the 7-day window forward at most once per day instead of every call."""
    return _int_env("SESSION_REFRESH_THRESHOLD_SECONDS", 24 * 3600)


def retention_days() -> int:
    """Auto-delete collages untouched for this many days. 0 disables deletion."""
    return _int_env("RETENTION_DAYS", 60)


def cleanup_interval_seconds() -> int:
    return _int_env("CLEANUP_INTERVAL_SECONDS", 24 * 3600)


def max_files_per_upload() -> int:
    return _int_env("MAX_FILES_PER_UPLOAD", 60)


def max_upload_bytes() -> int:
    """Per-file upload cap (S-002). 40 MB comfortably covers large DSLR/phone
    photos while bounding per-file memory. Lower it for untrusted deployments."""
    return _int_env("MAX_UPLOAD_BYTES", 40 * 1024 * 1024)


def max_upload_request_bytes() -> int:
    """Aggregate upload body budget after multipart decoding. The frontend sends
    roughly 15 MB batches; 50 MB leaves room for one maximum-size photo and
    multipart overhead while preventing multi-gigabyte authenticated requests."""
    return _int_env("MAX_UPLOAD_REQUEST_BYTES", 50 * 1024 * 1024)


def max_image_megapixels() -> int:
    """Reject uploads whose decoded pixel count exceeds this (anti decompression
    bomb / memory DoS). 80 MP covers any normal camera photo."""
    return _int_env("MAX_IMAGE_MEGAPIXELS", 80)


def max_export_megapixels() -> int:
    """Maximum rendered output size (S-001). Defaults to 200 MP so every
    supported paper size renders -- including the 1 m (139 MP) and 1.4 m
    (195 MP) posters. Lower it to disable the biggest sizes for untrusted /
    multi-tenant deployments; the 6/min export rate limit is the primary guard."""
    return _int_env("MAX_EXPORT_MEGAPIXELS", 200)


def cookie_secure() -> bool:
    """Whether to mark the session cookie Secure. Defaults to true on https."""
    value = os.environ.get("COOKIE_SECURE")
    if value is not None and value.strip() != "":
        return value.strip().lower() in ("1", "true", "yes", "on")
    # Unset OR empty (compose ${COOKIE_SECURE:-}) -> auto: Secure on https.
    return app_origin().startswith("https://")


def google_client_id() -> str:
    return os.environ.get("GOOGLE_CLIENT_ID", "")


def turnstile_secret() -> str:
    """Cloudflare Turnstile secret key. When empty, Turnstile is DISABLED (the
    login endpoints don't require/verify a token), so the app works without it."""
    return os.environ.get("TURNSTILE_SECRET", "")


def resend_api_key() -> str:
    return os.environ.get("RESEND_API_KEY", "")


def resend_from() -> str:
    # Default to Resend's sandbox sender; only delivers to the account owner
    # until a real domain is verified (set RESEND_FROM to your verified address).
    return os.environ.get("RESEND_FROM", "Posterly <onboarding@resend.dev>")


def brand_name() -> str:
    """Public brand name used in transactional emails (the OTP message). Defaults
    to "Posterly"; set BRAND_NAME to override. The SPA's brand is configured
    separately at build time via VITE_BRAND_NAME (see frontend/src/brand.ts)."""
    return os.environ.get("BRAND_NAME", "").strip() or "Posterly"


def enable_docs() -> bool:
    """Expose FastAPI's /docs, /redoc, /openapi.json. Off by default so the API
    surface isn't published in production; set ENABLE_DOCS=1 in dev."""
    return os.environ.get("ENABLE_DOCS", "").strip().lower() in ("1", "true", "yes")


def otp_dev_expose() -> bool:
    """LOCAL DEV/QA ONLY: when true, /api/auth/otp/request returns the code in
    its JSON response so automated tests can read it without real email."""
    return os.environ.get("OTP_DEV_EXPOSE", "").strip().lower() in ("1", "true", "yes")
