"""전송 큐.

메신저마다 속도 제한이 있다. 감시 루프가 전송을 직접 기다리면 알림이 몰릴 때
폴링까지 같이 느려지므로, 큐에 넣고 별도 작업이 일정한 간격으로 흘려보낸다.

채널이 여럿이면 같은 알림을 모두에게 보낸다. **하나라도 성공하면 본 것으로
기록한다** — 전부 성공해야 기록하게 하면 디스코드가 잠깐 죽었을 때 텔레그램으로
같은 알림이 계속 다시 간다.

알림마다 결과를 꼭 하나 돌려준다. 보냈으면 `on_sent`, 전부 실패했거나 대기열이
넘쳐 버렸으면 `on_failed`. 스케줄러는 그 사이 그 상품을 "보내는 중"으로 두고 다시
넣지 않는다. 결과를 안 알려주면 그 상품은 영영 보내는 중으로 남는다.
"""

import asyncio
import logging

from ..alerts import Alert
from .base import Notifier

SEND_GAP_SEC = 1.1

log = logging.getLogger(__name__)


def _settle(callback) -> None:
    """결과 콜백을 부른다. 콜백이 터져도 전송 작업은 이어간다."""
    if callback is None:
        return
    try:
        callback()
    except Exception:
        log.exception("전송 결과를 처리하다 예외가 발생했습니다")


class SendQueue:
    def __init__(self, notifiers: list[Notifier], max_pending: int = 500) -> None:
        self._notifiers = notifiers
        self._queue: asyncio.Queue[Alert] = asyncio.Queue(maxsize=max_pending)
        self.sent = 0
        self.dropped = 0

    @property
    def channels(self) -> list[str]:
        return [n.name for n in self._notifiers]

    @property
    def notifiers(self) -> list[Notifier]:
        """종료할 때 정리하려고 꺼내 본다."""
        return list(self._notifiers)

    def add(self, notifier: Notifier) -> None:
        """채널을 나중에 붙인다.

        디스코드 봇은 명령 코어를 들고 있어야 하는데, 그 코어는 스케줄러를,
        스케줄러는 이 큐를 필요로 한다. 그래서 봇만 큐보다 늦게 만들어진다.
        """
        self._notifiers.append(notifier)

    def put(self, alert: Alert) -> None:
        try:
            self._queue.put_nowait(alert)
        except asyncio.QueueFull:
            # 큐가 넘칠 정도면 이미 뭔가 잘못된 상태다. 메모리를 지키는 쪽을 택한다.
            self.dropped += 1
            log.error("전송 큐가 가득 차 메시지를 버렸습니다 (누적 %d건)", self.dropped)
            _settle(alert.on_failed)

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
                    _settle(alert.on_failed)
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
                    _settle(alert.on_sent)
                else:
                    _settle(alert.on_failed)
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
