import torch
import numpy as np
from collections import deque
import logging
from typing import Tuple
import time

logger = logging.getLogger(__name__)

class VADManager:
    def __init__(self, sample_rate: int = 16000, frame_duration_ms: int = 30):
        """
        Balanced VAD for speech detection
        - Sensitive enough to catch speech
        - Selective enough to ignore constant background noise
        """
        self.sample_rate = sample_rate
        self.frame_samples = (sample_rate * frame_duration_ms) // 1000
        self.frame_duration_ms = frame_duration_ms
        
        # Conservative thresholds - higher than before
        self.energy_threshold = 0.007  # Increased from 0.005
        self.zcr_threshold = 0.09     # Increased from 0.05
        
        # State management with hysteresis
        self.activity_buffer = []
        self.consecutive_silence = 0
        self.consecutive_activity = 0
        
        # More conservative detection parameters
        self.activity_start_frames = 3     # Need 3 frames (~90ms) to start speech
        self.silence_end_frames = 60       # Need 60 frames (~1800ms) to end speech
        
        # Buffers for adaptive thresholding
        self.energy_history = deque(maxlen=30)  # Longer history
        self.zcr_history = deque(maxlen=30)
        self.long_term_energy = deque(maxlen=100)  # For background estimation
        
        # Current state
        self.is_active = False
        self.activity_start_time = None
        self.last_activity_time = None
        
        # Adaptive noise floor
        self.noise_floor = 0.005
        self.noise_floor_alpha = 0.95  # Smoothing factor
        
        # For debugging
        self.activity_count = 0
        
        logger.info(f"VAD initialized - Energy threshold: {self.energy_threshold}")

    def reset(self):
        """Reset VAD state"""
        self.consecutive_silence = 0
        self.consecutive_activity = 0
        self.is_active = False
        self.activity_start_time = None
        self.last_activity_time = None
        self.activity_buffer = []

    def _calculate_energy(self, audio_chunk: np.ndarray) -> float:
        """Calculate RMS energy of audio chunk"""
        if len(audio_chunk) == 0:
            return 0.0
        return np.sqrt(np.mean(audio_chunk ** 2))

    def _calculate_zcr(self, audio_chunk: np.ndarray) -> float:
        """Calculate Zero Crossing Rate"""
        if len(audio_chunk) < 2:
            return 0.0
        signs = np.sign(audio_chunk)
        zcr = np.sum(np.abs(signs[1:] - signs[:-1])) / (2 * len(audio_chunk))
        return zcr

    def _update_noise_floor(self, energy: float, is_speech: bool):
        """Update noise floor estimate when no speech is detected"""
        if not is_speech:
            self.noise_floor = self.noise_floor_alpha * self.noise_floor + (1 - self.noise_floor_alpha) * energy
        
        self.long_term_energy.append(energy)
        
        # Adaptive threshold based on noise floor
        if len(self.long_term_energy) > 10:
            avg_energy = np.mean(self.long_term_energy)
            std_energy = np.std(self.long_term_energy)
            
            # Set threshold above noise floor with some margin
            self.energy_threshold = max(0.01, avg_energy + 2 * std_energy)

    def _has_activity(self, audio_chunk: np.ndarray) -> Tuple[bool, float, float]:
        """
        Detect significant audio activity (speech-like)
        More selective than before
        """
        if len(audio_chunk) == 0:
            return False, 0.0, 0.0
        
        # Calculate features

        #max_val = np.max(np.abs(audio_chunk))
        #if max_val > 0:
        #    audio_chunk = audio_chunk / max_val

        energy = self._calculate_energy(audio_chunk)
        zcr = self._calculate_zcr(audio_chunk)
        
        # Store in history
        self.energy_history.append(energy)
        self.zcr_history.append(zcr)
        
        # Calculate moving averages
        if len(self.energy_history) > 0:
            avg_energy = np.mean(self.energy_history)
            avg_zcr = np.mean(self.zcr_history)
        else:
            avg_energy = energy
            avg_zcr = zcr
        
        # Multiple conditions for robust detection
        has_high_energy = energy > self.energy_threshold
        has_high_zcr = zcr > self.zcr_threshold
        
        # Energy change from average (detects transients)
        energy_change = (energy - avg_energy) / (avg_energy + 1e-6)
        has_energy_spike = energy_change > 0.8  # 80% increase from average
        
        # For speech, we often see moderate ZCR (not too high, not too low)
        # Very high ZCR might be noise, very low might be silence
        has_moderate_zcr = 0.1 < zcr < 0.4
        
        # Combined decision logic
        # Primary: Energy spike or high energy
        # Secondary: Moderate ZCR (for voiced sounds)
        is_active = (has_high_energy or has_energy_spike) and (has_moderate_zcr or has_high_zcr)
        
        # Update noise floor
        self._update_noise_floor(energy, is_active)
        
        logger.debug(f"Energy: {energy:.4f}, ZCR: {zcr:.3f}, "
                    f"Noise floor: {self.noise_floor:.4f}, "
                    f"Threshold: {self.energy_threshold:.4f}, "
                    f"Active: {is_active}")
        
        return is_active, energy, zcr

    async def process_chunk(self, audio_chunk: np.ndarray) -> bool:
        """
        Process audio chunk with improved state management
        Returns: True if in speech period, False if in silence
        """
        # Ensure proper audio format
        if audio_chunk.dtype != np.float32:
            audio_chunk = audio_chunk.astype(np.float32)
        
        # Normalize
        max_val = np.max(np.abs(audio_chunk))
        if max_val > 0:
            # Gentle normalization, don't over-amplify
            audio_chunk = audio_chunk / (max_val + 0.1)  # Add small offset
        
        # Detect activity
        has_activity, energy, zcr = self._has_activity(audio_chunk)

        #logger.info(f"Energy: {energy:.4f}, ZCR: {zcr:.3f}, Activity: {has_activity}")
        
        # State machine with hysteresis
        if has_activity:
            
            self.consecutive_activity += 1
            self.consecutive_silence = 0
            self.last_activity_time = time.time()
            
            # SPEECH START: Need sustained activity
            if not self.is_active:
                if self.consecutive_activity >= self.activity_start_frames:
                    self.is_active = True
                    self.activity_start_time = self.last_activity_time
                    self.activity_count += 1
                    logger.info(f"🎤 SPEECH STARTED (after {self.consecutive_activity} frames)")
                    
        else:
            self.consecutive_silence += 1
            self.consecutive_activity = 0
            
            # SPEECH END: Need sustained silence
            if self.is_active:
                if self.consecutive_silence >= self.silence_end_frames:
                    self.is_active = False
                    if self.activity_start_time:
                        duration = time.time() - self.activity_start_time
                        logger.info(f"🔇 SPEECH ENDED ({duration:.2f}s, {self.consecutive_silence} silence frames)")
                    self.activity_start_time = None
        
        # Store audio if active
        if self.is_active:
            self.activity_buffer.append(audio_chunk)
            # Keep buffer from growing too large
            if len(self.activity_buffer) > 1000:  # ~30 seconds
                self.activity_buffer = self.activity_buffer[-500:]  # Keep last 15 seconds
        
        return self.is_active

    async def is_active_period(self) -> bool:
        """Return current activity state"""
        return self.is_active

    async def get_activity_audio(self) -> np.ndarray:
        """Get all audio from the current activity period"""
        if not self.activity_buffer:
            return np.array([], dtype=np.float32)
        
        # Concatenate all chunks
        full_audio = np.concatenate(self.activity_buffer)
        
        # Clear buffer but keep state
        self.activity_buffer = []
        
        return full_audio

    async def speech_ended(self) -> bool:
        """
        Check if speech has ended (for backward compatibility)
        Returns True if we were speaking and now have enough silence
        """
        if not self.is_active:
            return False
        
        return self.consecutive_silence >= self.silence_end_frames

    def calibrate(self, silence_audio: np.ndarray):
        """
        Calibrate VAD with a sample of background noise/silence
        """
        logger.info("Calibrating VAD with background noise...")
        
        chunk_size = self.frame_samples
        num_chunks = min(len(silence_audio) // chunk_size, 50)
        
        energies = []
        zcrs = []
        
        for i in range(num_chunks):
            start = i * chunk_size
            end = start + chunk_size
            chunk = silence_audio[start:end].astype(np.float32)
            
            # Normalize
            max_val = np.max(np.abs(chunk))
            if max_val > 0:
                chunk = chunk / max_val
            
            energies.append(self._calculate_energy(chunk))
            zcrs.append(self._calculate_zcr(chunk))
        
        # Calculate statistics
        avg_energy = np.mean(energies)
        std_energy = np.std(energies)
        avg_zcr = np.mean(zcrs)
        std_zcr = np.std(zcrs)
        
        # Set thresholds with good margin above noise
        self.energy_threshold = avg_energy + 3 * std_energy
        self.zcr_threshold = avg_zcr + 2 * std_zcr
        
        # Set minimum thresholds
        self.energy_threshold = max(self.energy_threshold, 0.015)
        self.zcr_threshold = max(self.zcr_threshold, 0.1)
        
        # Initialize noise floor
        self.noise_floor = avg_energy
        
        logger.info(f"Calibration complete:")
        logger.info(f"  Background - Energy: {avg_energy:.4f}±{std_energy:.4f}, ZCR: {avg_zcr:.3f}±{std_zcr:.3f}")
        logger.info(f"  Thresholds - Energy: {self.energy_threshold:.4f}, ZCR: {self.zcr_threshold:.3f}")
        logger.info(f"  Noise floor: {self.noise_floor:.4f}")

    def get_stats(self):
        """Get current statistics"""
        return {
            "is_active": self.is_active,
            "consecutive_activity": self.consecutive_activity,
            "consecutive_silence": self.consecutive_silence,
            "energy_threshold": self.energy_threshold,
            "zcr_threshold": self.zcr_threshold,
            "noise_floor": self.noise_floor,
            "activity_count": self.activity_count
        }

    def adjust_sensitivity(self, sensitivity: float = 0.5):
        """
        Adjust VAD sensitivity (0.0 = most sensitive, 1.0 = least sensitive)
        """
        sensitivity = max(0.0, min(1.0, sensitivity))
        
        # Base thresholds
        base_energy = 0.01
        base_zcr = 0.1
        
        # Adjust based on sensitivity
        self.energy_threshold = base_energy + (sensitivity * 0.04)  # 0.01 to 0.05
        self.zcr_threshold = base_zcr + (sensitivity * 0.15)       # 0.1 to 0.25
        
        # Adjust frame requirements
        self.activity_start_frames = max(1, int(2 + sensitivity * 5))      # 2 to 7 frames
        self.silence_end_frames = max(10, int(15 + sensitivity * 15))      # 15 to 30 frames
        
        logger.info(f"Sensitivity set to {sensitivity:.2f}")
        logger.info(f"  Energy threshold: {self.energy_threshold:.4f}")
        logger.info(f"  ZCR threshold: {self.zcr_threshold:.3f}")
        logger.info(f"  Start frames: {self.activity_start_frames}")
        logger.info(f"  End frames: {self.silence_end_frames}")
