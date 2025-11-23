# src/providers/llm/dify_provider.py
import aiohttp
import json
from typing import AsyncGenerator
import logging

logger = logging.getLogger(__name__)



class DifyLLMProvider:
    _instance = None

    def __init__(self, api_key: str, base_url: str, conversation_id: str = None):
        self.api_key = api_key
        self.base_url = base_url.rstrip('/')
        self.conversation_id = conversation_id
        self.session = None
        self.timeout = aiohttp.ClientTimeout(total=30)  # 30 second timeout

    @staticmethod
    def get_instance():
        if DifyLLMProvider._instance is None:
            DifyLLMProvider._instance = DifyLLMProvider(api_key="app-5JbtCFtDAk1eYe1TFvKppeZq", base_url="https://llm.internal.example/v1")
        return DifyLLMProvider._instance

    @staticmethod
    def renew_instance():
        DifyLLMProvider._instance = None
        DifyLLMProvider._instance = DifyLLMProvider(api_key="app-5JbtCFtDAk1eYe1TFvKppeZq", base_url="https://llm.internal.example/v1")

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
            
    async def generate(self, message: str, **kwargs) -> str:
        """Send message to Dify API and get response"""
        logger.info(f"Dify request: {message}")

        try:
            await self.ensure_session()
            logger.info(f"Dify session created: {self.session}")

            payload = {
                "inputs": {},
                "query": message,
                "response_mode": "blocking",
                "user": "audio_agent",
                "files": [
                    {                 
                        "type": "image",
                        "transfer_method": "remote_url",
                        "url": "https://cloud.dify.ai/logo/logo-site.png"
                    }
                ]
            }
            
            if self.conversation_id:
                payload["conversation_id"] = self.conversation_id
                
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            logger.info(f"Sending request to: {self.base_url}/chat-messages")
            
            async with self.session.post(
                f"{self.base_url}/chat-messages",
                json=payload,
                headers=headers
            ) as response:
                logger.info(f"Response status: {response.status}")
                
                if response.status != 200:
                    error_text = await response.text()
                    logger.error(f"Dify API error {response.status}: {error_text}")
                    return f"Error: API returned status {response.status}"
                
                result = await response.json()
                logger.info(f"Dify response received: {result.keys() if isinstance(result, dict) else 'Not a dict'}")
                
                # Update conversation ID for continuous conversation
                if 'conversation_id' in result:
                    self.conversation_id = result['conversation_id']
                    
                return result.get('answer', 'No answer found in response')
                
        except asyncio.TimeoutError:
            logger.error("Dify API request timed out")
            return "Error: Request timed out"
        except aiohttp.ClientError as e:
            logger.error(f"Network error: {e}")
            return f"Error: Network issue - {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return f"Error: {str(e)}"

    async def generate_stream(self, message: str, **kwargs) -> AsyncGenerator[str, None]:
        """Stream response from Dify API"""
        try:
            await self.ensure_session()
            
            payload = {
                "inputs": {},
                "query": message,
                "response_mode": "streaming",
                "user": "audio_agent",
                **kwargs
            }
            
            headers = {
                "Authorization": f"Bearer {self.api_key}",
                "Content-Type": "application/json"
            }
            
            async with self.session.post(
                f"{self.base_url}/chat-messages",
                json=payload,
                headers=headers
            ) as response:
                async for line in response.content:
                    if line.startswith(b'data: '):
                        data = line[6:].strip()
                        if data and data != b'[DONE]':
                            try:
                                json_data = json.loads(data)
                                if 'answer' in json_data:
                                    yield json_data['answer']
                            except:
                                continue
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"Error: {str(e)}"

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None


import asyncio

async def test():
    llm = DifyLLMProvider(
        api_key="app-5JbtCFtDAk1eYe1TFvKppeZq",
        base_url="https://llm.internal.example/v1"
    )
    
    try:
        print("Sending request to Dify...")
        response = await llm.generate("سلام باتری ماشینم خراب شده چه کار کنم؟")
        print(f"Response: {response}")
    except Exception as e:
        print(f"Test failed: {e}")
    finally:
        await llm.close()

if __name__ == "__main__":
    # Enable debug logging
    logging.basicConfig(level=logging.DEBUG)
    asyncio.run(test())