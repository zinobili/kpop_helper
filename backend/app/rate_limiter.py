import threading
import time


class RateLimiter:
    """Blocks callers so no more than max_calls occur in any trailing period_seconds window.

    Thread-safe: shared across job threads, since the rate limit is per API key/project, not
    per job - two videos processing concurrently must still stay under one combined cap.
    """

    def __init__(self, max_calls: int, period_seconds: float = 60.0):
        self.max_calls = max_calls
        self.period_seconds = period_seconds
        self._call_times: list[float] = []
        self._lock = threading.Lock()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.monotonic()
                self._call_times = [t for t in self._call_times if now - t < self.period_seconds]
                if len(self._call_times) < self.max_calls:
                    self._call_times.append(now)
                    return
                sleep_time = self.period_seconds - (now - self._call_times[0]) + 0.05
            time.sleep(max(sleep_time, 0.05))
