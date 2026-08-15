# Distill local runtime

Production-oriented local inference for Distill — same idea as
[MA-runtime](https://github.com/BMDarkLight/MA-runtime):

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

## Hybrid: NeMo only + cloud STT/LLM

If repo-root `.env` already has `LLM_*` / `STT_*` API keys:

```bash
# runtime/.env
DIARIZATION_BACKEND=nemo

DIARIZATION_BACKEND=nemo ./scripts/run_diarize.sh
# other terminal: python -m api   (with DIARIZATION_ENDPOINT=http://127.0.0.1:8090)
```

See [`docs/LOCAL_RUN.md`](../docs/LOCAL_RUN.md).

## Quick start (full local stack)

```bash
cd runtime
cp .env.example .env
# Optional: HF_TOKEN with pyannote gated access (accept model terms first)
./scripts/download_models.sh   # offline pyannote weights (needs valid HF_TOKEN)
./scripts/start.sh             # Docker GPU full stack; waits until ready
./scripts/healthcheck.sh
```

Windows (PowerShell / Git Bash):

```powershell
.\runtime\scripts\start.ps1
# or: bash ./runtime/scripts/start.sh
```

Native (no Docker compose) — three processes:

```bash
./scripts/start.sh --native
```

Audio only (STT + diarize):

```bash
./scripts/start.sh --audio
```

No HF token? Use NeMo:

```bash
# native
DIARIZATION_BACKEND=nemo ./scripts/run_diarize.sh

# docker
INSTALL_NEMO=1 DIARIZATION_BACKEND=nemo ./scripts/start.sh --audio
```

## Point Distill at the stack

In the **repo root** `.env`:

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

Then:

```bash
python -m api
```

## Diarization backends

`DIARIZATION_BACKEND` (sidecar):

| Value | Behavior |
|-------|----------|
| `auto` (default) | Offline pyannote → Hub (**only if token validates**) → NeMo |
| `pyannote` | Offline or validated Hub only |
| `nemo` | [NVIDIA NeMo ClusteringDiarizer](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/asr/speaker_diarization/intro.html) |

Models:

- PyAnnote: [`pyannote/speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1)
- NeMo: meeting-domain VAD + TitaNet clustering (no gated HF)

`DIARIZATION_ALLOW_FALLBACK=0` on the API prevents silent weak mono-mic heuristics
when the sidecar fails — required for production accuracy.

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
