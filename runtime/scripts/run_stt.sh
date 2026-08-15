#!/usr/bin/env bash
# Start local Whisper STT (OpenAI-compatible) for Distill.
# Usage: ./runtime/scripts/run_stt.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$RUNTIME_DIR/.." && pwd)"
STT_DIR="$RUNTIME_DIR/stt"
VENV="${STT_VENV:-$RUNTIME_DIR/.venv-stt}"

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a; source "$RUNTIME_DIR/.env"; set +a
elif [[ -f "$ROOT_DIR/.env" ]]; then
  set -a; source "$ROOT_DIR/.env"; set +a
fi

export STT_HOST="${STT_HOST:-127.0.0.1}"
export STT_PORT="${STT_PORT:-8080}"
export WHISPER_MODEL="${WHISPER_MODEL:-large-v3}"
export WHISPER_DEVICE="${WHISPER_DEVICE:-auto}"
export WHISPER_COMPUTE_TYPE="${WHISPER_COMPUTE_TYPE:-auto}"
export HF_HOME="${HF_HOME:-$RUNTIME_DIR/cache/hf}"
mkdir -p "$HF_HOME"

echo "=========================================="
echo "Distill STT (faster-whisper)"
echo "  model:  $WHISPER_MODEL"
echo "  listen: http://$STT_HOST:$STT_PORT"
echo "=========================================="

PYTHON_BIN="${PYTHON_BIN:-}"
if [[ -z "$PYTHON_BIN" ]]; then
  if command -v python3 >/dev/null 2>&1; then PYTHON_BIN=python3
  elif command -v python >/dev/null 2>&1; then PYTHON_BIN=python
  else echo "ERROR: python not found" >&2; exit 1; fi
fi

activate_venv() {
  if [[ -f "$VENV/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
  elif [[ -f "$VENV/Scripts/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/Scripts/activate"
  else
    echo "ERROR: venv activate missing in $VENV" >&2
    exit 1
  fi
}

if [[ ! -d "$VENV" ]]; then
  echo "==> creating venv $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
  activate_venv
  pip install --upgrade pip
  pip install -r "$STT_DIR/requirements.txt"
else
  activate_venv
fi

cd "$STT_DIR"
exec python server.py
