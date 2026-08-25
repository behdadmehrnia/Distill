from fastapi.testclient import TestClient

from tests.conftest import make_client, register_user


def _make_client(tmp_path) -> TestClient:
    return make_client(tmp_path)


def _auth(client):
    register_user(client)


def test_health(tmp_path):
    with _make_client(tmp_path) as client:
        resp = client.get("/health")
        assert resp.status_code == 200
        data = resp.json()
        assert data["service"] == "distill"
        assert data["status"] == "ok"
        assert "diarization_backend" in data
        assert data["diarization_backend"] in {
            "unloaded",
            "loading",
            "fallback",
            "pyannote",
            "nemo",
        }
        assert "diarization_ready" in data
        assert data["diarization_quality"] in {"high", "fallback"}
        assert "diarization_allow_fallback" in data
        assert "diarization_endpoint" in data


def test_create_meeting_without_start(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        resp = client.post("/meetings", json={"title": "تست", "start": False})
        assert resp.status_code == 201
        data = resp.json()
        assert data["title"] == "تست"
        assert "id" in data

        listing = client.get("/meetings")
        body = listing.json()
        assert any(m["id"] == data["id"] for m in body["meetings"])


def test_transcript_404(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        resp = client.get("/meetings/does-not-exist/transcript")
        assert resp.status_code == 404


def test_meeting_debug_endpoint(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "دیباگ", "start": False})
        assert created.status_code == 201
        meeting_id = created.json()["id"]
        resp = client.get(f"/meetings/{meeting_id}/debug")
        assert resp.status_code == 200
        data = resp.json()
        assert data["meeting_id"] == meeting_id
        assert "stt_calls" in data
        assert "tuning" in data
        assert "diarization_backend" in data


def test_speaker_map_patch(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "نام‌گذاری", "start": False})
        meeting_id = created.json()["id"]
        resp = client.patch(
            f"/meetings/{meeting_id}/speakers",
            json={"SPEAKER_00": "علی"},
        )
        assert resp.status_code == 200
        assert resp.json()["speaker_map"]["SPEAKER_00"] == "علی"
        again = client.get(f"/meetings/{meeting_id}")
        assert again.json()["speaker_map"]["SPEAKER_00"] == "علی"


def test_segment_text_patch(tmp_path):
    from api.meeting.models import TranscriptSegment

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "ویرایش متن", "start": False})
        meeting_id = created.json()["id"]
        store = client.app.state.manager.store

        final_seg = TranscriptSegment.create(
            meeting_id=meeting_id,
            speaker_id="SPEAKER_00",
            start_ms=0,
            end_ms=1500,
            text="متن اولیه",
            provisional=False,
        )
        live_seg = TranscriptSegment.create(
            meeting_id=meeting_id,
            speaker_id="SPEAKER_00",
            start_ms=2000,
            end_ms=3500,
            text="در حال صحبت",
            provisional=True,
        )
        store.save_segment(final_seg)
        store.save_segment(live_seg)

        ok = client.patch(
            f"/meetings/{meeting_id}/segments/{final_seg.id}",
            json={"text": "متن اصلاح‌شده"},
        )
        assert ok.status_code == 200
        body = ok.json()
        assert body["segments"][0]["text"] == "متن اصلاح‌شده"
        assert store.get_segment(meeting_id, final_seg.id).text == "متن اصلاح‌شده"

        blocked = client.patch(
            f"/meetings/{meeting_id}/segments/{live_seg.id}",
            json={"text": "نباید ذخیره شود"},
        )
        assert blocked.status_code == 400

        empty = client.patch(
            f"/meetings/{meeting_id}/segments/{final_seg.id}",
            json={"text": "   "},
        )
        assert empty.status_code == 400

        missing = client.patch(
            f"/meetings/{meeting_id}/segments/does-not-exist",
            json={"text": "x"},
        )
        assert missing.status_code == 404


def test_speakers_list_and_sample_audio(tmp_path):
    import wave

    from api.meeting.models import SpeakerInterval, TranscriptSegment

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "سخنگوها", "start": False})
        meeting_id = created.json()["id"]
        store = client.app.state.manager.store

        store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="SPEAKER_00",
                start_ms=0,
                end_ms=2000,
                text="سلام وقت بخیر",
                provisional=False,
            )
        )
        store.replace_speaker_intervals(
            meeting_id,
            [SpeakerInterval(speaker_id="SPEAKER_00", start_ms=0, end_ms=2000, is_overlap=False)],
        )

        no_audio = client.get(f"/meetings/{meeting_id}/speakers")
        assert no_audio.status_code == 200
        speakers = no_audio.json()["speakers"]
        assert speakers == []

        audio_dir = tmp_path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / f"{meeting_id}.wav"
        with wave.open(str(wav_path), "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(16000)
            wf.writeframes(b"\x00\x00" * 16000 * 2)
        meeting = store.get_meeting(meeting_id)
        meeting.audio_path = str(wav_path)
        store.save_meeting(meeting)

        # Ghost speaker with no span must not appear either.
        store.replace_speaker_intervals(
            meeting_id,
            [
                SpeakerInterval(
                    speaker_id="SPEAKER_00", start_ms=0, end_ms=2000, is_overlap=False
                ),
                SpeakerInterval(
                    speaker_id="SPEAKER_01", start_ms=0, end_ms=0, is_overlap=False
                ),
            ],
        )

        with_audio = client.get(f"/meetings/{meeting_id}/speakers")
        assert with_audio.status_code == 200
        speakers_ready = with_audio.json()["speakers"]
        assert [s["id"] for s in speakers_ready] == ["SPEAKER_00"]
        assert speakers_ready[0]["has_recording"] is True
        assert speakers_ready[0]["has_sample"] is True

        sample = client.get(f"/meetings/{meeting_id}/speakers/SPEAKER_00/audio")
        assert sample.status_code == 200
        assert sample.headers["content-type"] == "audio/wav"
        assert len(sample.content) > 0

        missing_speaker = client.get(f"/meetings/{meeting_id}/speakers/SPEAKER_09/audio")
        assert missing_speaker.status_code == 404

        rename = client.patch(
            f"/meetings/{meeting_id}/speakers", json={"SPEAKER_00": "مریم"}
        )
        assert rename.status_code == 200
        after_rename = client.get(f"/meetings/{meeting_id}/speakers")
        assert after_rename.json()["speakers"][0]["label"] == "مریم"


def test_minutes_generate_get_put(tmp_path):
    from api.meeting.models import TranscriptSegment

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "صورتجلسه", "start": False})
        meeting_id = created.json()["id"]
        store = client.app.state.manager.store

        missing = client.get(f"/meetings/{meeting_id}/minutes")
        assert missing.status_code == 404

        edited = client.put(
            f"/meetings/{meeting_id}/minutes",
            json={
                "subject": "بررسی بودجه",
                "meeting_date": "1404/05/18",
                "location": "اتاق جلسات",
                "attendees": ["مریم", "علی"],
                "absentees": [],
                "secretary": "مریم",
                "summary": "خلاصه دستی",
                "decisions": [
                    {
                        "description": "تهیه گزارش مالی",
                        "executor": "علی",
                        "due_date": "1404/05/25",
                        "status": "pending",
                    }
                ],
            },
        )
        assert edited.status_code == 200
        body = edited.json()
        assert body["subject"] == "بررسی بودجه"
        assert body["decisions"][0]["executor"] == "علی"

        fetched = client.get(f"/meetings/{meeting_id}/minutes")
        assert fetched.status_code == 200
        assert fetched.json()["subject"] == "بررسی بودجه"
        assert fetched.json()["decisions"][0]["description"] == "تهیه گزارش مالی"


def test_minutes_generate_returns_502_when_llm_unreachable(tmp_path):
    from api.meeting.minutes import MeetingMinutesGenerator
    from api.meeting.models import TranscriptSegment

    class BoomLLM:
        async def complete(self, *args, **kwargs):
            raise RuntimeError("زمان پاسخ مدل زبانی (LLM) در 81.29.248.136 به پایان رسید.")

    with _make_client(tmp_path) as client:
        _auth(client)
        client.app.state.minutes = MeetingMinutesGenerator(BoomLLM())
        created = client.post("/meetings", json={"title": "صورتجلسه", "start": False})
        meeting_id = created.json()["id"]
        store = client.app.state.manager.store
        meeting = store.get_meeting(meeting_id)
        meeting.speaker_map = {"SPEAKER_00": "علی"}
        store.save_meeting(meeting)
        store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="SPEAKER_00",
                start_ms=0,
                end_ms=1000,
                text="سلام، امروز جلسه برگزار شد.",
                provisional=False,
            )
        )
        resp = client.post(f"/meetings/{meeting_id}/minutes/generate")
        assert resp.status_code == 502
        assert "زمان پاسخ" in resp.json()["detail"]


def test_recording_endpoint_and_restart(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "ضبط", "start": False})
        meeting_id = created.json()["id"]

        missing = client.get(f"/meetings/{meeting_id}/recording")
        assert missing.status_code == 404

        meta = client.get(f"/meetings/{meeting_id}")
        assert meta.json()["has_recording"] is False

        audio_dir = tmp_path / "audio"
        audio_dir.mkdir(parents=True, exist_ok=True)
        wav_path = audio_dir / f"{meeting_id}.wav"
        wav_path.write_bytes(
            b"RIFF$\x00\x00\x00WAVEfmt \x10\x00\x00\x00"
            b"\x01\x00\x01\x00\x80>\x00\x00\x00}\x00\x00"
            b"\x02\x00\x10\x00data\x00\x00\x00\x00"
        )

        meeting = client.app.state.manager.store.get_meeting(meeting_id)
        meeting.audio_path = str(wav_path)
        client.app.state.manager.store.save_meeting(meeting)

        again = client.get(f"/meetings/{meeting_id}")
        assert again.json()["has_recording"] is True

        recording = client.get(f"/meetings/{meeting_id}/recording")
        assert recording.status_code == 200
        assert recording.headers["content-type"].startswith("audio/")

        blocked = client.post(f"/meetings/{meeting_id}/start", json={})
        assert blocked.status_code == 409

        started = client.post(
            f"/meetings/{meeting_id}/start", json={"reset": True}
        )
        assert started.status_code == 200
        body = started.json()
        assert body["status"] == "recording"
        assert body["has_recording"] is False
        assert not wav_path.exists()

        stopped = client.post(f"/meetings/{meeting_id}/stop")
        assert stopped.status_code == 200


def test_websocket_disconnect_auto_cancels_recording(tmp_path):
    """Closing the audio WS without POST /stop must gracefully finalize
    (preserve whatever was captured) rather than wipe it — a dropped
    connection (tab close, refresh, flaky network) is not an explicit
    user cancel and must not destroy already-completed work."""
    import time

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "تب بسته", "start": True})
        assert created.status_code == 201
        meeting_id = created.json()["id"]
        assert created.json()["status"] == "recording"

        with client.websocket_connect(f"/meetings/{meeting_id}/audio") as ws:
            hello = ws.receive_json()
            assert hello["status"] == "recording"
            ws.send_bytes(b"\x00\x00" * 160)

        status = None
        deadline = time.time() + 30
        while time.time() < deadline:
            meta = client.get(f"/meetings/{meeting_id}")
            assert meta.status_code == 200
            status = meta.json()["status"]
            if status == "stopped":
                break
            time.sleep(0.05)
        assert status == "stopped"

        again = client.post(f"/meetings/{meeting_id}/start", json={"reset": True})
        assert again.status_code == 200
        assert again.json()["status"] == "recording"

        client.post(f"/meetings/{meeting_id}/stop")


def test_upload_rejects_invalid_audio(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "آپلود", "start": False})
        meeting_id = created.json()["id"]
        resp = client.post(
            f"/meetings/{meeting_id}/upload",
            files={"file": ("bad.mp3", b"not-an-mp3", "audio/mpeg")},
        )
        assert resp.status_code == 400
        assert "decode" in resp.json()["detail"].lower()

        # The failed upload leaves status stuck at "processing" with no live
        # session; healing recovers it to "stopped" (not a destructive wipe
        # back to "created") so a stray failure never leaves a meeting
        # silently reset — same recovery path applies whether or not the
        # attempt captured anything.
        meeting = client.get(f"/meetings/{meeting_id}")
        assert meeting.json()["status"] == "stopped"


def test_upload_rejects_empty_body(tmp_path):
    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "آپلود", "start": False})
        meeting_id = created.json()["id"]
        resp = client.post(
            f"/meetings/{meeting_id}/upload",
            files={"file": ("empty.mp3", b"", "audio/mpeg")},
        )
        assert resp.status_code == 400
        detail = resp.json()["detail"]
        assert "خالی" in detail or "ناقص" in detail


def test_cancel_resets_processing_meeting(tmp_path):
    from api.meeting.models import MeetingStatus

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "لغو", "start": False})
        meeting_id = created.json()["id"]
        meeting = client.app.state.manager.store.get_meeting(meeting_id)
        meeting.status = MeetingStatus.PROCESSING
        client.app.state.manager.store.save_meeting(meeting)

        resp = client.post(f"/meetings/{meeting_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        # The DB row was set to "processing" out-of-band above without
        # touching the live session's own in-memory record, so the /cancel
        # above no-ops (nothing was actually in flight) and this GET is what
        # triggers healing — recovering to "stopped", not a destructive wipe.
        again = client.get(f"/meetings/{meeting_id}")
        assert again.json()["status"] == "stopped"


def test_orphaned_recording_status_allows_restart(tmp_path):
    """Stale DB status=recording with no live capture must not block /start."""
    from api.meeting.models import MeetingStatus

    with _make_client(tmp_path) as client:
        _auth(client)
        created = client.post("/meetings", json={"title": "یتیم", "start": False})
        meeting_id = created.json()["id"]

        meeting = client.app.state.manager.store.get_meeting(meeting_id)
        meeting.status = MeetingStatus.RECORDING
        client.app.state.manager.store.save_meeting(meeting)

        stuck = client.get(f"/meetings/{meeting_id}")
        assert stuck.status_code == 200
        assert stuck.json()["status"] == "stopped"

        started = client.post(f"/meetings/{meeting_id}/start", json={})
        assert started.status_code == 200
        assert started.json()["status"] == "recording"

        client.post(f"/meetings/{meeting_id}/stop")

