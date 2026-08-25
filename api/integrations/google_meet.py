from __future__ import annotations

from typing import Optional

from .base import MeetingAudioAdapter, StreamAudioCallback


class GoogleMeetAdapter(MeetingAudioAdapter):
    """
    Stub adapter for Google Meet per-participant audio.

    Google does not expose a public per-participant raw audio API for third-party
    apps. Production integrations typically use one of:

    - A browser extension with tabCapture + WebRTC track demuxing
    - A Meet add-on / Workspace bot with documented media APIs when available
    - An enterprise recording webhook that includes separated tracks

    Subclass this adapter and implement `connect`, `run`, and `disconnect` for
    your deployment model.
    """

    def __init__(self) -> None:
        self._connected = False
        self._meeting_url: Optional[str] = None

    async def connect(self, meeting_url: str, *, auth_token: Optional[str] = None) -> None:
        self._meeting_url = meeting_url
        self._connected = True
        raise NotImplementedError(
            "Google Meet per-participant audio is not wired yet. "
            "Implement GoogleMeetAdapter for your capture method (extension, bot, "
            "or recording webhook) and pipe events to MeetingSession.append_stream_audio_int16."
        )

    async def run(
        self,
        on_audio: StreamAudioCallback,
        *,
        stop_event=None,
    ) -> None:
        raise NotImplementedError("GoogleMeetAdapter.run is not implemented")

    async def disconnect(self) -> None:
        self._connected = False
        self._meeting_url = None
