"""텔레그램으로 알림을 보낸다.

전송 실패는 두 종류다. 429 처럼 기다리면 풀리는 것과, 토큰 오타처럼 몇 번을
다시 보내도 안 되는 것. 후자를 무한 재시도하면 프로그램이 그 자리에 갇히므로
시도 횟수를 제한하고 포기한 사실을 로그에 남긴다.
"""

import asyncio
import logging
from html import escape

import httpx

from .. import alerts
from ..alerts import Alert
from ..models import Item

API = "https://api.telegram.org/bot{token}/{method}"
MAX_ATTEMPTS = 3

log = logging.getLogger(__name__)


def _tag(keyword: str) -> str:
    # 해시태그로 쓰려면 공백이 없어야 텔레그램이 하나로 인식한다.
    return escape(keyword.replace(" ", "_"))


def _origin(item: Item) -> str:
    # Shops 는 사업자 판매라 링크 형태도 구매 방식도 다르다. 표시해두면
    # 링크가 이상해 보일 때 헷갈리지 않는다.
    return " · 🏪 Shops" if item.is_shops else ""


def _new_text(alert: Alert) -> str:
    item = alert.item
    mark, aged = alerts.headline(item)
    head = f"{mark} <b>#{_tag(alert.keyword)}</b>" + (f" · {aged}" if aged else "")
    return (
        f"{head}\n\n"
        f"{escape(item.name)}\n\n"
        f"💴 <b>{alerts.price_text(item, alert.krw_rate)}</b>\n"
        f"📦 {item.condition} · 배송비 {item.shipping_payer}{_origin(item)}\n"
        f"🕒 {item.created:%Y-%m-%d %H:%M} 출품\n\n"
        f'<a href="{item.url}">🛒 상품 보기</a>'
    )


def _drop_text(alert: Alert) -> str:
    item = alert.item
    cut = alert.old_price - item.price
    percent = cut / alert.old_price * 100
    return (
        f"📉 <b>#{_tag(alert.keyword)}</b> 가격 인하\n\n"
        f"{escape(item.name)}\n\n"
        f"💴 <s>¥{alert.old_price:,}</s> → <b>{alerts.price_text(item, alert.krw_rate)}</b>\n"
        f"🔻 ¥{cut:,} 내림 ({percent:.0f}%)\n"
        f"📦 {item.condition} · 배송비 {item.shipping_payer}{_origin(item)}\n\n"
        f'<a href="{item.url}">🛒 상품 보기</a>'
    )


def _batch_text(alert: Alert) -> str:
    lines = [f"🆕 <b>#{_tag(alert.keyword)}</b> 신규 {len(alert.items)}건\n"]
    for item in alert.items:
        lines.append(
            f'· <a href="{item.url}">{escape(item.name[:45])}</a>\n'
            f"  {alerts.price_text(item, alert.krw_rate)} · {item.condition}"
        )
    return "\n".join(lines)


class TelegramNotifier:
    name = "telegram"

    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=30)

    def render(self, alert: Alert) -> tuple[str, str | None]:
        """(본문, 사진 URL)."""
        if alert.kind == alerts.NEW:
            return _new_text(alert), alert.item.thumbnail or None
        if alert.kind == alerts.DROP:
            return _drop_text(alert), alert.item.thumbnail or None
        if alert.kind == alerts.BATCH:
            return _batch_text(alert), None
        if alert.kind == alerts.RAW:
            return alert.body, None
        return f"<b>{escape(alert.title)}</b>\n\n{escape(alert.body)}", None

    async def send(self, alert: Alert) -> bool:
        text, photo = self.render(alert)
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._post(text, photo)
            except httpx.HTTPError as e:
                log.warning("텔레그램 전송 오류 (%d/%d): %s", attempt, MAX_ATTEMPTS, e)
                await asyncio.sleep(2**attempt)
                continue

            body = response.json()
            if body.get("ok"):
                return True

            # 429 는 텔레그램이 알려준 만큼 기다리면 풀린다.
            retry_after = body.get("parameters", {}).get("retry_after")
            if retry_after:
                log.warning("텔레그램 속도 제한. %s초 대기", retry_after)
                await asyncio.sleep(float(retry_after) + 1)
                continue

            log.error("텔레그램이 거부했습니다: %s", body.get("description"))
            return False

        log.error("텔레그램 전송을 %d회 시도 후 포기했습니다", MAX_ATTEMPTS)
        return False

    async def _post(self, text: str, photo: str | None) -> httpx.Response:
        if photo:
            return await self._client.post(
                API.format(token=self._token, method="sendPhoto"),
                data={
                    "chat_id": self._chat_id,
                    "photo": photo,
                    "caption": text,
                    "parse_mode": "HTML",
                },
            )
        return await self._client.post(
            API.format(token=self._token, method="sendMessage"),
            data={
                "chat_id": self._chat_id,
                "text": text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()
