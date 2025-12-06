import torch
import numpy as np
from collections import deque

class VADManager:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.1):  # Much lower threshold
        self.model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True
        )
        self.get_speech_timestamps = utils[0]
        self.sample_rate = sample_rate
        self.threshold = threshold
        
        # More sensitive state management
        self.speech_buffer = []
        self.consecutive_silence = 0
        self.consecutive_speech = 0
        self.silence_threshold = 30  # frames
        self.min_speech_frames = 3   # Very low minimum
        
        # Window-based detection for better sensitivity
        self.detection_window = deque(maxlen=5)
        
    def reset(self):
        self.speech_buffer = []
        self.consecutive_silence = 0
        self.consecutive_speech = 0
        self.detection_window.clear()

    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech with window-based detection"""
        if len(audio_chunk) == 0:
            return False
            
        # Convert to tensor
        audio_tensor = torch.from_numpy(audio_chunk).float()
        
        # Get speech timestamps with very sensitive settings
        speech_timestamps = self.get_speech_timestamps(
            audio_tensor, 
            self.model, 
            sampling_rate=self.sample_rate,
            threshold=self.threshold,
            min_speech_duration_ms=50,     # Very short speech detection
            min_silence_duration_ms=100,   # Very short silence requirement
            speech_pad_ms=20               # Minimal padding
        )
        
        # Window-based detection
        current_detection = len(speech_timestamps) > 0
        self.detection_window.append(current_detection)
        
        # Consider speech if any detection in recent window
        window_speech = any(self.detection_window)
        
        if window_speech:
            self.consecutive_speech += 1
            self.consecutive_silence = 0
            print(f"✅ Speech detected! Consecutive: {self.consecutive_speech}")
            return True
        else:
            self.consecutive_silence += 1
            self.consecutive_speech = 0
            # print(f"❌ Silence - Consecutive: {self.consecutive_silence}")
            return False

    async def speech_ended(self) -> bool:
        """Check if speech has ended"""
        return self.consecutive_silence >= self.silence_threshold