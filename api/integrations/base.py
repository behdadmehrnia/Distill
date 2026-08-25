from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Awaitable, Callable, Optional

import numpy as np

StreamAudioCallback = Callable[[str, np.ndarray, Optional[str]], Awaitable[None]]


@dataclass
class StreamAudioEvent:
    """Normalized audio event from an external meeting provider."""

    speaker_id: str
    pcm_int16: np.ndarray
    display_name: Optional[str] = None


class MeetingAudioAdapter(ABC):
    """
    Bridge between an online meeting provider and Distill multi-stream capture.

    Implementations receive per-participant audio from providers such as Google
    Meet, Zoom, or Teams and forward tagged PCM to Distill via the callback.
    """

    @abstractmethod
    async def connect(self, meeting_url: str, *, auth_token: Optional[str] = None) -> None:
        """Join or attach to the remote meeting."""

    @abstractmethod
    async def run(
        self,
        on_audio: StreamAudioCallback,
        *,
        stop_event=None,
    ) -> None:
        """
        Stream audio until stopped.

        The callback receives (speaker_id, pcm_int16, display_name).
        """

    @abstractmethod
    async def disconnect(self) -> None:
        """Leave the meeting and release resources."""

    async def pipe_to_session(self, session, *, stop_event=None) -> None:
        """Forward provider audio into a Distill MeetingSession."""

        async def _forward(
            speaker_id: str, pcm: np.ndarray, name: Optional[str] = None
        ) -> None:
            if name:
                session.register_stream(speaker_id, name=name)
            await session.append_stream_audio_int16(speaker_id, pcm)

        await self.run(_forward, stop_event=stop_event)
