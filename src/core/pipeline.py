# src/core/pipeline.py
import asyncio
from typing import Callable, Optional
import numpy as np

class AudioAgentPipeline:
    def __init__(
        self,
        stt_provider,
        tts_provider,
        sample_rate: int = 16000,
        channels: int = 1
    ):
        self.stt = stt_provider
        self.tts = tts_provider
        self.sample_rate = sample_rate
        self.channels = channels
        
        self.is_running = False
        self.audio_buffer = []

        self.waiting_audio = None
        self.waiting_audio_duration = 0
    
    def set_waiting_audio(self, audio_base64, audio_duration):
        self.waiting_audio = audio_base64
        self.waiting_audio_duration = audio_duration
        
    
    
    def stop(self):
        self.is_running = False