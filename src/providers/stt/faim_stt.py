import asyncio
import wave
import io
import numpy as np
import requests
from abc import ABC, abstractmethod
import logging
import time
import os

from .file_cache import STTPersistentCache

logger = logging.getLogger(__name__)


class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_data: np.ndarray) -> str:
        pass


class WhisperSTT(STTProvider):
    def __init__(self, cache_dir: str = "./stt_cache"):
        self.cache = STTPersistentCache(cache_dir)

    async def transcribe(self, audio_data: np.ndarray, use_cache: bool = True) -> str:
        start_time = time.time()

        # # Generate cache key
        cache_key = self.cache._get_cache_key(audio_data, "faim-stt")

        # Check cache
        if use_cache:
            cached_transcription = self.cache.get(cache_key)
            if cached_transcription is not None:
                print(f"STT Cache hit! Transcription: {cached_transcription[:50]}...")
                return cached_transcription

        # Convert numpy -> int16 WAV
        if audio_data.dtype != np.int16:
            if audio_data.dtype == np.float32:
                audio_data = (audio_data * 32767).astype(np.int16)
            else:
                audio_data = audio_data.astype(np.int16)

        buffer = io.BytesIO()
        wav_file = wave.open(buffer, "wb")
        wav_file.setnchannels(1)
        wav_file.setsampwidth(2)
        wav_file.setframerate(16000)
        wav_file.writeframes(audio_data.tobytes())
        wav_file.close()

        buffer.seek(0)
        wav_file_name = "audio.wav"

        files = {
            "audio": (wav_file_name, buffer, "audio/webm"),
            "language": (None, "fa"),
            "model_name": (None, "small"),
            "device": (None, "mobile"),
        }


        response = await asyncio.to_thread(
            requests.post,
            "https://api.faim-group.ir/api/upload/",
            files=files 
        )

        # Validate response
        try:
            data = response.json()
        except Exception:
            logger.error(f"Eboo invalid JSON response: {response.text}")
            return None

        file_token = data.get("job_id")
        if not file_token:
            logger.error("No FileToken received")
            return None

        transcription = await self._get_result(file_token)

        if transcription and use_cache:
            self.cache.set(cache_key, transcription, audio_data, "whisper")

        end_time = time.time()
        print(f"STT Time taken: {end_time - start_time} seconds")

        return transcription

    async def _get_result(self, file_token: str) -> str:

        max_retries = 20
        retry_delay = 0.3

        for attempt in range(max_retries):

            response = await asyncio.to_thread(
                requests.get,
                f"https://api.faim-group.ir/api/status/{file_token}",
            )

            try:
                data = response.json()
            except Exception:
                logger.error(f"Invalid JSON on attempt {attempt + 1}: {response.text}")
                await asyncio.sleep(retry_delay)
                continue

            status = data.get("status")

            if status == "done":
                
                response = await asyncio.to_thread(
                    requests.get,
                    f"https://api.faim-group.ir/api/result/{file_token}",
                )

                try:
                    data = response.json()
                    return data.get("text", "")
                except Exception:
                    logger.error(f"Invalid JSON on attempt {attempt + 1}: {response.text}")
                    await asyncio.sleep(retry_delay)
                    continue

            if status == "transcribing":
                await asyncio.sleep(retry_delay)
                continue

            logger.error(f"Transcription failed: {status}")
            # return None

        logger.error(f"Max retries exceeded for fileToken={file_token}")
        return None

    def is_persian_valid(self, text: str) -> bool:
        if not text:
            return False
        for char in text.lower():
            if char in "abcdefghijklmnopqrstuvwxyz":
                return False
        return True





async def test():
    stt = WhisperSTT()
    with open("speech.mp3", "rb") as f:
        audio_data = f.read()
    transcription = await stt.transcribe(audio_data)
    print(f"Transcription: {transcription}")



if __name__ == "__main__":
    asyncio.run(test())
