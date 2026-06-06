"""Filesystem persistence for collage projects.

This module is the ONLY place that touches the data directory. The data root is
resolved from the ``COLLAGE_DATA_DIR`` env var *at call time* (default ``/data``)
so tests can point it at a temp dir without re-importing anything. Directories
are created lazily -- importing this module never touches the filesystem.

On-disk layout::

    <root>/projects/<project_id>/
        project.json
        uploads/
        exports/
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import shutil
import tempfile
import threading
import time
import uuid
from contextlib import contextmanager
from pathlib import Path
from typing import Optional

from PIL import Image, ImageOps, UnidentifiedImageError

from . import config

# Single canonical extension allowlist lives in collage_a4; reuse it here so the
# upload validator and the CLI never drift.
from collage_a4 import SUPPORTED_EXTENSIONS

DEFAULT_DATA_DIR = "/data"

# Project ids are uuid4 hex strings (32 lowercase hex chars). Anything else is
# rejected at the path layer so a crafted id (e.g. "..") cannot escape the root.
_PROJECT_ID_RE = re.compile(r"^[0-9a-f]{32}$")

EXPORT_PNG_NAME = "collage-print.png"
EXPORT_PDF_NAME = "collage-print.pdf"

# Small on-screen proxy of each upload so the UI stays smooth with big photos.
# The export always uses the full-resolution original; the proxy keeps the same
# aspect ratio, so the on-screen crop matches the export 1:1.
PREVIEW_MAX_SIDE = 1600


class StorageError(Exception):
    """Base class for storage-level problems."""


class ProjectNotFoundError(StorageError):
    """Raised when a requested project id has no directory/document."""


class UnsupportedImageError(StorageError):
    """Raised when an uploaded file is not a readable, supported image."""


def data_root() -> Path:
    """Resolve the data root from the environment at call time."""
    return Path(os.environ.get("COLLAGE_DATA_DIR", DEFAULT_DATA_DIR))


def projects_root() -> Path:
    return data_root() / "projects"


def project_dir(project_id: str) -> Path:
    """Resolve a project's directory, guarding against path traversal.

    Project ids are uuid4 hex; a malformed id (e.g. ``".."``) would otherwise
    escape the projects root. We treat any non-conforming id as not found.
    """
    if not _PROJECT_ID_RE.match(project_id or ""):
        raise ProjectNotFoundError(project_id)
    return projects_root() / project_id


def uploads_dir(project_id: str) -> Path:
    return project_dir(project_id) / "uploads"


def exports_dir(project_id: str) -> Path:
    return project_dir(project_id) / "exports"


def project_json_path(project_id: str) -> Path:
    return project_dir(project_id) / "project.json"


def project_exists(project_id: str) -> bool:
    try:
        return project_json_path(project_id).is_file()
    except ProjectNotFoundError:
        # Malformed id -> never exists (download/serve routes 404 cleanly).
        return False


def _ensure_project_dirs(project_id: str) -> None:
    uploads_dir(project_id).mkdir(parents=True, exist_ok=True)
    exports_dir(project_id).mkdir(parents=True, exist_ok=True)


def default_project_document(project_id: str, owner: str = "") -> dict:
    """Build a fresh project.json document with default settings."""
    now = int(time.time())
    return {
        "id": project_id,
        "owner": owner,
        "created_at": now,
        "updated_at": now,
        "settings": {
            "orientation": "landscape",
            "paper_size": "A4",
            "look": "soft-oval",
            "order_mode": "random",
            "spacing": 0.5,
            "rotation_intensity": 0.5,
            "border": 0.5,
            "feather": 0.5,
            "background": "#f4efe6",
            "seed": 7,
        },
        "images": [],
        "layout": [],
    }


def create_project(owner: str = "") -> dict:
    """Create a new project (uuid4 hex id), persist it, return the document."""
    project_id = uuid.uuid4().hex
    _ensure_project_dirs(project_id)
    document = default_project_document(project_id, owner=owner)
    save_project(document)
    return document


def load_project(project_id: str) -> dict:
    """Load and return a project document, or raise ProjectNotFoundError.

    Tolerates a legacy torn write -- a valid JSON document followed by trailing
    bytes left over from an older, longer write (``json.JSONDecodeError: Extra
    data``) -- by decoding just the leading object. Without this a once-corrupted
    project 500s on every read and the collage "disappears". ``save_project``
    (atomic + unique temp file) prevents new corruption; the next save rewrites
    the file cleanly.
    """
    path = project_json_path(project_id)
    if not path.is_file():
        raise ProjectNotFoundError(project_id)
    raw = path.read_text(encoding="utf-8")
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        # Recover the first complete JSON object; ignore any trailing garbage.
        # Re-raises if the leading content itself is incomplete/unparseable.
        document, _end = json.JSONDecoder().raw_decode(raw.lstrip())
        if not isinstance(document, dict):
            raise
        return document


def _fsync_dir(path: Path) -> None:
    """Best-effort fsync of a directory so a just-completed rename is durable.
    No-op where the platform/filesystem doesn't support directory fsync."""
    try:
        dir_fd = os.open(str(path), os.O_DIRECTORY)
    except (OSError, AttributeError):
        return
    try:
        os.fsync(dir_fd)
    except OSError:
        pass
    finally:
        os.close(dir_fd)


def _atomic_write_text(path: Path, text: str) -> None:
    """Write ``text`` to ``path`` atomically and durably.

    Each call writes a UNIQUE temp file in the same directory (so concurrent
    writers never share a temp file and the target is never torn/interleaved),
    fsyncs it, ``os.replace``s it into place, then fsyncs the parent dir so the
    rename survives a crash. The temp suffix is plain ``.tmp`` (NOT ``*.json.tmp``)
    so the retention sweep's legacy glob can't match an in-flight write.
    """
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=str(path.parent), prefix=".wtmp-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(tmp_name, path)
        _fsync_dir(path.parent)
    except BaseException:
        try:
            os.unlink(tmp_name)
        except OSError:
            pass
        raise


# Per-project lock so the read-modify-write that every mutating endpoint does
# (load_project -> mutate -> save_project) is serialized across threads. FastAPI
# runs sync endpoints in a threadpool, so without this two concurrent requests
# can both load, both save, and the last writer silently drops the other's change
# (a lost update -- e.g. an upload vanishing behind a stale layout PUT). Combined
# with the atomic write above this gives both corruption- and lost-update-safety.
_project_locks: dict[str, threading.Lock] = {}
_project_locks_guard = threading.Lock()


@contextmanager
def project_lock(project_id: str):
    with _project_locks_guard:
        lock = _project_locks.get(project_id)
        if lock is None:
            lock = threading.Lock()
            _project_locks[project_id] = lock
    with lock:
        yield


def save_project(document: dict) -> dict:
    """Persist a project document atomically; returns the document.

    Uses :func:`_atomic_write_text` (unique temp file + fsync + os.replace), so
    concurrent saves of the same project can't tear/interleave the file. (A
    shared ``<id>.json.tmp`` previously let two concurrent saves clobber the same
    temp file, corrupting project.json -> 500 on read -> the collage vanished.)
    Wrap multi-step updates in :func:`project_lock` to also avoid lost updates.
    """
    project_id = document["id"]
    document["updated_at"] = int(time.time())
    _ensure_project_dirs(project_id)
    _atomic_write_text(
        project_json_path(project_id),
        json.dumps(document, indent=2, ensure_ascii=False),
    )
    return document


def list_projects_for_owner(owner: str) -> list[dict]:
    """Scan all projects and return lightweight metadata for those owned by
    ``owner``, newest first. Suitable for a per-user 'My collages' history."""
    owner = (owner or "").strip().lower()
    root = projects_root()
    out: list[dict] = []
    if not root.is_dir():
        return out
    for entry in root.iterdir():
        if not entry.is_dir() or not _PROJECT_ID_RE.match(entry.name):
            continue
        try:
            doc = load_project(entry.name)
        except (ProjectNotFoundError, ValueError, OSError):
            continue
        if (doc.get("owner") or "").strip().lower() != owner:
            continue
        images = doc.get("images", [])
        out.append(
            {
                "id": doc["id"],
                "created_at": doc.get("created_at", 0),
                "updated_at": doc.get("updated_at", 0),
                "image_count": len(images),
                "thumb": images[0]["filename"] if images else None,
            }
        )
    out.sort(key=lambda m: m.get("updated_at", 0), reverse=True)
    return out


def delete_project(project_id: str) -> None:
    """Remove a project directory entirely (guarded by the id format check)."""
    target = project_dir(project_id)  # raises ProjectNotFoundError on bad id
    shutil.rmtree(target, ignore_errors=True)


def _sanitized_extension(filename: Optional[str]) -> str:
    """Return a safe, lowercased extension (with dot) from an upload name."""
    suffix = Path(filename or "").suffix.lower()
    # Strip anything that is not a-z/0-9 to be defensive about odd extensions.
    cleaned = "." + re.sub(r"[^a-z0-9]", "", suffix.lstrip("."))
    return cleaned if cleaned != "." else ""


def save_uploaded_image(project_id: str, original_name: str, raw_bytes: bytes) -> dict:
    """Validate and store one uploaded image.

    Opens the bytes with Pillow, applies EXIF transpose so the stored
    ``width``/``height`` match what ``collage_a4.load_image`` later produces,
    and writes the original bytes to ``uploads/<uuid><ext>``.

    Returns the image-meta dict ``{id, filename, name, width, height}``.
    Raises ``UnsupportedImageError`` for unreadable/unsupported files.
    """
    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            image.verify()  # cheap integrity check; consumes the file object
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedImageError(str(exc)) from exc

    # Re-open after verify() (which leaves the image unusable) to read real size.
    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            transposed = ImageOps.exif_transpose(image)
            width, height = transposed.size
            pil_format = (image.format or "").lower()
    except (UnidentifiedImageError, OSError, ValueError) as exc:
        raise UnsupportedImageError(str(exc)) from exc

    if width < 1 or height < 1:
        raise UnsupportedImageError("image has zero dimension")

    # Reject oversize images BEFORE any heavy decode (decompression-bomb guard:
    # a small file can declare/expand to a huge pixel count -> memory DoS).
    if width * height > config.max_image_megapixels() * 1_000_000:
        raise UnsupportedImageError(
            f"image too large: {width}x{height}px exceeds "
            f"{config.max_image_megapixels()} megapixels"
        )

    extension = _sanitized_extension(original_name)
    if not extension:
        # Fall back to the format Pillow detected so we always have a usable ext.
        fmt_map = {"jpeg": ".jpg", "png": ".png", "webp": ".webp", "bmp": ".bmp",
                   "tiff": ".tif"}
        extension = fmt_map.get(pil_format, "")
    if extension not in SUPPORTED_EXTENSIONS:
        raise UnsupportedImageError(f"unsupported image type: {extension or pil_format!r}")

    image_id = uuid.uuid4().hex
    filename = f"{image_id}{extension}"

    _ensure_project_dirs(project_id)
    target = uploads_dir(project_id) / filename
    target.write_bytes(raw_bytes)
    _write_preview(project_id, filename, raw_bytes)

    # Defense in depth: the original name is client-controlled and only used as a
    # display label. Strip control chars / angle brackets and cap length (React
    # already escapes it, but never trust it elsewhere).
    safe_name = re.sub(r"[\x00-\x1f<>]", "", original_name or "").strip()[:200]

    return {
        "id": image_id,
        "filename": filename,
        "name": safe_name or filename,
        "width": int(width),
        "height": int(height),
    }


def preview_filename(filename: str) -> str:
    """Proxy filename for an upload (WebP), e.g. <id>.jpg -> <id>_preview.webp."""
    return f"{Path(filename).stem}_preview.webp"


def _write_preview(project_id: str, filename: str, raw_bytes: bytes) -> None:
    """Write a downscaled WebP proxy next to the original. Best-effort: if it
    fails the UI just falls back to the full image."""
    try:
        with Image.open(io.BytesIO(raw_bytes)) as image:
            proxy = ImageOps.exif_transpose(image).convert("RGB")
            proxy.thumbnail(
                (PREVIEW_MAX_SIDE, PREVIEW_MAX_SIDE), Image.Resampling.LANCZOS
            )
            out = uploads_dir(project_id) / preview_filename(filename)
            tmp = out.with_suffix(".webp.tmp")
            proxy.save(tmp, "WEBP", quality=80, method=4)
            os.replace(tmp, out)
    except Exception:
        pass


def resolve_upload_path(project_id: str, filename: str) -> Optional[Path]:
    """Resolve an uploaded file to an absolute path, guarding path traversal.

    Returns the resolved path only if it exists and is genuinely inside this
    project's uploads dir; otherwise ``None``.
    """
    base = uploads_dir(project_id).resolve()
    candidate = (base / filename).resolve()
    try:
        candidate.relative_to(base)
    except ValueError:
        return None
    if not candidate.is_file():
        return None
    return candidate


def _session_epoch_path(email: str) -> Path:
    digest = hashlib.sha256(email.strip().lower().encode("utf-8")).hexdigest()
    return data_root() / "sessions" / f"{digest}.txt"


def user_session_epoch(email: str) -> int:
    """Tokens issued (iat) before this timestamp are invalid for the user. 0 = no
    revocation yet. Used to make logout actually revoke all of a user's sessions."""
    try:
        return int(_session_epoch_path(email).read_text(encoding="utf-8").strip())
    except (OSError, ValueError):
        return 0


def revoke_user_sessions(email: str, now: Optional[int] = None) -> None:
    """Invalidate all existing sessions for ``email`` (e.g. on logout). Atomic +
    unique-temp write so concurrent logouts of the same user can't race the
    shared temp file."""
    ts = int(time.time()) if now is None else now
    _atomic_write_text(_session_epoch_path(email), str(ts))


def delete_upload(project_id: str, filename: str) -> None:
    """Delete one uploaded file if it resolves safely inside the uploads dir.

    Best-effort: missing/invalid files are ignored (the project document is the
    source of truth for what exists).
    """
    path = resolve_upload_path(project_id, filename)
    if path is not None:
        try:
            path.unlink()
        except OSError:
            pass


def export_png_path(project_id: str) -> Path:
    return exports_dir(project_id) / EXPORT_PNG_NAME


def export_pdf_path(project_id: str) -> Path:
    return exports_dir(project_id) / EXPORT_PDF_NAME
