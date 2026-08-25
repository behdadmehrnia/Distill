# Distill client guide

How to build an external client (bot, browser extension, desktop app, or meeting-provider adapter) against the Distill HTTP + WebSocket API.

**Production example:** `https://api.distill.app`  
**Local default:** `http://127.0.0.1:8000`

Related docs: [AUTH.md](AUTH.md) · [LOCAL_RUN.md](LOCAL_RUN.md) · [MODELS.md](MODELS.md)  
Reference implementation: [`extension/`](../extension/) (Google Meet capture)

---

## 1. Concepts

| Concept | Meaning |
|---------|---------|
| **Meeting** | One capture session owned by a user. Has status, audio, transcript, optional insights/minutes. |
| **Capture mode** | `mono` (single mixed mic/file + **diarization**) or `multi_stream` (per-participant audio, **no diarization**). |
| **Speaker id** | Stable id for a talker. In mono mode usually `SPEAKER_00`, `SPEAKER_01`, … from diarization. In multi-stream mode you supply ids (e.g. `alice`); the API normalizes them to `SPEAKER_<id>` when needed. |
| **Speaker map** | `Dict[speaker_id → display name]` (e.g. `{"SPEAKER_alice": "Alice"}`). |
| **Stream** | One participant’s mono PCM feed in `multi_stream` mode. |
| **Segment** | Timed transcript row: speaker, start/end ms, text, provisional flag. |

### Meeting status

| Status | Meaning |
|--------|---------|
| `created` | Meeting exists; not recording |
| `recording` | Live capture in progress |
| `processing` | Finalizing (flush STT / diarize or multi-stream review) |
| `stopped` | Done; transcript available |
| `failed` | Terminal failure (rare) |

### Which mode to use

```text
One mixed microphone / uploaded recording  →  capture_mode: "mono"
Per-participant tracks (Meet, Zoom bot…) →  capture_mode: "multi_stream"
```

In **multi_stream**, speaker identity comes from your stream tags. Diarization is skipped (`speaker_update.backend` is `"stream"`).

---

## 2. Authentication

All meeting endpoints require a logged-in user. Auth is JWT.

### Login

```http
POST /auth/login
Content-Type: application/json

{"email": "user@example.com", "password": "secret"}
```

**Response `200`:**

```json
{
  "user": {
    "id": "…",
    "email": "user@example.com",
    "display_name": "…",
    "role": "user",
    "is_active": true
  },
  "access_token": "<jwt>"
}
```

Also sets HttpOnly cookie `access_token` (used by the web UI).

> **Note:** Older production builds may omit `access_token` in the JSON body and only set the cookie. Prefer reading `access_token` from the body when present; otherwise read the `access_token` cookie (browser extension) or redeploy an API that returns the token in JSON.

### Register

```http
POST /auth/register
Content-Type: application/json

{
  "email": "user@example.com",
  "password": "secret",
  "display_name": "Optional name"
}
```

Same response shape as login (`201`), including `access_token`.

### Sending the token

| Transport | How |
|-----------|-----|
| REST | `Authorization: Bearer <jwt>` **or** cookie `access_token` |
| WebSocket | Query string `?token=<jwt>` **or** cookie `access_token` |

Clients that are not same-origin (extensions, bots, CLIs) should always use **Bearer** / **`?token=`**.

### Current user

```http
GET /auth/me
Authorization: Bearer <jwt>
```

```json
{"user": { … }}
```

### Logout

```http
POST /auth/logout
```

Clears the cookie. Bearer clients can simply discard the JWT.

### Health (no auth)

```http
GET /health
```

---

## 3. End-to-end flows

### A) Multi-stream live capture (recommended for Meet / Zoom adapters)

```text
1. POST /auth/login                         → access_token
2. POST /meetings  { capture_mode: multi_stream, streams?: [...], start: true }
3. Optional: POST /meetings/{id}/streams    → register late joiners
4. WSS  /meetings/{id}/audio?token=…        → send tagged PCM
5. Receive JSON events (transcript, status, …) on the same socket
6. JSON {"type":"stop"}  and/or  POST /meetings/{id}/stop
7. GET  /meetings/{id}/transcript
8. Optional: POST /meetings/{id}/insights , /minutes/generate
```

### B) Mono live capture (browser mic)

```text
1. Login
2. POST /meetings { capture_mode: "mono", start: true }   # default mode
3. WSS /meetings/{id}/audio?token=…
4. Send raw int16 PCM binary frames (no speaker_id)
5. stop → diarization runs → name speakers via PATCH /speakers
```

### C) Offline upload (mono)

```text
1. Login
2. POST /meetings { start: false }
3. POST /meetings/{id}/upload  (multipart file)
4. Poll GET /meetings/{id} until status=stopped
5. GET /transcript , PATCH /speakers , insights/minutes
```

---

## 4. Creating a meeting

```http
POST /meetings
Authorization: Bearer <jwt>
Content-Type: application/json
```

### Body

| Field | Type | Default | Description |
|-------|------|---------|-------------|
| `title` | string | `"جلسه جدید"` | Meeting title |
| `participants` | string[] | `[]` | Optional attendee names (metadata for minutes) |
| `start` | bool | `true` | If true, begin recording immediately (`status: recording`) |
| `capture_mode` | `"mono"` \| `"multi_stream"` | `"mono"` | Capture pipeline |
| `streams` | object[] | `[]` | Pre-register participants (multi_stream only) |
| `stream_map` | object | — | Alternate map `{ "alice": "Alice", … }` |

**`streams` item:**

```json
{"speaker_id": "alice", "name": "Alice"}
```

Aliases accepted: `id` for `speaker_id`, `label` for `name`.

### Example — multi-stream with known participants

```json
{
  "title": "Weekly sync",
  "capture_mode": "multi_stream",
  "start": true,
  "streams": [
    {"speaker_id": "alice", "name": "Alice"},
    {"speaker_id": "bob", "name": "Bob"}
  ]
}
```

### Example — create without starting

```json
{
  "title": "Upload later",
  "capture_mode": "mono",
  "start": false
}
```

Then call `POST /meetings/{id}/start` or `POST /meetings/{id}/upload`.

### Response `201` (meeting payload)

```json
{
  "id": "f12fe35b-bac6-4cea-ad6f-094ab0f6d73e",
  "title": "Weekly sync",
  "status": "recording",
  "sample_rate": 16000,
  "created_at": 1787655142.0,
  "started_at": 1787655142.1,
  "stopped_at": null,
  "audio_path": "…",
  "participants": [],
  "speaker_map": {
    "SPEAKER_alice": "Alice",
    "SPEAKER_bob": "Bob"
  },
  "user_id": "…",
  "capture_mode": "multi_stream",
  "stream_paths": {},
  "has_recording": false
}
```

Speaker ids in `speaker_map` are **normalized** (e.g. `alice` → `SPEAKER_alice`). Prefer using the returned keys when tagging audio.

---

## 5. Registering streams at runtime

Use when participants join after the meeting was created.

```http
POST /meetings/{meeting_id}/streams
Authorization: Bearer <jwt>
Content-Type: application/json

{"speaker_id": "carol", "name": "Carol"}
```

**Response `201`:**

```json
{
  "meeting_id": "…",
  "speaker_id": "SPEAKER_carol",
  "name": "Carol",
  "stream_ids": ["SPEAKER_alice", "SPEAKER_bob", "SPEAKER_carol"]
}
```

Errors:

| Status | When |
|--------|------|
| `400` | Meeting is not `multi_stream`, or `speaker_id` missing |
| `404` | Meeting / live session not found |

You can also register over the WebSocket (see §7) without a separate REST call.

---

## 6. Starting / stopping / cancelling

### Start (or re-record)

```http
POST /meetings/{meeting_id}/start
Authorization: Bearer <jwt>
Content-Type: application/json

{"reset": false, "title": "optional new title"}
```

- `reset: true` wipes prior audio/transcript before starting again.
- If a recording file already exists and `reset` is false → `409`.

### Stop (finalize)

```http
POST /meetings/{meeting_id}/stop
Authorization: Bearer <jwt>
```

Runs the final pipeline (flush STT; diarize for mono; review). Returns the meeting payload with `status: stopped` (or `processing` briefly depending on timing).

You may also finalize via WebSocket `{"type":"stop"}`. Calling both is safe if the session is already stopped.

### Cancel (discard)

```http
POST /meetings/{meeting_id}/cancel
Authorization: Bearer <jwt>
```

Aborts in-flight work and clears captured artifacts back toward `created`. Prefer **stop** when you want to keep the transcript.

### Delete

```http
DELETE /meetings/{meeting_id}
Authorization: Bearer <jwt>
```

`204` — meeting and derived data removed.

---

## 7. Audio WebSocket protocol

```text
wss://<host>/meetings/{meeting_id}/audio?token=<jwt>
```

Use `ws://` for local HTTP. Sample rate is **16_000 Hz**, mono, **little-endian int16** PCM.

Open the socket only after the meeting is `recording` (create with `start: true`, or call `/start`).

### 7.1 Client → server (JSON text frames)

| `type` | Fields | Purpose |
|--------|--------|---------|
| `register_stream` | `speaker_id`, optional `name` / `label` | Register a multi-stream participant |
| `audio` | `data`: number[] of int16; optional `speaker_id` | PCM as JSON (heavy; prefer binary) |
| `ping` | — | Keepalive; server replies `pong` |
| `stop` | — | Finalize meeting (same as REST stop) |

**Register:**

```json
{"type": "register_stream", "speaker_id": "alice", "name": "Alice"}
```

**JSON audio (mono or multi_stream):**

```json
{"type": "audio", "speaker_id": "alice", "data": [12, -40, 100, …]}
```

For `multi_stream`, `speaker_id` is **required** on JSON audio messages.

### 7.2 Client → server (binary frames)

#### Mono mode

Raw int16 LE samples only (no header):

```text
[int16_0][int16_1]…[int16_n]
```

#### Multi-stream mode

Tagged frame:

```text
[u8 name_len][utf8 speaker_id][int16le pcm…]
```

- `name_len`: 1–64  
- `speaker_id`: UTF-8 (e.g. `alice` or `SPEAKER_alice`)  
- Remainder: little-endian int16 PCM at 16 kHz mono  

**Python encode:**

```python
def encode_multistream_frame(speaker_id: str, pcm_int16: bytes) -> bytes:
    name = speaker_id.encode("utf-8")
    assert 1 <= len(name) <= 64
    return bytes([len(name)]) + name + pcm_int16
```

**JavaScript encode:**

```js
function encodeFrame(speakerId, int16Array) {
  const nameBytes = new TextEncoder().encode(speakerId);
  const out = new Uint8Array(1 + nameBytes.length + int16Array.byteLength);
  out[0] = nameBytes.length;
  out.set(nameBytes, 1);
  out.set(new Uint8Array(int16Array.buffer, int16Array.byteOffset, int16Array.byteLength), 1 + nameBytes.length);
  return out.buffer;
}
```

Chunk size tip: ~20–250 ms of audio per frame (e.g. 320–4096 samples) is a good range. Send continuously while the participant is speaking; silence can be skipped client-side.

### 7.3 Server → client (JSON events)

All events are JSON objects. Many include `meeting_id`.

| `type` | When | Important fields |
|--------|------|------------------|
| `status` | Lifecycle | `status`, optional `phase`, `cancelled` |
| `transcript` | Live / final text | `segments[]` |
| `speaker_update` | Intervals updated | `backend`, `intervals[]`, `overlaps[]` |
| `speaker_map` | After PATCH /speakers | `speaker_map` |
| `stream_registered` | After WS register | `speaker_id`, `name` |
| `warning` | Soft failures | `code`, `message` |
| `error` | Hard errors | `message` |
| `pong` | Reply to `ping` | — |
| `insights` | After insights job | `insights` |
| `minutes` | After minutes job | `minutes` |
| `segment_update` | After segment edit | segment fields |

**Status values / phases (examples):**

- `recording` → live capture  
- `processing` with `phase`: `save_audio`, `flush_stt`, `diarize`, `review`, `enhance_audio`, …  
- `stopped` → done  

**`speaker_update.backend`:**

- `"pyannote"` / `"nemo"` / `"fallback"` — mono diarization  
- `"stream"` — multi_stream (no diarization)  

**Transcript segment shape:**

```json
{
  "id": "uuid",
  "meeting_id": "…",
  "speaker_id": "SPEAKER_alice",
  "start_ms": 1200,
  "end_ms": 4800,
  "text": "سلام دوستان",
  "is_overlap": false,
  "provisional": true,
  "overlap_speakers": [],
  "created_at": 1787655145.0
}
```

- `provisional: true` — live partial  
- `provisional: false` — finalized after stop/review  

### 7.4 Disconnect behavior

If the **last** WebSocket for a meeting disconnects while capture/processing is active, the server best-effort **auto-finalizes** (same idea as stop), so a crashed client does not leave the meeting stuck in `recording`. Prefer an explicit `stop` when possible.

Keepalive: send `{"type":"ping"}` every ~20s on long-lived connections (proxies / CDNs may idle-close otherwise).

---

## 8. Speakers, transcript, recording

### List speakers (for naming UI)

```http
GET /meetings/{meeting_id}/speakers
```

Returns speakers that have a playable sample clip:

```json
{
  "meeting_id": "…",
  "speakers": [
    {
      "id": "SPEAKER_00",
      "label": "سخنگوی ۱",
      "custom_label": null,
      "sample_start_ms": 1000,
      "sample_end_ms": 4000,
      "has_sample": true,
      "has_recording": true
    }
  ]
}
```

### Name speakers

```http
PATCH /meetings/{meeting_id}/speakers
Content-Type: application/json

{"SPEAKER_00": "علی", "SPEAKER_01": "سارا"}
```

or `{"speaker_map": {"SPEAKER_00": "علی"}}`.

Broadcasts a `speaker_map` event.

### Speaker sample audio

```http
GET /meetings/{meeting_id}/speakers/{speaker_id}/audio
```

Returns a short WAV clip (for naming previews). In multi_stream mode, prefers that speaker’s per-stream recording when present.

### Full transcript

```http
GET /meetings/{meeting_id}/transcript
```

```json
{"meeting_id": "…", "segments": [ … ]}
```

### Edit a segment

```http
PATCH /meetings/{meeting_id}/segments/{segment_id}
Content-Type: application/json

{"text": "corrected text"}
```

### Mixed recording download

```http
GET /meetings/{meeting_id}/recording
```

WAV (or stored path). Multi-stream meetings also keep per-speaker files under `stream_paths` on the meeting record.

### Meeting metadata

```http
GET /meetings/{meeting_id}
GET /meetings?limit=50&offset=0
```

### Debug counters

```http
GET /meetings/{meeting_id}/debug
```

STT/diarization stats for the live in-memory session.

---

## 9. Upload (mono offline)

```http
POST /meetings/{meeting_id}/upload
Authorization: Bearer <jwt>
Content-Type: multipart/form-data

file=@meeting.wav
```

Creates/uses a meeting (typically `start: false` at create). Server loads audio → optional denoise → diarize → windowed STT → align → review. Progress arrives as WebSocket `status` / `transcript` / `speaker_update` if a socket is connected; otherwise poll `GET /meetings/{id}` until `stopped`.

Supported formats depend on server deps (WAV always; others via pydub/ffmpeg when available).

---

## 10. Insights and minutes

### Insights

```http
POST /meetings/{meeting_id}/insights
GET  /meetings/{meeting_id}/insights
```

LLM summary / highlights / decisions / action items. Requires transcript content and configured `LLM_*` settings.

### Minutes

```http
POST /meetings/{meeting_id}/minutes/generate
GET  /meetings/{meeting_id}/minutes
PUT  /meetings/{meeting_id}/minutes
```

Generate or manually update formal minutes. `participants` on the meeting feed attendee hints.

---

## 11. Complete multi-stream client (Python)

```python
#!/usr/bin/env python3
"""Minimal Distill multi-stream client."""

from __future__ import annotations

import asyncio
import json
import struct
from typing import Optional

import httpx
import numpy as np
import websockets

API = "https://api.distill.app"
SAMPLE_RATE = 16000


def encode_frame(speaker_id: str, samples: np.ndarray) -> bytes:
    name = speaker_id.encode("utf-8")
    pcm = np.asarray(samples, dtype=np.int16).tobytes()
    return bytes([len(name)]) + name + pcm


async def main() -> None:
    async with httpx.AsyncClient(base_url=API, timeout=60.0) as http:
        login = (
            await http.post(
                "/auth/login",
                json={"email": "user@example.com", "password": "secret"},
            )
        ).raise_for_status().json()
        token = login["access_token"]
        headers = {"Authorization": f"Bearer {token}"}

        meeting = (
            await http.post(
                "/meetings",
                headers=headers,
                json={
                    "title": "Adapter demo",
                    "capture_mode": "multi_stream",
                    "start": True,
                    "streams": [
                        {"speaker_id": "alice", "name": "Alice"},
                        {"speaker_id": "bob", "name": "Bob"},
                    ],
                },
            )
        ).raise_for_status().json()
        meeting_id = meeting["id"]
        print("meeting", meeting_id, meeting["speaker_map"])

        # Optional late joiner
        await http.post(
            f"/meetings/{meeting_id}/streams",
            headers=headers,
            json={"speaker_id": "carol", "name": "Carol"},
        )

        ws_url = (
            API.replace("https://", "wss://").replace("http://", "ws://")
            + f"/meetings/{meeting_id}/audio?token={token}"
        )

        async with websockets.connect(ws_url, max_size=8_000_000) as ws:
            # Listen for server events in the background
            async def reader() -> None:
                async for raw in ws:
                    if isinstance(raw, bytes):
                        continue
                    ev = json.loads(raw)
                    print("←", ev.get("type"), ev.get("status") or "")
                    if ev.get("type") == "transcript":
                        for seg in ev.get("segments") or []:
                            print(
                                f"  [{seg['speaker_id']}] "
                                f"{seg['start_ms']}-{seg['end_ms']}: {seg['text']}"
                            )

            reader_task = asyncio.create_task(reader())

            # Fake 1s of tone as "alice", then "bob"
            t = np.linspace(0, 1.0, SAMPLE_RATE, endpoint=False)
            alice = (0.2 * np.sin(2 * np.pi * 440 * t) * 32767).astype(np.int16)
            bob = (0.2 * np.sin(2 * np.pi * 660 * t) * 32767).astype(np.int16)

            await ws.send(encode_frame("alice", alice))
            await asyncio.sleep(0.2)
            await ws.send(encode_frame("bob", bob))
            await asyncio.sleep(1.0)

            await ws.send(json.dumps({"type": "stop"}))
            await asyncio.sleep(2.0)
            reader_task.cancel()

        transcript = (
            await http.get(f"/meetings/{meeting_id}/transcript", headers=headers)
        ).raise_for_status().json()
        print("final segments", len(transcript["segments"]))


if __name__ == "__main__":
    asyncio.run(main())
```

Dependencies: `httpx`, `websockets`, `numpy`.

---

## 12. Meeting-provider adapter pattern

For Google Meet / Zoom / Teams, keep provider-specific capture behind a small adapter and always emit the same events into Distill:

```text
Provider tracks
  → (speaker_id, display_name, int16 PCM @ 16 kHz)
  → Distill multi_stream WebSocket
```

In-repo hooks:

| Piece | Role |
|-------|------|
| `api/integrations/base.py` | `MeetingAudioAdapter` + `pipe_to_session` |
| `api/integrations/google_meet.py` | Stub — implement your capture method |
| `extension/` | Browser extension that hooks Meet WebRTC and streams to the API |

**Adapter checklist:**

1. Authenticate to Distill; create `multi_stream` meeting (optionally with known `streams`).
2. Map each remote track / participant id → stable `speaker_id` + display `name`.
3. Resample to **16 kHz mono int16**.
4. Open the audio WebSocket with `?token=`.
5. `register_stream` (REST or WS) when a participant appears.
6. Send binary tagged frames; `ping` periodically.
7. On call end: `stop` then fetch transcript / insights.

Google does not expose a public per-participant raw audio API; production options are typically a Meet bot, recording webhook with separated tracks, or a browser extension (see `extension/README.md`).

---

## 13. Audio requirements

| Parameter | Value |
|-----------|-------|
| Sample rate | **16000** Hz |
| Channels | **1** (mono) per stream |
| Sample format | **signed 16-bit little-endian** |
| Float sources | Convert: `int16 = clip(float32 * 32768)` |

Downmix multi-channel provider audio to mono **per participant** before send. Do **not** mix participants into one stream if you want diarization bypass.

---

## 14. Error handling

| HTTP | Typical cause |
|------|----------------|
| `401` | Missing/invalid JWT |
| `403` | Disabled account / wrong owner |
| `404` | Meeting not found or not owned by you |
| `409` | Already recording; recording exists without `reset`; still processing |
| `400` | Bad `capture_mode`, missing `speaker_id`, invalid body |

WebSocket close without a clean stop may auto-finalize. Close code `1005` on clients often means the **client** dropped the socket (e.g. MV3 service worker sleep)—hold the socket in a long-lived context (tab/content script/bot process), not a short-lived worker.

---

## 15. REST quick reference

| Method | Path | Auth | Notes |
|--------|------|------|-------|
| `POST` | `/auth/register` | — | Returns `access_token` |
| `POST` | `/auth/login` | — | Returns `access_token` |
| `POST` | `/auth/logout` | cookie | Clears cookie |
| `GET` | `/auth/me` | yes | Current user |
| `GET` | `/health` | — | Liveness |
| `GET` | `/meetings` | yes | List owned meetings |
| `POST` | `/meetings` | yes | Create (± start, capture_mode, streams) |
| `GET` | `/meetings/{id}` | yes | Metadata |
| `DELETE` | `/meetings/{id}` | yes | Delete |
| `POST` | `/meetings/{id}/streams` | yes | Register multi-stream speaker |
| `POST` | `/meetings/{id}/start` | yes | Start / re-record |
| `POST` | `/meetings/{id}/stop` | yes | Finalize |
| `POST` | `/meetings/{id}/cancel` | yes | Abort & wipe capture |
| `POST` | `/meetings/{id}/upload` | yes | Offline file (mono pipeline) |
| `GET` | `/meetings/{id}/transcript` | yes | Segments |
| `PATCH` | `/meetings/{id}/segments/{sid}` | yes | Edit text |
| `GET` | `/meetings/{id}/speakers` | yes | Naming UI list |
| `PATCH` | `/meetings/{id}/speakers` | yes | Speaker map |
| `GET` | `/meetings/{id}/speakers/{sid}/audio` | yes | Sample WAV |
| `GET` | `/meetings/{id}/recording` | yes | Full recording |
| `GET` | `/meetings/{id}/debug` | yes | Pipeline stats |
| `POST` | `/meetings/{id}/insights` | yes | Generate insights |
| `GET` | `/meetings/{id}/insights` | yes | Fetch insights |
| `POST` | `/meetings/{id}/minutes/generate` | yes | Generate minutes |
| `GET`/`PUT` | `/meetings/{id}/minutes` | yes | Fetch / save minutes |
| `WSS` | `/meetings/{id}/audio?token=` | token/cookie | Live PCM + events |

Interactive OpenAPI UI (when the API is running): `https://<host>/docs`.

---

## 16. Ownership and isolation

- Meetings are **per-user**. `require_meeting` returns `404` if the id exists but belongs to someone else.
- Each meeting gets a forked diarizer / multi-stream ingest so labels do not bleed across sessions.
- Only one live recording session per meeting id at a time (`409` if already recording).
