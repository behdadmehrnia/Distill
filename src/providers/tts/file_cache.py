import aiohttp
import numpy as np
import hashlib
import json
import os
import pickle
from pathlib import Path
from aiohttp import TCPConnector
from abc import ABC, abstractmethod



class TTSPersistentCache:
    def __init__(self, cache_dir: str = "./tts_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.metadata_file = self.cache_dir / "cache_metadata.json"
        self._load_metadata()
        
    def _load_metadata(self):
        """Load cache metadata from file"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {}
            
    def _save_metadata(self):
        """Save cache metadata to file"""
        with open(self.metadata_file, 'w') as f:
            json.dump(self.metadata, f, indent=2)
            
    def _get_cache_key(self, text: str, voice: str, model: str) -> str:
        """Generate unique cache key"""
        content = f"{text}_{voice}_{model}"
        return hashlib.sha256(content.encode()).hexdigest()
        
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get file path for cached audio"""
        return self.cache_dir / f"{cache_key}.mp3"
        
    def get(self, cache_key: str) -> bytes | None:
        """Get cached audio data"""
        cache_path = self._get_cache_path(cache_key)
        if cache_path.exists():
            with open(cache_path, 'rb') as f:
                return f.read()
        return None
        
    def set(self, cache_key: str, audio_data: bytes, text: str, voice: str, model: str):
        """Store audio data in cache"""
        cache_path = self._get_cache_path(cache_key)
        
        # Save audio file
        with open(cache_path, 'wb') as f:
            f.write(audio_data)
            
        # Update metadata
        self.metadata[cache_key] = {
            "text": text[:100] + "..." if len(text) > 100 else text,  # Store preview
            "voice": voice,
            "model": model,
            "size_bytes": len(audio_data),
            "created_at": np.datetime64('now').astype(str)
        }
        self._save_metadata()