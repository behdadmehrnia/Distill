#!/usr/bin/env bash
# Smoke-test STT + diarization endpoints with a short synthetic WAV.
set -euo pipefail

STT_PORT="${STT_PORT:-8080}"
DIARIZE_PORT="${DIARIZE_PORT:-8090}"
TMP="$(mktemp -d)"
trap 'rm -rf "$TMP"' EXIT

python3 - <<'PY' "$TMP/tone.wav"
import sys, wave, math, struct
path = sys.argv[1]
sr = 16000
dur = 2.0
with wave.open(path, "w") as w:
    w.setnchannels(1)
    w.setsampwidth(2)
    w.setframerate(sr)
    for i in range(int(sr * dur)):
        # two tones to give the diarizer something non-silent
        t = i / sr
        v = 0.2 * math.sin(2 * math.pi * (220 if t < 1 else 440) * t)
        w.writeframes(struct.pack("<h", int(max(-1, min(1, v)) * 32767)))
print(path)
PY

echo "==> STT"
curl -fsS -F "file=@${TMP}/tone.wav" -F "language=fa" -F "response_format=json" \
  "http://127.0.0.1:${STT_PORT}/v1/audio/transcriptions" | head -c 500
echo ""

echo "==> Diarize"
curl -fsS -F "file=@${TMP}/tone.wav" -F "min_speakers=1" -F "max_speakers=2" \
  "http://127.0.0.1:${DIARIZE_PORT}/v1/diarize" | head -c 800
echo ""
echo "OK"
