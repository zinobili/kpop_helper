import threading
import time

from app.rate_limiter import RateLimiter


def test_allows_burst_up_to_the_cap_immediately():
    rl = RateLimiter(max_calls=3, period_seconds=2.0)
    start = time.monotonic()
    for _ in range(3):
        rl.acquire()
    assert time.monotonic() - start < 0.5


def test_blocks_once_the_cap_is_reached_within_the_window():
    rl = RateLimiter(max_calls=2, period_seconds=1.0)
    start = time.monotonic()
    rl.acquire()
    rl.acquire()
    rl.acquire()  # 3rd call must wait ~1s for the window to clear
    elapsed = time.monotonic() - start
    assert elapsed >= 0.9


def test_thread_safe_under_concurrent_callers():
    # 10 threads all fighting for a 4-calls/1s budget - none should be admitted early, and
    # all must eventually get through without deadlocking or double-admitting past the cap.
    rl = RateLimiter(max_calls=4, period_seconds=0.5)
    call_times = []
    lock = threading.Lock()

    def worker():
        rl.acquire()
        with lock:
            call_times.append(time.monotonic())

    threads = [threading.Thread(target=worker) for _ in range(10)]
    start = time.monotonic()
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5)

    assert len(call_times) == 10
    # No two overlapping 0.5s windows should ever contain more than 4 admitted calls.
    sorted_times = sorted(t - start for t in call_times)
    for i in range(len(sorted_times) - 4):
        assert sorted_times[i + 4] - sorted_times[i] >= 0.45
