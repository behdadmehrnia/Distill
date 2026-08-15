#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
SVC="${1:-}"

if [[ -n "$SVC" && -f "$RUNTIME_DIR/logs/${SVC}.log" ]]; then
  exec tail -n 200 -f "$RUNTIME_DIR/logs/${SVC}.log"
fi

cd "$RUNTIME_DIR"
if command -v docker >/dev/null 2>&1; then
  if [[ -n "$SVC" ]]; then
    exec docker compose logs -f --tail=200 "$SVC"
  fi
  exec docker compose logs -f --tail=100
fi

echo "No docker / no native logs. Usage: $0 [vllm|stt|diarize|llm]" >&2
exit 1
