"""텔레그램 메시지 조립과 전송.

전송 실패는 두 종류다. 429 처럼 기다리면 풀리는 것과, 토큰 오타처럼 몇 번을
다시 보내도 안 되는 것. 후자를 무한 재시도하면 프로그램이 그 자리에 갇히므로
시도 횟수를 제한하고 포기한 사실을 로그에 남긴다.
"""

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from html import escape

import httpx

from ..models import Item

API = "https://api.telegram.org/bot{token}/{method}"
MAX_ATTEMPTS = 3

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class Message:
    text: str
    photo: str | None = None
    # 전송에 성공한 뒤에만 실행된다. 보내기도 전에 "본 것"으로 기록해두면
    # 그 직전에 죽었을 때 해당 상품은 영영 알림이 오지 않는다.
    on_sent: Callable[[], None] | None = None


def _price(item: Item, krw_rate: float | None) -> str:
    text = f"¥{item.price:,}"
    if krw_rate:
        text += f"  (약 ₩{round(item.price * krw_rate):,})"
    return text


def _origin(item: Item) -> str:
    # Shops 는 사업자 판매라 링크 형태도 구매 방식도 다르다. 표시해두면
    # 링크가 이상해 보일 때 헷갈리지 않는다.
    return " · 🏪 Shops" if item.is_shops else ""


def _tag(keyword: str) -> str:
    # 해시태그로 쓰려면 공백이 없어야 텔레그램이 하나로 인식한다.
    return escape(keyword.replace(" ", "_"))


def format_new(item: Item, keyword: str, krw_rate: float | None) -> Message:
    # 처음 본 상품이 곧 새로 나온 상품은 아니다. 오래전에 올라온 물건이 갱신되면서
    # 검색 상위로 떠오른 것일 수 있어, 그 경우 🆕 를 붙이면 거짓말이 된다.
    age = item.age_days
    header = f"🆕 <b>#{_tag(keyword)}</b>" if age < 1 else (
        f"🔁 <b>#{_tag(keyword)}</b> · {age}일 전 출품"
    )
    return Message(
        text=(
            f"{header}\n\n"
            f"{escape(item.name)}\n\n"
            f"💴 <b>{_price(item, krw_rate)}</b>\n"
            f"📦 {item.condition} · 배송비 {item.shipping_payer}{_origin(item)}\n"
            f"🕒 {item.created:%Y-%m-%d %H:%M} 출품\n\n"
            f'<a href="{item.url}">🛒 상품 보기</a>'
        ),
        photo=item.thumbnail or None,
    )


def format_drop(item: Item, keyword: str, old_price: int, krw_rate: float | None) -> Message:
    cut = old_price - item.price
    percent = cut / old_price * 100
    return Message(
        text=(
            f"📉 <b>#{_tag(keyword)}</b> 가격 인하\n\n"
            f"{escape(item.name)}\n\n"
            f"💴 <s>¥{old_price:,}</s> → <b>{_price(item, krw_rate)}</b>\n"
            f"🔻 ¥{cut:,} 내림 ({percent:.0f}%)\n"
            f"📦 {item.condition} · 배송비 {item.shipping_payer}\n\n"
            f'<a href="{item.url}">🛒 상품 보기</a>'
        ),
        photo=item.thumbnail or None,
    )


def format_batch(items: list[Item], keyword: str, krw_rate: float | None) -> Message:
    """한꺼번에 많이 올라왔을 때. 개별 전송은 텔레그램 제한에 걸린다."""
    lines = [f"🆕 <b>#{_tag(keyword)}</b> 신규 {len(items)}건\n"]
    for item in items:
        lines.append(
            f'· <a href="{item.url}">{escape(item.name[:45])}</a>\n'
            f"  {_price(item, krw_rate)} · {item.condition}"
        )
    return Message(text="\n".join(lines))


def format_notice(title: str, body: str) -> Message:
    return Message(text=f"<b>{escape(title)}</b>\n\n{escape(body)}")


class TelegramNotifier:
    def __init__(self, token: str, chat_id: str) -> None:
        self._token = token
        self._chat_id = chat_id
        self._client = httpx.AsyncClient(timeout=30)

    async def send(self, message: Message) -> bool:
        for attempt in range(1, MAX_ATTEMPTS + 1):
            try:
                response = await self._post(message)
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

    async def _post(self, message: Message) -> httpx.Response:
        if message.photo:
            return await self._client.post(
                API.format(token=self._token, method="sendPhoto"),
                data={
                    "chat_id": self._chat_id,
                    "photo": message.photo,
                    "caption": message.text,
                    "parse_mode": "HTML",
                },
            )
        return await self._client.post(
            API.format(token=self._token, method="sendMessage"),
            data={
                "chat_id": self._chat_id,
                "text": message.text,
                "parse_mode": "HTML",
                "disable_web_page_preview": "true",
            },
        )

    async def close(self) -> None:
        await self._client.aclose()
