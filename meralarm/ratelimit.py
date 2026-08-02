"""전역 요청 간격 제어.

키워드마다 주기를 따로 두면 우연히 여러 개가 같은 순간에 몰릴 수 있다. 키워드가
몇 개든 전체 요청 사이의 최소 간격을 보장해 초당 요청량이 선형으로 늘어나는 것을
막는다.
"""

import asyncio
import time


class RateLimiter:
    def __init__(self, min_gap_sec: float) -> None:
        self._min_gap = max(0.0, min_gap_sec)
        self._next_allowed = 0.0
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            wait = self._next_allowed - time.monotonic()
            if wait > 0:
                await asyncio.sleep(wait)
            self._next_allowed = time.monotonic() + self._min_gap
