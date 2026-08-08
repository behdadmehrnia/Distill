# Standalone pyannote diarization HTTP service (CPU-only).
# Not part of the main Distill API image — run separately on a machine with RAM.
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    DIARIZE_HOST=0.0.0.0 \
    DIARIZE_PORT=8090 \
    PORT=8090 \
    CUDA_VISIBLE_DEVICES="" \
    HF_HOME=/data/hf_cache \
    TORCH_HOME=/data/torch_cache \
    XDG_CACHE_HOME=/data/cache \
    OMP_NUM_THREADS=1 \
    MKL_NUM_THREADS=1 \
    OPENBLAS_NUM_THREADS=1 \
    TORCH_NUM_THREADS=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
        libsndfile1 \
        libgomp1 \
        curl \
        ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt ./

RUN pip install --upgrade pip \
    && pip install --index-url https://download.pytorch.org/whl/cpu \
        torch \
        torchaudio \
    && pip install -r requirements.txt \
    && pip install --index-url https://download.pytorch.org/whl/cpu \
        --force-reinstall --no-deps \
        torch \
        torchaudio \
    && python -c "import torch; assert torch.version.cuda is None, torch.version.cuda; print('torch', torch.__version__, 'cpu-only OK')"

COPY server.py ./

RUN mkdir -p /data/hf_cache /data/torch_cache /data/cache
VOLUME ["/data"]

EXPOSE 8090

# Bind ASAP; model loads in a background thread (see server lifespan).
HEALTHCHECK --interval=15s --timeout=5s --start-period=60s --retries=5 \
    CMD curl -fsS http://127.0.0.1:8090/health || exit 1

CMD ["python", "server.py"]
