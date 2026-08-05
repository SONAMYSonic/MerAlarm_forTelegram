"""디스코드 임베드 만들기.

웹훅과 봇이 함께 쓴다. 보내는 방법만 다르고 보이는 모습은 같아야 하므로,
그림 그리는 일은 여기 한 곳에 둔다.

임베드로 보내면 썸네일과 항목을 칸으로 나눠 보여줄 수 있어, 이 용도에는 그냥
글로 보내는 것보다 읽기 편하다.
"""

from .. import alerts
from ..alerts import Alert
from ..models import Item

# 임베드 왼쪽 띠 색. 종류를 색으로 구분하면 목록에서 훑기 쉽다.
COLOR_NEW = 0x2EA043  # 초록
COLOR_AGED = 0x3B82F6  # 파랑 — 오래된 상품이 다시 떠오른 것
COLOR_DROP = 0xE0691E  # 주황
COLOR_NOTICE = 0x9CA3AF  # 회색

# 디스코드가 거부하는 길이. 임베드 본문은 넘치면 뒤가 통째로 잘려 나간다.
DESCRIPTION_LIMIT = 4096
TITLE_LIMIT = 250


def _origin(item: Item) -> str:
    return " · 🏪 Shops" if item.is_shops else ""


def _item_embed(alert: Alert) -> dict:
    item = alert.item
    mark, aged = alerts.headline(item)
    return {
        "title": item.name[:TITLE_LIMIT],
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
        "title": item.name[:TITLE_LIMIT],
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
    total = len(alert.items)
    lines: list[str] = []
    shown = 0

    for item in alert.items:
        entry = (
            f"[{item.name[:60]}]({item.url})\n"
            f"　{alerts.price_text(item, alert.krw_rate)} · {item.condition}"
        )
        # 다 넣으면 넘칠 것 같으면 거기서 멈춘다. 잘린 채로 보내면 마지막 줄이
        # 문장 중간에서 끊겨 무엇이 생략됐는지 알 수 없다.
        tail = f"\n\n*외 {total - shown}건은 목록이 길어 생략했습니다*"
        used = sum(len(x) + 2 for x in lines)
        if used + len(entry) + len(tail) > DESCRIPTION_LIMIT:
            lines.append(tail.strip())
            break
        lines.append(entry)
        shown += 1

    return {
        "title": f"신규 {total}건",
        "color": COLOR_NEW,
        "author": {"name": f"🆕 {alert.keyword}"},
        "description": "\n\n".join(lines)[:DESCRIPTION_LIMIT],
    }


def _notice_embed(alert: Alert) -> dict:
    return {
        "title": alert.title[:TITLE_LIMIT],
        "description": alert.body[:DESCRIPTION_LIMIT],
        "color": COLOR_NOTICE,
    }


def build(alert: Alert) -> dict:
    """알림 하나를 임베드 한 장으로."""
    if alert.kind == alerts.NEW:
        embed = _item_embed(alert)
    elif alert.kind == alerts.DROP:
        embed = _drop_embed(alert)
    elif alert.kind == alerts.BATCH:
        embed = _batch_embed(alert)
    else:
        embed = _notice_embed(alert)
    # 값이 None 인 키를 남기면 디스코드가 400 으로 거절한다.
    return {k: v for k, v in embed.items() if v is not None}
