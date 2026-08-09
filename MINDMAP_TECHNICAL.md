# Mindmap — Technical

---

## 1. Sound Recording

```
Recording
├── Live
│   ├── WebSocket: receives PCM int16 / 16kHz / mono
│   ├── AudioIngest: in-memory buffer + WAV writer to disk
│   ├── OverlappingChunker: 8s window / 6s hop
│   └── RMS threshold: drops silent chunks
├── File Upload
│   ├── POST /meetings/{id}/upload → save to data/uploads/
│   ├── Offline pipeline: load → diarize → chunk → STT → align → store
│   └── Output WAV saved to data/audio/{meeting_id}.wav
└── Improvements
    ├── Ring buffer instead of growing array
    ├── Audio compression (ogg opus) on disk
    ├── MP3/OGG/WebM upload support
    └── Pause/resume with client-side control
```

---

## 2. STT

```
STT
├── Provider
│   ├── OpenAI-compatible API (GapGPT Whisper)
│   ├── Encoding: float32 → int16 → WAV → MP3 → multipart form
│   ├── Cache: MD5 on PCM bytes → text saved in data/stt_cache/
│   └── Language: fa (configurable)
├── Windowing
│   ├── OverlappingChunker: window=8s, hop=6s (~25% overlap)
│   ├── flush_remainder on meeting end (>400ms remaining)
│   └── min_speech_rms: skips silent windows
├── Workers
│   ├── asyncio.Queue for chunk queue
│   ├── 2 parallel workers (configurable 1-5)
│   └── Retry: 3 attempts with backoff (0.5s, 1s, 2s)
├── Improvements
│   ├── Send WAV directly (remove pydub/ffmpeg)
│   ├── Streaming STT with whisper.cpp
│   ├── condition_on_previous_text=false
│   ├── Adaptive window based on speaker changes
│   └── Batch multiple chunks per request
└── Stats
    ├── _stt_calls: total API calls
    ├── _stt_retries: retry count
    ├── _stt_dropped: dropped chunks
    └── _stt_total_ms: total STT time
```

---

## 3. Transcription Quality (Review)

```
Review
├── Heuristic gate
│   ├── score_stt_text: quality score 0-1
│   ├── Detects: word run ≥3, char loop, unique ratio <0.25, tiny vocab
│   ├── collapse_consecutive: merges repeated tokens
│   └── localize_nonspeech_events: translates 35+ events to Persian
├── Review Agent
│   ├── __init__(llm, enabled)
│   ├── review_text: heuristic + LLM (single chunk)
│   ├── review_segments: heuristic + batch LLM (segment list)
│   └── LLM response: JSON {action: keep/fix/drop, text, reason}
├── Modes (stt_review_mode)
│   ├── off: only drops empty/punctuation-only text
│   ├── heuristic: only score_stt_text
│   ├── finalize: heuristic + LLM polish at meeting end
│   └── live: heuristic + LLM review per chunk
└── Improvements
    ├── Auto mode: selects heuristic or LLM based on quality
    ├── LLM response cache
    └── User feedback: save approve/edit per segment
```

---

## 4. Diarization

```
Diarization
├── pyannote.audio
│   ├── Pipeline.from_pretrained("pyannote/speaker-diarization-3.1")
│   ├── Requires: HF_TOKEN + torch
│   ├── Auto-detects speaker count
│   ├── Overlap detection: min(e,e2) > max(s,s2)
│   └── Automatic fallback on failure
├── Internal Fallback
│   ├── Feature extraction: RMS energy, spectral centroid, ZCR
│   ├── KMeans: k=1 or 2 (auto-detect via elbow-like heuristic)
│   ├── Label smoothing: majority filter (window=5)
│   ├── Merge short intervals (<400ms)
│   └── Merge flicker (rapid label change → keep previous speaker)
├── Label Stability
│   ├── _label_map: dict[old_label → new_label] by max time overlap
│   ├── _speaker_centroids: dict[speaker_id → feature vector]
│   └── cosine similarity > 0.8 → reuse previous label
├── Settings
│   ├── diarize_every_ms: refresh interval (default 20s)
│   ├── min/max_speakers: 1-8
│   ├── energy_threshold: silence threshold
│   └── merge_short_ms: merge short intervals
└── Improvements
    ├── More features: MFCC, pitch
    ├── Better algorithms: DBSCAN, HDBSCAN
    ├── Incremental diarization (new audio only)
    └── Multi-mic: channel separation + beamforming
```

---

## 5. Alignment & Integration

```
Alignment
├── align_stt_with_diarization
│   ├── dominant_speaker: max time overlap
│   ├── _significant_overlap_speakers: >1200ms real overlap
│   ├── _atomic_regions: split window into constant-speaker regions
│   └── emits overlap rows per speaker
├── dedupe_overlapping_transcripts
│   ├── Jaccard similarity on words
│   ├── Time overlap ratio
│   ├── Merges duplicate hop windows
│   └── Prefers longer text
├── merge_adjacent_segments
│   ├── Same speaker + gap <800ms → merge
│   └── Preserves time order
└── Improvements
    ├── VAD for speech boundaries
    ├── Export: SRT, VTT, TXT, JSON
    └── LLM-assisted text repair
```

---

## 6. Insights

```
Insights
├── MeetingInsightsGenerator
│   ├── generate(meeting_id, segments, speaker_map)
│   ├── Formatting: format_transcript_for_llm
│   │   ├── [start-end] speaker: text
│   │   └── [start-end] speaker + speaker [overlap]: text
│   ├── System prompt: Persian analysis → JSON
│   └── Response: {summary, highlights, decisions, action_items}
├── Storage
│   ├── save_insights: INSERT/ON CONFLICT UPDATE
│   └── get_insights: latest only per meeting
└── Improvements
    ├── Sentiment + consensus analysis
    ├── Custom output templates
    ├── Interactive Q&A
    └── Action item status tracking
```

---

## 7. Tuning

```
Tuning
├── System
│   ├── DEFAULT_TUNING: dict with default values
│   ├── TUNING_SCHEMA: list with parameter metadata
│   ├── make_tuning(overrides): build + sanitize
│   ├── Sanitization: min/max clamp, type cast, validation
│   └── public_tuning_payload: values + schema + defaults
├── Routes
│   ├── GET /tuning → payload
│   └── POST /tuning → apply + return new values
├── Parameters (18 total)
│   ├── Recording/STT: window_ms, hop_ms, stt_workers, stt_retry_count, min_speech_rms, stt_language
│   ├── Quality: stt_review_mode, stt_min_quality
│   ├── Diarization: diarize_every_ms, energy_threshold, min/max_speakers, merge_short_ms
│   └── Text: min_overlap_ms, dedupe_similarity, dedupe_time_overlap
└── UI Panel
    ├── Grouped display
    ├── Slider/input/select per parameter
    ├── Help text per parameter
    └── Apply: live (immediate) or next_session
```

---

## 8. User Interface

```
UI
├── Files
│   ├── landing.html — landing page
│   ├── assistant.html — assistant page
│   ├── styles.css — styles
│   ├── meeting.js — main client (DistillClient class)
│   └── logo.svg
├── Assistant Page
│   ├── Meeting creation form (title + participants)
│   ├── Microphone selector (dropdown)
│   ├── Start/stop recording buttons
│   ├── Audio file upload
│   ├── Live transcript display (timeline)
│   ├── Color-coded speakers (8 colors)
│   ├── Audio level meter
│   ├── Recording timer
│   ├── Generate insights button
│   └── Tuning settings panel
├── WebSocket
│   ├── Connect: /meetings/{id}/audio
│   ├── Send: audio bytes (PCM) + JSON (audio/stop/ping)
│   └── Receive: status/transcript/speaker_update/insights/error
├── Past Meetings
│   ├── URL: /assistant/{meeting_id}
│   ├── bootstrapFromUrl → loadExistingMeeting
│   └── Loads transcript history
└── Improvements
    ├── List/search/delete past meetings
    ├── Audio player synced with transcript
    ├── Transcript editing
    ├── Export TXT/SRT/VTT/JSON
    ├── Dark/light mode
    └── Virtual scrolling for long transcripts
```

---

## 9. Storage

```
Storage
├── SQLite (WAL mode, busy_timeout=5000ms)
│   ├── Table meetings: id, title, status, sample_rate, timestamps, audio_path, participants, speaker_map
│   ├── Table segments: id, meeting_id, speaker_id, start/end_ms, text, is_overlap, provisional, overlap_speakers
│   ├── Table insights: meeting_id, summary, highlights, decisions, action_items, raw_json
│   └── Index: idx_segments_meeting ON segments(meeting_id, start_ms)
├── TranscriptStore
│   ├── threading.Lock for all operations
│   ├── save_meeting, get_meeting, list_meetings
│   ├── save_segment, get_segments, delete_provisional_segments
│   ├── replace_meeting_segments (DELETE + INSERT in one transaction)
│   ├── save_insights, get_insights
│   └── Auto-migration: adds overlap_speakers column
├── Models
│   ├── MeetingRecord: id, title, status, sample_rate, timestamps, audio_path, participants, speaker_map
│   ├── TranscriptSegment: id, meeting_id, speaker_id, start/end_ms, text, is_overlap, provisional, overlap_speakers
│   ├── SpeakerInterval: speaker_id, start/end_ms, is_overlap
│   ├── MeetingInsights: meeting_id, summary, highlights, decisions, action_items, raw_json
│   └── MeetingStatus: CREATED, RECORDING, PROCESSING, STOPPED, FAILED
└── Improvements
    ├── aiosqlite or asyncio.to_thread for async DB
    ├── Export/import meeting as ZIP
    ├── Text search + speaker/date filter
    └── PostgreSQL migration (optional)
```

---

## 10. Project Structure

```
Project Structure
├── api/
│   ├── __init__.py
│   ├── __main__.py — entry point: python -m api
│   ├── main.py — uvicorn launcher
│   ├── app.py — FastAPI factory + _build_services + lifespan
│   ├── config.py — Settings dataclass + from_env()
│   ├── realtime.py — broadcast(websocket)
│   ├── tuning.py — DEFAULT_TUNING + TUNING_SCHEMA + make_tuning
│   ├── meeting/
│   │   ├── models.py — MeetingRecord, TranscriptSegment, SpeakerInterval, MeetingInsights
│   │   ├── session.py — MeetingSession + MeetingManager
│   │   ├── store.py — TranscriptStore (SQLite)
│   │   ├── chunker.py — OverlappingChunker + AudioChunk
│   │   ├── ingest.py — AudioIngest (buffer + WAV writer)
│   │   ├── diarization.py — SpeakerDiarizer (pyannote + fallback)
│   │   ├── aligner.py — align_stt_with_diarization + dedupe + merge
│   │   ├── review.py — gate_stt_text + TranscriptReviewAgent
│   │   └── insights.py — MeetingInsightsGenerator
│   ├── providers/
│   │   ├── stt.py — OpenAICompatibleSTT
│   │   ├── llm.py — OpenAICompatibleLLM
│   │   └── http_util.py — client_session (aiohttp)
│   ├── routes/
│   │   ├── __init__.py — setup_routes
│   │   ├── health.py — GET /health
│   │   ├── meetings.py — REST + WebSocket meetings
│   │   ├── pages.py — static file serving
│   │   └── tuning.py — GET/POST /tuning
│   └── web/
│       ├── landing.html
│       ├── assistant.html
│       ├── styles.css
│       ├── meeting.js
│       └── logo.svg
├── tests/
│   ├── conftest.py
│   ├── test_api_health.py
│   ├── test_meeting_core.py
│   ├── test_pipeline.py (new)
│   └── test_chunker.py (new)
├── data/
│   ├── meetings.db
│   ├── audio/
│   ├── uploads/
│   └── stt_cache/
├── main.py — thin launcher
├── requirements.txt
├── requirements.optional.txt
├── Dockerfile
├── docker-compose.yml
├── pyproject.toml
├── .env.example
└── .gitignore
```

---

## 11. API

```
API
├── REST
│   ├── GET / → landing page
│   ├── GET /assistant → assistant UI
│   ├── GET /assistant/{meeting_id} → assistant UI for specific meeting
│   ├── GET /health → service health
│   ├── GET /docs → Swagger UI
│   ├── POST /meetings → create meeting
│   ├── GET /meetings → list meetings
│   ├── GET /meetings/{id} → meeting info
│   ├── POST /meetings/{id}/stop → stop meeting
│   ├── POST /meetings/{id}/upload → upload audio file
│   ├── GET /meetings/{id}/transcript → transcript
│   ├── POST /meetings/{id}/insights → generate insights
│   ├── GET /meetings/{id}/insights → get insights
│   ├── GET /meetings/{id}/debug → debug info (new)
│   ├── GET /tuning → tuning settings
│   └── POST /tuning → apply tuning
├── WebSocket
│   ├── /meetings/{id}/audio
│   ├── Client → Server: audio bytes (PCM int16) + JSON {type: audio/stop/ping}
│   ├── Server → Client: {type: status/transcript/speaker_update/insights/error/pong}
│   └── Broadcast: sends to all clients connected to a meeting
└── Docker
    ├── Dockerfile
    ├── docker-compose.yml
    ├── Volume: distill-data:/app/data
    └── env_file: .env
```

---

## 12. Dependencies

```
Dependencies
├── Core (requirements.txt)
│   ├── fastapi + uvicorn
│   ├── aiohttp
│   ├── numpy
│   ├── python-dotenv
│   └── pydub + audioop-lts (audio conversion)
├── Optional (requirements.optional.txt)
│   ├── torch (CPU)
│   └── pyannote.audio (best diarization)
└── System
    └── ffmpeg (for pydub — removed in improvements)
```

---

## 13. Tests

```
Tests
├── Current
│   ├── pytest + pytest-asyncio
│   ├── tests/conftest.py → fixtures
│   ├── tests/test_api_health.py → GET /health
│   └── tests/test_meeting_core.py → MeetingSession, MeetingManager
├── New
│   ├── tests/test_pipeline.py
│   │   ├── Synthetic audio: sine waves at different frequencies
│   │   ├── Pipeline: chunker → mock STT → diarizer → aligner
│   │   ├── Retry logic test
│   │   ├── Label stability test
│   │   ├── Quality gate test
│   │   └── Upload path test
│   └── tests/test_chunker.py
│       ├── Correct hop interval emission
│       ├── flush_remainder behavior
│       ├── Silence removal
│       └── Overlap size verification
└── CI/CD (improvement)
    ├── GitHub Actions
    ├── ruff (lint)
    ├── mypy (type check)
    └── coverage report
```

---

## 14. Deployment

```
Deployment
├── Docker
│   ├── Simple Dockerfile
│   ├── docker-compose.yml + volume + env_file
│   └── Improvement: multi-stage, health check, resource limits
├── Local
│   ├── python -m api
│   ├── python main.py
│   └── uvicorn api.app:app --host 0.0.0.0 --port 8000
└── Cloud (improvement)
    ├── Railway / Fly.io / Render
    └── Auto-deploy from git
```
