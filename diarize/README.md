# Distill diarization sidecar

Local speaker diarization for Distill (`pyannote` and/or **NVIDIA NeMo**).

**Full model-stack docs:** [`../docs/MODELS.md`](../docs/MODELS.md)

Production path: run via `../runtime/scripts/start.sh` (or this compose / `run_diarize.sh`) and
point the API at `DIARIZATION_ENDPOINT=http://127.0.0.1:8090` with
`DIARIZATION_ALLOW_FALLBACK=0`.

## Backends

`DIARIZATION_BACKEND=auto|pyannote|nemo`

| Priority (`auto`) | Requirement |
|-------------------|-------------|
| 1. Offline pyannote | `.bin` weights in `models/` + YAML |
| 2. Hub pyannote | **Validated** `HF_TOKEN` with gated access to [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) (+ segmentation + wespeaker) |
| 3. NeMo | `pip install -r requirements.nemo.txt` — [NeMo speaker diarization](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/asr/speaker_diarization/intro.html) |

Hub is **never** used with a blank/invalid token or when gated terms are not accepted.

## Quick start

```bash
# Prefer the runtime orchestrator:
./runtime/scripts/download_models.sh   # needs valid HF_TOKEN once
./runtime/scripts/run_diarize.sh

# No HF? use NeMo:
DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh
```

API:

```bash
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

Health:

```bash
curl -s http://127.0.0.1:8090/health
# want: "ready": true, "backend": "pyannote" | "nemo"
```

Diarize:

```bash
curl -F file=@meeting.wav -F min_speakers=1 -F max_speakers=4 \
  http://127.0.0.1:8090/v1/diarize
```

## Docker

```bash
# pyannote (offline weights mounted from models/)
docker compose up -d --build

# NeMo without HF:
docker build --build-arg INSTALL_NEMO=1 -t distill-diarize .
```

See `../runtime/README.md` for the full LLM + STT + diarize stack.
