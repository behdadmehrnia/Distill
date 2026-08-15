#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
LOG_DIR="$RUNTIME_DIR/logs"

echo "Stopping Distill runtime…"

# Native PIDs
if [[ -d "$LOG_DIR" ]]; then
  for name in vllm stt diarize; do
    pidfile="$LOG_DIR/$name.pid"
    if [[ -f "$pidfile" ]]; then
      pid="$(cat "$pidfile" || true)"
      if [[ -n "${pid:-}" ]] && kill -0 "$pid" 2>/dev/null; then
        echo "  kill $name ($pid)"
        kill "$pid" 2>/dev/null || true
      fi
      rm -f "$pidfile"
    fi
  done
fi

# Docker
if command -v docker >/dev/null 2>&1; then
  cd "$RUNTIME_DIR"
  docker compose --profile full --profile audio --profile cpu --profile gpu down 2>/dev/null || true
fi

echo "Done."
