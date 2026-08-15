#!/usr/bin/env bash
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

check() {
  local name="$1" url="$2" need_ready="${3:-0}"
  if ! command -v curl >/dev/null 2>&1; then
    echo "curl required" >&2
    exit 1
  fi
  echo -n "[$name] $url … "
  if ! body="$(curl -fsS --max-time 8 "$url" 2>/dev/null)"; then
    echo "FAIL (unreachable)"
    return 1
  fi
  if [[ "$need_ready" == "1" ]]; then
    if echo "$body" | python3 -c 'import json,sys; d=json.load(sys.stdin); sys.exit(0 if d.get("ready") is True else 1)' 2>/dev/null; then
      echo "OK (ready)"
      echo "  $body" | head -c 400
      echo ""
      return 0
    fi
    echo "UP but NOT ready"
    echo "  $body" | head -c 400
    echo ""
    return 1
  fi
  echo "OK"
  echo "  $body" | head -c 400
  echo ""
  return 0
}

echo "=========================================="
echo "Distill runtime health"
echo "=========================================="

fail=0
check "LLM " "http://127.0.0.1:${VLLM_PORT}/v1/models" 0 || fail=1
check "STT " "http://127.0.0.1:${STT_PORT}/health" 1 || fail=1
check "DIAR" "http://127.0.0.1:${DIARIZE_PORT}/health" 1 || fail=1

if [[ "$fail" -ne 0 ]]; then
  echo ""
  echo "One or more services unhealthy. Check logs: ./runtime/scripts/logs.sh"
  echo "Or wait: ./runtime/scripts/wait_ready.sh"
  exit 1
fi

echo ""
echo "All reachable services OK and ready."
