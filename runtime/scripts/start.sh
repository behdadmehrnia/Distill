#!/usr/bin/env bash
# Start full local model suite for Distill (LLM + STT + Diarization).
# Pattern inspired by https://github.com/behdadmehrnia/MA-runtime
#
# Usage:
#   ./runtime/scripts/start.sh              # docker compose GPU/full stack
#   ./runtime/scripts/start.sh --native     # three local processes
#   ./runtime/scripts/start.sh --audio      # STT + diarize only
#   ./runtime/scripts/start.sh --llm        # vLLM only (keep existing STT/diarize)
#   ./runtime/scripts/start.sh --cpu        # diarize (compose cpu profile)
#   ./runtime/scripts/start.sh --no-wait    # do not block on model ready
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$RUNTIME_DIR/.." && pwd)"

MODE="docker"
PROFILE="full"
COMPOSE_SERVICES=()
NATIVE=0
WAIT=1

for arg in "$@"; do
  case "$arg" in
    --native) NATIVE=1; MODE="native" ;;
    --audio) PROFILE="audio" ;;
    --llm) PROFILE="full"; COMPOSE_SERVICES=(llm) ;;
    --cpu) PROFILE="cpu" ;;
    --full) PROFILE="full" ;;
    --no-wait) WAIT=0 ;;
    -h|--help)
      sed -n '2,14p' "$0"
      exit 0
      ;;
  esac
done

echo "=========================================="
echo "Distill Runtime — startup"
echo "=========================================="

if [[ ! -f "$RUNTIME_DIR/.env" ]]; then
  if [[ -f "$RUNTIME_DIR/.env.example" ]]; then
    cp "$RUNTIME_DIR/.env.example" "$RUNTIME_DIR/.env"
    echo "Created runtime/.env from example — edit HF_TOKEN / models if needed."
  fi
fi

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a; source "$RUNTIME_DIR/.env"; set +a
fi

# Map WHISPER_DEVICE / DIARIZATION_DEVICE → Dockerfile.cuda | Dockerfile
# shellcheck disable=SC1091
source "$SCRIPT_DIR/resolve_dockerfiles.sh" "$PROFILE"
echo "[docker] STT image: $STT_DOCKERFILE (WHISPER_DEVICE=${WHISPER_DEVICE:-cuda})"
echo "[docker] Diarize image: $DIARIZE_DOCKERFILE (DIARIZATION_DEVICE=${DIARIZATION_DEVICE:-cpu})"

# Best-effort weight download (does not fail startup if NeMo/offline later)
"$SCRIPT_DIR/download_models.sh" || true

write_distill_env_hint() {
  cat <<EOF

Point Distill API (.env at repo root) at this stack:

  LLM_ENDPOINT=http://127.0.0.1:${VLLM_PORT:-8001}/v1/chat/completions
  LLM_API_KEY=local
  LLM_MODEL_NAME=${LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}

  STT_ENDPOINT=http://127.0.0.1:${STT_PORT:-8080}/v1/audio/transcriptions
  STT_API_KEY=local
  STT_MODEL=${WHISPER_MODEL:-large-v3}

  DIARIZATION_ENDPOINT=http://127.0.0.1:${DIARIZE_PORT:-8090}
  DIARIZATION_ALLOW_FALLBACK=0
  DISTILL_ENABLE_PYANNOTE=0

Then: python -m api
EOF
}

if [[ "$NATIVE" -eq 1 ]]; then
  LOG_DIR="$RUNTIME_DIR/logs"
  mkdir -p "$LOG_DIR"
  echo "[native] starting processes (logs under $LOG_DIR)"

  if [[ "$PROFILE" == "full" || "$PROFILE" == "gpu" ]]; then
    nohup "$SCRIPT_DIR/run_vllm.sh" >"$LOG_DIR/vllm.log" 2>&1 &
    echo $! >"$LOG_DIR/vllm.pid"
    echo "  vLLM pid $(cat "$LOG_DIR/vllm.pid")"
  fi

  if [[ "${COMPOSE_SERVICES[*]-}" != "llm" && "$PROFILE" != "cpu" ]]; then
    nohup "$SCRIPT_DIR/run_stt.sh" >"$LOG_DIR/stt.log" 2>&1 &
    echo $! >"$LOG_DIR/stt.pid"
    echo "  STT  pid $(cat "$LOG_DIR/stt.pid")"
  fi

  if [[ "${COMPOSE_SERVICES[*]-}" != "llm" ]]; then
    nohup "$SCRIPT_DIR/run_diarize.sh" >"$LOG_DIR/diarize.log" 2>&1 &
    echo $! >"$LOG_DIR/diarize.pid"
    echo "  Diarize pid $(cat "$LOG_DIR/diarize.pid")"
  fi

  write_distill_env_hint
  if [[ "$WAIT" -eq 1 ]]; then
    wait_args=()
    [[ "$PROFILE" == "audio" ]] && wait_args+=(--audio)
    [[ "${COMPOSE_SERVICES[*]-}" == "llm" ]] && wait_args+=(--llm)
    [[ "$PROFILE" == "cpu" ]] && wait_args+=(--diarize)
    "$SCRIPT_DIR/wait_ready.sh" "${wait_args[@]+"${wait_args[@]}"}"
  else
    echo "Health: ./runtime/scripts/healthcheck.sh"
    echo "Wait:   ./runtime/scripts/wait_ready.sh"
  fi
  exit 0
fi

# Docker path
if ! command -v docker >/dev/null 2>&1; then
  echo "ERROR: Docker not found. Use --native or install Docker." >&2
  exit 1
fi
if ! docker compose version >/dev/null 2>&1; then
  echo "ERROR: Docker Compose v2 required." >&2
  exit 1
fi

cd "$RUNTIME_DIR"
echo "[docker] profile=$PROFILE${COMPOSE_SERVICES[*]:+ services=${COMPOSE_SERVICES[*]}}"
docker compose --profile "$PROFILE" up -d --build "${COMPOSE_SERVICES[@]+"${COMPOSE_SERVICES[@]}"}"

write_distill_env_hint
if [[ "$WAIT" -eq 1 ]]; then
  wait_args=()
  [[ "$PROFILE" == "audio" ]] && wait_args+=(--audio)
  [[ "${COMPOSE_SERVICES[*]-}" == "llm" ]] && wait_args+=(--llm)
  [[ "$PROFILE" == "cpu" ]] && wait_args+=(--diarize)
  "$SCRIPT_DIR/wait_ready.sh" "${wait_args[@]+"${wait_args[@]}"}"
else
  echo "Health: ./runtime/scripts/healthcheck.sh"
  echo "Wait:   ./runtime/scripts/wait_ready.sh"
fi
echo "Logs:   ./runtime/scripts/logs.sh"
echo "Stop:   ./runtime/scripts/stop.sh"
