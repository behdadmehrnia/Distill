# Distill Capture (Chrome extension)

Browser extension that taps **per-participant audio** inside Google Meet and streams it to a Distill `multi_stream` meeting — **no diarization**.

Full API protocol for any client (bots, adapters, CLIs): [`docs/CLIENT.md`](../docs/CLIENT.md).

Default API: `https://api.distill.app` (hardcoded; not configurable in the UI).

## Auth note

Production currently returns the JWT as an **HttpOnly cookie** (`access_token`), not in the JSON body. The extension reads that cookie via `chrome.cookies` after login (and still accepts `access_token` in JSON if the API is updated).

The audio WebSocket runs in the **Meet content script** (not the service worker). MV3 workers sleep and would otherwise drop the socket with close code `1005`.

## How it works

```text
Google Meet tab
  └─ meet-injected.js   (page world: hooks RTCPeerConnection)
  └─ meet-bridge.js     (content script: relays PCM)
       └─ service-worker.js
            ├─ POST /meetings  { capture_mode: "multi_stream" }
            ├─ WSS  /meetings/{id}/audio?token=…
            └─ binary frames: [u8 name_len][utf8 speaker_id][int16le pcm]
```

Each remote WebRTC audio track becomes `remote_1`, `remote_2`, …  
Your outbound mic becomes `local`. Display names are guessed from the Meet DOM when possible and pre-seed `speaker_map` via `register_stream`.

## Install (unpacked)

1. Open Chrome → `chrome://extensions`
2. Enable **Developer mode**
3. **Load unpacked** → select this `extension/` folder
4. Pin **Distill Capture**

## Use

1. Sign in with your Distill account (same credentials as the web app)
2. Open a [Google Meet](https://meet.google.com) call
3. Click the extension → **Start capture**
4. Speak / listen — open the meeting on `https://api.distill.app` to see live transcript
5. **Stop** when done (finalizes the meeting on the API)

## Requirements on the API

Deploy Distill with:

- `capture_mode: multi_stream` meeting support
- Login/register JSON including `access_token`
- Multi-stream WebSocket binary frames  
  `[u8 name_len][utf8 speaker_id][int16le pcm]`

## Limits (important)

Google does **not** expose a public per-participant audio API. This extension:

- Hooks `RTCPeerConnection` in the Meet page (fragile; Meet updates can break it)
- May need a **Meet refresh** after Start if tracks were already established
- Name ↔ track mapping is heuristic (DOM scrape), not guaranteed
- Does not capture screen-share-only audio specially
- Uses `ScriptProcessorNode` (fine for a demo; production should move to AudioWorklet)

If WebRTC hooks miss tracks, you still get whatever tracks appear after capture is armed.

## Privacy

Audio leaves the Meet tab only toward the API base you configure (default `api.distill.app`). Credentials stay in `chrome.storage.local` as a JWT.

## Dev tips

- Open Meet DevTools → Console for `[Distill]` logs
- Background errors: `chrome://extensions` → service worker **Inspect**
- Confirm API health: `curl -s https://api.distill.app/health`
