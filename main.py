# main.py
import asyncio
from src.core.pipeline import AudioAgentPipeline

from src.providers.stt.part_stt import WhisperSTT
# from src.providers.stt.base import WhisperSTT

# from src.providers.stt.faim_stt import WhisperSTT
from src.providers.tts.base import TTS
from src.providers.llms.dify_provider import DifyLLMProvider

import logging
import sys

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)

logger = logging.getLogger(__name__)

async def main():
    try:
        from src.websocket.server import AudioAgentServer
        from src.core.pipeline import AudioAgentPipeline
        
        # Import providers with fallbacks
        try:
            # stt = WhisperSTT(api_key="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")
            stt = WhisperSTT()
            logger.info("Loaded Whisper STT")
        except ImportError as e:
            logger.warning(f"Whisper not available: {e}")
            # Create a mock STT provider for testing
            class MockSTT:
                async def transcribe(self, audio_data):
                    return "This is a test transcription."
            stt = MockSTT()
            
        try:
            tts = TTS()
            logger.info("Loaded Coqui TTS")
        except ImportError as e:
            logger.warning(f"Coqui TTS not available: {e}")
            # Create a mock TTS provider for testing
            class MockTTS:
                async def synthesize(self, text):
                    import numpy as np
                    # Generate a simple sine wave as test audio
                    duration = 2.0  # seconds
                    sample_rate = 22050
                    t = np.linspace(0, duration, int(sample_rate * duration))
                    audio = 0.3 * np.sin(2 * np.pi * 440 * t)  # 440 Hz sine wave
                    return audio
            tts = MockTTS()

        pipeline = AudioAgentPipeline(stt, tts)
        
        # Start the combined server
        server = AudioAgentServer(pipeline, http_port=8020)
        await server.start()
        
    except ImportError as e:
        logger.error(f"Import error: {e}")
        logger.info("Please install required dependencies: pip install aiohttp websockets numpy")
    except Exception as e:
        logger.error(f"Failed to start application: {e}")
        raise

if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("Application stopped by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        sys.exit(1)
