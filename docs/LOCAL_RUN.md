# Run Distill locally

Two common setups:

| Mode | LLM | STT | Diarization |
|------|-----|-----|-------------|
| **A — Hybrid (quick test)** | your API keys in `.env` | your API keys in `.env` | **local NeMo** (or pyannote) sidecar |
| **B — Full local** | vLLM on `:8001` | Whisper on `:8080` | sidecar on `:8090` |

---

Native Windows NeMo via pip often fails (`triton` missing). Prefer Docker:

```powershell
$env:INSTALL_NEMO=1
$env:DIARIZATION_BACKEND="nemo"
docker compose -f diarize/docker-compose.yml up -d --build
curl http://127.0.0.1:8090/health
```

Then keep your existing cloud `LLM_*` / `STT_*` in `.env` and run `python -m api`.

## A) Hybrid: local NeMo diarization + cloud STT/LLM

Use this when you already have `LLM_*` / `STT_*` in the repo-root `.env` and only want to test speaker labels locally.

### 1. Root `.env` (keep your existing keys)

Leave `LLM_ENDPOINT`, `LLM_API_KEY`, `LLM_MODEL_NAME`, `STT_ENDPOINT`, `STT_API_KEY`, `STT_MODEL` as they are.

Ensure diarization points at the local sidecar:

```bash
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_TIMEOUT_S=120
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

### 2. Start **only** the NeMo diarization service

**Git Bash / WSL / Linux / macOS:**

```bash
# once: copy runtime env if missing
cp runtime/.env.example runtime/.env
# force NeMo
# in runtime/.env: DIARIZATION_BACKEND=nemo

DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh
```

**Windows PowerShell** (needs Git Bash on `PATH`):

```powershell
$env:DIARIZATION_BACKEND = "nemo"
bash ./runtime/scripts/run_diarize.sh
```

First start installs a venv under `runtime/.venv-diarize` and NeMo deps (can take a while). Wait until:

```bash
curl -s http://127.0.0.1:8090/health
# expect: "ready": true, "backend": "nemo"
```

Smoke test:

```bash
# optional: ./runtime/scripts/test.sh   (needs STT too — skip if hybrid)
curl -F "file=@some.wav" -F "min_speakers=1" -F "max_speakers=4" \
  http://127.0.0.1:8090/v1/diarize
```

### 3. Start the API (second terminal)

```bash
pip install -r requirements.txt
python -m api
```

Open `http://localhost:8000/assistant` — record or upload a meeting. STT/LLM hit your cloud keys; speakers come from local NeMo.

Stop diarize with `Ctrl+C` in that terminal (or `./runtime/scripts/stop.sh`).

---

## B) Full local stack (vLLM + Whisper + diarization)

No cloud STT/LLM required. Needs a decent NVIDIA GPU for the LLM (STT/diarize can be CPU).

### 1. Runtime env

```bash
cp runtime/.env.example runtime/.env
# edit: LLM_MODEL, WHISPER_MODEL, DIARIZATION_BACKEND, HF_TOKEN (optional)
```

Pyannote offline weights (optional, needs gated HF access once):

```bash
./runtime/scripts/download_models.sh
```

No HF token? use NeMo:

```bash
# in runtime/.env
DIARIZATION_BACKEND=nemo
INSTALL_NEMO=1   # for Docker builds
```

### 2. Start models

```bash
./runtime/scripts/start.sh              # Docker: LLM + STT + diarize
# or
./runtime/scripts/start.sh --native     # three local processes
# or audio only (no vLLM):
./runtime/scripts/start.sh --audio
```

Windows: `.\runtime\scripts\start.ps1`

Wait until ready:

```bash
./runtime/scripts/wait_ready.sh
./runtime/scripts/healthcheck.sh
```

### 3. Point API at local endpoints

Repo-root `.env`:

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

### 4. API

```bash
pip install -r requirements.txt
python -m api
```

UI: `http://localhost:8000/` · Assistant: `http://localhost:8000/assistant`

Stop models: `./runtime/scripts/stop.sh`

---

## Diarization backends (sidecar)

| `DIARIZATION_BACKEND` | Behavior |
|-----------------------|----------|
| `auto` | Offline pyannote → Hub (only if `HF_TOKEN` validates) → NeMo |
| `pyannote` | Offline or validated Hub |
| `nemo` | [NVIDIA NeMo ClusteringDiarizer](https://docs.nvidia.com/nemo-framework/user-guide/24.09/nemotoolkit/asr/speaker_diarization/intro.html) |

Docs: `runtime/README.md`, `diarize/README.md`.

---

## Quick checklist

1. Diarize health: `curl http://127.0.0.1:8090/health` → `ready: true`
2. API health: `curl http://127.0.0.1:8000/health` → `diarization_endpoint` set, `diarization_allow_fallback: false`
3. Create a meeting in the UI, upload or record, stop, check speaker labels
