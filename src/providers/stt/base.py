# src/providers/stt/base.py
from abc import ABC, abstractmethod
import numpy as np
import io
import asyncio
from openai import AsyncOpenAI


# GEMINI_API = "http://164.90.187.248:8083"
GEMINI_API = ""

class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_data: np.ndarray) -> str:
        pass

class WhisperSTT(STTProvider):
    def __init__(self, api_key: str, model_name: str = "whisper-1"):
        if GEMINI_API:
            self.client = AsyncOpenAI(api_key=api_key, base_url=GEMINI_API)
        else:
            self.client = AsyncOpenAI(api_key=api_key)
        self.model = model_name
        
    async def transcribe(self, audio_data: np.ndarray) -> str:
        # Convert numpy array to audio file format for API
        # First convert to int16 and then to bytes
        if audio_data.dtype != np.int16:
            if audio_data.dtype == np.float32:
                audio_data = (audio_data * 32767).astype(np.int16)
            else:
                audio_data = audio_data.astype(np.int16)
        
        # Write to BytesIO buffer as WAV format
        import wave
        buffer = io.BytesIO()
        buffer.name = "audio.wav"  # Required for OpenAI API
        
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Assuming mono
            wav_file.setsampwidth(2)  # 2 bytes for int16
            wav_file.setframerate(16000)  # Standard rate, adjust as needed
            wav_file.writeframes(audio_data.tobytes())
        
        buffer.seek(0)
        
        # Call OpenAI Whisper API
        response = await self.client.audio.transcriptions.create(
            model=self.model,
            file=buffer
        )
        
        return response.text

    def is_persian_valid(self, text: str) -> bool:
        for char in text.lower():
            if char in "abcdefghijklmnopqrstuvwxyz":
                return False
        return True
