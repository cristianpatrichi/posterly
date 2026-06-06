#!/bin/sh
set -e

# Make the app run as a non-root user on every platform (S-003).
#
# When the container starts as root, fix the data-dir ownership -- host bind
# mounts arrive owned by the host uid, which UID 10001 otherwise can't write
# (silent outage on Linux; Docker Desktop happens to remap perms) -- and then
# drop privileges with gosu so the application itself never runs as root.
# Dropping privileges is allowed even under `no-new-privileges`.
#
# When started already non-root (e.g. a compose `user:` override), just run.
DATA_DIR="${COLLAGE_DATA_DIR:-/data}"
if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA_DIR"
    chown -R app:app "$DATA_DIR" 2>/dev/null || true
    exec gosu app "$@"
fi
exec "$@"
