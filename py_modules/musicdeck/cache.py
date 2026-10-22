"""
The one in-memory cache every part of the backend uses: entries expire after a while, and past a
size limit the least recently used go first, so nothing grows for as long as the Deck stays on.
"""
import time
from collections import OrderedDict
from typing import Any, Callable, Generic, Hashable, Optional, TypeVar

V = TypeVar("V")
_MISSING = object()


class Cache(Generic[V]):
    def __init__(self, ttl: float, size: int):
        self.ttl = ttl
        self.size = size
        self._entries: OrderedDict[Hashable, tuple[float, V]] = OrderedDict()  # key -> (expires at, value)

    def get(self, key: Hashable, default: Any = None) -> Any:
        """The value, if it's there and fresh (and it becomes the most recently used); else `default`."""
        entry = self._entries.get(key)
        if entry is None:
            return default
        if entry[0] <= time.time():
            del self._entries[key]
            return default
        self._entries.move_to_end(key)
        return entry[1]

    def __contains__(self, key: Hashable) -> bool:
        return self.get(key, _MISSING) is not _MISSING

    def put(self, key: Hashable, value: V, ttl: Optional[float] = None) -> V:
        """Keeps a value for the cache's ttl, or this entry's own (a signed URL's remaining life)."""
        self._entries[key] = (time.time() + (self.ttl if ttl is None else ttl), value)
        self._entries.move_to_end(key)
        while len(self._entries) > self.size:
            self._entries.popitem(last=False)
        return value

    def pop(self, key: Hashable) -> None:
        self._entries.pop(key, None)

    def drop(self, matching: Callable[[Any], bool]) -> None:
        """Forgets every entry whose key matches (what a change made stale)."""
        for key in [k for k in self._entries if matching(k)]:
            del self._entries[key]

    def clear(self) -> None:
        self._entries.clear()

    def __len__(self) -> int:
        return len(self._entries)
