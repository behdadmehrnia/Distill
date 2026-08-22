#!/usr/bin/env bash
# docker compose wrapper — resolves Dockerfiles from WHISPER_DEVICE / DIARIZATION_DEVICE.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"

cd "$RUNTIME_DIR"

if [[ -f "$RUNTIME_DIR/.env" ]]; then
  # shellcheck disable=SC1091
  set -a; source "$RUNTIME_DIR/.env"; set +a
fi

PROFILE=full
args=("$@")
for ((i = 0; i < ${#args[@]}; i++)); do
  if [[ "${args[i]}" == "--profile" && $((i + 1)) -lt ${#args[@]} ]]; then
    PROFILE="${args[i + 1]}"
    break
  fi
  if [[ "${args[i]}" == --profile=* ]]; then
    PROFILE="${args[i]#--profile=}"
    break
  fi
done

# shellcheck disable=SC1091
source "$SCRIPT_DIR/resolve_dockerfiles.sh" "$PROFILE"

echo "[compose] STT → $STT_DOCKERFILE (WHISPER_DEVICE=${WHISPER_DEVICE:-cuda})"
echo "[compose] Diarize → $DIARIZE_DOCKERFILE (DIARIZATION_DEVICE=${DIARIZATION_DEVICE:-cpu})"

exec docker compose "$@"
