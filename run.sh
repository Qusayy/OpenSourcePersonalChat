#!/usr/bin/env bash
# Development server. Production goes through deploy/aurora.service + nginx.
set -euo pipefail
cd "$(dirname "$0")"

[ -d .venv ] && source .venv/bin/activate

# One worker: the model is a single in-process object.
exec uvicorn app.main:app \
  --host "${AURORA_HOST:-127.0.0.1}" \
  --port "${AURORA_PORT:-8000}" \
  --workers 1 \
  --timeout-keep-alive 75 \
  "$@"
