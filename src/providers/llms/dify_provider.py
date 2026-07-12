import aiohttp
import asyncio
import json
import time
import logging
from typing import Optional

logger = logging.getLogger(__name__)

class SemanticCache:
    # Placeholder for your SemanticCache implementation
    def __init__(self, similarity_threshold=0.85, max_size=1000, ttl_hours=24):
        self.cache = {}
        self.similarity_threshold = similarity_threshold
        self.max_size = max_size
        self.ttl_hours = ttl_hours
    
    def get(self, key):
        return self.cache.get(key)
    
    def set(self, key, value):
        self.cache[key] = value
    
    def get_stats(self):
        return {"total_entries": len(self.cache)}

class DifyLLMProvider:
    def __init__(self, api_key: str = None, base_url: str = None, conversation_id: str = None):
        self.api_key = api_key or "app-opT28oNpIOpDiC7EmUUa0Qyy"
        self.base_url = "http://localhost:8070/v1"
        self.conversation_id = conversation_id
        self.session = None
        self.timeout = aiohttp.ClientTimeout(total=60)
        
        # Initialize semantic cache
        self.cache = SemanticCache(
            similarity_threshold=0.85,
            max_size=1000,
            ttl_hours=24
        )
        
        # Statistics
        self.cache_hits = 0
        self.cache_misses = 0

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
    
    async def generate(self, message: str, use_cache: bool = False, **kwargs) -> str:
        """Send message to Dify API and get response - collects streaming response into blocking mode"""
        logger.info(f"Dify request: {message}")
        start_time = time.time()

        # Check cache if enabled
        if use_cache:
            cached_response = self.cache.get(message)
            if cached_response:
                self.cache_hits += 1
                logger.info(f"Cache hit for: {message[:50]}...")
                end_time = time.time()
                print(f"Dify Time taken (cached): {end_time - start_time} seconds")
                return cached_response
            self.cache_misses += 1

        try:
            await self.ensure_session()
            logger.info(f"Dify session created: {self.session}")

            payload = {
                "inputs": {},
                "query": message,
                "response_mode": "streaming",  # Keep streaming mode since your Dify only works in streaming
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
            
            logger.info(f"Sending streaming request to: {self.base_url}/chat-messages")
            
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
                
                full_answer = ""
                
                # Collect all streaming chunks into a single response
                async for line in response.content:
                    if not line:
                        continue
                    
                    try:
                        # Dify sends data in SSE format: "data: {...}\n\n"
                        line_str = line.decode('utf-8').strip()
                        
                        if line_str.startswith('data: '):
                            data_str = line_str[6:]  # Remove 'data: ' prefix
                            
                            if data_str == '[DONE]':
                                break
                            
                            try:
                                data = json.loads(data_str)
                                
                                # Extract the answer chunk
                                if 'answer' in data:
                                    full_answer += data['answer']
                                
                                # Update conversation ID if present
                                if 'conversation_id' in data:
                                    self.conversation_id = data['conversation_id']
                                    
                                # Check for errors
                                if 'error' in data:
                                    logger.error(f"Stream error: {data['error']}")
                                    return f"Error: {data['error']}"
                                    
                            except json.JSONDecodeError as e:
                                logger.warning(f"Failed to parse JSON: {data_str}, error: {e}")
                                continue
                                
                    except Exception as e:
                        logger.error(f"Error processing stream chunk: {e}")
                        continue
                
                end_time = time.time()
                print(f"Dify Time taken: {end_time - start_time} seconds")
                
                # Cache the response if caching is enabled
                if use_cache and full_answer:
                    self.cache.set(message, full_answer)
                
                return full_answer if full_answer else "No answer found in response"
                
        except asyncio.TimeoutError:
            logger.error("Dify API request timed out")
            return "Error: Request timed out"
        except aiohttp.ClientError as e:
            logger.error(f"Network error: {e}")
            return f"Error: Network issue - {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return f"Error: {str(e)}"

    def get_cache_stats(self) -> dict:
        """Get cache statistics"""
        cache_stats = self.cache.get_stats()
        cache_stats.update({
            "cache_hits": self.cache_hits,
            "cache_misses": self.cache_misses,
            "hit_ratio": self.cache_hits / (self.cache_hits + self.cache_misses) if (self.cache_hits + self.cache_misses) > 0 else 0
        })
        return cache_stats

    async def clear_cache(self):
        """Clear the semantic cache"""
        self.cache.cache.clear()
        self.cache_hits = 0
        self.cache_misses = 0
        logger.info("Semantic cache cleared")

    async def close(self):
        """Close the session"""
        if self.session:
            await self.session.close()
            self.session = None



async def test():
    llm = DifyLLMProvider(
        api_key="app-M1i9mNZeDYUpYbZRgm4rG8Vw",
        base_url="https://difyv2.internal.example/v1"
    )
    
    try:
        print("Testing semantic cache...")
        
        # First request (will miss cache)
        print("First request...")
        response1 = await llm.generate("سلام باتری ماشینم خراب شده چه کار کنم؟")
        print(f"Response 1: {response1}")
        
        # Similar request (should hit cache)
        print("\nSimilar request...")
        response2 = await llm.generate("سلام ماشینم روشن نمیشه، احتمالا باتری مشکل داره، چیکار کنم؟")
        print(f"Response 2: {response2}")
        
        # Different request (should miss cache)
        print("\nDifferent request...")
        response3 = await llm.generate("آب و هوای تهران چطوره؟")
        print(f"Response 3: {response3}")
        
        # Print cache statistics
        stats = llm.get_cache_stats()
        print(f"\nCache Statistics: {stats}")
        
    except Exception as e:
        print(f"Test failed: {e}")
    finally:
        await llm.close()

if __name__ == "__main__":
    # Enable debug logging
    logging.basicConfig(level=logging.INFO)
    asyncio.run(test())
