"""디스코드 채널 추가 검증. 네트워크 없이 렌더링과 팬아웃을 확인한다."""

import asyncio
import json
import sys
from datetime import datetime, timedelta

from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm import alerts
from meralarm.models import TYPE_SHOPS, Item
from meralarm.notifiers.discord_webhook import DiscordNotifier
from meralarm.notifiers.queue import SendQueue
from meralarm.notifiers.telegram import TelegramNotifier

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def item(name="테스트 상품", price=5000, days=0, shops=False):
    return Item(
        id="m123", name=name, price=price, thumbnail="http://x/t.jpg",
        created=datetime.now() - timedelta(days=days, hours=1), updated=datetime.now(),
        condition_id=1, shipping_payer_id=2, seller_id="s1",
        item_type=TYPE_SHOPS if shops else "ITEM_TYPE_MERCARI",
    )


class FakeNotifier:
    def __init__(self, name, succeed=True):
        self.name = name
        self.succeed = succeed
        self.received = []

    async def send(self, alert):
        self.received.append(alert)
        return self.succeed

    async def close(self):
        pass


async def main():
    tg = TelegramNotifier("1:x", "1")
    dc = DiscordNotifier("https://discord.com/api/webhooks/1/x")

    print("\n=== 1. 디스코드 임베드 구조 ===")
    payload = dc.render(alerts.new_item(item(), "테스트키워드", 9.05))
    embed = payload["embeds"][0]
    check("embeds 하나", len(payload["embeds"]) == 1)
    check("제목이 상품명", embed["title"] == "테스트 상품")
    check("링크 포함", embed["url"].startswith("https://jp.mercari.com/item/"))
    check("썸네일", embed["thumbnail"]["url"] == "http://x/t.jpg")
    check("필드 3개(가격·상태·배송비)", len(embed["fields"]) == 3)
    check("원화 환산", "₩45,250" in embed["fields"][0]["value"], embed["fields"][0]["value"])
    check("None 값이 남지 않음", all(v is not None for v in embed.values()))
    check("JSON 직렬화 가능", bool(json.dumps(payload)))

    print("\n=== 2. 종류별 색과 표시 ===")
    fresh = dc.render(alerts.new_item(item(), "k", None))["embeds"][0]
    aged = dc.render(alerts.new_item(item(days=90), "k", None))["embeds"][0]
    check("당일은 🆕", "🆕" in fresh["author"]["name"])
    check("오래된 건 🔁 + 경과일", "🔁" in aged["author"]["name"] and "90일 전" in aged["author"]["name"])
    check("색이 다름", fresh["color"] != aged["color"])

    drop = dc.render(alerts.price_drop(item(price=4000), "k", 8000, None))["embeds"][0]
    check("인하: 이전가 취소선", "~~¥8,000~~" in drop["fields"][0]["value"])
    check("인하: 하락폭", "¥4,000 (50%)" in drop["fields"][1]["value"], drop["fields"][1]["value"])

    shops = dc.render(alerts.new_item(item(shops=True), "k", None))["embeds"][0]
    check("Shops 표시", "Shops" in shops["fields"][1]["value"])

    print("\n=== 3. 썸네일 없는 상품 ===")
    no_thumb = Item(id="m1", name="n", price=1, thumbnail="", created=datetime.now(),
                    updated=datetime.now(), condition_id=1, shipping_payer_id=2, seller_id="s")
    e = dc.render(alerts.new_item(no_thumb, "k", None))["embeds"][0]
    check("thumbnail 키가 아예 없음", "thumbnail" not in e, f"got {e.get('thumbnail')}")

    print("\n=== 4. 묶음 요약 길이 제한 ===")
    many = [item(name="긴상품명" * 30, price=i) for i in range(60)]
    big = dc.render(alerts.batch(many, "k", None))["embeds"][0]
    check("본문이 4096자 제한 안", len(big["description"]) <= 4096, f"{len(big['description'])}자")

    print("\n=== 5. 텔레그램은 그대로 동작 ===")
    text, photo = tg.render(alerts.new_item(item(), "테스트", 9.05))
    check("HTML 유지", "<b>" in text and "🛒 상품 보기" in text)
    check("사진 포함", photo == "http://x/t.jpg")
    raw_text, _ = tg.render(alerts.raw("<b>굵게</b> <code>코드</code>", only="telegram"))
    check("raw 는 이스케이프하지 않음", raw_text == "<b>굵게</b> <code>코드</code>", raw_text)
    notice_text, _ = tg.render(alerts.notice("제목", "본문 <위험>"))
    check("공지는 이스케이프함", "&lt;위험&gt;" in notice_text, notice_text)

    print("\n=== 6. 두 채널로 팬아웃 ===")
    a, b = FakeNotifier("telegram"), FakeNotifier("discord")
    q = SendQueue([a, b])
    check("채널 목록", q.channels == ["telegram", "discord"])

    marked = []
    q.put(alerts.new_item(item(), "k", None, on_sent=lambda: marked.append(1)))
    worker = asyncio.create_task(q.run())
    await asyncio.sleep(0.3)
    check("둘 다 받음", len(a.received) == 1 and len(b.received) == 1)
    check("기록은 한 번만", len(marked) == 1)

    print("\n=== 7. 한쪽이 실패해도 기록된다 ===")
    a2, b2 = FakeNotifier("telegram", True), FakeNotifier("discord", False)
    q2 = SendQueue([a2, b2])
    marked2 = []
    q2.put(alerts.new_item(item(), "k", None, on_sent=lambda: marked2.append(1)))
    w2 = asyncio.create_task(q2.run())
    await asyncio.sleep(0.3)
    check("디스코드가 실패해도 기록됨", len(marked2) == 1, "안 그러면 텔레그램에 계속 재전송된다")

    print("\n=== 8. 둘 다 실패하면 기록하지 않는다 ===")
    a3, b3 = FakeNotifier("telegram", False), FakeNotifier("discord", False)
    q3 = SendQueue([a3, b3])
    marked3 = []
    q3.put(alerts.new_item(item(), "k", None, on_sent=lambda: marked3.append(1)))
    w3 = asyncio.create_task(q3.run())
    await asyncio.sleep(0.3)
    check("기록 안 함 → 다음에 다시 시도", len(marked3) == 0)

    print("\n=== 9. only 지정은 그 채널에만 ===")
    a4, b4 = FakeNotifier("telegram"), FakeNotifier("discord")
    q4 = SendQueue([a4, b4])
    q4.put(alerts.raw("명령 응답", only="telegram"))
    w4 = asyncio.create_task(q4.run())
    await asyncio.sleep(0.3)
    check("텔레그램만 받음", len(a4.received) == 1 and len(b4.received) == 0)

    for t in (worker, w2, w3, w4):
        t.cancel()
    await tg.close()
    await dc.close()

    print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)


asyncio.run(main())
