#!/usr/bin/env bash
# Download pyannote offline weights when HF_TOKEN has gated access.
# Usage: ./runtime/scripts/download_models.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$RUNTIME_DIR/.." && pwd)"
MODELS_DIR="$ROOT_DIR/diarize/models"

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$RUNTIME_DIR/.env"
  set +a
elif [[ -f "$ROOT_DIR/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "$ROOT_DIR/.env"
  set +a
fi

SEG_URL="https://huggingface.co/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin"
EMB_URL="https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM/resolve/main/pytorch_model.bin"
SEG_OUT="$MODELS_DIR/pyannote_model_segmentation-3.0.bin"
EMB_OUT="$MODELS_DIR/pyannote_model_wespeaker-voxceleb-resnet34-LM.bin"

mkdir -p "$MODELS_DIR"

download() {
  local url="$1" out="$2" name="$3"
  if [[ -f "$out" && -s "$out" ]]; then
    echo "[ok] $name already present: $out"
    return 0
  fi
  if [[ -z "${HF_TOKEN:-}" ]]; then
    echo "[skip] $name — HF_TOKEN not set (need Hub) or place file manually at:"
    echo "       $out"
    return 1
  fi
  echo "[..] downloading $name"
  if command -v curl >/dev/null 2>&1; then
    curl -fL --progress-bar \
      -H "Authorization: Bearer $HF_TOKEN" \
      -o "$out" \
      "$url"
  else
    echo "ERROR: curl required" >&2
    return 1
  fi
  if [[ ! -s "$out" ]]; then
    echo "ERROR: download empty: $out" >&2
    rm -f "$out"
    return 1
  fi
  echo "[ok] $name → $out"
}

echo "=========================================="
echo "Distill — download diarization weights"
echo "=========================================="
echo "Target: $MODELS_DIR"
echo ""

# Validate token access (best-effort)
if [[ -n "${HF_TOKEN:-}" ]]; then
  code="$(curl -s -o /dev/null -w "%{http_code}" \
    -H "Authorization: Bearer $HF_TOKEN" \
    "https://huggingface.co/api/models/pyannote/speaker-diarization-3.1" || true)"
  if [[ "$code" == "200" ]]; then
    echo "[ok] HF_TOKEN has access to pyannote/speaker-diarization-3.1"
  else
    echo "[warn] HF check returned HTTP $code — accept gated terms on Hugging Face:"
    echo "       https://huggingface.co/pyannote/speaker-diarization-3.1"
    echo "       https://huggingface.co/pyannote/segmentation-3.0"
    echo "       https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM"
  fi
else
  echo "[info] No HF_TOKEN — will only succeed if weights already exist,"
  echo "       or use DIARIZATION_BACKEND=nemo (no gated pyannote needed)."
fi

ok=0
download "$SEG_URL" "$SEG_OUT" "segmentation-3.0" && ok=$((ok + 1)) || true
download "$EMB_URL" "$EMB_OUT" "wespeaker embedding" && ok=$((ok + 1)) || true

if [[ -f "$MODELS_DIR/pyannote_diarization_config.yaml" ]]; then
  echo "[ok] config yaml present"
else
  echo "[warn] missing pyannote_diarization_config.yaml"
fi

echo ""
if [[ -f "$SEG_OUT" && -f "$EMB_OUT" ]]; then
  echo "Offline pyannote weights ready. Start diarize with:"
  echo "  ./runtime/scripts/run_diarize.sh"
  echo "  # or: ./runtime/scripts/start.sh"
  exit 0
fi

echo "Weights incomplete ($ok/2 files)."
echo "Options:"
echo "  1) Set a working HF_TOKEN with gated access and re-run this script"
echo "  2) Manually place the two .bin files (see diarize/models/README.md)"
echo "  3) Use NeMo instead: DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh"
exit 1
