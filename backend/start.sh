#!/bin/sh
# Start both the FastAPI server and the Celery worker in one container.
# They share the same filesystem so SQLite and uploaded files are accessible to both.

set -e

PORT=${PORT:-8000}

# Apply any pending Alembic migrations before starting the server.
alembic upgrade head

celery -A tasks.celery_app worker \
    --loglevel=info \
    --concurrency=2 \
    --queues=celery &

exec uvicorn main:app --host 0.0.0.0 --port "$PORT"
