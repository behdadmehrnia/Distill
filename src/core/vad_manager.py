import webrtcvad
import numpy as np
from collections import deque
import time

class VADManager:
    def __init__(self, sample_rate: int = 16000, aggressiveness: int = 2):
        self.vad = webrtcvad.Vad(aggressiveness)
        self.sample_rate = sample_rate
        
        # Buffers and state
        self.speech_buffer = []
        self.silence_frames = 0
        self.speech_frames = 0
        self.consecutive_speech_frames = 0
        self.consecutive_silence_frames = 0
        
        # Configurable thresholds
        self.silence_threshold = 60  # Increased from 50 to 60
        self.min_speech_frames = 5  # Increased from 20 to 25
        self.activation_threshold = 0  # Need consecutive speech frames to activate
        self.amplitude_threshold = 8000  # Lowered from 13000
        
        # Noise profiling
        self.noise_level = None
        self.noise_learning_frames = 10
        self.noise_frames_collected = 0
        self.is_noise_profiled = False
        
        # History for better decision making
        self.speech_history = deque(maxlen=10)
        self.amplitude_history = deque(maxlen=20)
        
        # Frame configuration
        self.frame_duration = 30  # ms
        self.frame_size = int(self.sample_rate * self.frame_duration / 1000)

    def reset(self):
        """Reset all state variables"""
        self.speech_buffer = []
        self.silence_frames = 0
        self.speech_frames = 0
        self.consecutive_speech_frames = 0
        self.consecutive_silence_frames = 0
        self.speech_history.clear()
        self.amplitude_history.clear()
        self.is_noise_profiled = False
        self.noise_level = None
        self.noise_frames_collected = 0

    def _calculate_amplitude_threshold(self, audio_chunk: np.ndarray) -> float:
        """Calculate dynamic amplitude threshold based on noise level"""
        if len(audio_chunk) == 0:
            return self.amplitude_threshold
            
        current_amplitude = np.abs(audio_chunk).mean()
        self.amplitude_history.append(current_amplitude)
        
        if not self.is_noise_profiled:
            # Learning phase - collect noise samples
            if self.noise_level is None:
                self.noise_level = current_amplitude
            else:
                # Smooth noise level estimation
                self.noise_level = 0.98 * self.noise_level + 0.02 * current_amplitude
            
            self.noise_frames_collected += 1
            if self.noise_frames_collected >= self.noise_learning_frames:
                self.is_noise_profiled = True
                # Set threshold just above noise level - more permissive
                # For 16-bit audio, typical speech is in the 1000-30000 range
                noise_based_threshold = self.noise_level * 1.8  # Reduced from 2.5 to 1.8

                print(f"Noise based threshold: {noise_based_threshold:.1f}")


                # Set a reasonable minimum threshold that won't filter out quiet speech
                self.amplitude_threshold = max(300, noise_based_threshold)  # Reduced min from 3000 to 800
                print(f"Noise profiling complete: noise_level={self.noise_level:.1f}, amplitude_threshold={self.amplitude_threshold:.1f}")
        
        # Allow dynamic adjustment even after profiling
        elif len(self.amplitude_history) >= 5:
            # If we consistently see higher amplitudes, adjust threshold
            recent_avg = np.mean(list(self.amplitude_history)[-5:])
            if recent_avg > self.noise_level * 3:  # Significant increase in levels
                self.amplitude_threshold = max(800, self.noise_level * 1.5)
        
        return self.amplitude_threshold

    def _analyze_spectral_characteristics(self, frame: np.ndarray) -> bool:
        """Analyze spectral characteristics to distinguish speech from noise"""
        if len(frame) == 0:
            return False
            
        # Calculate spectral features
        fft = np.fft.rfft(frame)
        magnitude = np.abs(fft)
        freq_bins = np.fft.rfftfreq(len(frame), 1.0/self.sample_rate)
        
        # Speech typically has more energy in 300-3400Hz range
        speech_band_mask = (freq_bins >= 300) & (freq_bins <= 3400)
        full_band_mask = (freq_bins >= 0) & (freq_bins <= 8000)
        

        if np.sum(full_band_mask) == 0:
            return False
            
        # Calculate spectral centroid (brightness)
        if np.sum(magnitude) > 0:
            spectral_centroid = np.sum(freq_bins * magnitude) / np.sum(magnitude)
        else:
            spectral_centroid = 0

        # Speech has higher spectral centroid than most noise
        speech_energy_ratio = np.sum(magnitude[speech_band_mask]) / np.sum(magnitude[full_band_mask])
        
        # Speech-like characteristics
        return (spectral_centroid > 500 and 
                spectral_centroid < 2500 and 
                speech_energy_ratio > 0.3)

    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """Check if audio chunk contains speech with improved noise rejection"""
        if audio_chunk.dtype != np.int16:
            audio_chunk = (audio_chunk * 32767).astype(np.int16)
        
        frame_speech_count = 0
        total_frames = 0
        
        # Calculate dynamic threshold
        amplitude_threshold = self._calculate_amplitude_threshold(audio_chunk)
        
        for i in range(0, len(audio_chunk), self.frame_size):
            frame = audio_chunk[i:i+self.frame_size]
            if len(frame) < self.frame_size:
                break
                
            total_frames += 1
            
            try:
                # Multi-condition speech detection
                vad_result = self.vad.is_speech(frame.tobytes(), self.sample_rate)
                amplitude_ok = np.abs(frame).mean() > amplitude_threshold
                spectral_ok = self._analyze_spectral_characteristics(frame)
                
                # Require multiple conditions for speech detection
                if vad_result and amplitude_ok and spectral_ok:
                    frame_speech_count += 1
                    self.consecutive_speech_frames += 1
                    self.consecutive_silence_frames = 0
                else:
                    self.consecutive_silence_frames += 1
                    self.consecutive_speech_frames = 0
                    
            except Exception:
                pass
        
        # Update state based on frame analysis
        if total_frames > 0:
            speech_ratio = frame_speech_count / total_frames
            self.speech_history.append(speech_ratio > 0.5)
            
            # Only count as speech if we have consistent detection
            if (self.consecutive_speech_frames >= self.activation_threshold and 
                frame_speech_count > 0):
                self.speech_frames += 1
                self.silence_frames = 0

                return True
            else:
                self.silence_frames += 1
                return False
        
        return False

    async def speech_ended(self) -> bool:
        """Check if speech has ended based on extended silence"""
        # More conservative end-of-speech detection
        speech_ended = (self.silence_frames >= self.silence_threshold and 
                       self.speech_frames > self.min_speech_frames)


        # Additional check: if we had very little speech, don't trigger
        if speech_ended and self.speech_frames < self.min_speech_frames * 1.5:
            return False
            
        return speech_ended

    async def get_speech_confidence(self) -> float:
        """Get confidence level of current speech detection"""
        if len(self.speech_history) == 0:
            return 0.0
        return sum(self.speech_history) / len(self.speech_history)

    async def is_speech_with_noise_cancellation(self, audio_chunk: np.ndarray) -> bool:
        """Legacy method for backward compatibility"""
        return await self.is_speech(audio_chunk)