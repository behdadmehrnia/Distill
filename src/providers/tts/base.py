# src/providers/tts/base.py
from abc import ABC, abstractmethod
import numpy as np

class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> np.ndarray:
        pass

# Example with Coqui TTS
class TTS(TTSProvider):
    def __init__(self, model_name: str = "tts_models/en/ljspeech/tacotron2-DDC"):
        pass
        
    async def synthesize(self, text: str) -> np.ndarray:
        API_KEY = "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"

        url = "https://api.openai.com/v1/audio/speech"

        headers = {
            "Authorization": f"Bearer {API_KEY}",
            "Content-Type": "application/json"
        }

        payload = {
            "model": "gpt-4o-mini-tts",   # or "gpt-4o-realtime" etc.
            "voice": "alloy",             # available voices: alloy, verse, horizon...
            "input": text,
            "format": "mp3"               # mp3, wav, pcm...
        }

        response = requests.post(url, headers=headers, json=payload)

        # Save output file
        output_path = "speech.mp3"
        with open(output_path, "wb") as f:
            f.write(response.content)

        return response.content

    def symthesize(self):
        audio_data = open("speech_shimmer.mp3", "rb").read()
        return audio_data
