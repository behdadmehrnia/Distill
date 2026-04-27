import aiohttp
import hashlib
from typing import Optional
import os

class OpenAICompatibleSTT:
    """OpenAI-compatible STT wrapper for Sahab API"""
    
    def __init__(self, 
                 gap_endpoint: str = "https://api.gapgpt.app/v1/audio/transcriptions",
                 gap_token: str = "sk-REDACTED-ROTATE-ME",
                 cache_dir: str = "./stt_cache"):
        self.gap_endpoint = gap_endpoint
        self.gap_token = gap_token
        
    async def transcribe(
        self, 
        file_content: bytes,
        model: str = "gapgpt/whisper-1",
        language: Optional[str] = "fa",
        response_format: str = "json",
        temperature: float = 0.0
    ) -> dict:
        """OpenAI-compatible transcription method"""
        
        # Call Gap API
        session = aiohttp.ClientSession()
        try:
            data = aiohttp.FormData()
            data.add_field('file', 
                          file_content,
                          filename='audio.wav',
                          content_type='audio/wav')
            data.add_field('language', language or 'fa')
            data.add_field('model', model)
            
            headers = {'Authorization': f'Bearer {self.gap_token}'}
            
            async with session.post(self.gap_endpoint, data=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    transcription = result.get('text', '') or result.get('transcription', '')
                    
                    return {"text": transcription.strip()}
                else:
                    error_text = await response.text()
                    raise Exception(f"Gap API error: {response.status} - {error_text}")
        finally:
            await session.close()


async def test_stt(audio_file_path: str):
    """Simple test function for STT"""
    
    # Initialize STT client
    stt = OpenAICompatibleSTT()
    
    # Read audio file
    with open(audio_file_path, 'rb') as f:
        audio_content = f.read()
    
    print(f"📁 Testing: {audio_file_path}")
    print(f"📊 Size: {len(audio_content)} bytes")
    
    # Transcribe
    result = await stt.transcribe(audio_content, language="fa")
    
    print(f"✅ Result: {result}")
    return result




# if __name__ == "__main__":
#     import asyncio
#     asyncio.run(test_stt("./test-9.wav"))