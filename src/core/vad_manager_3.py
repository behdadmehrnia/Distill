import torch
import numpy as np
from collections import deque
import logging

logger = logging.getLogger(__name__)

class VADManager:
    def __init__(self, sample_rate: int = 16000):
        self.model, _ = torch.hub.load(
            repo_or_dir='snakers4/silero-vad',
            model='silero_vad',
            force_reload=False,
            trust_repo=True,
            onnx=False
        )
        self.model.eval()
        self.sample_rate = sample_rate

        # --- Core thresholds ---
        self.SPEECH_THRESHOLD = 0.5       # prob above = speech
        self.SILENCE_THRESHOLD = 0.35     # prob below = silence (hysteresis gap)
        
        # --- Timing (in chunks, each chunk ~64ms at 16kHz/1024 samples) ---
        self.MIN_SPEECH_CHUNKS = 3        # must see speech for 3 chunks before triggering
        self.SILENCE_CHUNKS_TO_END = 10   # 10 chunks of silence = end of speech (~640ms)
        self.PRE_BUFFER_SIZE = 3          # keep 3 chunks before speech starts

        # --- State ---
        self.is_speaking = False
        self.speech_chunk_count = 0
        self.silence_chunk_count = 0
        self.pre_buffer = deque(maxlen=self.PRE_BUFFER_SIZE)
        self._speech_ended_flag = False

        # --- Noise calibration ---
        self.noise_probs = deque(maxlen=30)   # rolling noise floor
        self.calibrated = False
        self.calibration_chunks = 0

    def reset(self):
        self.model.reset_states()
        self.is_speaking = False
        self.speech_chunk_count = 0
        self.silence_chunk_count = 0
        self.pre_buffer.clear()
        self._speech_ended_flag = False

    def _to_tensor(self, audio_chunk: np.ndarray) -> torch.Tensor:
        audio = audio_chunk.copy().astype(np.float32)
        
        # Mono
        if audio.ndim > 1:
            audio = audio.mean(axis=-1)
        audio = audio.squeeze()

        # Normalize only if very quiet — NO tanh distortion
        peak = np.max(np.abs(audio))
        if 0 < peak < 0.1:
            audio = audio / peak * 0.3  # gentle normalize, cap at 0.3
        
        return torch.from_numpy(audio).float()

    def _get_prob(self, tensor: torch.Tensor) -> float:
        """Get raw speech probability from Silero"""
        with torch.no_grad():
            prob = self.model(tensor, self.sample_rate).item()
        return prob

    def _calibrate_noise(self, prob: float):
        """Collect noise floor during first ~2 seconds"""
        self.noise_probs.append(prob)
        self.calibration_chunks += 1
        if self.calibration_chunks >= 20:
            self.calibrated = True
            noise_floor = np.percentile(list(self.noise_probs), 85)
            # Dynamically raise threshold above noise floor
            self.SPEECH_THRESHOLD = min(max(noise_floor + 0.15, 0.45), 0.65)
            self.SILENCE_THRESHOLD = self.SPEECH_THRESHOLD - 0.15
            logger.info(f"VAD calibrated — noise floor: {noise_floor:.2f}, "
                       f"threshold: {self.SPEECH_THRESHOLD:.2f}")

    async def is_speech(self, audio_chunk: np.ndarray) -> bool:
        """
        Returns True if currently in a speech segment.
        Call this on every chunk.
        """
        self._speech_ended_flag = False

        if len(audio_chunk) == 0:
            return False

        tensor = self._to_tensor(audio_chunk)
        prob = self._get_prob(tensor)

        # Calibrate noise floor on startup
        if not self.calibrated:
            self._calibrate_noise(prob)
            self.pre_buffer.append(audio_chunk)
            return False

        logger.debug(f"VAD prob: {prob:.3f} | speaking: {self.is_speaking}")

        if not self.is_speaking:
            self.pre_buffer.append(audio_chunk)

            if prob >= self.SPEECH_THRESHOLD:
                self.speech_chunk_count += 1
                if self.speech_chunk_count >= self.MIN_SPEECH_CHUNKS:
                    # Confirmed speech start
                    self.is_speaking = True
                    self.silence_chunk_count = 0
                    logger.info(f"🗣 Speech started (prob={prob:.2f})")
            else:
                self.speech_chunk_count = 0  # reset if gap in speech onset

            return self.is_speaking

        else:  # currently speaking
            if prob >= self.SILENCE_THRESHOLD:
                # Still speech (hysteresis: lower bar to stay in speech)
                self.silence_chunk_count = 0
                return True
            else:
                # Possible silence
                self.silence_chunk_count += 1
                if self.silence_chunk_count >= self.SILENCE_CHUNKS_TO_END:
                    self.is_speaking = False
                    self.speech_chunk_count = 0
                    self._speech_ended_flag = True
                    logger.info(f"🔇 Speech ended after "
                               f"{self.silence_chunk_count} silence chunks")
                return True  # still return True until fully confirmed ended

    async def speech_ended(self) -> bool:
        return self._speech_ended_flag

    def get_pre_buffer(self) -> list:
        """Returns pre-speech audio to avoid clipped word starts"""
        return list(self.pre_buffer)
