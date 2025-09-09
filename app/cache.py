import time
from typing import Any, Dict, Optional, Tuple

class TTLCache:
    """
    Ultra-simple in-memory cache with per-key TTL.
    Not for clustering; perfect for a single free-tier instance.
    """
    def __init__(self, default_ttl_seconds: int = 180):
        self._store: Dict[str, Tuple[float, Any]] = {}
        self.default_ttl = default_ttl_seconds

    def get(self, key: str) -> Optional[Any]:
        rec = self._store.get(key)
        if not rec:
            return None
        expires_at, value = rec
        if time.time() > expires_at:
            # expired
            self._store.pop(key, None)
            return None
        return value

    def set(self, key: str, value: Any, ttl_seconds: Optional[int] = None) -> None:
        ttl = ttl_seconds if ttl_seconds is not None else self.default_ttl
        self._store[key] = (time.time() + ttl, value)

# Singleton cache you can import elsewhere
cache = TTLCache()
