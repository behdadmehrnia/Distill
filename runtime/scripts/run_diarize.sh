#!/usr/bin/env bash
# Start local diarization sidecar (pyannote offline / validated Hub / NeMo).
# Usage: ./runtime/scripts/run_diarize.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$RUNTIME_DIR/.." && pwd)"
DIARIZE_DIR="$ROOT_DIR/diarize"
VENV="${DIARIZE_VENV:-$RUNTIME_DIR/.venv-diarize}"

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a; source "$RUNTIME_DIR/.env"; set +a
elif [[ -f "$ROOT_DIR/.env" ]]; then
  set -a; source "$ROOT_DIR/.env"; set +a
fi

export DIARIZE_HOST="${DIARIZE_HOST:-127.0.0.1}"
export DIARIZE_PORT="${DIARIZE_PORT:-8090}"
export DIARIZATION_BACKEND="${DIARIZATION_BACKEND:-auto}"
export DIARIZATION_DEVICE="${DIARIZATION_DEVICE:-auto}"
export HF_HOME="${HF_HOME:-$RUNTIME_DIR/cache/hf}"
export TORCH_HOME="${TORCH_HOME:-$RUNTIME_DIR/cache/torch}"
mkdir -p "$HF_HOME" "$TORCH_HOME"

SEG="$DIARIZE_DIR/models/pyannote_model_segmentation-3.0.bin"
EMB="$DIARIZE_DIR/models/pyannote_model_wespeaker-voxceleb-resnet34-LM.bin"

echo "=========================================="
echo "Distill Diarization"
echo "  backend_pref: $DIARIZATION_BACKEND"
echo "  listen:       http://$DIARIZE_HOST:$DIARIZE_PORT"
echo "=========================================="

HAS_OFFLINE=0
if [[ -f "$SEG" && -f "$EMB" ]]; then
  echo "[ok] offline pyannote weights found"
  HAS_OFFLINE=1
elif [[ -n "${HF_TOKEN:-}" ]]; then
  echo "[info] no offline weights — Hub only if HF_TOKEN has gated access"
  echo "       tip: ./runtime/scripts/download_models.sh"
else
  echo "[info] no offline weights and no HF_TOKEN"
  echo "       → will use NeMo when DIARIZATION_BACKEND=auto|nemo"
fi

pick_python() {
  if [[ -n "${PYTHON_BIN:-}" ]]; then
    echo "$PYTHON_BIN"
    return
  fi
  if command -v python3 >/dev/null 2>&1; then
    echo "python3"
    return
  fi
  if command -v python >/dev/null 2>&1; then
    echo "python"
    return
  fi
  echo "ERROR: python3/python not found" >&2
  exit 1
}

PYTHON_BIN="$(pick_python)"

activate_venv() {
  if [[ -f "$VENV/bin/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/bin/activate"
  elif [[ -f "$VENV/Scripts/activate" ]]; then
    # shellcheck disable=SC1091
    source "$VENV/Scripts/activate"
  else
    echo "ERROR: venv activate script missing in $VENV" >&2
    exit 1
  fi
}

if [[ ! -d "$VENV" ]]; then
  echo "==> creating venv $VENV"
  "$PYTHON_BIN" -m venv "$VENV"
  activate_venv
  pip install --upgrade pip
  if command -v nvidia-smi >/dev/null 2>&1; then
    # cu128 required for RTX 50xx (sm_120); cu124 crashes with "no kernel image"
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cu128
  else
    pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
  fi
  pip install -r "$DIARIZE_DIR/requirements.txt"
else
  activate_venv
fi

need_nemo=0
if [[ "$DIARIZATION_BACKEND" == "nemo" ]]; then
  need_nemo=1
elif [[ "$DIARIZATION_BACKEND" == "auto" && "$HAS_OFFLINE" -eq 0 ]]; then
  need_nemo=1
fi

if [[ "$need_nemo" -eq 1 ]]; then
  if ! python -c "from nemo.collections.asr.models import ClusteringDiarizer" >/dev/null 2>&1; then
    echo "==> installing NeMo extras (no gated pyannote path)"
    pip install -r "$DIARIZE_DIR/requirements.nemo.txt" || {
      echo "ERROR: NeMo install failed and pyannote offline/Hub path unavailable." >&2
      echo "       Fix: set HF_TOKEN with gated access + download_models.sh," >&2
      echo "       or place .bin weights under diarize/models/." >&2
      exit 1
    }
  fi
fi

cd "$DIARIZE_DIR"
exec python server.py
