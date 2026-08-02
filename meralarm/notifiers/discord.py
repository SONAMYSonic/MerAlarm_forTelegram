"""디스코드 웹훅으로 알림을 보낸다.

웹훅은 URL 하나면 끝이라 사용자가 할 일이 가장 적다. 대신 **보내기 전용**이라
명령어는 받을 수 없다. 명령어까지 하려면 상시 연결을 유지하는 봇이 필요하다.

임베드로 보내면 썸네일과 항목을 칸으로 나눠 보여줄 수 있어, 이 용도에는 텔레그램
메시지보다 읽기 편하다.
"""

import asyncio
import logging

import httpx

from .. import alerts
from ..alerts import Alert
from ..models import Item

MAX_ATTEMPTS = 3
# 임베드 왼쪽 띠 색. 종류를 색으로 구분하면 목록에서 훑기 쉽다.
COLOR_NEW = 0x2EA043  # 초록
COLOR_AGED = 0x3B82F6  # 파랑 — 오래된 상품이 다시 떠오른 것
COLOR_DROP = 0xE0691E  # 주황
COLOR_NOTICE = 0x9CA3AF  # 회색

log = logging.getLogger(__name__)


def _origin(item: Item) -> str:
    return " · 🏪 Shops" if item.is_shops else ""


def _item_embed(alert: Alert) -> dict:
    item = alert.item
    mark, aged = alerts.headline(item)
    return {
        "title": item.name[:250],
        "url": item.url,
        "color": COLOR_NEW if not aged else COLOR_AGED,
        "author": {"name": f"{mark} {alert.keyword}" + (f" · {aged}" if aged else "")},
        "thumbnail": {"url": item.thumbnail} if item.thumbnail else None,
        "fields": [
            {"name": "가격", "value": alerts.price_text(item, alert.krw_rate), "inline": True},
            {"name": "상태", "value": item.condition + _origin(item), "inline": True},
            {"name": "배송비", "value": item.shipping_payer, "inline": True},
        ],
        "footer": {"text": f"{item.created:%Y-%m-%d %H:%M} 출품"},
    }


def _drop_embed(alert: Alert) -> dict:
    item = alert.item
    cut = alert.old_price - item.price
    percent = cut / alert.old_price * 100
    return {
        "title": item.name[:250],
        "url": item.url,
        "color": COLOR_DROP,
        "author": {"name": f"📉 {alert.keyword} · 가격 인하"},
        "thumbnail": {"url": item.thumbnail} if item.thumbnail else None,
        "fields": [
            {
                "name": "가격",
                "value": f"~~¥{alert.old_price:,}~~ → **{alerts.price_text(item, alert.krw_rate)}**",
                "inline": False,
            },
            {"name": "내린 폭", "value": f"¥{cut:,} ({percent:.0f}%)", "inline": True},
            {"name": "상태", "value": item.condition + _origin(item), "inline": True},
        ],
        "footer": {"text": f"{item.created:%Y-%m-%d %H:%M} 출품"},
    }


def _batch_embed(alert: Alert) -> dict:
    lines = []
    for item in alert.items:
        lines.append(
            f"[{item.name[:60]}]({item.url})\n"
            f"　{alerts.price_text(item, alert.krw_rate)} · {item.condition}"
        )
    return {
        "title": f"신규 {len(alert.items)}건",
        "color": COLOR_NEW,
        "author": {"name": f"🆕 {alert.keyword}"},
        # 임베드 본문은 4096자 제한이 있다. 넘치면 뒤가 통째로 잘려 나간다.
        "description": "\n\n".join(lines)[:3900],
    }


def _notice_embed(alert: Alert) -> dict:
    return {"title": alert.title, "description": alert.body, "color": COLOR_NOTICE}


class DiscordNotifier:
    name = "discord"

    def __init__(self, webhook_url: str) -> None:
        self._url = webhook_url
        self._client = httpx.AsyncClient(timeout=30)

    def render(self, alert: Alert) -> dict:
        if alert.kind == alerts.NEW:
            embed = _item_embed(alert)
        elif alert.kind == alerts.DROP:
            embed = _drop_embed(alert)
        elif alert.kind == alerts.BATCH:
            embed = _batch_embed(alert)
        else:
            embed = _notice_embed(alert)
        # 값이 None 인 키를 남기면 디스코드가 400 으로 거절한다.
        return {"embeds": [{k: v for k, v in embed.items() if v is not None}]}

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
