# Distill model stack — run, choose, configure

This guide covers the **local inference services** Distill talks to:

| Service | Default port | Role |
|---------|--------------|------|
| **Diarization** | `8090` | Who spoke when (`SPEAKER_XX`) |
| **STT** | `8080` | Speech → text (OpenAI-compatible transcriptions) |
| **LLM** | `8001` | Polish / insights / minutes (OpenAI-compatible chat) |

The **API** (`python -m api` on `:8000`) does not load these models in-process when you use the recommended sidecar setup. It only calls HTTP endpoints from the **repo-root** `.env`.

Related: [`LOCAL_RUN.md`](LOCAL_RUN.md) (quick hybrid vs full-local), [`../runtime/README.md`](../runtime/README.md), [`../diarize/README.md`](../diarize/README.md).

---

## Architecture

```
                    runtime/ (or cloud APIs)
         ┌──────────────┬──────────────┬──────────────┐
         │  LLM :8001   │  STT :8080   │ Diarize :8090│
         │  (vLLM)      │  (Whisper)   │ (NeMo/pyann.)│
         └──────▲───────┴──────▲───────┴──────▲───────┘
                │              │              │
                └──────────────┼──────────────┘
                               │  OpenAI-compatible HTTP
                        ┌──────┴──────┐
                        │  API :8000  │  ← repo-root .env
                        │  UI / WS    │
                        └─────────────┘
```

Two config files:

| File | Purpose |
|------|---------|
| **Repo root `.env`** | What the **API** calls (`LLM_*`, `STT_*`, `DIARIZATION_*`) |
| **`runtime/.env`** | How the **model containers/processes** start (model IDs, GPU, diarize backend) |

---

## Choose a run profile

| Profile | Start command | What runs | Use when |
|---------|---------------|-----------|----------|
| **1 — Diarization only** | `run_diarize.sh` or `diarize/docker-compose` | `:8090` | Speakers local; STT/LLM stay cloud (or later) |
| **2 — Audio (STT + diarize)** | `./runtime/scripts/start.sh --audio` | `:8080` + `:8090` | Local transcription + speakers; LLM still cloud or skip insights |
| **3 — Full local** | `./runtime/scripts/start.sh` | `:8001` + `:8080` + `:8090` | Fully offline assistant (needs NVIDIA GPU for vLLM) |
| **4 — Cloud only** | nothing in `runtime/` | — | All `LLM_*` / `STT_*` point at remote APIs; diarize optional |

After any profile that includes models, start the API:

```bash
pip install -r requirements.txt
python -m api
# UI: http://localhost:8000/assistant
```

---

## 1) Diarization only

### Recommended defaults

| Setting | Value | Notes |
|---------|--------|------|
| Backend | `nemo` | No gated Hugging Face models |
| Or | `pyannote` | Needs offline `.bin` weights **or** valid gated `HF_TOKEN` |
| Device | `cpu` (fine) or `cuda` | GPU helps long files |

**Docker image note:** one image is either NeMo **or** pyannote (`INSTALL_NEMO=1` vs `0`). Do not expect both in the same container.

### Docker (Windows / Linux) — NeMo

```bash
# runtime/.env (or export)
DIARIZATION_BACKEND=nemo
INSTALL_NEMO=1
DIARIZATION_DEVICE=cpu   # or cuda

cd diarize   # or from repo root:
INSTALL_NEMO=1 DIARIZATION_BACKEND=nemo \
  docker compose -f diarize/docker-compose.yml up -d --build

curl -s http://127.0.0.1:8090/health
# expect: "ready": true, "backend": "nemo"
```

### Native script (Linux / WSL / Git Bash)

```bash
cp runtime/.env.example runtime/.env
# set DIARIZATION_BACKEND=nemo

DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh
```

Native **Windows** NeMo via pip often fails (`triton`). Prefer Docker.

### Pyannote instead of NeMo

```bash
# 1) Accept gated terms + set HF_TOKEN, then once:
./runtime/scripts/download_models.sh
# → diarize/models/*.bin

# 2) Build/run without NeMo
INSTALL_NEMO=0 DIARIZATION_BACKEND=pyannote \
  docker compose -f diarize/docker-compose.yml up -d --build
```

### Point the API at diarize (keep cloud STT/LLM if you want)

Repo-root `.env`:

```bash
# leave your existing LLM_* and STT_* as-is for hybrid

DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_TIMEOUT_S=120
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

Smoke:

```bash
curl -F "file=@meeting.wav" -F "min_speakers=1" -F "max_speakers=4" \
  http://127.0.0.1:8090/v1/diarize
```

---

## 2) STT + diarization (no local LLM)

```bash
cp runtime/.env.example runtime/.env
# WHISPER_MODEL=large-v3
# DIARIZATION_BACKEND=nemo
# INSTALL_NEMO=1

./runtime/scripts/start.sh --audio
./runtime/scripts/wait_ready.sh --audio
```

Repo-root `.env`:

```bash
STT_ENDPOINT=http://127.0.0.1:8080/v1/audio/transcriptions
STT_API_KEY=local
STT_MODEL=large-v3

DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0

# LLM can stay cloud, or leave unset if you skip insights/minutes
# LLM_ENDPOINT=...
```

Then `python -m api`.

---

## 3) Full local (LLM + STT + diarization)

**Hardware:** NVIDIA GPU recommended. An **RTX 4090 (24 GB)** fits the default accuracy stack comfortably.

### Default “full accuracy” models

| Role | Config key | Default |
|------|------------|---------|
| LLM | `LLM_MODEL` / `LLM_MODEL_NAME` | `Qwen/Qwen2.5-7B-Instruct` |
| STT | `WHISPER_MODEL` / `STT_MODEL` | `large-v3` |
| Diarize | `DIARIZATION_BACKEND` | `nemo` (or `pyannote` with weights) |

Rough VRAM (one GPU):

| Piece | ~VRAM |
|-------|--------|
| Whisper large-v3 fp16 | ~3 GB |
| Diarize | ~1–2 GB (or CPU) |
| Qwen2.5-7B vLLM | ~14–16 GB |
| **Comfortable total** | **~24 GB** |

Keep diarize on **CPU** (`DIARIZATION_DEVICE=cpu`) and `VLLM_GPU_MEMORY_UTILIZATION≈0.45` so Whisper + vLLM can share one 4090.

### Start

```bash
cp runtime/.env.example runtime/.env
# Recommended no-HF prod:
#   DIARIZATION_BACKEND=nemo
#   INSTALL_NEMO=1
#   DIARIZATION_DEVICE=cpu
#   WHISPER_MODEL=large-v3
#   LLM_MODEL=Qwen/Qwen2.5-7B-Instruct

./runtime/scripts/start.sh          # Docker GPU profile (full)
# Windows: .\runtime\scripts\start.ps1

./runtime/scripts/healthcheck.sh
```

Repo-root `.env`:

```bash
LLM_ENDPOINT=http://127.0.0.1:8001/v1/chat/completions
LLM_API_KEY=local
LLM_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct

STT_ENDPOINT=http://127.0.0.1:8080/v1/audio/transcriptions
STT_API_KEY=local
STT_MODEL=large-v3

DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_TIMEOUT_S=120
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

```bash
python -m api
```

Stop models: `./runtime/scripts/stop.sh`

---

## Choosing models

### STT (`WHISPER_MODEL` in `runtime/.env`, `STT_MODEL` in root `.env`)

| Model | Quality | Speed / VRAM | Notes |
|-------|---------|--------------|--------|
| `large-v3` | **Best** (default) | Highest | Full accuracy; ~3 GB fp16 |
| `medium` | Good | Faster | Smaller GPU / CPU |
| `small` / `base` | OK for drafts | Fastest | Not recommended for prod Persian meetings |

Use the **same id** in root `STT_MODEL` as in `WHISPER_MODEL` when pointing at local STT.

Device:

```bash
WHISPER_DEVICE=cuda          # or cpu
WHISPER_COMPUTE_TYPE=float16 # cuda; use int8 on CPU
```

### LLM (`LLM_MODEL` in `runtime/.env`, `LLM_MODEL_NAME` in root `.env`)

| Model | Fits ~24 GB w/ Whisper? | Notes |
|-------|-------------------------|--------|
| `Qwen/Qwen2.5-7B-Instruct` | **Yes** (default) | Polish / insights / minutes |
| Other ~7B instruct models | Usually | Must be vLLM-compatible |
| 14B+ without quant | Tight / no | Needs more VRAM or lower Whisper / quant |

vLLM knobs (`runtime/.env`):

```bash
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
VLLM_GPU_MEMORY_UTILIZATION=0.45   # leave headroom for Whisper on same GPU
VLLM_MAX_MODEL_LEN=8192
VLLM_MAX_NUM_SEQS=8
LLM_GPU=0
```

Root `.env` must use the **same model name** the vLLM server loaded:

```bash
LLM_MODEL_NAME=Qwen/Qwen2.5-7B-Instruct
```

### Diarization

| Backend | When to choose | Requirements |
|---------|----------------|--------------|
| **`nemo`** | Default for prod without HF | Docker `INSTALL_NEMO=1`; [NeMo speaker diarization](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/asr/speaker_diarization/intro.html) |
| **`pyannote`** | Prefer pyannote 3.1 quality | Offline weights via `download_models.sh` **or** gated [`speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1) token |
| **`auto`** | Convenience in native sidecar | Tries offline pyannote → validated Hub → NeMo. **Docker still builds only one stack** via `INSTALL_NEMO`. |

```bash
DIARIZATION_BACKEND=nemo      # or pyannote | auto
DIARIZATION_DEVICE=cpu        # or cuda
DIARIZATION_MODEL=pyannote/speaker-diarization-3.1   # pyannote Hub id
INSTALL_NEMO=1                # Docker build arg / compose
```

Optional NeMo model overrides (sidecar env):

```bash
NEMO_VAD_MODEL=vad_multilingual_marblenet
NEMO_SPK_MODEL=titanet_large
NEMO_MAX_SPEAKERS=8
```

---

## Configuration reference

### Repo-root `.env` (API client)

| Variable | Example | Meaning |
|----------|---------|---------|
| `LLM_ENDPOINT` | `http://127.0.0.1:8001/v1/chat/completions` | Chat completions URL |
| `LLM_API_KEY` | `local` | Sent as Bearer (any non-empty for local) |
| `LLM_MODEL_NAME` | `Qwen/Qwen2.5-7B-Instruct` | Model id in requests |
| `STT_ENDPOINT` | `http://127.0.0.1:8080/v1/audio/transcriptions` | Transcriptions URL |
| `STT_API_KEY` | `local` | STT auth |
| `STT_MODEL` | `large-v3` | Whisper model name |
| `DIARIZATION_ENDPOINT` | `http://127.0.0.1:8090` | Sidecar base URL |
| `DIARIZATION_TIMEOUT_S` | `120` | HTTP timeout (raise for long files / cold start) |
| `DIARIZATION_ALLOW_FALLBACK` | `0` | **Prod:** `0` = fail if sidecar down (no weak heuristic) |
| `DISTILL_ENABLE_PYANNOTE` | `0` | Keep `0` when using sidecar (no torch in API) |
| `HF_TOKEN` | — | Only for downloading gated pyannote weights |

### `runtime/.env` (model processes)

| Variable | Default | Meaning |
|----------|---------|---------|
| `LLM_MODEL` | `Qwen/Qwen2.5-7B-Instruct` | vLLM `--model` |
| `VLLM_PORT` | `8001` | Host port |
| `VLLM_GPU_MEMORY_UTILIZATION` | `0.45` | Fraction of GPU for vLLM |
| `VLLM_MAX_MODEL_LEN` | `8192` | Context length |
| `LLM_GPU` | `0` | `CUDA_VISIBLE_DEVICES` for LLM |
| `WHISPER_MODEL` | `large-v3` | faster-whisper model |
| `WHISPER_DEVICE` | `cuda` | `cuda` / `cpu` |
| `WHISPER_COMPUTE_TYPE` | `float16` | `float16` / `int8` / … |
| `STT_PORT` | `8080` | Host port |
| `AUDIO_GPU` | `0` | GPU for STT container |
| `DIARIZATION_BACKEND` | `auto` | `nemo` / `pyannote` / `auto` |
| `DIARIZATION_DEVICE` | `cpu` | `cpu` / `cuda` / `mps` |
| `DIARIZE_PORT` | `8090` | Host port |
| `INSTALL_NEMO` | `0` | `1` = build NeMo image (no pyannote in that image) |
| `HF_TOKEN` | — | Gated pyannote download only |

Copy examples:

```bash
cp .env.example .env                 # API
cp runtime/.env.example runtime/.env # models
```

---

## Scripts cheat sheet

| Script | Action |
|--------|--------|
| `runtime/scripts/start.sh` | Full Docker stack (`--audio`, `--native`, `--cpu`, `--no-wait`) |
| `runtime/scripts/start.ps1` | Windows helper |
| `runtime/scripts/run_diarize.sh` | Diarize only |
| `runtime/scripts/run_stt.sh` | STT only |
| `runtime/scripts/run_vllm.sh` | LLM only |
| `runtime/scripts/wait_ready.sh` | Block until `ready` |
| `runtime/scripts/healthcheck.sh` | Probe all three |
| `runtime/scripts/download_models.sh` | Offline pyannote `.bin` files |
| `runtime/scripts/test.sh` | Smoke STT + diarize with a WAV |
| `runtime/scripts/stop.sh` | Stop Docker + native PIDs |
| `runtime/scripts/logs.sh` | Tail logs |

---

## Health checks

```bash
curl -s http://127.0.0.1:8090/health     # diarize: ready + backend
curl -s http://127.0.0.1:8080/health     # STT: ready
curl -s http://127.0.0.1:8001/v1/models  # vLLM
curl -s http://127.0.0.1:8000/health     # API: diarization_endpoint / quality
```

Prod API health should show roughly:

- `diarization_endpoint`: `http://127.0.0.1:8090`
- `diarization_allow_fallback`: `false`
- `diarization_quality`: `high` when the sidecar is bound and ready

---

## Troubleshooting

| Symptom | Likely fix |
|---------|------------|
| Diarize `backend: error`, NeMo import fail | Rebuild with `INSTALL_NEMO=1`; use Docker on Windows |
| Diarize 503 / not ready | Wait; first call downloads NeMo/pyannote weights — raise `DIARIZATION_TIMEOUT_S` |
| Empty `intervals` | Silence / non-speech to VAD — try real meeting audio |
| API falls back / weak speakers | Set `DIARIZATION_ALLOW_FALLBACK=0` and fix sidecar |
| CUDA OOM with full stack | Lower `VLLM_GPU_MEMORY_UTILIZATION`, put diarize on CPU, or use smaller Whisper |
| vLLM won’t start | Needs NVIDIA GPU + Container Toolkit; CPU cannot run default vLLM path |
| Pyannote Hub 401/403 | Accept model terms; or use offline weights / switch to `nemo` |

---

## Suggested prod defaults (fully local, no HF)

**`runtime/.env`:**

```bash
LLM_MODEL=Qwen/Qwen2.5-7B-Instruct
VLLM_GPU_MEMORY_UTILIZATION=0.45
WHISPER_MODEL=large-v3
WHISPER_DEVICE=cuda
WHISPER_COMPUTE_TYPE=float16
DIARIZATION_BACKEND=nemo
DIARIZATION_DEVICE=cpu
INSTALL_NEMO=1
```

**Root `.env`:** point `LLM_*` / `STT_*` / `DIARIZATION_*` at `:8001` / `:8080` / `:8090` as in section 3, with `DIARIZATION_ALLOW_FALLBACK=0`.

Then:

```bash
./runtime/scripts/start.sh
python -m api
```
