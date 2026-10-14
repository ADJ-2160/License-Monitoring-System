# -*- coding: utf-8 -*-
"""
In-Memory Thread-Safe Cache Utility
"""
import time
from typing import Dict, Any, Optional

class MemoryCache:
    def __init__(self):
        self._store: Dict[str, Dict[str, Any]] = {}

    def get(self, key: str) -> Optional[Any]:
        item = self._store.get(key)
        if not item:
            return None
        if time.time() > item["expires_at"]:
            del self._store[key]
            return None
        return item["value"]

    def set(self, key: str, value: Any, ttl: int = 60):
        self._store[key] = {
            "value": value,
            "expires_at": time.time() + ttl
        }

    def clear(self):
        self._store.clear()

# Global cache singleton
cache_store = MemoryCache()
