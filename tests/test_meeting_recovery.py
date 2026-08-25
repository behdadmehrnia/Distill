from api.meeting.models import MeetingStatus, TranscriptSegment

from tests.conftest import make_client, register_user


def test_orphaned_processing_meeting_heals_without_losing_data(tmp_path):
    """Simulates a server restart/crash while a meeting was mid-flight:
    status stuck at 'processing' with no live in-memory session. Touching
    the meeting again (e.g. the page refresh that calls GET /meetings/{id})
    must recover it to 'stopped' WITHOUT discarding already-captured
    segments or a saved minutes document.
    """
    with make_client(tmp_path) as client:
        register_user(client, email="heal@test.com")
        created = client.post("/meetings", json={"title": "heal test", "start": False})
        meeting_id = created.json()["id"]

        store = client.app.state.manager.store
        record = store.get_meeting(meeting_id)
        record.status = MeetingStatus.PROCESSING
        store.save_meeting(record)
        store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="s1",
                start_ms=0,
                end_ms=1000,
                text="این یک متن آزمایشی است",
            )
        )
        put = client.put(
            f"/meetings/{meeting_id}/minutes",
            json={"subject": "heal check", "summary": "باید باقی بماند"},
        )
        assert put.status_code == 200

        got = client.get(f"/meetings/{meeting_id}")
        assert got.status_code == 200
        assert got.json()["status"] == "stopped"

        segments = client.get(f"/meetings/{meeting_id}/transcript").json()["segments"]
        assert len(segments) == 1

        minutes = client.get(f"/meetings/{meeting_id}/minutes")
        assert minutes.status_code == 200
        assert minutes.json()["summary"] == "باید باقی بماند"


def test_orphaned_recording_meeting_heals_without_losing_data(tmp_path):
    """Same recovery guarantee when the record was stuck at 'recording'."""
    with make_client(tmp_path) as client:
        register_user(client, email="heal2@test.com")
        created = client.post("/meetings", json={"title": "heal test 2", "start": False})
        meeting_id = created.json()["id"]

        store = client.app.state.manager.store
        record = store.get_meeting(meeting_id)
        record.status = MeetingStatus.RECORDING
        store.save_meeting(record)
        store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="s1",
                start_ms=0,
                end_ms=1000,
                text="یک جمله دیگر برای تست",
            )
        )

        got = client.get(f"/meetings/{meeting_id}")
        assert got.status_code == 200
        assert got.json()["status"] == "stopped"

        segments = client.get(f"/meetings/{meeting_id}/transcript").json()["segments"]
        assert len(segments) == 1


def test_explicit_cancel_on_orphaned_meeting_still_wipes(tmp_path):
    """Contrast case: an explicit cancel on a meeting whose live session
    itself is stuck at recording/processing is a deliberate user action and
    should still discard data — only a cancel on an already-finished
    session must be non-destructive.
    """
    with make_client(tmp_path) as client:
        register_user(client, email="cancel@test.com")
        created = client.post("/meetings", json={"title": "cancel test", "start": False})
        meeting_id = created.json()["id"]

        # Mutate the SAME record object the live in-memory session holds
        # (not a separately-fetched copy) — this is what stop()/start()
        # actually do in the real pipeline.
        session = client.app.state.manager.get(meeting_id)
        session.record.status = MeetingStatus.PROCESSING
        client.app.state.manager.store.save_meeting(session.record)
        client.app.state.manager.store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="s1",
                start_ms=0,
                end_ms=1000,
                text="این باید پاک شود",
            )
        )

        resp = client.post(f"/meetings/{meeting_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "created"

        segments = client.get(f"/meetings/{meeting_id}/transcript").json()["segments"]
        assert len(segments) == 0


def test_cancel_on_already_finished_meeting_is_a_no_op(tmp_path):
    """The bug: a stray /cancel (e.g. a page-unload beacon racing an
    already-completed, saved meeting) must NOT wipe it. cancel() should only
    be destructive while something is genuinely in flight.
    """
    with make_client(tmp_path) as client:
        register_user(client, email="noop-cancel@test.com")
        created = client.post("/meetings", json={"title": "finished", "start": False})
        meeting_id = created.json()["id"]

        # This mirrors exactly what a real stop() does: it mutates the live
        # session's own record object directly, in place.
        session = client.app.state.manager.get(meeting_id)
        session.record.status = MeetingStatus.STOPPED
        client.app.state.manager.store.save_meeting(session.record)
        client.app.state.manager.store.save_segment(
            TranscriptSegment.create(
                meeting_id=meeting_id,
                speaker_id="s1",
                start_ms=0,
                end_ms=1000,
                text="این جلسه با موفقیت تمام شده است",
            )
        )
        put = client.put(
            f"/meetings/{meeting_id}/minutes",
            json={"subject": "done", "summary": "این نباید پاک شود"},
        )
        assert put.status_code == 200

        # Simulate the stray cancel beacon firing after everything finished.
        resp = client.post(f"/meetings/{meeting_id}/cancel")
        assert resp.status_code == 200
        assert resp.json()["status"] == "stopped"

        got = client.get(f"/meetings/{meeting_id}")
        assert got.json()["status"] == "stopped"

        segments = client.get(f"/meetings/{meeting_id}/transcript").json()["segments"]
        assert len(segments) == 1

        minutes = client.get(f"/meetings/{meeting_id}/minutes")
        assert minutes.status_code == 200
        assert minutes.json()["summary"] == "این نباید پاک شود"
