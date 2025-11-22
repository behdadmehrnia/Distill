

import aiohttp
import numpy as np
import hashlib
from aiohttp import TCPConnector
from abc import ABC, abstractmethod
from file_cache import TTSPersistentCache
import asyncio


class TTS:
    def __init__(self, 
                 cache_dir: str = "./tts_cache"):
        self.api_key = "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"
        self.session = None
        self.cache = TTSPersistentCache(cache_dir)
        self.model = "gpt-4o-mini-tts"
        
    async def get_session(self):
        if self.session is None:
            connector = TCPConnector(limit=10, limit_per_host=5)
            timeout = aiohttp.ClientTimeout(total=30)
            self.session = aiohttp.ClientSession(connector=connector, timeout=timeout)
        return self.session
        
    async def synthesize(self, text: str, voice: str = "alloy", use_cache: bool = True) -> np.ndarray:
        # Generate cache key
        cache_key = self.cache._get_cache_key(text, voice, self.model)
        
        # Check cache first
        if use_cache:
            cached_audio = self.cache.get(cache_key)
            if cached_audio is not None:
                print(f"Cache hit for: {text[:50]}...")
                return cached_audio
        
        # If not in cache, call API
        print(f"Cache miss, calling API for: {text[:50]}...")
        session = await self.get_session()
        
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "voice": voice,
            "input": text,
            "format": "mp3"
        }
        
        async with session.post("https://api.openai.com/v1/audio/speech", 
                              headers=headers, json=payload) as response:
            if response.status == 200:
                audio_data = await response.read()
                
                # Store in cache
                if use_cache:
                    self.cache.set(cache_key, audio_data, text, voice, self.model)
                    
                return audio_data
            else:
                error_text = await response.text()
                raise Exception(f"TTS API error: {response.status} - {error_text}")
                
    async def close(self):
        if self.session:
            await self.session.close()



async def test_tts():
    tts = TTS()
    audio_data = await tts.synthesize("سلام من باتری ماشینم خراب شده و نمیدونم چکار کنم")
    print(audio_data)



if __name__ == "__main__":
    asyncio.run(test_tts())