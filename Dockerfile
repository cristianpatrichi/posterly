# syntax=docker/dockerfile:1

# --------------------------------------------------------------------------- #
# Stage 1: build the React/Vite frontend.
# --------------------------------------------------------------------------- #
FROM node:22-alpine AS frontend
WORKDIR /frontend

# Install deps first (cached unless the lockfile changes).
COPY frontend/package.json frontend/package-lock.json ./
RUN npm ci

# Google OAuth client id + branding are baked into the bundle at build time
# (Vite inlines VITE_* env vars). docker-compose passes these from .env.
ARG VITE_GOOGLE_CLIENT_ID=""
ARG VITE_BRAND_NAME=""
ARG VITE_BRAND_TAGLINE=""
ARG VITE_TURNSTILE_SITE_KEY=""
ENV VITE_GOOGLE_CLIENT_ID=$VITE_GOOGLE_CLIENT_ID \
    VITE_BRAND_NAME=$VITE_BRAND_NAME \
    VITE_BRAND_TAGLINE=$VITE_BRAND_TAGLINE \
    VITE_TURNSTILE_SITE_KEY=$VITE_TURNSTILE_SITE_KEY

# Build the SPA -> /frontend/dist (index.html + hashed assets).
COPY frontend/ ./
RUN npm run build

# --------------------------------------------------------------------------- #
# Stage 2: Python runtime serving the API + the built SPA.
# --------------------------------------------------------------------------- #
FROM python:3.14-slim AS runtime
WORKDIR /app

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    COLLAGE_DATA_DIR=/data \
    TZ=Europe/Bucharest

# Install backend deps from the hash-pinned lock (S-006): reproducible builds,
# and pip refuses any artifact whose hash isn't listed. Regenerate the lock with
# pip-compile --generate-hashes (see README "Dependency updates"). Pillow et al.
# ship manylinux wheels, so slim needs no build tools.
COPY requirements.txt ./
RUN pip install --no-cache-dir --require-hashes -r requirements.txt

# Application code: the collage renderer (imported as `collage_a4`) and the
# backend package (`backend.app.main:app`). /app on sys.path resolves both.
COPY collage_a4.py ./
COPY backend/ ./backend/

# Copy the built frontend into the exact dir main.py resolves:
#   STATIC_DIR = Path(__file__).parent / "static"  ->  backend/app/static
COPY --from=frontend /frontend/dist ./backend/app/static

# Run as an unprivileged user (S-003): shrinks blast radius if the app is ever
# compromised. gosu lets the entrypoint fix the /data ownership and drop to the
# app user, so the app process is non-root on EVERY platform -- including Linux
# hosts where a bind mount keeps the host's ownership (the build-time chown only
# helps named volumes / no-mount runs).
RUN apt-get update \
    && apt-get install -y --no-install-recommends gosu tzdata \
    && rm -rf /var/lib/apt/lists/* \
    && addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --ingroup app app \
    && mkdir -p /data \
    && chown -R app:app /app /data

COPY docker/entrypoint.sh /usr/local/bin/entrypoint.sh
RUN chmod +x /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]

EXPOSE 8787

HEALTHCHECK --interval=30s --timeout=5s --retries=3 --start-period=10s \
    CMD python -c "import urllib.request,sys; sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:8787/api/health', timeout=3).status==200 else sys.exit(1)"

# --proxy-headers + --forwarded-allow-ips lets uvicorn trust the X-Forwarded-*
# from the TLS reverse proxy, so HTTPS detection and per-IP rate limiting use the
# real client. (Safe because only the proxy can reach the container.)
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8787", \
     "--proxy-headers", "--forwarded-allow-ips", "*"]
