# src/providers/tts/base.py
from abc import ABC, abstractmethod
import numpy as np
import requests


class TTSProvider(ABC):
    @abstractmethod
    async def synthesize(self, text: str) -> np.ndarray:
        pass

# Example with Coqui TTS
class TTS(TTSProvider):
    def __init__(self):
        pass
        
    async def synthesize(self, text: str) -> np.ndarray:

        url = "http://localhost:8101/v1/audio/speech"

        headers = {
            "Content-Type": "application/json"
        }

        payload = {
            "input": text,
        }

        response = requests.post(url, headers=headers, json=payload)

        # Save output file
        output_path = "speech.wav"
        with open(output_path, "wb") as f:
            f.write(response.content)

        return response.content


# if __name__ == "__main__":
#     import asyncio
#     tts = TTS()
#     audio_data = asyncio.run(tts.synthesize("سلام من باتری ماشینم خراب شده و نمیدونم چکار کنم"))
#     print(audio_data)
#     with open("speech.mp3", "wb") as f:
#         f.write(audio_data)
