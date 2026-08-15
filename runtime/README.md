# Distill local runtime

Production-oriented local inference for Distill — same idea as
[MA-runtime](https://github.com/BMDarkLight/MA-runtime).

**Full guide (profiles, model choice, every env var):**  
[`docs/MODELS.md`](../docs/MODELS.md)

| Service | Port | Role |
|---------|------|------|
| **llm** (vLLM) | `8001` | Chat completions (insights / minutes / polish) |
| **stt** (faster-whisper) | `8080` | OpenAI-compatible `/v1/audio/transcriptions` |
| **diarize** | `8090` | Local speaker diarization `/v1/diarize` |

Diarization **always runs locally**. Hugging Face is used **only** to download
gated pyannote weights when `HF_TOKEN` is valid and has accepted model terms.
Otherwise use offline `.bin` weights under `diarize/models/`, or set
`DIARIZATION_BACKEND=nemo` / `INSTALL_NEMO=1` (NVIDIA NeMo ClusteringDiarizer —
no gated pyannote).

## Profiles

| Command | Services |
|---------|----------|
| `./scripts/run_diarize.sh` | Diarize only |
| `./scripts/start.sh --audio` | STT + diarize |
| `./scripts/start.sh` | LLM + STT + diarize |
| `./scripts/start.sh --native` | Same, without Compose |

See [`docs/MODELS.md`](../docs/MODELS.md) for root `.env` wiring and model selection.

## Hybrid: NeMo only + cloud STT/LLM

If repo-root `.env` already has `LLM_*` / `STT_*` API keys:

```bash
# Prefer Docker on Windows:
INSTALL_NEMO=1 DIARIZATION_BACKEND=nemo \
  docker compose -f ../diarize/docker-compose.yml up -d --build

# or native (Linux/WSL):
DIARIZATION_BACKEND=nemo ./scripts/run_diarize.sh
```

Then `python -m api` with `DIARIZATION_ENDPOINT=http://127.0.0.1:8090`.

## Quick start (full local stack)

```bash
cd runtime
cp .env.example .env
# Recommended without HF: DIARIZATION_BACKEND=nemo, INSTALL_NEMO=1
./scripts/download_models.sh   # optional — pyannote offline weights only
./scripts/start.sh
./scripts/healthcheck.sh
```

Windows: `.\runtime\scripts\start.ps1`

## Point Distill at the stack

In the **repo root** `.env` (see also `docs/MODELS.md`):

```bash
LLM_ENDPOINT=http://127.0.0.1:8001/v1/chat/completions
LLM_API_KEY=local
LLM_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct

STT_ENDPOINT=http://127.0.0.1:8080/v1/audio/transcriptions
STT_API_KEY=local
STT_MODEL=large-v3

DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

Then: `python -m api`

## Default models (full accuracy)

| Role | Default |
|------|---------|
| STT | Whisper **`large-v3`** |
| LLM | **`Qwen/Qwen2.5-7B-Instruct`** (vLLM) |
| Diarize | **NeMo** (`INSTALL_NEMO=1`) or **pyannote 3.1** |

**RTX 4090 (24 GB)** is adequate. Prefer `DIARIZATION_DEVICE=cpu` and
`VLLM_GPU_MEMORY_UTILIZATION=0.45` when sharing one GPU with Whisper.

## Diarization backends

`DIARIZATION_BACKEND` (sidecar):

| Value | Behavior |
|-------|----------|
| `auto` | Offline pyannote → Hub (**only if token validates**) → NeMo |
| `pyannote` | Offline or validated Hub only |
| `nemo` | [NVIDIA NeMo ClusteringDiarizer](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/asr/speaker_diarization/intro.html) |

Docker builds **one** stack per image via `INSTALL_NEMO=0|1` (not both).

## Scripts

| Script | Purpose |
|--------|---------|
| `scripts/start.sh` / `start.ps1` | Bring up stack (Docker or `--native`) |
| `scripts/stop.sh` | Stop Docker + native PIDs |
| `scripts/wait_ready.sh` | Block until models report `ready` |
| `scripts/healthcheck.sh` | Probe LLM / STT / diarize |
| `scripts/download_models.sh` | Fetch offline pyannote `.bin` weights |
| `scripts/run_vllm.sh` | LLM only |
| `scripts/run_stt.sh` | STT only |
| `scripts/run_diarize.sh` | Diarization only |
| `scripts/test.sh` | Smoke WAV against STT + diarize |
| `scripts/logs.sh` | Tail logs |

## VRAM (rough)

| Mode | Estimate |
|------|----------|
| Whisper large-v3 + pyannote | ~5 GB |
| + Qwen2.5-7B (vLLM) | ~12–16 GB |
| Full stack comfortable | 24 GB |

CPU works for STT/diarize (slower). vLLM needs NVIDIA GPU.

## License

Same as Distill. Runtime layout inspired by MA-runtime (MIT).
