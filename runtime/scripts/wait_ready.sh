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

# stdin: JSON body. kind: ready | models
# Prints a one-line summary to stdout. Exit 0 if ready, 2 if the service
# reported a permanent load error, 1 if still loading / not ready.
check_body() {
  local kind="$1"
  python3 -c '
import json, sys
kind = sys.argv[1]
try:
    data = json.load(sys.stdin)
except Exception:
    print("invalid JSON")
    sys.exit(1)
if kind == "models":
    ok = isinstance(data.get("data"), list)
    print("models listed" if ok else "no model list yet")
    sys.exit(0 if ok else 1)
if kind == "ready":
    ready = data.get("ready") is True
    err = data.get("error")
    status = data.get("status")
    extra = []
    if data.get("backend"):
        extra.append("backend=" + str(data.get("backend")))
    if data.get("model"):
        extra.append("model=" + str(data.get("model")))
    if data.get("device"):
        extra.append("device=" + str(data.get("device")))
    suffix = (" (" + ", ".join(extra) + ")") if extra else ""
    if ready:
        print("ready" + suffix)
        sys.exit(0)
    if err or status == "error":
        print("load failed" + suffix + ": " + str(err or status))
        sys.exit(2)
    print("still loading" + suffix)
    sys.exit(1)
print("unknown probe")
sys.exit(1)
' "$kind"
}

probe() {
  local name="$1" url="$2" kind="$3"
  local body summary rc
  if ! body="$(curl -fsS --max-time 8 "$url" 2>/dev/null)"; then
    echo "[$name] not reachable"
    return 1
  fi
  set +e
  summary="$(printf '%s' "$body" | check_body "$kind")"
  rc=$?
  set -e
  if [[ "$rc" -eq 0 ]]; then
    echo "[$name] READY — ${summary}"
    return 0
  fi
  if [[ "$rc" -eq 2 ]]; then
    echo "[$name] FAILED — ${summary}" >&2
    echo "  ${body}" >&2
    return 2
  fi
  echo "[$name] up but not ready yet — ${summary}"
  return 1
}

echo "Waiting up to ${TIMEOUT_S}s for runtime services…"
echo "(HTTP /docs up is not enough — /health must report ready=true after the model loads.)"
start_ts="$(date +%s)"
while true; do
  ok=1
  if [[ "$WANT_LLM" -eq 1 ]]; then
    probe "LLM " "http://127.0.0.1:${VLLM_PORT}/v1/models" models || ok=0
  fi
  if [[ "$WANT_STT" -eq 1 ]]; then
    rc=0
    probe "STT " "http://127.0.0.1:${STT_PORT}/health" ready || rc=$?
    if [[ "$rc" -eq 2 ]]; then
      echo "ERROR: STT reported a load failure (see above). Check: docker logs distill-stt" >&2
      exit 1
    fi
    [[ "$rc" -eq 0 ]] || ok=0
  fi
  if [[ "$WANT_DIAR" -eq 1 ]]; then
    rc=0
    probe "DIAR" "http://127.0.0.1:${DIARIZE_PORT}/health" ready || rc=$?
    if [[ "$rc" -eq 2 ]]; then
      echo "ERROR: diarization reported a load failure (see above). Check: docker logs distill-diarize" >&2
      echo "No pyannote .bin weights and no HF_TOKEN → set DIARIZATION_BACKEND=nemo INSTALL_NEMO=1 in runtime/.env" >&2
      exit 1
    fi
    [[ "$rc" -eq 0 ]] || ok=0
  fi
  if [[ "$ok" -eq 1 ]]; then
    echo "All requested services ready."
    exit 0
  fi
  now="$(date +%s)"
  if (( now - start_ts >= TIMEOUT_S )); then
    echo "ERROR: timed out after ${TIMEOUT_S}s" >&2
    echo "Inspect: curl -s http://127.0.0.1:${STT_PORT}/health ; curl -s http://127.0.0.1:${DIARIZE_PORT}/health" >&2
    exit 1
  fi
  sleep "$INTERVAL_S"
done
