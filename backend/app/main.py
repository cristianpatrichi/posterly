"""FastAPI application for the self-hosted collage web app.

Route handlers are intentionally thin -- they validate input, delegate to
``storage`` and ``render_service``, and shape responses. The data directory is
resolved lazily inside ``storage`` so this module is safe to import anywhere
(including before ``COLLAGE_DATA_DIR`` is set, e.g. in tests).
"""

from __future__ import annotations

import argparse
import mimetypes
from datetime import datetime
from pathlib import Path

# Some systems' mimetypes db lacks WebP; register it so image previews are served
# as image/webp (required because we send X-Content-Type-Options: nosniff).
mimetypes.add_type("image/webp", ".webp")

from fastapi import (
    BackgroundTasks,
    Depends,
    FastAPI,
    File,
    HTTPException,
    Request,
    Response,
    UploadFile,
)
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from slowapi import Limiter
from slowapi.errors import RateLimitExceeded
from slowapi.util import get_remote_address
from starlette.middleware.trustedhost import TrustedHostMiddleware

from collage_a4 import canvas_size_for

from . import auth, config, email_service, otp, render_service, storage
from .models import (
    AutoLayoutRequest,
    DeleteImagesRequest,
    ExportRequest,
    ExportResponse,
    GoogleAuthRequest,
    OtpRequestRequest,
    OtpVerifyRequest,
    ProjectOut,
    UpdateProjectRequest,
)

# Fail-fast: never expose OTP codes in a production (https) deployment.
if config.otp_dev_expose() and config.app_origin().startswith("https://"):
    raise RuntimeError(
        "OTP_DEV_EXPOSE must be disabled in production (APP_ORIGIN is https)."
    )

app = FastAPI(
    title="Collage Backend",
    version="1.0.0",
    # API docs/schema are off in production (set ENABLE_DOCS=1 to re-enable).
    docs_url="/api/docs" if config.enable_docs() else None,
    redoc_url="/api/redoc" if config.enable_docs() else None,
    openapi_url="/api/openapi.json" if config.enable_docs() else None,
)

# Rate limiting, keyed by client IP. Behind a reverse proxy run uvicorn with
# --proxy-headers --forwarded-allow-ips="*" so the real client IP is used.
limiter = Limiter(key_func=get_remote_address)
app.state.limiter = limiter


@app.exception_handler(RateLimitExceeded)
def _rate_limit_handler(request: Request, exc: RateLimitExceeded) -> JSONResponse:
    return JSONResponse({"detail": "too many requests, slow down"}, status_code=429)


# CORS restricted to known origins, WITH credentials so the session cookie is
# accepted on cross-origin dev (Vite). In prod the SPA is same-origin.
app.add_middleware(
    CORSMiddleware,
    allow_origins=config.allowed_origins(),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Reject requests with an unexpected Host header (S-004). Added last so it runs
# FIRST (Starlette applies middleware outermost-last), bouncing bad hosts before
# any other processing. allowed_hosts is fixed at startup from APP_ORIGIN.
app.add_middleware(
    TrustedHostMiddleware,
    allowed_hosts=config.trusted_hosts(),
)

_SECURITY_HEADERS = {
    "X-Content-Type-Options": "nosniff",
    "X-Frame-Options": "DENY",
    "Referrer-Policy": "strict-origin-when-cross-origin",
    "Permissions-Policy": "camera=(), microphone=(), geolocation=()",
    "Content-Security-Policy": (
        "default-src 'self'; "
        "script-src 'self' https://accounts.google.com https://challenges.cloudflare.com; "
        "frame-src https://accounts.google.com https://challenges.cloudflare.com; "
        "connect-src 'self' https://accounts.google.com https://challenges.cloudflare.com; "
        "img-src 'self' data: blob:; "
        "style-src 'self' 'unsafe-inline'; "
        "object-src 'none'; base-uri 'self'; form-action 'self'"
    ),
}


@app.middleware("http")
async def security_middleware(request: Request, call_next):
    # CSRF defense: block cross-origin mutating requests (with SameSite cookies).
    if request.method not in ("GET", "HEAD", "OPTIONS") and request.url.path.startswith(
        "/api/"
    ):
        origin = request.headers.get("origin")
        if origin and origin.rstrip("/") not in config.allowed_origins():
            return JSONResponse(
                {"detail": "cross-origin request blocked"}, status_code=403
            )
    response = await call_next(request)
    # Sliding sessions: roll an active user's cookie forward (at most ~daily).
    auth.refresh_session_if_stale(request, response)
    for key, value in _SECURITY_HEADERS.items():
        response.headers.setdefault(key, value)
    if config.cookie_secure():
        response.headers.setdefault(
            "Strict-Transport-Security", "max-age=63072000; includeSubDomains"
        )
    return response


STATIC_DIR = Path(__file__).parent / "static"

# Per-file upload cap lives in config (MAX_UPLOAD_BYTES env, default 40 MB) so it
# can be tuned per deployment. Rejects oversize payloads before/just after
# reading them into RAM. Pillow's decompression-bomb ceiling (storage's
# megapixel cap) additionally guards against tiny files that explode in memory.


# --------------------------------------------------------------------------- #
# Helpers
# --------------------------------------------------------------------------- #
def _image_url(project_id: str, filename: str) -> str:
    return f"/api/projects/{project_id}/images/{filename}"


def _download_filename(document: dict, ext: str) -> str:
    """A descriptive, unique download name: brand + paper + orientation + a simple
    date-time stamp, e.g. posterly-a3-portrait-300dpi-20260604-153012.pdf. The
    stamp (download time) keeps every downloaded file unique. (The on-disk export
    name is generic; this only sets the Content-Disposition filename.)"""
    settings = document.get("settings", {})
    paper = str(settings.get("paper_size", "A4")).lower()
    orient = str(settings.get("orientation", "landscape")).lower()
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    slug = "".join(c if c.isalnum() else "-" for c in config.brand_name().lower())
    slug = slug.strip("-") or "collage"
    return f"{slug}-{paper}-{orient}-300dpi-{stamp}.{ext}"


def _export_pixel_count(document: dict) -> int:
    """Rendered output pixel count for the project's paper + orientation, used to
    enforce the export budget (S-001) before kicking off a heavy 300 DPI render."""
    settings = document.get("settings", {})
    orientation = str(settings.get("orientation", "landscape"))
    paper = str(settings.get("paper_size", "A4"))
    width, height = canvas_size_for(orientation, paper)
    return width * height


def _project_out(document: dict) -> dict:
    """Augment a stored project document with per-image URLs for the client."""
    project_id = document["id"]
    images = [
        {
            **image,
            "url": _image_url(project_id, image["filename"]),
            "preview_url": _image_url(project_id, image["filename"]) + "?preview=1",
        }
        for image in document.get("images", [])
    ]
    out = {
        "id": project_id,
        "settings": document.get("settings", {}),
        "images": images,
        "layout": document.get("layout", []),
    }
    # Validate/normalize through the response model so the contract is enforced.
    return ProjectOut.model_validate(out).model_dump()


def _load_or_404(project_id: str) -> dict:
    try:
        return storage.load_project(project_id)
    except storage.ProjectNotFoundError:
        raise HTTPException(status_code=404, detail="project not found")


def _load_owned_or_404(project_id: str, user: str) -> dict:
    """Load a project only if it belongs to ``user``; otherwise 404 (we never
    reveal that someone else's project exists)."""
    document = _load_or_404(project_id)
    if (document.get("owner") or "").strip().lower() != user.strip().lower():
        raise HTTPException(status_code=404, detail="project not found")
    return document


# --------------------------------------------------------------------------- #
# Auth
# --------------------------------------------------------------------------- #
@app.get("/api/auth/me")
def auth_me(user: str = Depends(auth.current_user)) -> dict:
    return {"email": user}


@app.post("/api/auth/google")
@limiter.limit("20/minute")
def auth_google(request: Request, response: Response, body: GoogleAuthRequest) -> dict:
    if not auth.verify_turnstile(body.turnstile_token, get_remote_address(request)):
        raise HTTPException(status_code=403, detail="anti-bot verification failed")
    email = auth.verify_google_credential(body.credential)
    if not email:
        raise HTTPException(status_code=401, detail="invalid Google sign-in")
    if not auth.is_email_allowed(email):
        raise HTTPException(status_code=403, detail="this email is not allowed")
    auth.set_session_cookie(response, email)
    return {"email": email}


@app.post("/api/auth/otp/request")
@limiter.limit("8/minute")
@limiter.limit("30/hour")
def auth_otp_request(
    request: Request, body: OtpRequestRequest, background: BackgroundTasks
) -> dict:
    # Anti-bot first (when Turnstile is configured) so automated clients can't
    # spam OTP emails or farm codes. Per-IP rate limits above bound a single IP;
    # the per-email send caps in otp.create_otp protect a known address's inbox.
    if not auth.verify_turnstile(body.turnstile_token, get_remote_address(request)):
        raise HTTPException(status_code=403, detail="anti-bot verification failed")
    # Always respond the same way so we never reveal who is on the allow-list.
    # Email is sent in the background so response time doesn't leak membership.
    email = (body.email or "").strip().lower()
    result: dict = {"ok": True}
    if auth.is_email_allowed(email):
        code, _reason = otp.create_otp(email)
        if code:
            background.add_task(email_service.send_otp_email, email, code)
            if config.otp_dev_expose():
                result["dev_code"] = code  # LOCAL DEV/QA ONLY
    return result


@app.post("/api/auth/otp/verify")
@limiter.limit("15/minute")
def auth_otp_verify(
    request: Request, response: Response, body: OtpVerifyRequest
) -> dict:
    email = (body.email or "").strip().lower()
    if not auth.is_email_allowed(email) or not otp.verify_otp(email, body.code):
        raise HTTPException(status_code=401, detail="invalid or expired code")
    auth.set_session_cookie(response, email)
    return {"email": email}


@app.post("/api/auth/logout")
def auth_logout(request: Request, response: Response) -> dict:
    # Revoke ALL of this user's sessions (kills a stolen sliding token too).
    token = request.cookies.get(auth.SESSION_COOKIE)
    payload = auth.session_payload(token) if token else None
    sub = payload.get("sub") if payload else None
    if isinstance(sub, str) and sub:
        storage.revoke_user_sessions(sub)
    auth.clear_session_cookie(response)
    return {"ok": True}


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
@app.get("/api/health")
def health() -> dict:
    return {"status": "ok"}


# --------------------------------------------------------------------------- #
# Projects
# --------------------------------------------------------------------------- #
@app.post("/api/projects", status_code=201)
def create_project(user: str = Depends(auth.current_user)) -> dict:
    document = storage.create_project(owner=user)
    return _project_out(document)


@app.get("/api/projects")
def list_projects(user: str = Depends(auth.current_user)) -> list[dict]:
    """The current user's collages (history), newest first."""
    return storage.list_projects_for_owner(user)


@app.get("/api/projects/{project_id}")
def get_project(project_id: str, user: str = Depends(auth.current_user)) -> dict:
    document = _load_owned_or_404(project_id, user)
    return _project_out(document)


@app.delete("/api/projects/{project_id}")
def delete_project(project_id: str, user: str = Depends(auth.current_user)) -> dict:
    _load_owned_or_404(project_id, user)
    storage.delete_project(project_id)
    return {"ok": True}


@app.put("/api/projects/{project_id}")
def update_project(
    project_id: str, body: UpdateProjectRequest, user: str = Depends(auth.current_user)
) -> dict:
    # Serialize the read-modify-write so a concurrent upload/auto-layout can't be
    # clobbered by this PUT's stale snapshot (lost update).
    with storage.project_lock(project_id):
        document = _load_owned_or_404(project_id, user)

        if body.settings is not None:
            document["settings"] = body.settings.model_dump()
        if body.layout is not None:
            document["layout"] = [item.model_dump() for item in body.layout]
        if body.image_order is not None:
            document["images"] = _reorder_images(
                document.get("images", []), body.image_order
            )

        storage.save_project(document)
        return _project_out(document)


def _reorder_images(images: list[dict], order: list[str]) -> list[dict]:
    """Reorder stored images by the given list of ids (stable).

    Ids in ``order`` that don't exist are ignored; images not mentioned in
    ``order`` are appended afterwards in their original relative order, so the
    operation can never drop or duplicate an image.
    """
    by_id = {img["id"]: img for img in images}
    seen: set[str] = set()
    ordered = []
    for image_id in order:
        img = by_id.get(image_id)
        if img is not None and image_id not in seen:
            ordered.append(img)
            seen.add(image_id)
    for img in images:
        if img["id"] not in seen:
            ordered.append(img)
    return ordered


@app.post("/api/projects/{project_id}/images")
@limiter.limit("30/minute")
async def upload_images(
    request: Request,
    project_id: str,
    files: list[UploadFile] = File(...),
    user: str = Depends(auth.current_user),
) -> dict:
    # Ownership/existence check up front (the heavy file reads below run without
    # holding the per-project lock).
    _load_owned_or_404(project_id, user)

    if not files:
        raise HTTPException(status_code=400, detail="no files uploaded")
    if len(files) > config.max_files_per_upload():
        raise HTTPException(
            status_code=413,
            detail=f"too many files (max {config.max_files_per_upload()} per upload)",
        )

    max_upload_bytes = config.max_upload_bytes()
    max_request_bytes = config.max_upload_request_bytes()
    declared_total = sum(upload.size or 0 for upload in files)
    if declared_total > max_request_bytes:
        raise HTTPException(status_code=413, detail="upload request too large")

    prepared_uploads: list[tuple[str, bytes]] = []
    total_upload_bytes = 0
    for upload in files:
        # Reject oversize uploads early when the multipart part declares a size,
        # so we never read a huge body into RAM.
        if upload.size is not None and upload.size > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"file too large: {upload.filename!r} "
                    f"(limit {max_upload_bytes} bytes)"
                ),
            )
        raw = await upload.read()
        if not raw:
            raise HTTPException(
                status_code=400, detail=f"empty file: {upload.filename!r}"
            )
        # Defense in depth: also enforce the cap on the actual bytes read.
        if len(raw) > max_upload_bytes:
            raise HTTPException(
                status_code=413,
                detail=(
                    f"file too large: {upload.filename!r} "
                    f"(limit {max_upload_bytes} bytes)"
                ),
            )
        total_upload_bytes += len(raw)
        if total_upload_bytes > max_request_bytes:
            raise HTTPException(status_code=413, detail="upload request too large")
        prepared_uploads.append((upload.filename or "", raw))

    new_images = []
    for original_name, raw in prepared_uploads:
        try:
            meta = storage.save_uploaded_image(project_id, original_name, raw)
        except storage.UnsupportedImageError as exc:
            raise HTTPException(
                status_code=400,
                detail=f"unsupported or unreadable image {original_name!r}: {exc}",
            )
        new_images.append(meta)

    # Re-load + append under the lock so a concurrent layout PUT can't overwrite
    # this with a snapshot that predates the upload (which would orphan the new
    # files). Append -- never wipe existing images.
    with storage.project_lock(project_id):
        document = _load_owned_or_404(project_id, user)
        document.setdefault("images", []).extend(new_images)
        storage.save_project(document)
        return _project_out(document)


@app.post("/api/projects/{project_id}/images/delete")
def delete_images(
    project_id: str,
    body: DeleteImagesRequest,
    user: str = Depends(auth.current_user),
) -> dict:
    """Delete one or more images: drop them from images + layout and unlink the
    upload files. Unknown ids are ignored. Returns the updated project."""
    remove = set(body.image_ids)
    with storage.project_lock(project_id):
        document = _load_owned_or_404(project_id, user)
        if remove:
            kept_images = []
            for image in document.get("images", []):
                if image["id"] in remove:
                    storage.delete_upload(project_id, image["filename"])
                else:
                    kept_images.append(image)
            document["images"] = kept_images
            document["layout"] = [
                item
                for item in document.get("layout", [])
                if item.get("image_id") not in remove
            ]
            storage.save_project(document)
        return _project_out(document)


@app.post("/api/projects/{project_id}/auto-layout")
def auto_layout(
    project_id: str,
    body: AutoLayoutRequest | None = None,
    user: str = Depends(auth.current_user),
) -> dict:
    with storage.project_lock(project_id):
        document = _load_owned_or_404(project_id, user)

        if body is not None and body.settings is not None:
            document["settings"] = body.settings.model_dump()

        layout = render_service.generate_auto_layout(
            document.get("images", []), document.get("settings", {})
        )
        document["layout"] = layout
        storage.save_project(document)
        return _project_out(document)


@app.post("/api/projects/{project_id}/export", response_model=ExportResponse)
@limiter.limit("6/minute")
def export_project(
    request: Request,
    project_id: str,
    body: ExportRequest | None = None,
    user: str = Depends(auth.current_user),
) -> ExportResponse:
    document = _load_owned_or_404(project_id, user)

    fmt = body.format if body is not None else "both"
    write_png = fmt in ("png", "both")
    write_pdf = fmt in ("pdf", "both")

    if not document.get("images") or not document.get("layout"):
        raise HTTPException(
            status_code=400, detail="nothing to render: project has no images or layout"
        )

    # Bound render cost: reject outputs above the configured pixel budget before
    # the expensive 300 DPI render starts (S-001).
    pixels = _export_pixel_count(document)
    max_pixels = config.max_export_megapixels() * 1_000_000
    if pixels > max_pixels:
        raise HTTPException(
            status_code=413,
            detail=(
                f"export too large: {pixels / 1_000_000:.1f} MP exceeds "
                f"{config.max_export_megapixels()} MP"
            ),
        )

    try:
        render_service.export_project(
            document, write_png=write_png, write_pdf=write_pdf
        )
    except (ValueError, argparse.ArgumentTypeError) as exc:
        # collage_a4.parse_color raises argparse.ArgumentTypeError (not a
        # ValueError subclass) for a bad background color; catch both so any
        # color/render failure returns a clean 400 instead of an unhandled 500.
        raise HTTPException(status_code=400, detail=str(exc))

    return ExportResponse(
        png_url=f"/api/projects/{project_id}/download/png",
        pdf_url=f"/api/projects/{project_id}/download/pdf",
        png_ready=storage.export_png_path(project_id).is_file(),
        pdf_ready=storage.export_pdf_path(project_id).is_file(),
    )


# --------------------------------------------------------------------------- #
# Files: uploaded images + exported downloads
# --------------------------------------------------------------------------- #
@app.get("/api/projects/{project_id}/images/{filename}")
def serve_image(
    project_id: str,
    filename: str,
    preview: bool = False,
    user: str = Depends(auth.current_user),
) -> FileResponse:
    _load_owned_or_404(project_id, user)

    # Serve the small WebP proxy when ?preview=1 and it exists; else the original.
    target = filename
    if preview:
        proxy = storage.preview_filename(filename)
        if storage.resolve_upload_path(project_id, proxy) is not None:
            target = proxy

    path = storage.resolve_upload_path(project_id, target)
    if path is None:
        raise HTTPException(status_code=404, detail="image not found")

    media_type = mimetypes.guess_type(str(path))[0] or "application/octet-stream"
    # Uploads are immutable (uuid filenames) -> let the browser cache hard, which
    # keeps the canvas smooth (no re-fetch on every re-render/drag).
    return FileResponse(
        path,
        media_type=media_type,
        headers={"Cache-Control": "private, max-age=31536000, immutable"},
    )


@app.get("/api/projects/{project_id}/download/png")
def download_png(project_id: str, user: str = Depends(auth.current_user)) -> FileResponse:
    document = _load_owned_or_404(project_id, user)
    path = storage.export_png_path(project_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="png not exported yet")
    return FileResponse(
        path,
        media_type="image/png",
        filename=_download_filename(document, "png"),
        content_disposition_type="attachment",
    )


@app.get("/api/projects/{project_id}/download/pdf")
def download_pdf(project_id: str, user: str = Depends(auth.current_user)) -> FileResponse:
    document = _load_owned_or_404(project_id, user)
    path = storage.export_pdf_path(project_id)
    if not path.is_file():
        raise HTTPException(status_code=404, detail="pdf not exported yet")
    return FileResponse(
        path,
        media_type="application/pdf",
        filename=_download_filename(document, "pdf"),
        content_disposition_type="attachment",
    )


# --------------------------------------------------------------------------- #
# Static SPA serving (Task 4 builds the frontend into backend/app/static).
# When the static dir is absent (dev), the app still serves the API fine.
# --------------------------------------------------------------------------- #
if STATIC_DIR.is_dir():
    index_file = STATIC_DIR / "index.html"

    # Serve hashed assets and the SPA. The catch-all below handles client-side
    # routing by falling back to index.html for unknown, non-/api paths.
    if (STATIC_DIR / "assets").is_dir():
        app.mount("/assets", StaticFiles(directory=STATIC_DIR / "assets"), name="assets")

    @app.get("/")
    def serve_index() -> FileResponse:
        return FileResponse(index_file)

    @app.get("/{full_path:path}")
    def serve_spa(full_path: str):
        # Never shadow the API; let it 404 normally.
        if full_path.startswith("api/"):
            return JSONResponse({"detail": "not found"}, status_code=404)
        candidate = (STATIC_DIR / full_path).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            return FileResponse(index_file)
        if candidate.is_file():
            return FileResponse(candidate)
        return FileResponse(index_file)
