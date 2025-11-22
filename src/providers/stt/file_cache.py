# src/providers/stt/file_cache.py
import hashlib
import json
from pathlib import Path
import numpy as np



class STTPersistentCache:
    def __init__(self, cache_dir: str = "./stt_cache"):
        self.cache_dir = Path(cache_dir)
        self.cache_dir.mkdir(exist_ok=True)
        self.metadata_file = self.cache_dir / "cache_metadata.json"
        self._load_metadata()
        
    def _load_metadata(self):
        """Load cache metadata from file"""
        if self.metadata_file.exists():
            with open(self.metadata_file, 'r', encoding='utf-8') as f:
                self.metadata = json.load(f)
        else:
            self.metadata = {}
            
    def _save_metadata(self):
        """Save cache metadata to file"""
        with open(self.metadata_file, 'w', encoding='utf-8') as f:
            json.dump(self.metadata, f, indent=2, ensure_ascii=False)
            
    def _get_cache_key(self, audio_data: np.ndarray, model: str) -> str:
        """Generate unique cache key from numpy audio data"""
        # Create hash from audio data and model
        audio_hash = hashlib.sha256(audio_data.tobytes()).hexdigest()
        model_hash = hashlib.sha256(model.encode()).hexdigest()
        return f"{audio_hash}_{model_hash}"
        
    def _get_cache_path(self, cache_key: str) -> Path:
        """Get file path for cached transcription"""
        return self.cache_dir / f"{cache_key}.txt"
        
    def get(self, cache_key: str) -> str | None:
        """Get cached transcription"""
        cache_path = self._get_cache_path(cache_key)
        if cache_path.exists():
            try:
                with open(cache_path, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except UnicodeDecodeError:
                # Fallback for encoding issues
                with open(cache_path, 'r', encoding='latin-1') as f:
                    return f.read().strip()
        return None
        
    def set(self, cache_key: str, transcription: str, audio_data: np.ndarray, model: str):
        """Store transcription in cache"""
        cache_path = self._get_cache_path(cache_key)
        
        # Save transcription file
        with open(cache_path, 'w', encoding='utf-8') as f:
            f.write(transcription)
            
        # Update metadata
        self.metadata[cache_key] = {
            "text_preview": transcription[:100] + "..." if len(transcription) > 100 else transcription,
            "model": model,
            "audio_size_bytes": len(audio_data.tobytes()),
            "audio_shape": audio_data.shape,
            "audio_dtype": str(audio_data.dtype),
            "created_at": np.datetime64('now').astype(str)
        }
        self._save_metadata()