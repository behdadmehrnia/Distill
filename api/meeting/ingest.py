from __future__ import annotations

import io
import logging
import os
import wave
from typing import Optional, Tuple

import numpy as np

logger = logging.getLogger(__name__)


class AudioIngest:
    """Accumulate continuous mono PCM and optionally persist to WAV."""

    def __init__(self, sample_rate: int = 16000, audio_path: Optional[str] = None):
        self.sample_rate = sample_rate
        self.audio_path = audio_path
        self._chunks: list[np.ndarray] = []
        self._total_samples = 0
        self._cached: Optional[np.ndarray] = None

    @property
    def duration_ms(self) -> int:
        return int(self._total_samples * 1000 / self.sample_rate)

    @property
    def total_samples(self) -> int:
        return self._total_samples

    def append_float32(self, audio: np.ndarray) -> None:
        if audio is None or len(audio) == 0:
            return
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        self._chunks.append(arr.copy())
        self._total_samples += len(arr)
        self._cached = None

    def append_int16(self, samples: np.ndarray) -> None:
        arr = np.asarray(samples, dtype=np.int16).astype(np.float32) / 32768.0
        self.append_float32(arr)

    def get_buffer(self) -> np.ndarray:
        if self._cached is not None:
            return self._cached
        if not self._chunks:
            self._cached = np.zeros(0, dtype=np.float32)
            return self._cached
        self._cached = np.concatenate(self._chunks)
        return self._cached

    def slice_ms(self, start_ms: int, end_ms: int) -> np.ndarray:
        buf = self.get_buffer()
        start = max(0, int(start_ms * self.sample_rate / 1000))
        end = min(len(buf), int(end_ms * self.sample_rate / 1000))
        if end <= start:
            return np.zeros(0, dtype=np.float32)
        return buf[start:end].copy()

    def save_wav(self, path: Optional[str] = None) -> str:
        out = path or self.audio_path
        if not out:
            raise ValueError("No audio_path provided for save_wav")
        os.makedirs(os.path.dirname(os.path.abspath(out)) or ".", exist_ok=True)
        buf = self.get_buffer()
        pcm = np.clip(buf * 32768.0, -32768, 32767).astype(np.int16)
        with wave.open(out, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        self.audio_path = out
        logger.info("Saved meeting audio to %s (%.1fs)", out, len(buf) / self.sample_rate)
        return out

    @staticmethod
    def _load_wav(path: str, target_sr: int) -> Tuple[np.ndarray, int]:
        with wave.open(path, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            frames = wf.readframes(wf.getnframes())
        if sampwidth != 2:
            raise ValueError(f"unsupported WAV sample width: {sampwidth}")
        samples = np.frombuffer(frames, dtype=np.int16)
        if n_channels > 1:
            samples = samples.reshape(-1, n_channels).mean(axis=1).astype(np.int16)
        audio = samples.astype(np.float32) / 32768.0
        if framerate != target_sr and len(audio) > 0:
            duration = len(audio) / framerate
            target_len = max(1, int(round(duration * target_sr)))
            x_old = np.linspace(0.0, 1.0, num=len(audio), endpoint=False)
            x_new = np.linspace(0.0, 1.0, num=target_len, endpoint=False)
            audio = np.interp(x_new, x_old, audio).astype(np.float32)
        return audio, target_sr

    @staticmethod
    def load_audio_file(path: str, target_sr: int = 16000) -> Tuple[np.ndarray, int]:
        """Load audio file as float32 mono at target_sr."""
        if not os.path.isfile(path) or os.path.getsize(path) == 0:
            raise ValueError("empty or missing audio file")

        ext = os.path.splitext(path)[1].lower()
        if ext == ".wav":
            try:
                return AudioIngest._load_wav(path, target_sr)
            except wave.Error as exc:
                raise ValueError(f"invalid WAV file: {exc}") from exc

        try:
            from pydub import AudioSegment
            from pydub.exceptions import CouldntDecodeError
        except ImportError as exc:
            raise ImportError("pydub is required to load audio files") from exc

        format_hints = {
            ".m4a": "mp4",
            ".mp4": "mp4",
            ".aac": "aac",
            ".ogg": "ogg",
            ".opus": "ogg",
            ".mp3": "mp3",
            ".flac": "flac",
        }
        load_kwargs: dict = {}
        if ext in format_hints:
            load_kwargs["format"] = format_hints[ext]

        try:
            segment = AudioSegment.from_file(path, **load_kwargs)
        except CouldntDecodeError as exc:
            raise ValueError(
                "could not decode audio file; it may be corrupt, truncated, "
                "or an unsupported format"
            ) from exc
        except FileNotFoundError as exc:
            # pydub shells out to ffprobe/ffmpeg for anything that is not WAV.
            # The file itself was checked above, so a missing-file error here
            # is the external tool, not the upload — say so rather than
            # surfacing a bare "No such file or directory: 'ffprobe'".
            missing = os.path.basename(str(getattr(exc, "filename", "") or ""))
            if missing in {"ffprobe", "ffmpeg", "avprobe", "avconv"}:
                raise RuntimeError(
                    f"{missing} not found: decoding {ext or 'this format'} "
                    "requires ffmpeg to be installed and on PATH. Install it, "
                    "or upload a .wav file, which is decoded natively."
                ) from exc
            raise
        segment = segment.set_channels(1).set_frame_rate(target_sr).set_sample_width(2)
        samples = np.array(segment.get_array_of_samples(), dtype=np.int16)
        audio = samples.astype(np.float32) / 32768.0
        return audio, target_sr

    def load_from_file(self, path: str) -> None:
        audio, sr = self.load_audio_file(path, self.sample_rate)
        if sr != self.sample_rate:
            raise ValueError(f"Unexpected sample rate {sr}")
        self._chunks = [audio]
        self._total_samples = len(audio)
        self._cached = audio

    def replace_buffer(self, audio: np.ndarray) -> None:
        """Replace the in-memory PCM buffer (e.g. after upload denoise)."""
        arr = np.asarray(audio, dtype=np.float32).reshape(-1)
        self._chunks = [arr.copy()]
        self._total_samples = len(arr)
        self._cached = arr.copy()

    def clear(self) -> None:
        """Drop in-memory PCM; leave audio_path unchanged for reuse on save."""
        self._chunks = []
        self._total_samples = 0
        self._cached = None

    def to_wav_bytes(self) -> bytes:
        buf = self.get_buffer()
        pcm = np.clip(buf * 32768.0, -32768, 32767).astype(np.int16)
        bio = io.BytesIO()
        with wave.open(bio, "wb") as wf:
            wf.setnchannels(1)
            wf.setsampwidth(2)
            wf.setframerate(self.sample_rate)
            wf.writeframes(pcm.tobytes())
        return bio.getvalue()

    @staticmethod
    def slice_wav_file(path: str, start_ms: int, end_ms: int) -> bytes:
        """Read a mono WAV file on disk and return WAV bytes for [start_ms, end_ms)."""
        with wave.open(path, "rb") as wf:
            n_channels = wf.getnchannels()
            sampwidth = wf.getsampwidth()
            framerate = wf.getframerate()
            n_frames = wf.getnframes()

            start_frame = max(0, int(start_ms * framerate / 1000))
            end_frame = min(n_frames, int(end_ms * framerate / 1000))
            if end_frame <= start_frame:
                start_frame, end_frame = 0, n_frames

            wf.setpos(start_frame)
            frames = wf.readframes(end_frame - start_frame)

            bio = io.BytesIO()
            with wave.open(bio, "wb") as out:
                out.setnchannels(n_channels)
                out.setsampwidth(sampwidth)
                out.setframerate(framerate)
                out.writeframes(frames)
            return bio.getvalue()
