"""Bounded optional media telemetry cache; failures and missing paths never persist."""
from collections import OrderedDict
from copy import deepcopy
from functools import wraps
import os
from threading import RLock


def cached_media(maxsize=128):
    def decorate(probe):
        cache = OrderedDict()
        lock = RLock()
        @wraps(probe)
        def get(path):
            try:
                absolute = os.path.normcase(os.path.abspath(path))
                stat = os.stat(absolute)
                key = (absolute, stat.st_mtime_ns, stat.st_size, stat.st_ctime_ns, os.environ.get('PATH', ''))
            except (OSError, ValueError, TypeError):
                return probe(path)
            with lock:
                if key in cache:
                    cache.move_to_end(key)
                    return deepcopy(cache[key])
            value = probe(path)
            if value is not None:
                with lock:
                    # Remove obsolete versions of this path as well as LRU overflow.
                    for old in list(cache):
                        if old[0] == absolute:
                            cache.pop(old)
                    cache[key] = deepcopy(value)
                    while len(cache) > maxsize:
                        cache.popitem(last=False)
            return value
        def clear():
            with lock:
                cache.clear()
        get.cache_clear = clear
        return get
    return decorate
