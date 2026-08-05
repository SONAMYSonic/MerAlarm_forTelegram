"""디스코드 웹훅으로 알림을 보낸다.

웹훅은 URL 하나면 끝이라 사용자가 할 일이 가장 적다. 대신 **보내기 전용**이라
명령어는 받을 수 없다. 명령어까지 원하면 `discord_bot.py` 를 쓴다.

봇 토큰과 웹훅이 둘 다 설정돼 있으면 봇만 쓴다. 둘 다 보내면 같은 알림이 두 번
온다. 그 판단은 `__main__.py` 에서 한 번만 한다.
"""

import asyncio
import logging

import httpx

from ..alerts import Alert
from .discord_embed import build

MAX_ATTEMPTS = 3

log = logging.getLogger(__name__)


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url
        self._client = httpx.AsyncClient(timeout=30)

    def render(self, alert: Alert) -> dict:
        return {"embeds": [build(alert)]}

    async def send(self, alert: Alert) -> bool:
        payload = self.render(alert)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._client.post(self._url, json=payload)
            except httpx.HTTPError as e:
                log.warning("디스코드 전송 오류 (%d/%d): %s", attempt, MAX_ATTEMPTS, e)
                await asyncio.sleep(2**attempt)
                continue

            if response.status_code in (200, 204):
                return True

            if response.status_code == 429:
                # 디스코드는 남은 대기 시간을 초 단위 실수로 알려준다.
                try:
                    wait = float(response.json().get("retry_after", 1))
                except Exception:
                    wait = 1.0
                log.warning("디스코드 속도 제한. %.1f초 대기", wait)
                await asyncio.sleep(wait + 0.5)
                continue

            if response.status_code in (401, 403, 404):
                log.error(
                    "디스코드 웹훅이 거부됐습니다(%s). URL 이 맞는지, 웹훅을 지우지 "
                    "않았는지 확인하세요.",
                    response.status_code,
                )
                return False

            log.error("디스코드 전송 실패(%s): %s", response.status_code, response.text[:200])
            return False

        log.error("디스코드 전송을 %d회 시도 후 포기했습니다", MAX_ATTEMPTS)
        return False

    async def close(self) -> None:
        await self._client.aclose()
