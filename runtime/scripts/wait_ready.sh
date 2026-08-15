#!/usr/bin/env bash
# Wait until LLM / STT / diarization report ready (not just TCP up).
# Usage:
#   ./runtime/scripts/wait_ready.sh
#   ./runtime/scripts/wait_ready.sh --audio   # STT + diarize only
#   ./runtime/scripts/wait_ready.sh --diarize # diarize only
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a; source "$RUNTIME_DIR/.env"; set +a
fi

VLLM_PORT="${VLLM_PORT:-8001}"
STT_PORT="${STT_PORT:-8080}"
DIARIZE_PORT="${DIARIZE_PORT:-8090}"
TIMEOUT_S="${WAIT_READY_TIMEOUT_S:-600}"
INTERVAL_S="${WAIT_READY_INTERVAL_S:-5}"

WANT_LLM=1
WANT_STT=1
WANT_DIAR=1

for arg in "$@"; do
  case "$arg" in
    --audio) WANT_LLM=0 ;;
    --diarize) WANT_LLM=0; WANT_STT=0 ;;
    --stt) WANT_LLM=0; WANT_DIAR=0 ;;
    --llm) WANT_STT=0; WANT_DIAR=0 ;;
  esac
done

json_ready() {
  # stdin: JSON body; argv1: jq-ish python expression returning truthy
  local expr="$1"
  python3 - "$expr" <<'PY' || return 1
import json, sys
expr = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    sys.exit(1)
# Supported expressions: ready | models
if expr == "ready":
    sys.exit(0 if data.get("ready") is True else 1)
if expr == "models":
    sys.exit(0 if isinstance(data.get("data"), list) else 1)
sys.exit(1)
PY
}

probe() {
  local name="$1" url="$2" kind="$3"
  local body
  if ! body="$(curl -fsS --max-time 8 "$url" 2>/dev/null)"; then
    echo "[$name] not reachable"
    return 1
  fi
  if echo "$body" | json_ready "$kind"; then
    echo "[$name] READY"
    return 0
  fi
  echo "[$name] up but not ready yet"
  return 1
}

echo "Waiting up to ${TIMEOUT_S}s for runtime services…"
start_ts="$(date +%s)"
while true; do
  ok=1
  if [[ "$WANT_LLM" -eq 1 ]]; then
    probe "LLM " "http://127.0.0.1:${VLLM_PORT}/v1/models" models || ok=0
  fi
  if [[ "$WANT_STT" -eq 1 ]]; then
    probe "STT " "http://127.0.0.1:${STT_PORT}/health" ready || ok=0
  fi
  if [[ "$WANT_DIAR" -eq 1 ]]; then
    probe "DIAR" "http://127.0.0.1:${DIARIZE_PORT}/health" ready || ok=0
  fi
  if [[ "$ok" -eq 1 ]]; then
    echo "All requested services ready."
    exit 0
  fi
  now="$(date +%s)"
  if (( now - start_ts >= TIMEOUT_S )); then
    echo "ERROR: timed out after ${TIMEOUT_S}s" >&2
    exit 1
  fi
  sleep "$INTERVAL_S"
done
