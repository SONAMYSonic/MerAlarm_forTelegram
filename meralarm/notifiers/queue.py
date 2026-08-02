"""전송 큐.

메신저마다 속도 제한이 있다. 감시 루프가 전송을 직접 기다리면 알림이 몰릴 때
폴링까지 같이 느려지므로, 큐에 넣고 별도 작업이 일정한 간격으로 흘려보낸다.

채널이 여럿이면 같은 알림을 모두에게 보낸다. **하나라도 성공하면 본 것으로
기록한다** — 전부 성공해야 기록하게 하면 디스코드가 잠깐 죽었을 때 텔레그램으로
같은 알림이 계속 다시 간다.
"""

import asyncio
import logging

from ..alerts import Alert

SEND_GAP_SEC = 1.1

log = logging.getLogger(__name__)


class SendQueue:
    def __init__(self, notifiers: list, max_pending: int = 500) -> None:
        self._notifiers = notifiers
        self._queue: asyncio.Queue[Alert] = asyncio.Queue(maxsize=max_pending)
        self.sent = 0
        self.dropped = 0

    @property
    def channels(self) -> list[str]:
        return [n.name for n in self._notifiers]

    def put(self, alert: Alert) -> None:
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            # 큐가 넘칠 정도면 이미 뭔가 잘못된 상태다. 메모리를 지키는 쪽을 택한다.
            self.dropped += 1
            log.error("전송 큐가 가득 차 메시지를 버렸습니다 (누적 %d건)", self.dropped)

    @property
    def pending(self) -> int:
        return self._queue.qsize()

    async def run(self) -> None:
        while True:
            alert = await self._queue.get()
            try:
                targets = [
                    n for n in self._notifiers if alert.only in (None, n.name)
                ]
                if not targets:
                    log.warning("보낼 채널이 없습니다: only=%s", alert.only)
                    continue

                results = []
                for notifier in targets:
                    try:
                        results.append(await notifier.send(alert))
                    except Exception:
                        log.exception("%s 전송 중 예외", notifier.name)
                        results.append(False)

                if any(results):
                    self.sent += 1
                    if alert.on_sent is not None:
                        alert.on_sent()
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
