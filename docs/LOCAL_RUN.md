# Run Distill locally

**Model stack (profiles, choosing models, all env vars):**  
→ **[`MODELS.md`](MODELS.md)**

**Auth + PostgreSQL:**  
→ **[`AUTH.md`](AUTH.md)** — start Postgres before `python -m api` / `pytest`.

This page is a short entry point. Prefer `MODELS.md` for model configuration and `AUTH.md` for login/database.

---

## Profiles at a glance

| Profile | Models | How |
|---------|--------|-----|
| Diarization only | `:8090` | [`MODELS.md` §1](MODELS.md#1-diarization-only) |
| STT + diarize | `:8080` + `:8090` | [`MODELS.md` §2](MODELS.md#2-stt--diarization-no-local-llm) |
| Full local | `:8001` + `:8080` + `:8090` | [`MODELS.md` §3](MODELS.md#3-full-local-llm--stt--diarization) |
| Hybrid | Cloud STT/LLM + local diarize | Below |
| Cloud only | Remote APIs | Root `.env` only |

---

## Hybrid (local diarize + cloud STT/LLM)

Keep existing `LLM_*` / `STT_*` in repo-root `.env`. Add:

```bash
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DIARIZATION_ALLOW_FALLBACK=0
DISTILL_ENABLE_PYANNOTE=0
```

**Docker NeMo (recommended on Windows):**

```powershell
$env:INSTALL_NEMO="1"
$env:DIARIZATION_BACKEND="nemo"
docker compose -f diarize/docker-compose.yml up -d --build
curl http://127.0.0.1:8090/health
```

**Linux / WSL script:**

```bash
DIARIZATION_BACKEND=nemo ./runtime/scripts/run_diarize.sh
```

Then:

```bash
python -m api
# http://localhost:8000/assistant
```

---

## Full local (quick)

```bash
cp runtime/.env.example runtime/.env
# Set: DIARIZATION_BACKEND=nemo, INSTALL_NEMO=1

./runtime/scripts/start.sh
# Point root .env at :8001 / :8080 / :8090 — see MODELS.md §3

python -m api
```

**GPU:** RTX 4090 (24 GB) is enough for defaults — Whisper `large-v3` + Qwen2.5-7B + diarize (CPU). Details in [`MODELS.md`](MODELS.md#3-full-local-llm--stt--diarization).

---

## Checklist

1. `curl -s http://127.0.0.1:8090/health` → `ready: true`
2. (If used) STT / vLLM health — see [`MODELS.md`](MODELS.md#health-checks)
3. `curl -s http://127.0.0.1:8000/health` → `diarization_allow_fallback: false`
4. UI: record or upload a meeting, check speaker labels

Stop models: `./runtime/scripts/stop.sh`
