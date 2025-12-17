import torch
import numpy as np
from collections import deque

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
        self.silence_threshold = 250
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
        # Ensure float32
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32)
        
        # Handle multi-dimensional arrays
        if len(audio_chunk.shape) > 1:
            # If shape is (channels, samples) or (samples, channels)
            if audio_chunk.shape[0] == 1 or audio_chunk.shape[0] == 2:
                # (channels, samples) -> convert to mono
                audio_chunk = audio_chunk.mean(axis=0)
            elif audio_chunk.shape[1] == 1 or audio_chunk.shape[1] == 2:
                # (samples, channels) -> convert to mono
                audio_chunk = audio_chunk.mean(axis=1)
        
        # Ensure it's 1D
        audio_chunk = audio_chunk.squeeze()
        
        # Normalize to [-1, 1] if needed
        max_val = np.max(np.abs(audio_chunk))
        if max_val > 1.0:
            audio_chunk = audio_chunk / max_val
        
        # Convert to tensor and ensure it's contiguous
        audio_tensor = torch.from_numpy(audio_chunk.copy()).float()
        
        # The model expects a 1D tensor with shape (n_samples,)
        # Not (1, n_samples) or (n_samples, 1)
        audio_tensor = audio_tensor.squeeze()
        
        # Ensure the tensor is contiguous in memory
        if not audio_tensor.is_contiguous():
            audio_tensor = audio_tensor.contiguous()
        
        return audio_tensor

    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech with window-based detection"""
        if len(audio_chunk) == 0:
            return False
            
        try:
            # Properly prepare the audio tensor
            audio_tensor = self._prepare_audio_tensor(audio_chunk)
            
            # Debug: Check tensor properties
            # print(f"Tensor shape: {audio_tensor.shape}, dtype: {audio_tensor.dtype}, max: {audio_tensor.max():.3f}, min: {audio_tensor.min():.3f}")
            
            # Get speech timestamps - NOTE: Parameter order is (audio, model, ...)
            speech_timestamps = self.get_speech_timestamps(
                audio_tensor,           # Audio tensor first
                self.model,             # Model second
                sampling_rate=self.sample_rate,
                threshold=self.threshold,
                min_speech_duration_ms=self.min_speech_frames,
                min_silence_duration_ms=self.silence_threshold,
                speech_pad_ms=20,
                return_seconds=False    # Return samples, not seconds
            )
            
            # Check if any speech was detected
            current_detection = len(speech_timestamps) > 0
            
            if current_detection:
                # Debug info when speech is detected
                print(f"✅ Speech detected! Timestamps: {speech_timestamps}")
            
        except Exception as e:
            print(f"VAD Error: {type(e).__name__}: {e}")
            print(f"Audio chunk shape: {audio_chunk.shape}, dtype: {audio_chunk.dtype}")
            
            # Fallback to simple energy-based VAD
            energy = np.sum(audio_chunk.astype(np.float32) ** 2) / max(len(audio_chunk), 1)
            current_detection = energy > 0.0005  # Adjust this threshold
            print(f"Fallback VAD - Energy: {energy:.6f}, Detection: {current_detection}")

        # Update detection window
        self.detection_window.append(current_detection)
        
        # Consider speech if any detection in recent window
        window_speech = any(self.detection_window)
        
        if window_speech:
            self.consecutive_speech += 1
            self.consecutive_silence = 0
            return True
        else:
            self.consecutive_silence += 1
            self.consecutive_speech = 0
            return False

    async def speech_ended(self) -> bool:
        """Check if speech has ended"""
        return self.consecutive_silence >= self.silence_threshold