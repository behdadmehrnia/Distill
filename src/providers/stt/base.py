# src/providers/stt/base.py
from abc import ABC, abstractmethod
import numpy as np
import io
import asyncio
import wave
from openai import AsyncOpenAI
from .file_cache import STTPersistentCache


# GEMINI_API = "http://164.90.187.248:8083"
GEMINI_API = ""

class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_data: np.ndarray) -> str:
        pass

class WhisperSTT(STTProvider):
    def __init__(self, api_key: str, model_name: str = "whisper-1", cache_dir: str = "./stt_cache"):
        if GEMINI_API:
            self.client = AsyncOpenAI(api_key=api_key, base_url=GEMINI_API)
        else:
            self.client = AsyncOpenAI(api_key=api_key)
        self.model = model_name
        self.cache = STTPersistentCache(cache_dir)
        
    async def transcribe(self, audio_data: np.ndarray, use_cache: bool = True) -> str:
        # Generate cache key from audio data
        cache_key = self.cache._get_cache_key(audio_data, self.model)
        
        # Check cache first
        if use_cache:
            cached_transcription = self.cache.get(cache_key)
            if cached_transcription is not None:
                print(f"STT Cache hit! Transcription: {cached_transcription[:50]}...")
                return cached_transcription
        
        # If not in cache, call API
        print(f"STT Cache miss, calling API...")
        
        # Convert numpy array to WAV format
        if audio_data.dtype != np.int16:
            if audio_data.dtype == np.float32:
                audio_data = (audio_data * 32767).astype(np.int16)
            else:
                audio_data = audio_data.astype(np.int16)
        
        # Write to BytesIO buffer as WAV format
        buffer = io.BytesIO()
        
        with wave.open(buffer, 'wb') as wav_file:
            wav_file.setnchannels(1)  # Mono
            wav_file.setsampwidth(2)  # 2 bytes for int16
            wav_file.setframerate(16000)  # 16kHz sample rate
            wav_file.writeframes(audio_data.tobytes())
        
        buffer.seek(0)
        buffer.name = "audio.wav"  # Set the filename with .wav extension
        
        # Call OpenAI Whisper API with WAV file
        response = await self.client.audio.transcriptions.create(
            model=self.model,
            file=buffer,
            language="fa"  # Optional: specify language for better accuracy
        )
        
        transcription = response.text
        
        # Store in cache
        if use_cache:
            self.cache.set(cache_key, transcription, audio_data, self.model)
            
        return transcription

    def is_persian_valid(self, text: str) -> bool:
        for char in text.lower():
            if char in "abcdefghijklmnopqrstuvwxyz":
                return False
        return True