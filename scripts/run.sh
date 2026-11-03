#!/usr/bin/env bash
# 启动 docmind 服务。用法：bash scripts/run.sh
set -euo pipefail

cd "$(dirname "$0")/.."

if [ -f .env ]; then
  set -a
  # shellcheck disable=SC1091
  . ./.env
  set +a
fi

if [ -z "${POWERCONTEXT_URL:-}" ]; then
  echo "[run.sh] 提示：未设置 POWERCONTEXT_URL，服务仍会启动，但路由只返回占位结果。" >&2
fi

HOST="${DOCMIND_HOST:-127.0.0.1}"
PORT="${DOCMIND_PORT:-8000}"

echo "[run.sh] http://${HOST}:${PORT}  (docs: http://${HOST}:${PORT}/docs)" >&2
exec python -m uvicorn app.main:app --host "$HOST" --port "$PORT" --reload
