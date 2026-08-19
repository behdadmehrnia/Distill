#!/usr/bin/env bash
# Start local vLLM OpenAI-compatible LLM for Distill.
# Usage: ./runtime/scripts/run_vllm.sh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$(cd "$SCRIPT_DIR/.." && pwd)"
ROOT_DIR="$(cd "$RUNTIME_DIR/.." && pwd)"

# shellcheck disable=SC1091
if [[ -f "$RUNTIME_DIR/.env" ]]; then
  set -a; source "$RUNTIME_DIR/.env"; set +a
elif [[ -f "$ROOT_DIR/.env" ]]; then
  set -a; source "$ROOT_DIR/.env"; set +a
fi

MODEL="${LLM_MODEL:-Qwen/Qwen2.5-7B-Instruct}"
PORT="${VLLM_PORT:-8001}"
UTIL="${VLLM_GPU_MEMORY_UTILIZATION:-0.45}"
MAX_LEN="${VLLM_MAX_MODEL_LEN:-8192}"
MAX_SEQS="${VLLM_MAX_NUM_SEQS:-8}"
GPU="${LLM_GPU:-0}"

export CUDA_VISIBLE_DEVICES="$GPU"
export HF_TOKEN="${HF_TOKEN:-}"
export HUGGING_FACE_HUB_TOKEN="${HF_TOKEN:-}"

echo "=========================================="
echo "Distill vLLM"
echo "  model: $MODEL"
echo "  port:  $PORT"
echo "  gpu:   $GPU"
echo "=========================================="

if command -v nvidia-smi >/dev/null 2>&1; then
  nvidia-smi --query-gpu=name,memory.total --format=csv,noheader || true
else
  echo "WARNING: nvidia-smi not found — vLLM usually needs an NVIDIA GPU."
fi

IMAGE="${VLLM_IMAGE:-vllm/vllm-openai:latest}"

if command -v vllm >/dev/null 2>&1; then
  exec vllm serve "$MODEL" \
    --host 127.0.0.1 \
    --port "$PORT" \
    --gpu-memory-utilization "$UTIL" \
    --max-model-len "$MAX_LEN" \
    --max-num-seqs "$MAX_SEQS" \
    --dtype auto \
    --trust-remote-code
fi

# Docker fallback (same image as compose)
if command -v docker >/dev/null 2>&1; then
  echo "vllm CLI not found — starting Docker image $IMAGE"
  exec docker run --rm --gpus "device=$GPU" --ipc=host \
    -p "127.0.0.1:${PORT}:8000" \
    -e HF_TOKEN \
    -e HUGGING_FACE_HUB_TOKEN \
    -v distill-hf-cache:/root/.cache/huggingface \
    "$IMAGE" \
    "$MODEL" \
    --host 0.0.0.0 \
    --port 8000 \
    --gpu-memory-utilization "$UTIL" \
    --max-model-len "$MAX_LEN" \
    --max-num-seqs "$MAX_SEQS" \
    --dtype auto \
    --trust-remote-code
fi

echo "ERROR: neither 'vllm' nor 'docker' available." >&2
echo "Install: pip install vllm   OR   install Docker + NVIDIA Container Toolkit" >&2
exit 1
