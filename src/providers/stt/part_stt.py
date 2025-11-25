# src/providers/stt/base.py
from abc import ABC, abstractmethod
import numpy as np
import io
import asyncio
import wave
import aiohttp
import json
from .file_cache import STTPersistentCache
import time

# API Configuration
API_ENDPOINT = "https://partai.gw.isahab.ir/speechRecognition/v1/file"
API_TOKEN = "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJzeXN0ZW0iOiJzYWhhYiIsImNyZWF0ZVRpbWUiOiIxNDA0MDIzMDEzMTY1NzU3MiIsInVuaXF1ZUZpZWxkcyI6eyJ1c2VybmFtZSI6IjhmNjk4ZmFlLThlY2EtNGNlNS05ZjhhLTQzNzFkYWE2MzM1YSJ9LCJkYXRhIjp7InNlcnZpY2VJRCI6IjlmMjE1NjVjLTcxZmEtNDViMy1hZDQwLTM4ZmY2YTZjNWM2OCIsInJhbmRvbVRleHQiOiJneVhGcyJ9LCJncm91cE5hbWUiOiI3YmNiMGE4YzUxNDNhMjI1NTJjYWJlMTc0NDlkMTRlNSJ9.0YN8aKhqgXmV6ZQpSS8TpnuOD1-SEuxaWzo4ROauqFY"

class STTProvider(ABC):
    @abstractmethod
    async def transcribe(self, audio_data: np.ndarray) -> str:
        pass

class WhisperSTT(STTProvider):
    def __init__(self, cache_dir: str = "./stt_cache"):
        self.endpoint = API_ENDPOINT
        self.token = API_TOKEN
        self.cache = STTPersistentCache(cache_dir)
        self.session = None
        
    async def get_session(self):
        """Get or create aiohttp session"""
        if self.session is None:
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(timeout=timeout)
        return self.session
        
    async def transcribe(self, audio_data: np.ndarray, use_cache: bool = True) -> str:
        start_time = time.time()

        # Generate cache key from audio data
        cache_key = self.cache._get_cache_key(audio_data, "sahab-stt")
        
        # Check cache first
        if use_cache:
            cached_transcription = self.cache.get(cache_key)
            if cached_transcription is not None:
                print(f"STT Cache hit! Transcription: {cached_transcription[:50]}...")
                return cached_transcription
        
        # If not in cache, call API
        print(f"STT Cache miss, calling Sahab API...")
        
        # Convert numpy array to WAV format
        wav_buffer = self._create_wav_buffer(audio_data)
        
        # Call Sahab API with WAV file
        transcription = await self._call_sahab_api(wav_buffer)
        
        # Store in cache
        if use_cache:
            self.cache.set(cache_key, transcription, audio_data, "sahab-stt")
            
        end_time = time.time()
        print(f"STT Time taken: {end_time - start_time} seconds")
        
        return transcription

    def _create_wav_buffer(self, audio_data: np.ndarray) -> io.BytesIO:
        """Convert numpy array to WAV format in memory"""
        # Convert to int16 if needed
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
        return buffer

    async def _call_sahab_api(self, wav_buffer: io.BytesIO) -> str:
        """Call the Sahab speech recognition API"""
        session = await self.get_session()
        
        # Prepare form data
        data = aiohttp.FormData()
        data.add_field('file', 
                      wav_buffer, 
                      filename='audio.wav',
                      content_type='audio/wav')
        data.add_field('language', 'fa')
        
        headers = {
            'gateway-token': self.token
        }
        
        try:
            async with session.post(self.endpoint, data=data, headers=headers) as response:
                if response.status == 200:
                    print(f"Sahab API response: {await response.text()}")
                    result = json.loads(await response.text())
                    # Extract transcription from response
                    # You may need to adjust this based on the actual API response structure
                    transcription = result.get("data", {}).get("data", "").get("result", "")
                    if not transcription:
                        # If the structure is different, try to get the first value
                        transcription = str(result)
                    return transcription.strip()
                else:
                    error_text = await response.text()
                    raise Exception(f"Sahab API error: {response.status} - {error_text}")
                    
        except aiohttp.ClientError as e:
            raise Exception(f"Network error calling Sahab API: {e}")
        except Exception as e:
            raise Exception(f"Error processing Sahab API response: {e}")

    def is_persian_valid(self, text: str) -> bool:
        """Check if text contains only Persian characters (no English)"""
        for char in text.lower():
            if char in "abcdefghijklmnopqrstuvwxyz":
                return False
        return True

    async def close(self):
        """Close the aiohttp session"""
        if self.session:
            await self.session.close()
            self.session = None