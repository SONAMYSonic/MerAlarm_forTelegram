"""감시 루프를 바깥에서 조작하기 위한 손잡이.

트레이 아이콘은 별도 스레드에서 돌고 감시 루프는 asyncio 로 돈다. 둘 사이를
오가는 신호는 이 객체 하나로 모은다. 스레드에서 asyncio 객체를 직접 건드리면
안 되므로 정지 신호만 이벤트 루프에 넘겨 예약한다.
"""

import asyncio
import threading
import time
from dataclasses import dataclass, field


@dataclass
class Controls:
    # 일시정지. 감시 루프가 매 회차 확인한다.
    _paused: threading.Event = field(default_factory=threading.Event)
    # 시한부 일시정지의 해제 시각(monotonic). None 이면 무기한.
    _resume_at: float | None = None
    # 종료. asyncio 쪽에서만 기다린다.
    _stop: asyncio.Event | None = None
    _loop: asyncio.AbstractEventLoop | None = None

    def bind(self, loop: asyncio.AbstractEventLoop) -> None:
        """감시 루프가 시작될 때 자기 이벤트 루프를 등록한다."""
        self._loop = loop
        self._stop = asyncio.Event()

    @property
    def paused(self) -> bool:
        return self._paused.is_set()

    def pause(self, seconds: float | None = None) -> None:
        """seconds 를 주면 그만큼 지난 뒤 자동으로 재개된다."""
        self._resume_at = time.monotonic() + seconds if seconds else None
        self._paused.set()

    def resume(self) -> None:
        self._resume_at = None
        self._paused.clear()

    @property
    def resume_in(self) -> float | None:
        """자동 재개까지 남은 초. 무기한이거나 정지 중이 아니면 None."""
        if not self.paused or self._resume_at is None:
            return None
        return max(0.0, self._resume_at - time.monotonic())

    def check_auto_resume(self) -> bool:
        """시한이 다 됐으면 재개한다. 실제로 재개했으면 True."""
        if self.paused and self._resume_at is not None and time.monotonic() >= self._resume_at:
            self.resume()
            return True
        return False

    def toggle_pause(self) -> bool:
        if self.paused:
            self.resume()
        else:
            self.pause()
        return self.paused

    def stop(self) -> None:
        """다른 스레드에서 불러도 안전하다."""
        if self._loop is not None and self._stop is not None:
            self._loop.call_soon_threadsafe(self._stop.set)

    async def wait_stop(self) -> None:
        assert self._stop is not None, "bind() 를 먼저 불러야 한다"
        await self._stop.wait()
