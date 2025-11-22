# src/providers/stt/sahab_stt.py
import aiohttp
import hashlib
from .file_cache import STTPersistentCache

API_ENDPOINT = "https://api.sahab.ai/v1/transcribe"
API_TOKEN = "YOUR_API_TOKEN"

class SahabFileSTT:
    def __init__(self, cache_dir: str = "./stt_cache"):
        self.endpoint = API_ENDPOINT
        self.token = API_TOKEN
        self.cache = STTPersistentCache(cache_dir)
        self.session = None
        
    async def transcribe(self, file_path: str, use_cache: bool = True) -> str:
        """Transcribe directly from audio file path"""
        import hashlib
        
        # Generate cache key from file content
        with open(file_path, 'rb') as f:
            file_content = f.read()
            cache_key = hashlib.sha256(file_content).hexdigest()
        
        # Check cache
        if use_cache:
            cached_transcription = self.cache.get(cache_key)
            if cached_transcription is not None:
                print(f"STT Cache hit! Transcription: {cached_transcription[:50]}...")
                return cached_transcription
        
        # Call API
        session = await self.get_session()
        data = aiohttp.FormData()
        data.add_field('file', 
                      open(file_path, 'rb'),
                      filename='audio.wav',
                      content_type='audio/wav')
        data.add_field('language', 'fa')
        
        headers = {'gateway-token': self.token}
        
        async with session.post(self.endpoint, data=data, headers=headers) as response:
            if response.status == 200:
                result = await response.json()
                transcription = result.get('text', '') or result.get('transcription', '')
                
                if use_cache:
                    self.cache.set(cache_key, transcription, file_content, "sahab-file-stt")
                
                return transcription.strip()
            else:
                error_text = await response.text()
                raise Exception(f"Sahab API error: {response.status} - {error_text}")