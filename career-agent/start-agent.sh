#!/bin/sh
set -eu
worker_pid=""
web_pid=""
cleanup() {
  if [ -n "$worker_pid" ]; then kill "$worker_pid" 2>/dev/null || true; fi
  if [ -n "$web_pid" ]; then kill "$web_pid" 2>/dev/null || true; fi
  wait 2>/dev/null || true
}
trap cleanup TERM INT EXIT
if [ "${CAREER_RAG_AUTO_SYNC:-false}" = "true" ]; then
  echo '[career-agent] Word -> RAG background sync enabled'
  python -u /app/career_rag_auto_sync.py daemon &
  worker_pid=$!
fi
uvicorn app:app --host 0.0.0.0 --port 8001 --proxy-headers --no-access-log &
web_pid=$!
wait "$web_pid"
