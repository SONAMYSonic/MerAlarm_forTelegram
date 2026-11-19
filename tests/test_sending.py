"""보내는 중인 상품을 다시 대기열에 넣지 않는지 검증.

2026-11-19 에 텔레그램이 Bad Gateway 를 내며 전송이 밀렸다. 알림은 보낸 뒤에
기록하므로, 대기열에서 기다리는 동안 15초마다 다음 회차가 같은 상품을 또 신규·인하로
잡아 넣었다. 3시간 동안 인하 36건이 740번 나갔고 대기열 500칸이 같은 알림으로 찼다.

확인할 것은 셋이다.

- 결과가 나오기 전에는 같은 상품을 다시 넣지 않는가 (신규 · 인하 · 묶음 · 키워드 둘)
- 끝내 못 보내면 표시가 풀려 다음 회차에 **한 번만** 다시 넣는가 (기록은 안 남긴다)
- 보내는 동안 값이 더 내려도, 보낸 뒤 그 차이로 알리는가

전송은 진짜 SendQueue.run() 으로 돈다. 간격만 0 으로 줄였다.
실제 config.yaml · .env · data/ 는 건드리지 않는다.
"""

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

from meralarm import alerts  # noqa: E402
from meralarm.config import (  # noqa: E402
    BackoffConfig, Config, KeywordConfig, NightMode, NotifyConfig, PollConfig, StoreConfig,
)
from meralarm.models import Item  # noqa: E402
from meralarm.notifiers import queue as queuemod  # noqa: E402
from meralarm.notifiers.queue import SendQueue  # noqa: E402
from meralarm.scheduler import Scheduler  # noqa: E402
from meralarm.store import SeenStore  # noqa: E402

queuemod.SEND_GAP_SEC = 0
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def item(n, price=6800):
    now = datetime.now()
    return Item(
        id=f"m{n:011d}", name=f"シャニマス カラ鉄 #{n}", price=price, thumbnail="",
        created=now, updated=now, condition_id=1, shipping_payer_id=2, seller_id="964893670",
    )


def kwcfg(name="k"):
    return KeywordConfig(name=name, query=name, interval_sec=15, price_min=None, price_max=None,
                         exclude=(), require=(), conditions=(), shipping_payers=(),
                         max_age_days=30, max_bump_days=0)


def mkcfg(*kws, batch=5):
    return Config(
        poll=PollConfig(15, 0.2, 0.0, NightMode(False, dtime(2), dtime(7), 180)),
        backoff=BackoffConfig(3, 900, 5), store=StoreConfig(60),
        notify=NotifyConfig(False, True, batch, None, True, 30, 0),
        keywords=kws, exclude=(), telegram_token="t", telegram_chat_id="1",
        discord_webhook="", discord_bot_token="", discord_channel_id=0, discord_owner_id=0,
        config_path=TMP / "config.yaml", db_path=TMP / "x.db", log_path=TMP / "l.log",
        fx_cache_path=TMP / "fx.json",
    )


class Source:
    """키워드마다 회차별로 정해 둔 검색 결과를 돌려준다. 끝나면 마지막 것을 되풀이."""

    name = "scripted"

    def __init__(self, **scripts):
        self.scripts = {k: list(v) for k, v in scripts.items()}
        self.turns = dict.fromkeys(scripts, 0)

    async def search(self, keyword):
        script = self.scripts[keyword]
        out = script[min(self.turns[keyword], len(script) - 1)]
        self.turns[keyword] += 1
        return list(out)


class Channel:
    """가짜 채널. ok 가 False 면 텔레그램이 Bad Gateway 를 낸 것처럼 실패한다."""

    name = "telegram"

    def __init__(self):
        self.ok = True
        self.got = []

    async def send(self, alert):
        self.got.append(alert)
        return self.ok

    async def close(self):
        pass


async def deliver(queue):
    """대기열을 진짜 전송 루프로 비운다."""
    task = asyncio.create_task(queue.run())
    await asyncio.wait_for(queue._queue.join(), 5)
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


def queued(queue):
    """대기열에 든 것을 (종류, 상품번호, 이전가, 묶음 수) 로. 꺼내지 않는다."""
    return [
        (a.kind, a.item.id if a.item else None, a.old_price, len(a.items))
        for a in list(queue._queue._queue)
    ]


def setup(batch=5, max_pending=500, **scripts):
    store = SeenStore(Path(tempfile.mkdtemp()) / "s.db")
    channel = Channel()
    queue = SendQueue([channel], max_pending=max_pending)
    names = list(scripts)
    sch = Scheduler(mkcfg(*[kwcfg(n) for n in names], batch=batch), Source(**scripts), store, queue)
    states = {s.cfg.name: s for s in sch._states}
    return store, channel, queue, sch, states


async def main():
    print("=== 1. 신규 — 보내기 전에는 다시 안 넣는다 ===")
    store, ch, q, sch, st = setup(k=[[item(1)], [item(1), item(2)]])
    await sch._poll(st["k"])                       # 최초 적재
    await sch._poll(st["k"])
    check("신규 1건이 대기열에", queued(q) == [("new", "m00000000002", None, 0)], queued(q))
    check("보내는 중으로 표시", sch._sending == {"m00000000002"}, sch._sending)
    for _ in range(3):
        await sch._poll(st["k"])                   # 텔레그램이 밀려 아직 못 보냄
    check("세 회차가 더 돌아도 1건 그대로", len(queued(q)) == 1, queued(q))
    check("보내기 전에는 기록하지 않음", store.known_prices("k", ["m00000000002"]) == {})
    await deliver(q)
    check("채널에는 한 번만", len(ch.got) == 1, len(ch.got))
    check("보낸 뒤 기록됨", store.known_prices("k", ["m00000000002"]) == {"m00000000002": 6800})
    check("표시가 풀림", sch._sending == set(), sch._sending)
    await sch._poll(st["k"])
    check("다음 회차엔 아무것도 없음", queued(q) == [], queued(q))
    check("통계도 한 번만 셈", sch.stats.new_items == 1, sch.stats.new_items)

    print("\n=== 2. 2026-11-19 재현 — 인하 10건이 4회차 동안 못 나갈 때 ===")
    before = [item(n) for n in range(10, 20)]
    after = [item(n, 6000) for n in range(10, 20)]
    store, ch, q, sch, st = setup(k=[before, after])
    await sch._poll(st["k"])                       # 최초 적재
    for _ in range(4):
        await sch._poll(st["k"])
    check("대기열에 10건뿐 (예전엔 40건)", len(queued(q)) == 10, len(queued(q)))
    check("전부 인하", all(k == "drop" for k, *_ in queued(q)))
    await deliver(q)
    check("채널에도 10건", len(ch.got) == 10, len(ch.got))
    await sch._poll(st["k"])
    check("보낸 뒤엔 조용함", queued(q) == [], queued(q))

    print("\n=== 3. 보내는 동안 값이 더 내려가면 ===")
    store, ch, q, sch, st = setup(k=[[item(3)], [item(3, 6000)], [item(3, 5000)], [item(3, 5000)]])
    await sch._poll(st["k"])                       # 6800 으로 적재
    await sch._poll(st["k"])                       # 6000 인하 → 대기열
    await sch._poll(st["k"])                       # 5000 — 아직 6000 을 보내는 중
    check("보내는 중엔 새 인하를 안 넣음", queued(q) == [("drop", "m00000000003", 6800, 0)], queued(q))
    await deliver(q)
    check("보낸 값으로 기록", store.known_prices("k", ["m00000000003"]) == {"m00000000003": 6000})
    await sch._poll(st["k"])
    check("다음 회차에 그 차이로 알림 (6000 → 5000)",
          queued(q) == [("drop", "m00000000003", 6000, 0)], queued(q))

    print("\n=== 4. 끝내 못 보내면 다음 회차에 한 번만 다시 ===")
    store, ch, q, sch, st = setup(k=[[item(1)], [item(1), item(4)]])
    await sch._poll(st["k"])
    await sch._poll(st["k"])
    ch.ok = False
    await deliver(q)
    check("실패하면 표시가 풀림", sch._sending == set(), sch._sending)
    check("실패하면 기록 안 함", store.known_prices("k", ["m00000000004"]) == {})
    check("원장에도 안 남음", store.notified_prices(["m00000000004"]) == {})
    await sch._poll(st["k"])
    await sch._poll(st["k"])
    check("다시 한 번만 넣음", queued(q) == [("new", "m00000000004", None, 0)], queued(q))
    ch.ok = True
    await deliver(q)
    check("이번엔 보내고 기록", store.notified_prices(["m00000000004"]) == {"m00000000004": 6800})

    print("\n=== 5. 대기열이 넘쳐 버려도 다시 잡힌다 ===")
    store, ch, q, sch, st = setup(max_pending=1, k=[[item(1)], [item(1), item(5), item(6)]])
    await sch._poll(st["k"])
    await sch._poll(st["k"])                       # 둘 중 하나는 넘쳐서 버려짐
    check("대기열엔 하나", len(queued(q)) == 1 and q.dropped == 1, (queued(q), q.dropped))
    check("버려진 쪽은 표시가 풀림", len(sch._sending) == 1, sch._sending)
    await deliver(q)
    await sch._poll(st["k"])
    check("버려졌던 상품이 다음 회차에 들어감", len(queued(q)) == 1, queued(q))
    await deliver(q)
    check("둘 다 결국 한 번씩", sorted(a.item.id for a in ch.got) == ["m00000000005", "m00000000006"],
          [a.item.id for a in ch.got])

    print("\n=== 6. 묶음 요약도 한 번만 ===")
    store, ch, q, sch, st = setup(batch=3, k=[[item(1)], [item(1)] + [item(n) for n in range(30, 34)]])
    await sch._poll(st["k"])
    await sch._poll(st["k"])
    await sch._poll(st["k"])
    check("묶음 1건만", queued(q) == [("batch", None, None, 4)], queued(q))
    check("묶음 속 넷 다 보내는 중", len(sch._sending) == 4, sch._sending)
    await deliver(q)
    check("보낸 뒤 넷 다 기록", len(store.known_prices("k", [f"m{n:011d}" for n in range(30, 34)])) == 4)
    check("표시가 풀림", sch._sending == set())

    print("\n=== 7. 키워드 둘이 같은 상품을 잡을 때 ===")
    store, ch, q, sch, st = setup(a=[[item(1)], [item(1), item(7)]], b=[[item(2)], [item(2), item(7)]])
    await sch._poll(st["a"])
    await sch._poll(st["b"])                       # 둘 다 최초 적재
    await sch._poll(st["a"])
    await sch._poll(st["b"])                       # a 가 보내는 중
    check("두 번째 키워드는 안 넣음", queued(q) == [("new", "m00000000007", None, 0)], queued(q))
    check("두 번째 키워드도 아직 기록 안 함", store.known_prices("b", ["m00000000007"]) == {})
    await deliver(q)
    await sch._poll(st["b"])
    check("보낸 뒤엔 중복 제거가 받아서 조용함", queued(q) == [], queued(q))
    check("두 번째 키워드도 이제 기록", store.known_prices("b", ["m00000000007"]) == {"m00000000007": 6800})

    print("\n=== 8. 기록하다 터져도 표시는 풀린다 ===")
    store, ch, q, sch, st = setup(k=[[item(1)], [item(1), item(8)]])
    await sch._poll(st["k"])
    await sch._poll(st["k"])
    real = store.record

    def broken(*args, **kwargs):
        raise RuntimeError("디스크가 가득 참")

    store.record = broken
    await deliver(q)
    store.record = real
    check("표시가 풀림 (안 그러면 영영 보내는 중)", sch._sending == set(), sch._sending)
    check("전송 루프는 멈추지 않음", q.pending == 0)

    print("\n=== 9. 상품이 없는 알림은 상관없다 ===")
    q = SendQueue([], max_pending=1)
    q.put(alerts.notice("제목", "본문"))
    q.put(alerts.notice("넘침", "버려져도 콜백이 없으니 조용히"))
    check("공지가 넘쳐도 예외 없음", q.dropped == 1)
    settled = []
    q2 = SendQueue([])
    q2.put(alerts.new_item(item(9), "k", None, on_sent=lambda: settled.append("sent"),
                           on_failed=lambda: settled.append("failed")))
    await deliver(q2)
    check("보낼 채널이 없으면 실패로 알림", settled == ["failed"], settled)


asyncio.run(main())
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 54}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
