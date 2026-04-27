import aiohttp
import hashlib
from typing import Optional
import os
import io
from pydub import AudioSegment
import tempfile
import wave
import numpy as np

class OpenAICompatibleSTT:
    """OpenAI-compatible STT wrapper for Sahab API"""
    
    def __init__(self, 
                 gap_endpoint: str = "https://api.gapgpt.app/v1/audio/transcriptions",
                 gap_token: str = "sk-REDACTED-ROTATE-ME",
                 cache_dir: str = "./stt_cache",
                 sample_rate: int = 16000):
        self.gap_endpoint = gap_endpoint
        self.gap_token = gap_token
        self.cache_dir = cache_dir
        self.sample_rate = sample_rate
        
        # Create cache directory if it doesn't exist
        os.makedirs(cache_dir, exist_ok=True)
    
    def convert_pcm_to_wav(self, pcm_content: bytes) -> bytes:
        """Convert raw PCM audio bytes to WAV format"""
        try:
            # Create WAV file in memory
            wav_buffer = io.BytesIO()
            
            # Write WAV header
            with wave.open(wav_buffer, 'wb') as wav_file:
                wav_file.setnchannels(1)  # Mono
                wav_file.setsampwidth(2)  # 16-bit = 2 bytes
                wav_file.setframerate(self.sample_rate)  # 16kHz
                wav_file.writeframes(pcm_content)
            
            return wav_buffer.getvalue()
        except Exception as e:
            raise Exception(f"Failed to convert PCM to WAV: {e}")
    
    def convert_wav_to_mp3(self, wav_content: bytes) -> bytes:
        """Convert WAV audio bytes to MP3 bytes"""
        try:
            # Load WAV from bytes
            audio = AudioSegment.from_wav(io.BytesIO(wav_content))
            
            # Convert to MP3
            mp3_buffer = io.BytesIO()
            audio.export(mp3_buffer, format="mp3", bitrate="128k")
            
            return mp3_buffer.getvalue()
        except Exception as e:
            raise Exception(f"Failed to convert WAV to MP3: {e}")
    
    def get_file_hash(self, content: bytes) -> str:
        """Generate hash for caching"""
        return hashlib.md5(content).hexdigest()
    
    async def transcribe(
        self, 
        file_content: np.ndarray,
        model: str = "gapgpt/whisper-1",
        language: Optional[str] = "fa",
        response_format: str = "json",
        temperature: float = 0.0
    ) -> str:
        """Transcribe audio - handles raw PCM, WAV, and MP3 formats"""

        audio_int16 = (file_content * 32768.0).astype(np.int16)
        file_content = audio_int16.tobytes()
        
        # Check cache first
        file_hash = self.get_file_hash(file_content)
        cache_file = os.path.join(self.cache_dir, f"{file_hash}.txt")
        
        if os.path.exists(cache_file):
            with open(cache_file, 'r', encoding='utf-8') as f:
                cached_result = f.read()
                print(f"📦 Using cached transcription")
                return cached_result.strip()
        
        # Determine audio format and convert if needed
        is_wav = False
        is_raw_pcm = False
        
        # Check for WAV header (RIFF....WAVE)
        if len(file_content) > 12 and file_content[:4] == b'RIFF' and file_content[8:12] == b'WAVE':
            is_wav = True
            print("🎵 Detected WAV format")
        else:
            # Assume it's raw PCM (from WebSocket)
            is_raw_pcm = True
            print("🎵 Detected raw PCM format - converting to WAV first")
            
            # Convert raw PCM to WAV
            wav_content = self.convert_pcm_to_wav(file_content)
            is_wav = True
            file_content = wav_content
        
        # Convert WAV to MP3 for the API
        if is_wav:
            print("🔄 Converting to MP3 for API...")
            audio_content = self.convert_wav_to_mp3(file_content)
            content_type = 'audio/mpeg'
            filename = 'audio.mp3'
        else:
            # If for some reason it's not WAV, send as is
            audio_content = file_content
            content_type = 'audio/mpeg'
            filename = 'audio.mp3'
        
        # Call Gap API
        session = aiohttp.ClientSession()
        try:
            data = aiohttp.FormData()
            data.add_field('file', 
                          audio_content,
                          filename=filename,
                          content_type=content_type)
            data.add_field('language', language or 'fa')
            data.add_field('model', model)
            
            headers = {'Authorization': f'Bearer {self.gap_token}'}
            
            print(f"📤 Sending request to {self.gap_endpoint} (size: {len(audio_content)} bytes)")
            async with session.post(self.gap_endpoint, data=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    transcription = result.get('text', '') or result.get('transcription', '')
                    
                    # Cache the result
                    with open(cache_file, 'w', encoding='utf-8') as f:
                        f.write(transcription)
                    
                    print(f"✅ Transcription: {transcription[:50]}...")
                    return transcription.strip()
                else:
                    error_text = await response.text()
                    raise Exception(f"Gap API error: {response.status} - {error_text}")
        finally:
            await session.close()

    def is_persian_valid(self, text: str) -> bool:
        """Check if text contains Persian characters (no Latin letters)"""
        if not text or not isinstance(text, str):
            return False
        
        # More robust Persian validation
        persian_chars = set("ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیئ")
        
        # Check if there's at least one Persian character
        has_persian = any(char in persian_chars for char in text)
        
        # Check if there are too many Latin characters (more than 30%)
        latin_count = sum(1 for char in text.lower() if char in "abcdefghijklmnopqrstuvwxyz")
        latin_ratio = latin_count / max(len(text), 1)
        
        # Valid if has Persian characters AND Latin characters are less than 30%
        return has_persian and latin_ratio < 0.3


async def test_stt(audio_file_path: str):
    """Simple test function for STT"""
    
    # Initialize STT client
    stt = OpenAICompatibleSTT()
    
    # Read audio file
    with open(audio_file_path, 'rb') as f:
        audio_content = f.read()
    
    print(f"📁 Testing: {audio_file_path}")
    print(f"📊 Size: {len(audio_content)} bytes")
    
    # Detect format
    if len(audio_content) > 12 and audio_content[:4] == b'RIFF' and audio_content[8:12] == b'WAVE':
        print(f"🎵 Format: WAV")
    else:
        print(f"🎵 Format: Raw PCM (assuming)")
    
    # Transcribe
    result = await stt.transcribe(audio_content, language="fa")
    
    print(f"✅ Result: {result}")
    return result