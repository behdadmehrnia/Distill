# Distill diarization sidecar

Runs `pyannote/speaker-diarization-3.1` as a separate HTTP service so the light
API (`python -m api`) does not load torch.

## Offline weights (recommended when Hub downloads fail)

Download **two** files on any machine/browser that can reach Hugging Face
(after accepting gated terms), rename them exactly, and put them in
`diarize/models/`:

| Download | Save as |
|----------|---------|
| [segmentation-3.0 `pytorch_model.bin`](https://huggingface.co/pyannote/segmentation-3.0/resolve/main/pytorch_model.bin) | `pyannote_model_segmentation-3.0.bin` |
| [wespeaker embedding `pytorch_model.bin`](https://huggingface.co/pyannote/wespeaker-voxceleb-resnet34-LM/resolve/main/pytorch_model.bin) | `pyannote_model_wespeaker-voxceleb-resnet34-LM.bin` |

Also accept [speaker-diarization-3.1](https://huggingface.co/pyannote/speaker-diarization-3.1) terms (needed to authorize the downloads).

Then:

```bash
./diarize/run_local.sh
curl -s http://127.0.0.1:8090/health
# want: "ready": true, "local": true
```

No Hub access needed at runtime once the `.bin` files are present.

## Hub download (optional)

1. `HF_TOKEN` in `.env`
2. Accept gated terms for the models above
3. Prefer **no** `HF_ENDPOINT` (hf-mirror often 308-redirects and breaks downloads)
4. `./diarize/run_local.sh` or `cd diarize && docker compose up -d --build`

## Point the API at it

```bash
# .env
DIARIZATION_ENDPOINT=http://127.0.0.1:8090
DISTILL_ENABLE_PYANNOTE=0
```

```bash
python -m api
```
