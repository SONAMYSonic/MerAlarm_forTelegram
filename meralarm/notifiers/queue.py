"""전송 큐.

텔레그램은 같은 채팅방에 초당 약 1건 제한이 있다. 감시 루프가 전송을 직접
기다리면 알림이 몰릴 때 폴링까지 같이 느려지므로, 큐에 넣고 별도 작업이
일정한 간격으로 흘려보낸다.
"""

import asyncio
import logging

from .telegram import Message, TelegramNotifier

SEND_GAP_SEC = 1.1

log = logging.getLogger(__name__)


class SendQueue:
    def __init__(self, notifier: TelegramNotifier, max_pending: int = 500) -> None:
        self._notifier = notifier
        self._queue: asyncio.Queue[Message] = asyncio.Queue(maxsize=max_pending)
        self.sent = 0
        self.dropped = 0

    def put(self, message: Message) -> None:
        try:
            self._queue.put_nowait(message)
        except asyncio.QueueFull:
            # 큐가 넘칠 정도면 이미 뭔가 잘못된 상태다. 메모리를 지키는 쪽을 택한다.
            self.dropped += 1
            log.error("전송 큐가 가득 차 메시지를 버렸습니다 (누적 %d건)", self.dropped)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def run(self) -> None:
        while True:
            message = await self._queue.get()
            try:
                if await self._notifier.send(message):
                    self.sent += 1
                    if message.on_sent is not None:
                        message.on_sent()
            except Exception:
                log.exception("전송 작업에서 예외가 발생했습니다")
            finally:
                self._queue.task_done()
            await asyncio.sleep(SEND_GAP_SEC)

    async def drain(self, timeout: float = 30) -> None:
        """종료 전에 남은 메시지를 최대한 내보낸다."""
        try:
            await asyncio.wait_for(self._queue.join(), timeout)
        except asyncio.TimeoutError:
            log.warning("전송 대기 중인 %d건을 남기고 종료합니다", self.pending)
