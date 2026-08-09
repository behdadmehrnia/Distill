# Distill diarization sidecar

Runs `pyannote/speaker-diarization-3.1` as a separate HTTP service so the light
API (`python -m api`) does not load torch.

## Prerequisites

1. `HF_TOKEN` in the repo `.env` (read-access token).
2. Accept gated terms while logged into Hugging Face:
   - https://huggingface.co/pyannote/speaker-diarization-3.1
   - https://huggingface.co/pyannote/segmentation-3.0
3. Outbound access to the Hub (or a mirror). If `huggingface.co` is blocked,
   add to `.env`:

```bash
HF_ENDPOINT=https://hf-mirror.com
```

## Run

```bash
cd diarize
docker compose up -d --build
curl -s http://127.0.0.1:8090/health
# wait until "ready": true  (first download can take several minutes)
```

If the model failed earlier and you fixed network/token:

```bash
curl -X POST http://127.0.0.1:8090/v1/reload
```

Diarize: `POST /v1/diarize` (multipart WAV + optional `min_speakers` / `max_speakers`)

Needs roughly **≥2Gi** RAM. Weights land in the `diarize-cache` volume.

## Alternative: run locally (no Docker)

```bash
# Accept gated model terms on Hugging Face first (same account as HF_TOKEN).
# Ensure .env has HF_TOKEN and preferably:
#   HF_ENDPOINT=https://hf-mirror.com

./diarize/run_local.sh
# then: curl -s http://127.0.0.1:8090/health
```

Keep `DIARIZATION_ENDPOINT=http://127.0.0.1:8090` for `python -m api`.

In the repo `.env`:

```bash
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DISTILL_ENABLE_PYANNOTE=0
```

Then:

```bash
python -m api
```
