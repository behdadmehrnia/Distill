import torch
import numpy as np
from collections import deque
import logging

logger = logging.getLogger(__name__)

class VADManager:
    def __init__(self, sample_rate: int = 16000, threshold: float = 0.7):
        # Load Silero VAD using torch.hub
        self.model, utils = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True,
            onnx=False  # Ensure we use PyTorch model
        )
        
        # Store the utility function
        self.get_speech_timestamps = utils[0]
        self.model.eval()  # Set model to evaluation mode
        
        self.sample_rate = sample_rate
        self.threshold = threshold
        
        # State management
        self.speech_buffer = []
        self.consecutive_silence = 0
        self.consecutive_speech = 0
        self.silence_threshold = 50
        self.min_speech_frames = 200
        
        # Window-based detection
        self.detection_window = deque(maxlen=5)
        
    def reset(self):
        self.speech_buffer = []
        self.consecutive_silence = 0
        self.consecutive_speech = 0
        self.detection_window.clear()

    def _prepare_audio_tensor(self, audio_chunk: np.ndarray) -> torch.Tensor:
        """
        Properly convert numpy array to tensor format expected by Silero VAD
        """
        # Make a copy to avoid modifying original
        audio = audio_chunk.copy()
        
        # Ensure float32
        if audio.dtype != np.float32:
            audio = audio.astype(np.float32)
        
        # Handle multi-dimensional arrays - convert to mono
        if len(audio.shape) > 1:
            if audio.shape[0] < audio.shape[-1]:  # (channels, samples)
                audio = audio.mean(axis=0)
            else:  # (samples, channels)
                audio = audio.mean(axis=1)
        
        # Ensure it's 1D
        audio = audio.squeeze()
        
        # --- IMPROVED GAIN CONTROL ---
        current_peak = np.max(np.abs(audio))
        
        if current_peak > 0:
            # Target a reasonable peak level for speech
            target_peak = 0.5  # 50% of full scale
            
            if current_peak < target_peak * 0.8:  # If significantly below target
                # Calculate required gain (with safety margin)
                required_gain = target_peak / current_peak
                
                # Apply gain
                audio = audio * required_gain
                
                # Gentle compression to prevent harsh clipping
                audio = np.tanh(audio)
        
        # Convert to tensor
        audio_tensor = torch.from_numpy(audio).float()
        
        # Ensure tensor is contiguous
        if not audio_tensor.is_contiguous():
            audio_tensor = audio_tensor.contiguous()
        
        return audio_tensor

    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech with window-based detection"""
        if len(audio_chunk) == 0:
            return False

        #print(f"silence {self.consecutive_silence}")
        #print(f"activity {self.consecutive_speech}")

            
        try:
            # Properly prepare the audio tensor
            audio_tensor = self._prepare_audio_tensor(audio_chunk)


            chunk_duration_ms = (len(audio_chunk) / self.sample_rate) * 1000


            speech_timestamps = self.get_speech_timestamps(
                audio_tensor,
                self.model,
                sampling_rate=self.sample_rate,
                threshold=0.3, # Lowered threshold
                min_speech_duration_ms=int(chunk_duration_ms * 0.5), # Scale to chunk size
                min_silence_duration_ms=self.silence_threshold,
                return_seconds=False
            )
            
            # Check if any speech was detected
            current_detection = len(speech_timestamps) > 0
            
            # if current_detection:
            #     # Debug info when speech is detected
            #     print(f"✅ Speech detected! Timestamps: {speech_timestamps}")
            
        except Exception as e:
            print(f"VAD Error: {type(e).__name__}: {e}")
            print(f"Audio chunk shape: {audio_chunk.shape}, dtype: {audio_chunk.dtype}")
            
            # Fallback to simple energy-based VAD
            energy = np.sum(audio_chunk.astype(np.float32) ** 2) / max(len(audio_chunk), 1)
            current_detection = energy > 0.0015  # Adjust this threshold
            print(f"Fallback VAD - Energy: {energy:.6f}, Detection: {current_detection}")

        # Update detection window
        self.detection_window.append(current_detection)
        
        # Consider speech if any detection in recent window
        window_speech = any(self.detection_window) > 0
        
        if window_speech:
            self.consecutive_speech += 1
            self.consecutive_silence = 0
            # print(f"activity {self.consecutive_speech}")
            return True
        else:
            self.consecutive_silence += 1
            self.consecutive_speech = 0
            # print(f"silence {self.consecutive_silence}")
            return False

    async def speech_ended(self) -> bool:
        """Check if speech has ended"""
        return self.consecutive_silence >= self.silence_threshold
        
        
import re
from collections import Counter

def is_valid_persian(text: str) -> bool:
    """
    Lightweight validation for Persian text.
    Returns False if text is junk/repetitive, True if valid.
    """
    if not text or len(text) < 3:
        return False
    
    # Remove extra spaces
    text = ' '.join(text.split())
    words = text.split()
    
    # Too few words
    if len(words) < 2:
        return False
    
    # Quick repetition check
    if len(words) >= 3:
        # Check if same word repeated too much
        word_counts = Counter(words)
        max_repeat = max(word_counts.values())
        if max_repeat / len(words) > 0.4:  # >40% same word
            return False
        
        # Check unique word ratio
        if len(set(words)) / len(words) < 0.3:
            return False
    
    # Common junk patterns (compiled for speed)
    junk_patterns = re.compile(
        r'(از اینجای\s*از اینجای)|'  # Repeated phrase
        r'(\S+\s+\S+)(\s+\1){2,}|'    # Repeated 2-word pattern
        r'^(\S)\s+\1\s+\1|'            # Same char repeated at start
        r'[\u0600-\u06FF]{1,2}\s+[\u0600-\u06FF]{1,2}\s+$'  # Short syllables at end
    )
    
    if junk_patterns.search(text):
        return False
    
    # Check for Persian characters (optimized)
    persian_chars = set('ابپتثجچحخدذرزژسشصضطظعغفقکگلمنوهیآ')
    persian_count = sum(1 for c in text if c in persian_chars)
    
    # Needs at least some Persian characters
    if persian_count < 3:
        return False
    
    return True

# Even more optimized version for batch processing
def is_valid_persian_fast(text: str) -> bool:
    """Ultra-fast version with minimal checks."""
    if not text or len(text) < 5:
        return False
    
    # Quick check for obvious junk
    junk = ['از اینجای', 'چیجوی', 'هاست']  # Known junk patterns
    
    # Fast check without regex if possible
    text_lower = text.lower()
    for j in junk:
        if text_lower.count(j) > 2:  # Repeated too many times
            return False
    
    # Split once
    words = text.split()
    if len(words) < 2:
        return False
    
    # Fast repetition check using set
    if len(words) >= 5:
        if len(set(words)) / len(words) < 0.3:
            return False
    
    return True

