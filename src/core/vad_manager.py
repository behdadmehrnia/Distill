# src/core/vad_manager.py
import webrtcvad
import numpy as np

import logging
logger = logging.getLogger(__name__)


class VADManager:
    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 3):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        self.speech_buffer = []
        self.silence_frames = 0
        self.speech_frames = 0
        self.silence_threshold = 20  # frames of silence to end speech
        self.min_speech_frames = 20

    def reset(self):
        self.speech_buffer = []
        self.silence_frames = 0
        self.speech_frames = 0
        
    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech"""
        # Convert to 16-bit PCM for VAD
        if audio_chunk.dtype != np.int16:
            audio_chunk = (audio_chunk * 32767).astype(np.int16)
            
        # VAD requires 10, 20, or 30 ms frames
        frame_duration = 30  # ms
        frame_size = int(self.sample_rate * frame_duration / 1000)
        
        is_speech = False
        for i in range(0, len(audio_chunk), frame_size):
            frame = audio_chunk[i:i+frame_size]
            if len(frame) < frame_size:
                break
                
            try:
                if self.vad.is_speech(frame.tobytes(), self.sample_rate):
                    self.speech_frames += 1
                    self.silence_frames = 0
                    is_speech = True
                else:
                    self.silence_frames += 1
            except:
                pass
                
        return is_speech
    
    async def speech_ended(self) -> bool:
        """Check if speech has ended based on silence"""
        return self.silence_frames >= self.silence_threshold and self.speech_frames > self.min_speech_frames

    async def is_speech_with_noise_cancellation(self, audio_chunk: np.ndarray, amplitude_threshold: float = 1000) -> bool:
        """Check if audio chunk contains speech with noise cancellation"""
        if audio_chunk.dtype != np.int16:
            audio_chunk = (audio_chunk * 32767).astype(np.int16)
            
        # VAD requires 10, 20, or 30 ms frames
        frame_duration = 30  # ms
        frame_size = int(self.sample_rate * frame_duration / 1000)
        
        for i in range(0, len(audio_chunk), frame_size):
            frame = audio_chunk[i:i+frame_size]
            logger.info(f"Frame amplitude: {np.abs(frame).mean()}")
            if len(frame) < frame_size:
                break
            try:
                if np.abs(frame).mean() > amplitude_threshold:
                    return True
            except:
                pass
        return False