#!/usr/bin/env bash
# Run the pyannote diarization sidecar locally (no Docker).
# Usage (from repo root or this directory):
#   ./diarize/run_local.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
DIR="$(cd "$(dirname "$0")" && pwd)"
VENV="${DIR}/.venv"
PY="${VENV}/bin/python"
PORT="${DIARIZE_PORT:-8090}"
ENV_FILE="${ROOT}/.env"

if [[ -f "$ENV_FILE" ]]; then
  # shellcheck disable=SC1090
  set -a
  # shellcheck disable=SC1091
  source "$ENV_FILE"
  set +a
fi

export HF_ENDPOINT="${HF_ENDPOINT:-https://hf-mirror.com}"
export HF_HOME="${HF_HOME:-$ROOT/data/hf_cache}"
export TORCH_HOME="${TORCH_HOME:-$ROOT/data/torch_cache}"
export DIARIZE_HOST="${DIARIZE_HOST:-0.0.0.0}"
export DIARIZE_PORT="$PORT"
export CUDA_VISIBLE_DEVICES=""
mkdir -p "$HF_HOME" "$TORCH_HOME"

if [[ -z "${HF_TOKEN:-}" && -z "${HUGGINGFACE_TOKEN:-}" && -z "${HUGGING_FACE_HUB_TOKEN:-}" ]]; then
  echo "ERROR: HF_TOKEN missing in environment / $ENV_FILE" >&2
  exit 1
fi

echo "==> HF_ENDPOINT=$HF_ENDPOINT"
echo "==> HF_HOME=$HF_HOME"
echo "==> binding 0.0.0.0:$PORT"

venv_ok() {
  [[ -x "$PY" && -f "$VENV/bin/activate" && -x "$VENV/bin/pip" ]]
}

if ! venv_ok; then
  echo "==> (re)creating venv at $VENV"
  rm -rf "$VENV"
  # Prefer the project venv interpreter; fall back to python3.
  BOOTSTRAP="${ROOT}/.venv/bin/python"
  if [[ ! -x "$BOOTSTRAP" ]]; then
    BOOTSTRAP="$(command -v python3)"
  fi
  echo "==> bootstrap interpreter: $BOOTSTRAP"
  "$BOOTSTRAP" -m venv "$VENV"
  if ! venv_ok; then
    echo "ERROR: venv still incomplete after create (missing activate/pip)." >&2
    echo "Try: rm -rf diarize/.venv && /usr/bin/python3 -m venv diarize/.venv" >&2
    exit 1
  fi
fi

"$PY" -m pip install -q --upgrade pip

echo "==> installing CPU torch + pyannote (first run can take a while)"
"$PY" -m pip install -q --index-url https://download.pytorch.org/whl/cpu torch torchaudio
"$PY" -m pip install -q -r "$DIR/requirements.txt"
"$PY" -m pip install -q --index-url https://download.pytorch.org/whl/cpu --force-reinstall --no-deps torch torchaudio
"$PY" -c "import torch; assert torch.version.cuda is None; print('torch', torch.__version__, 'cpu-only OK')"

echo "==> prefetching pyannote/speaker-diarization-3.1 (and deps)"
"$PY" - <<'PY'
import os
from huggingface_hub import snapshot_download

token = (
    os.getenv("HF_TOKEN")
    or os.getenv("HUGGINGFACE_TOKEN")
    or os.getenv("HUGGING_FACE_HUB_TOKEN")
)
endpoint = (os.getenv("HF_ENDPOINT") or "https://huggingface.co").rstrip("/")
models = [
    "pyannote/speaker-diarization-3.1",
    "pyannote/segmentation-3.0",
    "pyannote/wespeaker-voxceleb-resnet34-LM",
]
for mid in models:
    print(f"download {mid} via {endpoint} ...")
    try:
        path = snapshot_download(repo_id=mid, token=token)
        print(f"  ok -> {path}")
    except Exception as exc:
        print(f"  WARN {mid}: {exc}")
PY

echo "==> starting diarize server on :$PORT"
cd "$DIR"
exec "$PY" server.py
