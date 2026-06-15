# ─────────────────────────────────────────────────────────────────────────────
# Memora — backend image
# Used for both the FastAPI server and the Celery worker (different CMD).
# ─────────────────────────────────────────────────────────────────────────────

FROM python:3.11-slim-bullseye

# System dependencies
#   ffmpeg  — audio extraction / transcoding
#   curl    — used by docker-compose healthcheck
#   nodejs  — JS runtime for yt-dlp (YouTube web-client fallback)
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
        ffmpeg \
        curl \
        nodejs \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python deps before copying source so this layer is cached across
# code-only changes.
COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
 && pip install --no-cache-dir -r requirements.txt

# Copy application source
COPY . .

# Persistent data lives in a mounted volume at /app/data.
# Create the directory so it exists even without a volume (e.g. local dev).
RUN mkdir -p /app/data/uploads /app/data/jobs /app/data/db

# Expose the API port (informational; actual binding is via CMD / compose)
EXPOSE 8000

# Default: run both FastAPI server and Celery worker together.
# The Celery worker override in docker-compose still works for local dev.
COPY start.sh .
RUN chmod +x start.sh
CMD ["./start.sh"]
