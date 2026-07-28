# Distill API — meeting assistant
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    MEETING_HOST=0.0.0.0 \
    MEETING_PORT=8000 \
    PORT=8000

WORKDIR /app

# pydub needs ffmpeg; ca-certificates needed for HTTPS STT/LLM calls
RUN apt-get update \
    && apt-get install -y --no-install-recommends ffmpeg ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --upgrade pip \
    && pip install -r requirements.txt

COPY api/ ./api/
COPY main.py .

RUN mkdir -p /app/data/uploads /app/data/audio /app/data/stt_cache

EXPOSE 8000

CMD ["python", "-m", "api"]
