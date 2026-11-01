#!/bin/sh
# Container entry point: apply migrations, then serve on $PORT (Render sets it; default 8000).
set -e
alembic upgrade head
exec uvicorn app.api.main:app \
  --host 0.0.0.0 \
  --port "${PORT:-8000}" \
  --workers 1 \
  --proxy-headers \
  --forwarded-allow-ips "*" \
  --timeout-keep-alive 75
