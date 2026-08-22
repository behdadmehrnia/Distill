#!/usr/bin/env bash
# Map WHISPER_DEVICE / DIARIZATION_DEVICE → STT_DOCKERFILE / DIARIZE_DOCKERFILE.
#
# Usage (after loading runtime/.env):
#   source runtime/scripts/resolve_dockerfiles.sh [compose_profile]
#
# Device → Dockerfile:
#   cuda | gpu  → Dockerfile.cuda
#   cpu | mps   → Dockerfile
#   auto        → Dockerfile.cuda for full/gpu/audio profiles, else Dockerfile

_distill_device_dockerfile() {
  local device="${1:-cpu}"
  local profile="${2:-full}"
  device="$(printf '%s' "$device" | tr '[:upper:]' '[:lower:]')"
  if [[ "$device" == "auto" ]]; then
    if [[ "$profile" == "cpu" ]]; then
      device=cpu
    else
      device=cuda
    fi
  fi
  case "$device" in
    cuda|gpu) echo "Dockerfile.cuda" ;;
    *) echo "Dockerfile" ;;
  esac
}

_distill_resolve_dockerfiles() {
  local profile="${1:-full}"
  local stt_dev="${WHISPER_DEVICE:-cuda}"
  local dia_dev="${DIARIZATION_DEVICE:-cpu}"
  if [[ "$profile" == "cpu" ]]; then
    dia_dev=cpu
  fi
  export STT_DOCKERFILE="$(_distill_device_dockerfile "$stt_dev" "$profile")"
  export DIARIZE_DOCKERFILE="$(_distill_device_dockerfile "$dia_dev" "$profile")"
}

if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  set -euo pipefail
  SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
  RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
  PROFILE="${1:-full}"
  if [[ -f "$RUNTIME_DIR/.env" ]]; then
    # shellcheck disable=SC1091
    set -a; source "$RUNTIME_DIR/.env"; set +a
  fi
  _distill_resolve_dockerfiles "$PROFILE"
  printf 'STT_DOCKERFILE=%s\nDIARIZE_DOCKERFILE=%s\n' "$STT_DOCKERFILE" "$DIARIZE_DOCKERFILE"
else
  _distill_resolve_dockerfiles "${1:-full}"
fi
