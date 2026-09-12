<div align="center">

# Distill

**Long conversations in. Decisions out.**

Distill records a multi-person meeting from a single shared microphone, transcribes it live or from a file, works out who said what — including when people talk over each other — and turns the result into a summary, the key points, and the decisions that were actually reached.

[![CI](https://github.com/behdadmehrnia/Distill/actions/workflows/ci.yml/badge.svg)](https://github.com/behdadmehrnia/Distill/actions/workflows/ci.yml)
[![License: MIT](https://img.shields.io/badge/License-MIT-black.svg)](LICENSE)
[![Python 3.10+](https://img.shields.io/badge/Python-3.10%2B-black.svg)](https://www.python.org/)
[![Self-hosted](https://img.shields.io/badge/Runs-fully%20offline-black.svg)](docs/MODELS.md)

</div>

![The Distill recorder, showing a speaker-labelled live transcript](docs/images/02-recorder.png)

---

## What it does

- **Live transcription** — one shared microphone turns a whole room into searchable text as the conversation happens.
- **Speaker separation** — diarization splits the audio by voice and builds a readable timeline with a label on every turn.
- **Overlap detection** — interruptions and cross-talk are caught and marked inline instead of collapsing into one garbled line.
- **Summaries that hold up** — an LLM pass distils the transcript into a summary, the key points, and the decisions reached.
- **Actions and owners** — structured output you can follow up on: who agreed to what, and what happens next.
- **Works on existing audio** — upload a recording you already have; long files are chunked and processed the same way as a live session.
- **Runs entirely on your own hardware** — local Whisper, local diarization, and a local LLM, with no audio leaving the machine. Cloud API keys are optional, not required.

## Screenshots

### Wrapping up a meeting

When a recording stops, Distill walks through processing, speaker naming, transcript review, and the minutes — each step editable before anything is committed.

![The wrap-up wizard with the minutes step open](docs/images/06-wrapup.png)

### Meeting minutes

The finished minutes render as a printable sheet with attendees, a summary, and a numbered decision table with owners and due dates.

![A rendered meeting minutes sheet](docs/images/05-minutes.png)

### Tuning the pipeline

Chunk length, overlap, worker count, retry policy, the speech-energy gate, and the Whisper review agent are all adjustable from the UI. Settings marked *live* apply immediately; the rest take effect from the next recording.

![The sensitivity panel](docs/images/03-tuning.png)

### Your meetings

![The meetings dashboard](docs/images/04-dashboard.png)

### Landing page

![The Distill landing page](docs/images/01-landing.png)

## How it works

```
microphone / uploaded file
        │
        ▼
   audio chunker ──────────► speech-energy gate (skips silence)
        │
        ▼
   STT (Whisper)  ────────► review agent: drops hallucination loops,
        │                   then polishes the accepted text
        ▼
   diarization sidecar ───► speaker turns + overlap regions
        │
        ▼
   aligner ───────────────► speaker-labelled transcript
        │
        ▼
   LLM ───────────────────► summary · key points · decisions · minutes
```

Long recordings are windowed rather than sent whole, so a two-hour file goes through the same path as a live session without exhausting GPU memory.

## Quick start

The fastest path uses cloud STT/LLM endpoints and no local models:

```bash
git clone https://github.com/behdadmehrnia/Distill.git
cd Distill
cp .env.example .env
```

Set `LLM_*` and `STT_*` in `.env` (any OpenAI-compatible endpoint works), start Postgres, then run:

```bash
docker compose up -d postgres
pip install -r requirements.txt
python -m api
```

Open `http://localhost:8000`. For fully local models — Whisper, diarization, and vLLM on your own GPU — see [Running](#running) below.

| URL | Page |
|-----|------|
| `http://localhost:8000/` | Landing |
| `http://localhost:8000/assistant` | Recorder |
| `http://localhost:8000/assistant/{meeting_id}` | Reopen a past meeting |
| `http://localhost:8000/dashboard` | Your meetings |
| `http://localhost:8000/docs` | Swagger API docs |

## Project layout

```
api/                 # application + UI
  web/               # landing, login, dashboard, assistant (HTML/JS)
  routes/
  auth/
  meeting/
  providers/
diarize/             # diarization sidecar (pyannote / NeMo)
runtime/             # local model stack: vLLM + Whisper + diarize
extension/           # browser extension meeting client
docs/                # MODELS, LOCAL_RUN, AUTH, CLIENT
tests/
data/
.env
```

## Running

**Model stack (diarize-only → full LLM/STT/diarize, model choice, env vars):**  
[`docs/MODELS.md`](docs/MODELS.md)

**Auth + PostgreSQL:** [`docs/AUTH.md`](docs/AUTH.md)

**HTTP / WebSocket client API (meetings, multi-stream, audio protocol):** [`docs/CLIENT.md`](docs/CLIENT.md)

**Quick local / hybrid:** [`docs/LOCAL_RUN.md`](docs/LOCAL_RUN.md) · [`runtime/README.md`](runtime/README.md)

### A) Hybrid — local NeMo diarization + your STT/LLM API keys

Keep existing `LLM_*` / `STT_*` in `.env`. Point diarization at the sidecar:

```bash
# .env
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

Terminal 1 — **only** NeMo (Docker recommended on Windows):

```bash
INSTALL_NEMO=1 DIARIZATION_BACKEND=nemo \
  docker compose -f diarize/docker-compose.yml up -d --build
# or: DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh
curl -s http://127.0.0.1:8090/health   # ready:true, backend:nemo
```

Terminal 2 — API:

```bash
pip install -r requirements.txt
python -m api
```

### B) Full local models (vLLM + Whisper + diarization)

```bash
cp runtime/.env.example runtime/.env
# Recommended: DIARIZATION_BACKEND=nemo, INSTALL_NEMO=1
./runtime/scripts/start.sh
# Configure root .env — see docs/MODELS.md §3
pip install -r requirements.txt
python -m api
```

Defaults: **STT** `large-v3`, **LLM** `Qwen/Qwen2.5-7B-Instruct`, **diarize** NeMo or pyannote.  
An **RTX 4090 (24 GB)** is adequate. Full options: [`docs/MODELS.md`](docs/MODELS.md).

### API only (cloud STT/LLM, no local models)

```bash
cp .env.example .env
# set LLM_* and STT_* keys; optional DIARIZATION_ENDPOINT
pip install -r requirements.txt
python -m api
```

The main STT path no longer needs **ffmpeg / pydub**; audio is sent as WAV directly to an OpenAI-compatible endpoint.

## Whisper quality (Review Agent)

After each STT chunk, a **fast quality gate** removes well-known Whisper hallucinations (for example a "very very very…" repetition loop).  
If the endpoint supports `verbose_json`, word/segment timings are captured too, so text can be split on speaker boundaries.  
At the end of a meeting or upload, in the default `finalize` mode, the same LLM polishes the accepted text.

From the tuning panel (click the status chip) or `/tuning`:

| Key | Default | Meaning |
|------|---------|------|
| `window_ms` | `10000` | STT window length (next meeting) |
| `hop_ms` | `8500` | Window hop, ≈1.5s overlap (next meeting) |
| `stt_workers` | `1` | Parallel STT workers — 1 keeps ordering stable |
| `stt_retry_count` | `3` | Retries with backoff |
| `stt_review_mode` | `finalize` | `off` / `heuristic` / `finalize` / `live` |
| `stt_min_quality` | `0.35` | Minimum score to accept raw text |
| `stt_language` | `en` | STT language code |

## Diarization

Production path: **local sidecar** via `runtime/` (same pattern as [MA-runtime](https://github.com/behdadmehrnia/MA-runtime)).

```bash
./runtime/scripts/download_models.sh
./runtime/scripts/start.sh
# API .env:
#   DIARIZATION_ENDPOINT=http://127.0.0.1:8090
#   DIARIZATION_ALLOW_FALLBACK=0
```

- **PyAnnote** [`speaker-diarization-3.1`](https://huggingface.co/pyannote/speaker-diarization-3.1) — offline weights preferred; Hub only if `HF_TOKEN` has gated access
- **NVIDIA NeMo** — set `DIARIZATION_BACKEND=nemo` when pyannote weights/token are unavailable
- **Fallback** heuristic is disabled in production (`DIARIZATION_ALLOW_FALLBACK=0`)
- Each meeting gets a forked diarizer so speaker state does not bleed across sessions

See `runtime/README.md` and `diarize/README.md`.

> Model weights are **not** distributed with this repository. pyannote models are gated on Hugging Face — accept their terms and supply your own `HF_TOKEN` to download them.

## Docker

The whole API (landing, assistant, REST, WebSocket, `/docs`) runs in one container. The image is **CPU-only** and includes torch/pyannote. **Users and meetings** are stored in the **PostgreSQL** service (details: [`docs/AUTH.md`](docs/AUTH.md)). File data is mapped onto a volume at `/app/data`:

- `uploads/` — uploaded files
- `audio/` — live recording audio
- `stt_cache/` — transcription cache
- `hf_cache/` / `torch_cache/` — diarization model caches (so they are not re-downloaded after a rebuild)

Put `HF_TOKEN` in `.env` and accept the gated pyannote model terms on Hugging Face. Set `JWT_SECRET` and `DATABASE_URL` for production.

```bash
cp .env.example .env
docker compose up -d --build
```

The service is available at `http://localhost:8000`.

**Kubernetes:** readiness/liveness should be `GET /health` on port `8000`. The pyannote model is deliberately not loaded at boot (loading torch on low-memory pods caused OOM and `connection reset` / CrashLoop). Set `DISTILL_ENABLE_PYANNOTE=0` for small pods; pyannote quality needs roughly ≥2Gi RAM and an `HF_TOKEN`. Keep `MEETING_PORT`/`PORT` at `8000`.

Docker only (without compose):

```bash
docker build -t distill .
docker run --rm -p 8000:8000 --env-file .env \
  -v distill-data:/app/data \
  distill
```

For local runs without Docker the base dependency set is light; for quality diarization:

```bash
pip install torch torchaudio --index-url https://download.pytorch.org/whl/cpu
pip install -r requirements.optional.txt
# then put HF_TOKEN in .env
```

## Environment variables

Persistent file paths are fixed in code and are not read from the environment:

- `data/uploads/`
- `data/audio/`
- `data/stt_cache/`
- `data/hf_cache/` (in Docker)
- `api/web/`

Users and meetings live in Postgres (`DATABASE_URL`) — [`docs/AUTH.md`](docs/AUTH.md).

In Docker these file paths sit under `/app/...`; a volume on `/app/data` is enough. The `postgres` service has its own volume.

| Variable | Default | Description |
|--------|---------|--------|
| `DATABASE_URL` | `postgresql://distill:distill@127.0.0.1:5432/distill` | Postgres (users + meetings) |
| `JWT_SECRET` | placeholder | JWT signing key — change it in production |
| `JWT_EXPIRE_MINUTES` | `10080` | Token/cookie lifetime (minutes) |
| `AUTH_COOKIE_SECURE` | `0` | `1` behind HTTPS |
| `LLM_ENDPOINT` | — | Chat completions URL |
| `LLM_API_KEY` | — | LLM key |
| `LLM_MODEL_NAME` | — | Model name |
| `AUDIO_SAMPLE_RATE` | `16000` | Sample rate |
| `AUDIO_CHANNELS` | `1` | Channel count (mono) |
| `MEETING_HOST` | `0.0.0.0` | Server bind address |
| `MEETING_PORT` | `8000` | Port |
| `MEETING_WINDOW_MS` | `15000` | STT window length |
| `MEETING_HOP_MS` | `15000` | Window hop |
| `MEETING_DIARIZE_EVERY_MS` | `0` | Live diarization interval (`0` disables) |
| `STT_ENDPOINT` | — | STT URL |
| `STT_API_KEY` | — | STT key |
| `STT_MODEL` | — | STT model |
| `DIARIZATION_ENDPOINT` | — | Local diarize service URL (e.g. `http://127.0.0.1:8090`) |
| `DIARIZATION_TIMEOUT_S` | `120` | Sidecar request timeout |
| `DIARIZATION_ALLOW_FALLBACK` | `1` without endpoint / `0` with endpoint | Allow the weak heuristic when the quality backend fails |
| `DISTILL_ENABLE_PYANNOTE` | `auto` | `0` disables pyannote (use on low-memory pods) |
| `HF_TOKEN` | — | Only for the one-time download of gated pyannote weights |

## Tests

Postgres must be reachable (e.g. `docker compose up -d postgres`). Details: [`docs/AUTH.md`](docs/AUTH.md).

```bash
pip install -r requirements.txt
pytest
```

Covers unit tests for the chunker, the pipeline with a mock STT, and a light WER/DER harness in `api/meeting/eval_metrics.py`.

## API

| Method | Path | Description |
|-----|------|--------|
| `GET` | `/` | Distill landing page |
| `GET` | `/assistant` | Assistant UI |
| `GET` | `/assistant/{meeting_id}` | Assistant UI with that meeting's history |
| `GET` | `/docs` | Swagger documentation |
| `GET` | `/health` | Health + diarization backend |
| `GET` / `PUT` | `/tuning` | Read / apply live settings |
| `POST` | `/meetings` | Create a meeting |
| `WS` | `/meetings/{id}/audio` | Audio stream + events |
| `POST` | `/meetings/{id}/upload` | Upload a file |
| `GET` | `/meetings/{id}/transcript` | Transcript timeline |
| `GET` | `/meetings/{id}/debug` | STT / diarization counters |
| `PATCH` | `/meetings/{id}/speakers` | Name the speakers |
| `POST` | `/meetings/{id}/insights` | Generate analysis |

Full client protocol, including the WebSocket audio format and multi-stream capture: [`docs/CLIENT.md`](docs/CLIENT.md).

---

## License

Licensed under the **MIT License**. See [LICENSE](LICENSE).

## Author

Built with ❤️ by **Behdad**
