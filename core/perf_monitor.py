"""
core/perf_monitor.py - Lightweight performance timing for MayaVC.
Thread-safe singleton. Use @perf_timed or with perf_scope().
"""

import time
import functools
import threading

# ---------------------------------------------------------------------------
# Global singleton
# ---------------------------------------------------------------------------

class PerfMonitor:
    __slots__ = ("_records", "_lock", "_enabled")

    def __init__(self):
        self._records = {}   # label -> list of elapsed_ms
        self._lock = threading.Lock()
        self._enabled = True

    # -- config --

    @property
    def enabled(self):
        return self._enabled

    @enabled.setter
    def enabled(self, v):
        self._enabled = bool(v)

    # -- record a timing --

    def record(self, label: str, elapsed_ms: float):
        if not self._enabled:
            return
        with self._lock:
            self._records.setdefault(label, []).append(elapsed_ms)

    # -- read stats --

    def stats(self):
        """Return list of dicts: label, count, last_ms, avg_ms, min_ms, max_ms, total_ms.
        Sorted by total_ms descending (slowest first)."""
        with self._lock:
            result = []
            for label, times in self._records.items():
                n = len(times)
                if n == 0:
                    continue
                total = sum(times)
                result.append({
                    "label": label,
                    "count": n,
                    "last_ms": round(times[-1], 2),
                    "avg_ms": round(total / n, 2),
                    "min_ms": round(min(times), 2),
                    "max_ms": round(max(times), 2),
                    "total_ms": round(total, 2),
                })
            result.sort(key=lambda d: d["total_ms"], reverse=True)
            return result

    def clear(self):
        with self._lock:
            self._records.clear()

    def snapshot(self):
        """Return a copy of all records, then clear. Useful for export."""
        with self._lock:
            data = dict(self._records)
            self._records.clear()
            return data


# Singleton instance
_perf = PerfMonitor()


def get_perf():
    return _perf


# ---------------------------------------------------------------------------
# Public API: context manager
# ---------------------------------------------------------------------------

class perf_scope:
    """Context manager: with perf_scope("git_add"): ..."""
    __slots__ = ("_label", "_t0")

    def __init__(self, label: str):
        self._label = label
        self._t0 = None

    def __enter__(self):
        self._t0 = time.perf_counter()
        return self

    def __exit__(self, *args):
        if self._t0 is not None:
            ms = (time.perf_counter() - self._t0) * 1000.0
            _perf.record(self._label, ms)


# ---------------------------------------------------------------------------
# Public API: decorator
# ---------------------------------------------------------------------------

def perf_timed(label: str = None):
    """Decorator: @perf_timed() or @perf_timed("my_label").

    Usage:
        @perf_timed("git_add")
        def _git(args, cwd, ...): ...

        @perf_timed()
        def get_history(scenes_dir): ...
    """
    def deco(func):
        name = label or func.__name__

        @functools.wraps(func)
        def wrapper(*args, **kwargs):
            t0 = time.perf_counter()
            try:
                return func(*args, **kwargs)
            finally:
                ms = (time.perf_counter() - t0) * 1000.0
                _perf.record(name, ms)

        return wrapper
    return deco


# ---------------------------------------------------------------------------
# Convenience: show the perf panel
# ---------------------------------------------------------------------------

def show_perf_panel():
    """Open the performance monitor panel in Maya."""
    from ui.perf_panel import show as _show
    _show()
