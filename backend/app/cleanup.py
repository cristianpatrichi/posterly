"""Retention sweep: delete abandoned collages + stale OTP/temp files.

Standalone (no web server) so the `cleanup` cron container can run it on a
schedule sharing the same /data volume:

    python -m backend.app.cleanup

Imports only config/storage (never main), so importing it never starts uvicorn.
"""

from __future__ import annotations

import json
import logging
import time

from . import config, storage

log = logging.getLogger("collage.cleanup")

# Never delete a *.tmp younger than this: atomic writes (save_project, OTP,
# sessions, exports) create a unique temp file then os.replace it within
# milliseconds. Only files older than this are genuine orphans from a crash.
TMP_MAX_AGE_SECONDS = 600


def _sweep_projects(retention_days: int, now: int) -> tuple[int, int]:
    deleted = kept = 0
    root = storage.projects_root()
    if not root.is_dir():
        return 0, 0
    cutoff = now - retention_days * 86400
    for entry in root.iterdir():
        if not entry.is_dir() or not storage._PROJECT_ID_RE.match(entry.name):
            continue
        try:
            doc = storage.load_project(entry.name)
        except Exception:
            kept += 1  # unreadable -> leave it alone, never delete blindly
            continue
        updated = int(doc.get("updated_at") or doc.get("created_at") or 0)
        if retention_days > 0 and updated < cutoff:
            storage.delete_project(entry.name)
            deleted += 1
        else:
            kept += 1
    return deleted, kept


def _sweep_otp(now: int) -> int:
    deleted = 0
    otp_dir = storage.data_root() / "otp"
    if not otp_dir.is_dir():
        return 0
    for f in otp_dir.iterdir():
        if f.suffix != ".json":
            continue  # *.tmp orphans handled (age-gated) by _sweep_tmp
        try:
            record = json.loads(f.read_text(encoding="utf-8"))
        except Exception:
            continue
        if int(record.get("expires_at", 0)) < now:
            f.unlink(missing_ok=True)
            deleted += 1
    return deleted


def _is_temp_artifact(name: str) -> bool:
    """A file left by an in-flight/crashed atomic write: ``*.tmp`` (project/OTP/
    session writes via storage._atomic_write_text use ``.wtmp-*.tmp``) or
    ``.exp-*.png/.pdf`` (export render temps)."""
    return name.endswith(".tmp") or name.startswith(".wtmp-") or name.startswith(".exp-")


def _sweep_tmp(now: int) -> int:
    """Remove ORPHANED temp artifacts (left by a crashed atomic write) anywhere
    under the data root, but only those older than ``TMP_MAX_AGE_SECONDS`` so an
    in-flight write a live request is about to ``os.replace`` is never deleted."""
    removed = 0
    root = storage.data_root()
    if not root.is_dir():
        return 0
    cutoff = now - TMP_MAX_AGE_SECONDS
    for p in root.rglob("*"):
        if not _is_temp_artifact(p.name):
            continue
        try:
            if p.is_file() and p.stat().st_mtime < cutoff:
                p.unlink(missing_ok=True)
                removed += 1
        except OSError:
            continue
    return removed


def sweep(retention_days: int | None = None, now: int | None = None) -> dict:
    if retention_days is None:
        retention_days = config.retention_days()
    if now is None:
        now = int(time.time())
    deleted, kept = _sweep_projects(retention_days, now)
    return {
        "retention_days": retention_days,
        "projects_deleted": deleted,
        "projects_kept": kept,
        "otp_deleted": _sweep_otp(now),
        "tmp_removed": _sweep_tmp(now),
    }


def main() -> None:
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s"
    )
    log.info("cleanup sweep: %s", sweep())


if __name__ == "__main__":
    main()
