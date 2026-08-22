# Distill API — meeting assistant (CPU-only, includes pyannote diarization)
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEETING_HOST=0.0.0.0 \
    MEETING_PORT=8000 \
    PORT=8000 \
    # Force CPU; keep HF/torch caches on the data volume across rebuilds
    CUDA_VISIBLE_DEVICES="" \
    HF_HOME=/app/data/hf_cache \
    TORCH_HOME=/app/data/torch_cache \
    XDG_CACHE_HOME=/app/data/cache \
    # Keep native thread pools small (matters when pyannote/torch eventually loads)
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    TORCH_NUM_THREADS=1

WORKDIR /app

# ffmpeg: pydub; libsndfile1: soundfile/pyannote; libgomp1: torch CPU
RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        ffmpeg \
        ca-certificates \
        libsndfile1 \
        libgomp1 \
        curl \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.optional.txt ./

# Core deps → CPU torch first → pyannote (re-pin CPU torch so PyPI cannot swap in CUDA)
RUN pip install --upgrade pip \
    && pip install -r requirements.txt \
    && pip install --index-url https://download.pytorch.org/whl/cpu \
        torch \
        torchaudio \
    && pip install -r requirements.optional.txt \
    && pip install --index-url https://download.pytorch.org/whl/cpu \
        --force-reinstall --no-deps \
        torch \
        torchaudio \
    && python -c "import torch; assert torch.version.cuda is None, torch.version.cuda; print('torch', torch.__version__, 'cpu-only OK')" \
    && pip check

COPY api/ ./api/
COPY main.py .

ARG DEEPFILTER_STREAM_TAG=model-dfn3-512-v1
RUN mkdir -p /app/share/deepfilter-stream/dfn3-512-v1 \
    && curl -fsSL -o /app/share/deepfilter-stream/dfn3-512-v1/denoiser_model.onnx \
        "https://github.com/wuxuedaifu/deepfilter-stream/releases/download/${DEEPFILTER_STREAM_TAG}/denoiser_model.onnx" \
    && curl -fsSL -o /app/share/deepfilter-stream/dfn3-512-v1/initial_states.npz \
        "https://github.com/wuxuedaifu/deepfilter-stream/releases/download/${DEEPFILTER_STREAM_TAG}/initial_states.npz" \
    && curl -fsSL -o /app/share/deepfilter-stream/dfn3-512-v1/meta.json \
        "https://github.com/wuxuedaifu/deepfilter-stream/releases/download/${DEEPFILTER_STREAM_TAG}/meta.json"

ENV DEEPFILTER_STREAM_MODEL_DIR=/app/share/deepfilter-stream/dfn3-512-v1

RUN mkdir -p \
        /app/data/uploads \
        /app/data/audio \
        /app/data/stt_cache \
        /app/data/hf_cache \
        /app/data/torch_cache \
        /app/data/cache

# Persist DB, uploads, audio, STT/HF/torch caches across image rebuilds
VOLUME ["/app/data"]

EXPOSE 8000

# Process must listen ASAP; do not load pyannote at boot (OOM → CrashLoop).
# K8s: readiness/liveness GET /health on port 8000.
# Low-RAM pods: set DISTILL_ENABLE_PYANNOTE=0 (fallback diarization only).
# Pyannote quality needs roughly ≥2Gi memory and HF_TOKEN.
HEALTHCHECK --interval=10s --timeout=3s --start-period=20s --retries=3 \
    CMD curl -fsS http://127.0.0.1:8000/health || exit 1

CMD ["python", "-m", "api"]
