# src/providers/llm/dify_provider.py
import aiohttp
import json
from typing import AsyncGenerator, Optional, Tuple
import logging
import time
import hashlib
import pickle
import asyncio
from dataclasses import dataclass
from datetime import datetime, timedelta
import numpy as np
from sentence_transformers import SentenceTransformer
import os

logger = logging.getLogger(__name__)

@dataclass
class CacheEntry:
    query: str
    response: str
    query_embedding: np.ndarray
    timestamp: datetime
    usage_count: int = 0

class SemanticCache:
    def __init__(self, similarity_threshold: float = 0.85, max_size: int = 1000, ttl_hours: int = 24):
        self.similarity_threshold = similarity_threshold
        self.max_size = max_size
        self.ttl = timedelta(hours=ttl_hours)
        self.cache: dict[str, CacheEntry] = {}
        self.embedding_model = None
        self._model_lock = asyncio.Lock()
        
        # Cache persistence
        self.cache_file = "semantic_cache.pkl"
        self._load_cache()
    
    async def _get_embedding_model(self):
        """Lazy load the embedding model"""
        if self.embedding_model is None:
            async with self._model_lock:
                if self.embedding_model is None:
                    try:
                        # Using a lightweight model for embeddings
                        self.embedding_model = SentenceTransformer('all-MiniLM-L6-v2')
                        logger.info("Embedding model loaded successfully")
                    except Exception as e:
                        logger.error(f"Failed to load embedding model: {e}")
                        # Fallback to a simple TF-IDF like approach
                        self.embedding_model = None
        return self.embedding_model
    
    def _compute_embedding(self, text: str) -> np.ndarray:
        """Compute embedding for text"""
        if self.embedding_model is not None:
            return self.embedding_model.encode([text])[0]
        else:
            # Fallback: simple hash-based embedding (less accurate but functional)
            return self._fallback_embedding(text)
    
    def _fallback_embedding(self, text: str) -> np.ndarray:
        """Fallback embedding using hash functions"""
        # Simple character-level frequency based embedding
        text = text.lower()
        embedding = np.zeros(128)  # Fixed size embedding
        for i, char in enumerate(text[:128]):  # Use first 128 chars
            embedding[i] = ord(char) % 256 / 255.0  # Normalize to 0-1
        return embedding
    
    def _cosine_similarity(self, vec1: np.ndarray, vec2: np.ndarray) -> float:
        """Compute cosine similarity between two vectors"""
        dot_product = np.dot(vec1, vec2)
        norm1 = np.linalg.norm(vec1)
        norm2 = np.linalg.norm(vec2)
        
        if norm1 == 0 or norm2 == 0:
            return 0.0
        
        return dot_product / (norm1 * norm2)
    
    def _clean_expired_entries(self):
        """Remove expired cache entries"""
        now = datetime.now()
        expired_keys = [
            key for key, entry in self.cache.items()
            if now - entry.timestamp > self.ttl
        ]
        for key in expired_keys:
            del self.cache[key]
        
        if expired_keys:
            logger.info(f"Cleaned up {len(expired_keys)} expired cache entries")
    
    def _enforce_size_limit(self):
        """Enforce cache size limit by removing least recently used entries"""
        if len(self.cache) > self.max_size:
            # Sort by usage count and timestamp, remove least used
            entries_to_remove = sorted(
                self.cache.items(),
                key=lambda x: (x[1].usage_count, x[1].timestamp)
            )[:len(self.cache) - self.max_size]
            
            for key, _ in entries_to_remove:
                del self.cache[key]
            
            logger.info(f"Removed {len(entries_to_remove)} entries to enforce size limit")
    
    async def get_similar_query(self, query: str) -> Optional[Tuple[str, CacheEntry]]:
        """Find similar query in cache"""
        if not self.cache:
            return None
        
        query_embedding = self._compute_embedding(query)
        best_similarity = 0.0
        best_match = None
        
        for cache_key, cache_entry in self.cache.items():
            similarity = self._cosine_similarity(query_embedding, cache_entry.query_embedding)
            
            if similarity > best_similarity and similarity >= self.similarity_threshold:
                best_similarity = similarity
                best_match = (cache_key, cache_entry)
        
        if best_match:
            logger.info(f"Found similar query in cache with similarity: {best_similarity:.3f}")
            # Update usage count and timestamp
            best_match[1].usage_count += 1
            best_match[1].timestamp = datetime.now()
        
        return best_match
    
    async def store(self, query: str, response: str):
        """Store query-response pair in cache"""
        query_embedding = self._compute_embedding(query)
        cache_key = hashlib.md5(query.encode()).hexdigest()
        
        cache_entry = CacheEntry(
            query=query,
            response=response,
            query_embedding=query_embedding,
            timestamp=datetime.now(),
            usage_count=1
        )
        
        self.cache[cache_key] = cache_entry
        
        # Clean up if needed
        self._clean_expired_entries()
        self._enforce_size_limit()
        
        # Persist cache
        self._save_cache()
        
        logger.info(f"Stored query in cache. Total cache size: {len(self.cache)}")
    
    def _save_cache(self):
        """Save cache to disk"""
        try:
            # Convert numpy arrays to lists for serialization
            cache_to_save = {}
            for key, entry in self.cache.items():
                cache_to_save[key] = CacheEntry(
                    query=entry.query,
                    response=entry.response,
                    query_embedding=entry.query_embedding.tolist(),  # Convert to list
                    timestamp=entry.timestamp,
                    usage_count=entry.usage_count
                )
            
            with open(self.cache_file, 'wb') as f:
                pickle.dump(cache_to_save, f)
            logger.debug("Cache saved to disk")
        except Exception as e:
            logger.error(f"Failed to save cache: {e}")
    
    def _load_cache(self):
        """Load cache from disk"""
        try:
            if os.path.exists(self.cache_file):
                with open(self.cache_file, 'rb') as f:
                    loaded_cache = pickle.load(f)
                
                # Convert lists back to numpy arrays
                for key, entry in loaded_cache.items():
                    if isinstance(entry.query_embedding, list):
                        entry.query_embedding = np.array(entry.query_embedding)
                
                self.cache = loaded_cache
                logger.info(f"Loaded cache from disk with {len(self.cache)} entries")
        except Exception as e:
            logger.error(f"Failed to load cache: {e}")
            self.cache = {}
    
    def get_stats(self) -> dict:
        """Get cache statistics"""
        return {
            "total_entries": len(self.cache),
            "similarity_threshold": self.similarity_threshold,
            "max_size": self.max_size,
            "ttl_hours": self.ttl.total_seconds() / 3600,
            "avg_usage_count": np.mean([entry.usage_count for entry in self.cache.values()]) if self.cache else 0
        }

class DifyLLMProvider:
    _instance = None

    def __init__(self, api_key: str = None, base_url: str = None, conversation_id: str = None):
        # self.api_key = api_key
        #self.api_key = api_key or "app-5JbtCFtDAk1eYe1TFvKppeZq"
        self.api_key = "app-xPSzCgu7xneW6Hl94d2UXPXq"
        self.base_url = base_url.rstrip('/') if base_url else "https://llm.internal.example/v1"
        self.conversation_id = conversation_id
        self.session = None
        self.timeout = aiohttp.ClientTimeout(total=30)
        
        # Initialize semantic cache
        self.cache = SemanticCache(
            similarity_threshold=0.85,  # Adjust based on your needs
            max_size=1000,
            ttl_hours=24
        )
        
        # Statistics
        self.cache_hits = 0
        self.cache_misses = 0

    @staticmethod
    def get_instance():
        if DifyLLMProvider._instance is None:
            DifyLLMProvider._instance = DifyLLMProvider(
                api_key="app-xPSzCgu7xneW6Hl94d2UXPXq", 
                base_url="https://llm.internal.example/v1"
            )
        return DifyLLMProvider._instance

    @staticmethod
    def renew_instance():
        DifyLLMProvider._instance = None
        DifyLLMProvider._instance = DifyLLMProvider(
            api_key="app-xPSzCgu7xneW6Hl94d2UXPXq", 
            base_url="https://llm.internal.example/v1"
        )

    async def ensure_session(self):
        if self.session is None:
            self.session = aiohttp.ClientSession(timeout=self.timeout)
    
    async def generate(self, message: str, use_cache: bool = False, **kwargs) -> str:
        """Send message to Dify API and get response with semantic caching"""
        logger.info(f"Dify request: {message}")
        start_time = time.time()

        # Check cache first
        if use_cache:
            cache_result = await self.cache.get_similar_query(message)
            if cache_result:
                cache_key, cache_entry = cache_result
                self.cache_hits += 1
                logger.info(f"Cache HIT for query: {message[:50]}...")
                
                end_time = time.time()
                print(f"Cache Time taken: {end_time - start_time} seconds")
                print(f"Cache stats - Hits: {self.cache_hits}, Misses: {self.cache_misses}")
                
                return cache_entry.response
        
        self.cache_misses += 1
        logger.info(f"Cache MISS for query: {message[:50]}...")

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
                
                answer = result.get('answer', 'No answer found in response')
                
                # Store in cache
                if use_cache and answer and not answer.startswith("Error:"):
                    await self.cache.store(message, answer)
                
                end_time = time.time()
                print(f"Dify Time taken: {end_time - start_time} seconds")
                print(f"Cache stats - Hits: {self.cache_hits}, Misses: {self.cache_misses}")
                
                return answer
                
        except asyncio.TimeoutError:
            logger.error("Dify API request timed out")
            return "Error: Request timed out"
        except aiohttp.ClientError as e:
            logger.error(f"Network error: {e}")
            return f"Error: Network issue - {str(e)}"
        except Exception as e:
            logger.error(f"Unexpected error: {e}")
            return f"Error: {str(e)}"

    async def generate_stream(self, message: str, use_cache: bool = True, **kwargs) -> AsyncGenerator[str, None]:
        """Stream response from Dify API with cache support"""
        # For streaming, we can only use cache for complete responses
        if use_cache:
            cache_result = await self.cache.get_similar_query(message)
            if cache_result:
                cache_key, cache_entry = cache_result
                self.cache_hits += 1
                logger.info(f"Cache HIT for streaming query: {message[:50]}...")
                yield cache_entry.response
                return
        
        self.cache_misses += 1
        
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
            
            full_response = ""
            
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
                                    chunk = json_data['answer']
                                    full_response += chunk
                                    yield chunk
                            except:
                                continue
            
            # Store complete response in cache
            if use_cache and full_response and not full_response.startswith("Error:"):
                await self.cache.store(message, full_response)
                
        except Exception as e:
            logger.error(f"Stream error: {e}")
            yield f"Error: {str(e)}"

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
        api_key="app-5JbtCFtDAk1eYe1TFvKppeZq",
        base_url="https://llm.internal.example/v1"
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
