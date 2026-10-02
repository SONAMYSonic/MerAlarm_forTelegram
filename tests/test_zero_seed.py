"""0건으로 시작한 키워드가 첫 매물을 잡는가, 그리고 원인을 제대로 가르는가."""

import asyncio
import shutil
import sys
import tempfile
from datetime import datetime
from datetime import time as dtime
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import meralarm.config as cfgmod  # noqa: E402

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
shutil.copy(REPO / "config.example.yaml", TMP / "config.yaml")

from meralarm import alerts  # noqa: E402
from meralarm.config import (  # noqa: E402
    BackoffConfig, Config, KeywordConfig, NightMode, NotifyConfig, PollConfig, StoreConfig,
)
from meralarm.models import Item  # noqa: E402
from meralarm.notifiers.queue import SendQueue  # noqa: E402
from meralarm.scheduler import Scheduler  # noqa: E402
from meralarm.store import SeenStore  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def item(n, name=None):
    now = datetime.now()
    return Item(
        id=f"m{n:011d}", name=name or f"c108 学マス 花海佑芽 ふもふもはなみうめ #{n}",
        price=6800, thumbnail="", created=now, updated=now,
        condition_id=1, shipping_payer_id=2, seller_id="s",
    )


class ScriptedSource:
    """회차마다 정해진 결과를 돌려준다. sold 는 고정."""

    name = "scripted"

    def __init__(self, script, sold=None):
        self.script = list(script)
        self._sold = sold
        self.turn = 0

    async def search(self, keyword):
        out = self.script[min(self.turn, len(self.script) - 1)]
        self.turn += 1
        return list(out)

    # sold 를 None 으로 주면 이 메서드를 아예 안 붙여, 판매완료를 못 세는
    # 수집기(폴백 등)를 흉내낸다.
    async def search_sold(self, keyword):
        return list(self._sold or [])


class NoSoldSource(ScriptedSource):
    search_sold = None  # getattr 이 None 을 집도록


def make_cfg(kw):
    return Config(
        poll=PollConfig(30, 0.2, 0.0, NightMode(False, dtime(2), dtime(7), 180)),
        backoff=BackoffConfig(3, 900, 5), store=StoreConfig(60),
        notify=NotifyConfig(False, True, 5, None, True, 30, 0),
        keywords=(kw,), exclude=(), telegram_token="t", telegram_chat_id="1",
        discord_webhook="", discord_bot_token="", discord_channel_id=0,
        discord_owner_id=0, config_path=TMP / "config.yaml", db_path=TMP / "seen.db",
        log_path=TMP / "l.log", fx_cache_path=TMP / "fx.json",
    )


KW = KeywordConfig(
    name="花海佑芽 ふもふも", query="花海佑芽 ふもふも", interval_sec=30,
    price_min=None, price_max=None, exclude=(), require=(),
    conditions=(), shipping_payers=(), max_age_days=30, max_bump_days=0,
)


async def run(source, turns, label, kw=KW):
    """회차마다 (상품 알림 수, 공지 문구) 를 돌려준다."""
    store = SeenStore(TMP / f"{label}.db")
    queue = SendQueue([])
    sch = Scheduler(make_cfg(kw), source, store, queue, None)
    state = sch._states[0]

    out = []
    for _ in range(turns):
        await sch._poll(state)
        items = notices = 0
        text = ""
        while queue.pending:
            alert = queue._queue.get_nowait()
            if alert.kind == alerts.NOTICE:
                notices += 1
                text = f"{alert.title} {alert.body}"
            else:
                items += 1
            # 실제 전송을 흉내낸다. on_sent 가 있어야 "알린 것"으로 기록된다.
            if alert.on_sent is not None:
                alert.on_sent()
        out.append((items, text))
    store.close()
    return out


async def main():
    print("=== 1. 0건으로 시작 → 첫 매물 등장 (제보자 상황) ===")
    turns = await run(ScriptedSource([[], [], [item(1)], [item(1)], [item(1), item(2)]]), 5, "A")
    for i, (n, _) in enumerate(turns, 1):
        print(f"      {i}회차 상품 알림 {n}건")
    check("첫 매물에 알림이 간다", turns[2][0] == 1, f"{turns[2][0]}건")
    check("같은 매물은 다시 안 간다", turns[3][0] == 0, f"{turns[3][0]}건")
    check("그다음 신규도 간다", turns[4][0] == 1, f"{turns[4][0]}건")

    print("\n=== 2. 처음부터 매물이 있으면 첫 회차는 조용히 (기존 동작) ===")
    turns = await run(ScriptedSource([[item(1)], [item(1)], [item(1), item(2)]]), 3, "B")
    check("첫 회차 조용", turns[0][0] == 0, f"{turns[0][0]}건")
    check("그다음 신규는 알림", turns[2][0] == 1, f"{turns[2][0]}건")
    check("첫 회차에 공지도 없음", turns[0][1] == "", turns[0][1][:40])

    print("\n=== 3. 원인을 가려서 알려준다 ===")
    turns = await run(ScriptedSource([[]], sold=[item(9)] * 5), 1, "C1")
    check("다 팔린 경우 → 검색어는 맞다고 알려줌",
          "판매완료가 5건" in turns[0][1] and "검색어는 맞습니다" in turns[0][1], turns[0][1])

    turns = await run(ScriptedSource([[]], sold=[]), 1, "C2")
    check("둘 다 0 → 검색어를 의심하라고 알려줌",
          "검색어를 확인" in turns[0][1] and "둘 다 든" in turns[0][1], turns[0][1])

    turns = await run(NoSoldSource([[]]), 1, "C3")
    check("판매완료를 못 세는 수집기여도 안 터짐",
          "지금 잡히는 상품이 없습니다" in turns[0][1], turns[0][1])

    picky = KeywordConfig(
        name=KW.name, query=KW.query, interval_sec=30, price_min=None, price_max=None,
        exclude=(), require=("존재하지않는말",), conditions=(), shipping_payers=(),
        max_age_days=30, max_bump_days=0,
    )
    turns = await run(ScriptedSource([[item(1), item(2), item(3)]]), 1, "C4", kw=picky)
    check("검색은 되는데 조건이 다 걸러낸 경우 → 조건을 보라고 알려줌",
          "3건이 잡혔지만" in turns[0][1] and "/exclude" in turns[0][1], turns[0][1])

    print("\n=== 4. 공지는 한 번만 ===")
    turns = await run(ScriptedSource([[], [], []], sold=[]), 3, "D")
    said = sum(1 for _, t in turns if t)
    check("0건이 이어져도 공지는 1회", said == 1, f"{said}회")

    shutil.rmtree(TMP, ignore_errors=True)
    print(f"\n{'=' * 56}\n통과 {ok} · 실패 {fail}")


asyncio.run(main())
sys.exit(1 if fail else 0)
