"""채널에 묶이지 않은 알림 표현.

전에는 스케줄러가 텔레그램용 HTML 문자열을 직접 만들어 큐에 넣었다. 그러면
채널을 하나 더 붙일 때 스케줄러까지 손대야 한다. 무엇을 알릴지(여기)와 어떻게
보여줄지(각 notifier)를 나눠두면 새 채널은 파일 하나 추가로 끝난다.
"""

from collections.abc import Callable
from dataclasses import dataclass

from .models import Item

NEW = "new"
DROP = "drop"
BATCH = "batch"
NOTICE = "notice"
# 이미 그 채널 문법으로 쓰인 글. 명령 응답처럼 손대면 안 되는 것에 쓴다.
RAW = "raw"


@dataclass(frozen=True, slots=True)
class Alert:
    kind: str
    keyword: str = ""
    item: Item | None = None
    items: tuple[Item, ...] = ()
    old_price: int | None = None
    title: str = ""
    body: str = ""
    krw_rate: float | None = None
    # 전송에 성공한 뒤에만 불린다. 채널이 여럿이면 하나라도 성공하면 실행한다.
    on_sent: Callable[[], None] | None = None
    # 특정 채널에만 보낼 때 그 이름. 명령 응답은 물어본 곳에만 가야 한다.
    only: str | None = None


def new_item(
    item: Item, keyword: str, krw_rate: float | None, on_sent: Callable[[], None] | None = None
) -> Alert:
    return Alert(kind=NEW, keyword=keyword, item=item, krw_rate=krw_rate, on_sent=on_sent)


def price_drop(
    item: Item,
    keyword: str,
    old_price: int,
    krw_rate: float | None,
    on_sent: Callable[[], None] | None = None,
) -> Alert:
    return Alert(
        kind=DROP,
        keyword=keyword,
        item=item,
        old_price=old_price,
        krw_rate=krw_rate,
        on_sent=on_sent,
    )


def batch(
    items: list[Item],
    keyword: str,
    krw_rate: float | None,
    on_sent: Callable[[], None] | None = None,
) -> Alert:
    return Alert(
        kind=BATCH, keyword=keyword, items=tuple(items), krw_rate=krw_rate, on_sent=on_sent
    )


def notice(title: str, body: str, only: str | None = None) -> Alert:
    return Alert(kind=NOTICE, title=title, body=body, only=only)


def raw(text: str, only: str) -> Alert:
    """이미 그 채널 문법으로 쓰인 글을 그대로 보낸다.

    `only` 를 반드시 지정한다. 한 채널의 문법으로 쓰인 글을 다른 채널로 보내면
    태그가 글자 그대로 보이거나 전송이 거절된다.
    """
    return Alert(kind=RAW, body=text, only=only)


# ---- 채널 공통 계산 ----


def price_text(item: Item, krw_rate: float | None) -> str:
    text = f"¥{item.price:,}"
    if krw_rate:
        text += f" (약 ₩{round(item.price * krw_rate):,})"
    return text


def headline(item: Item) -> tuple[str, str]:
    """(머리말 기호, 덧붙일 설명).

    처음 본 상품이 곧 새로 나온 상품은 아니다. 오래전에 올라온 물건이 갱신되면서
    검색 상위로 떠오른 것일 수 있어, 그 경우 신규 표시를 붙이면 거짓말이 된다.
    """
    age = item.age_days
    return ("🆕", "") if age < 1 else ("🔁", f"{age}일 전 출품")
