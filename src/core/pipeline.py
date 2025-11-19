# src/core/pipeline.py
import asyncio
from typing import Callable, Optional
import numpy as np

class AudioAgentPipeline:
    def __init__(
        self,
        stt_provider,
        llm_provider,
        tts_provider,
        vad_provider,
        sample_rate: int = 16000,
        channels: int = 1
    ):
        self.stt = stt_provider
        self.llm = llm_provider
        self.tts = tts_provider
        self.vad = vad_provider
        self.sample_rate = sample_rate
        self.channels = channels
        
        self.is_running = False
        self.audio_buffer = []
        
    async def process_audio_stream(self, audio_generator):
        """Process real-time audio stream"""
        self.is_running = True
        
        async for audio_data in audio_generator:
            if not self.is_running:
                break
                
            # VAD detection
            if await self.vad.is_speech(audio_data):
                self.audio_buffer.extend(audio_data)
                
                # Check if speech ended
                if await self.vad.speech_ended():
                    transcript = await self.stt.transcribe(np.array(self.audio_buffer))
                    if transcript:
                        response = await self.llm.generate(transcript)
                        audio_output = await self.tts.synthesize(response)
                        yield audio_output
                    
                    self.audio_buffer.clear()
    
    def stop(self):
        self.is_running = False