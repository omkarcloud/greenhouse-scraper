"""Response cache hooks used by the greenhouse package.

The hosted API keeps responses in a database; this package keeps them in
memory for the life of the process (greenhouse/fetch.py already memoizes a
board's job list for 10 minutes), so calls simply run. `storage()` is an
in-memory store, which is what lets New & Closed Jobs report the jobs that
CLOSED since your previous call for the same company."""
import threading
import time


def cached_call(namespace, data, fn, ttl, cache=True, refresh=False):
    """Run fn(**data). Same signature as the hosted cache."""
    return fn(**data)


class _MemoryStore:
    def __init__(self):
        self._rows = {}
        self._lock = threading.Lock()

    @staticmethod
    def _key(namespace, data):
        return namespace, repr(sorted((data or {}).items()))

    def get(self, namespace, data):
        with self._lock:
            row = self._rows.get(self._key(namespace, data))
        if not row or row[0] < time.time():
            return None
        return {"data": row[1]}

    def put(self, namespace, data, result, ttl):
        seconds = ttl.total_seconds() if hasattr(ttl, "total_seconds") else float(ttl)
        with self._lock:
            self._rows[self._key(namespace, data)] = (time.time() + seconds, result)


_store = _MemoryStore()


def storage():
    return _store
