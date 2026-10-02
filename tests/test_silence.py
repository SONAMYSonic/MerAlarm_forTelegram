"""상품 무시 · 판매자 차단 검증.

기능이 되는지만큼 **다른 기능이 그대로인지**를 본다. 특히 셋.

  - 무시한 상품도 가격은 계속 기록되는가 (안 그러면 되돌릴 때 옛 매물이 쏟아진다)
  - 중복 제거·최초 적재·0건 진단·묶음 요약이 그대로인가
  - 버튼이 텔레그램 64바이트 · 디스코드 100자 안에 드는가

실제 config.yaml · .env · data/ 는 건드리지 않는다.
"""

import asyncio
import json
import shutil
import sys
import tempfile
from datetime import datetime, timedelta, timezone
from datetime import time as dtime
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod  # noqa: E402

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
shutil.copy(REPO / "config.example.yaml", TMP / "config.yaml")

import discord  # noqa: E402
from discord import app_commands  # noqa: E402

from meralarm import alerts  # noqa: E402
from meralarm.commands import CommandCore, TelegramCommands  # noqa: E402
from meralarm.config import (  # noqa: E402
    BackoffConfig, Config, KeywordConfig, NightMode, NotifyConfig, PollConfig, StoreConfig, load,
)
from meralarm.control import Controls  # noqa: E402
from meralarm.models import TYPE_SHOPS, Item  # noqa: E402
from meralarm.notifiers.discord_bot import ACTION_PREFIX, DiscordBot  # noqa: E402
from meralarm.notifiers.queue import SendQueue  # noqa: E402
from meralarm.notifiers.telegram import TelegramNotifier  # noqa: E402
from meralarm.scheduler import Scheduler  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402
from meralarm.silence import (  # noqa: E402
    action_to_command, item_actions, parse_item, parse_seller, undo_actions,
)
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


SELLER = "964893670"
OTHER = "111222333"
SHOPS_ID = "2JXbp8ht6N4Rgmkc9LTQVB"  # 실측한 Shops 상품 번호. 대소문자가 섞여 있다.


def item(n, price=6800, seller=SELLER, name=None, shops=False):
    now = datetime.now()
    return Item(
        id=SHOPS_ID if shops else f"m{n:011d}",
        name=name or f"c108 学マス ふもふも #{n}",
        price=price, thumbnail="", created=now, updated=now,
        condition_id=1, shipping_payer_id=2, seller_id=seller,
        **({"item_type": TYPE_SHOPS} if shops else {}),
    )


# ---------------------------------------------------------------- 1
print("\n=== 1. 주소·번호 읽기 ===")
for text, want in [
    ("m12345678901", "m12345678901"),
    ("https://jp.mercari.com/item/m12345678901", "m12345678901"),
    ("https://jp.mercari.com/item/m12345678901?source=share", "m12345678901"),
    ("<https://jp.mercari.com/item/m12345678901>", "m12345678901"),
    (f"https://jp.mercari.com/shops/product/{SHOPS_ID}", SHOPS_ID),
    (SHOPS_ID, SHOPS_ID),
    ("abc", None),
    ("m1 /del 1", None),
    ("", None),
]:
    check(f"상품 {text[:42]!r:<46} → {want}", parse_item(text) == want, parse_item(text))

check("Shops 번호의 대소문자를 바꾸지 않음", parse_item(SHOPS_ID) == SHOPS_ID)

for text, want in [
    (SELLER, SELLER),
    (f"https://jp.mercari.com/user/profile/{SELLER}", SELLER),
    ("m12345678901", None),
    ("판매자", None),
]:
    check(f"판매자 {text[:42]!r:<44} → {want}", parse_seller(text) == want, parse_seller(text))

# ---------------------------------------------------------------- 2
print("\n=== 2. 버튼 표식 ===")
acts = item_actions(item(1))
check("개인 상품은 버튼 둘", len(acts) == 2, acts)
check("무시 표식", acts[0][1] == "mute:m00000000001", acts[0])
check("차단 표식에 판매자·상품", acts[1][1] == f"block:{SELLER}:m00000000001", acts[1])
check("판매자 번호가 이상하면 차단 버튼을 빼고 무시만",
      len(item_actions(item(1, seller="None"))) == 1)

worst = max(len(t.encode()) for it in (item(1), item(1, shops=True)) for _, t in item_actions(it))
check(f"텔레그램 64바이트 안 (최장 {worst})", worst <= 64)
check(f"디스코드 100자 안 (접두사 포함 {worst + len(ACTION_PREFIX)})", worst + len(ACTION_PREFIX) <= 100)

for token, want in [
    ("mute:m00000000001", "/mute add m00000000001"),
    (f"mute:{SHOPS_ID}", f"/mute add {SHOPS_ID}"),
    ("unmute:m00000000001", "/mute del m00000000001"),
    (f"block:{SELLER}:m00000000001", f"/block add {SELLER} m00000000001"),
    (f"unblock:{SELLER}", f"/block del {SELLER}"),
]:
    check(f"{token[:40]:<42} → 명령", action_to_command(token) == want, action_to_command(token))

for bad in ["", "mute:", "mute:m1", "mute:m00000000001 /del 1", "mute:../../x",
            "block:abc:m00000000001", "del:1", "/del 1", "MUTE:m00000000001",
            f"unblock:{SELLER}:x", "mute:m00000000001\n/del 1"]:
    check(f"엉뚱한 표식은 실행 안 함 {bad[:30]!r}", action_to_command(bad) is None)

check("무시 → 되돌리기는 무시 풀기",
      undo_actions("mute:m00000000001") == (("↩ 되돌리기", "unmute:m00000000001"),))
check("차단 → 되돌리기는 차단 풀기",
      undo_actions(f"block:{SELLER}:m00000000001") == (("↩ 되돌리기", f"unblock:{SELLER}"),))
check("되돌리기의 되돌리기는 없음", undo_actions("unmute:m00000000001") == ())

# ---------------------------------------------------------------- 3
print("\n=== 3. 저장소 ===")
store = SeenStore(TMP / "s3.db")
store.record("k", [item(1), item(2)])
check("처음 무시하면 True", store.mute("m00000000001", "이름1") is True)
check("또 무시하면 False (그대로)", store.mute("m00000000001", "바뀐이름") is False)
check("이름은 처음 것 유지", store.muted()[0][1] == "이름1")
check("풀면 이름을 돌려줌", store.unmute("m00000000001") == "이름1")
check("또 풀면 None", store.unmute("m00000000001") is None)
check("차단 True/False", store.block(SELLER, "메모") and not store.block(SELLER, "다른"))
check("차단 풀기", store.unblock(SELLER) == "메모" and store.unblock(SELLER) is None)
check("기록에 있는 이름", store.name_of("m00000000002") == "c108 学マス ふもふも #2")
check("기록에 없으면 None", store.name_of("m99999999999") is None)

store.mute("m00000000001", "a")
store.block(SELLER, "")
muted, blocked = store.silenced(["m00000000001", "m00000000002"], [SELLER, OTHER])
check("silenced: 무시한 것만", muted == {"m00000000001"}, muted)
check("silenced: 차단한 것만", blocked == {SELLER}, blocked)
check("silenced: 빈 목록", store.silenced([], []) == (set(), set()))

many = [f"m{n:011d}" for n in range(1000)]
store.mute("m00000000999", "끝 쪽")
check("400개 넘게 물어도 나눠서 찾음", "m00000000999" in store.silenced(many, [])[0])
store.close()

# ---------------------------------------------------------------- 4
print("\n=== 4. 판매완료 정리 ===")
store = SeenStore(TMP / "s4.db")
store.record("k", [item(1)])                 # 아직 검색에 나오는 상품
store.mute("m00000000001", "아직 팔리는 중")
store.mute("m00000000002", "팔림")            # items 에 없음
store.mute("m00000000003", "방금 직접 무시")    # items 에 없음, 하지만 최근
old = (datetime.now(timezone.utc) - timedelta(days=90)).isoformat(timespec="seconds")
store._db.execute("UPDATE muted SET muted_at = ? WHERE item_id IN (?, ?)",
                  (old, "m00000000001", "m00000000002"))
store.block(SELLER, "")
store._db.execute("UPDATE blocked_sellers SET blocked_at = ?", (old,))
store._db.commit()

gone = store.purge_muted(60)
left = {r[0] for r in store.muted()}
check("팔린 상품의 무시 기록만 지움", gone == 1 and "m00000000002" not in left, (gone, left))
check("아직 검색에 나오는 상품은 남김", "m00000000001" in left)
check("직접 무시한 지 얼마 안 된 것은 남김", "m00000000003" in left)
check("차단한 판매자는 자동으로 안 지움", len(store.blocked()) == 1)
check("기존 purge() 는 여전히 두 값", len(store.purge(60)) == 2)
store.close()


# ---------------------------------------------------------------- 5
print("\n=== 5. 스케줄러 — 알림만 끄고 기록은 이어간다 ===")


class Scripted:
    name = "scripted"

    def __init__(self, script):
        self.script, self.turn = list(script), 0

    async def search(self, keyword):
        out = self.script[min(self.turn, len(self.script) - 1)]
        self.turn += 1
        return list(out)


def kwcfg(name="k"):
    return KeywordConfig(name=name, query=name, interval_sec=30, price_min=None, price_max=None,
                         exclude=(), require=(), conditions=(), shipping_payers=(),
                         max_age_days=30, max_bump_days=0)


def mkcfg(*kws, batch=5):
    return Config(
        poll=PollConfig(30, 0.2, 0.0, NightMode(False, dtime(2), dtime(7), 180)),
        backoff=BackoffConfig(3, 900, 5), store=StoreConfig(60),
        notify=NotifyConfig(False, True, batch, None, True, 30, 0),
        keywords=kws, exclude=(), telegram_token="t", telegram_chat_id="1",
        discord_webhook="", discord_bot_token="", discord_channel_id=0, discord_owner_id=0,
        config_path=TMP / "config.yaml", db_path=TMP / "x.db", log_path=TMP / "l.log",
        fx_cache_path=TMP / "fx.json",
    )


async def drive(sch, store, queue, state, script_len):
    """회차마다 (알림 종류 목록) 을 돌려준다. 실제 전송처럼 on_sent 를 부른다."""
    out = []
    for _ in range(script_len):
        await sch._poll(state)
        kinds = []
        while queue.pending:
            a = queue._queue.get_nowait()
            kinds.append((a.kind, a.item.id if a.item else None, a.old_price, len(a.items)))
            if a.on_sent:
                a.on_sent()
        out.append(kinds)
    return out


async def scheduler_tests():
    # 5-1. 무시한 상품은 신규로도 인하로도 안 온다. 가격은 기록된다.
    store = SeenStore(TMP / "s5.db")
    queue = SendQueue([])
    sch = Scheduler(mkcfg(kwcfg()), Scripted([
        [item(1)],                    # 최초 적재
        [item(1), item(2)],           # 2 가 신규 → 알림
        [item(1), item(2, 6000)],     # 2 인하 → 하지만 아래서 무시
        [item(1), item(2, 5000)],     # 무시 중 인하
        [item(1), item(2, 4000)],     # 되돌린 뒤 인하 → 알림
    ]), store, queue, None)
    st = sch._states[0]
    r = await drive(sch, store, queue, st, 2)
    check("신규는 평소처럼 옴", r[1] == [("new", "m00000000002", None, 0)], r[1])

    store.mute("m00000000002", "x")
    r = await drive(sch, store, queue, st, 2)
    check("무시 중 인하 알림 없음", r == [[], []], r)
    check("무시 중에도 가격은 기록됨", store.known_prices("k", ["m00000000002"]) == {"m00000000002": 5000},
          store.known_prices("k", ["m00000000002"]))

    store.unmute("m00000000002")
    r = await drive(sch, store, queue, st, 1)
    check("되돌리면 다음 인하부터 다시 옴", len(r[0]) == 1 and r[0][0][0] == "drop", r)
    # 마지막으로 알린 가격(6800)과 견준다 — 사용자가 마지막으로 본 가격
    check("인하 기준은 사용자가 마지막으로 본 가격", r[0] and r[0][0][2] == 6800, r)
    store.close()

    # 5-2. 차단한 판매자의 상품만 빠진다
    store = SeenStore(TMP / "s6.db")
    queue = SendQueue([])
    sch = Scheduler(mkcfg(kwcfg()), Scripted([
        [item(1)],
        [item(1), item(2, seller=SELLER), item(3, seller=OTHER)],
    ]), store, queue, None)
    store.block(SELLER, "")
    r = await drive(sch, store, queue, sch._states[0], 2)
    ids = [x[1] for x in r[1]]
    check("차단한 판매자는 빠지고 다른 판매자는 옴", ids == ["m00000000003"], r[1])
    check("차단한 판매자 상품도 기록은 됨", "m00000000002" in store.known_prices("k", ["m00000000002"]))
    store.close()

    # 5-3. 키워드가 달라도 무시는 무시다
    store = SeenStore(TMP / "s7.db")
    queue = SendQueue([])
    a, b = kwcfg("A"), kwcfg("B")
    sch = Scheduler(mkcfg(a, b), Scripted([[item(9)], [item(9), item(1)]]), store, queue, None)
    store.mute("m00000000001", "x")
    for st in sch._states:
        st_script = Scripted([[item(9)], [item(9), item(1)]])
        sch._source = st_script
        r = await drive(sch, store, queue, st, 2)
        check(f"키워드 {st.cfg.name} 에서도 조용함", r[1] == [], r[1])
    store.close()

    # 5-4. 묶음 요약 기준을 셀 때 무시한 것은 빠진다
    store = SeenStore(TMP / "s8.db")
    queue = SendQueue([])
    six = [item(n) for n in range(10, 16)]
    sch = Scheduler(mkcfg(kwcfg(), batch=5), Scripted([[item(1)], [item(1)] + six]), store, queue, None)
    store.mute("m00000000010", "x")
    store.mute("m00000000011", "y")
    r = await drive(sch, store, queue, sch._states[0], 2)
    check("6건 중 2건 무시 → 4건이라 묶지 않고 하나씩", [x[0] for x in r[1]] == ["new"] * 4, r[1])
    store.close()

    # 5-5. 최초 적재 · 0건 진단은 그대로
    store = SeenStore(TMP / "s9.db")
    queue = SendQueue([])
    sch = Scheduler(mkcfg(kwcfg()), Scripted([[item(1), item(2)]]), store, queue, None)
    store.mute("m00000000001", "x")
    r = await drive(sch, store, queue, sch._states[0], 1)
    check("최초 적재는 무시와 상관없이 조용", r == [[]], r)
    check("최초 적재로 둘 다 기록", len(store.known_prices("k", ["m00000000001", "m00000000002"])) == 2)
    store.close()

    # 5-6. 후보가 전부 무시면 알림도 공지도 없다
    store = SeenStore(TMP / "s10.db")
    queue = SendQueue([])
    sch = Scheduler(mkcfg(kwcfg()), Scripted([[item(1)], [item(1), item(2)]]), store, queue, None)
    store.mute("m00000000002", "x")
    r = await drive(sch, store, queue, sch._states[0], 2)
    check("전부 무시면 아무것도 안 감 (공지도 없음)", r[1] == [], r[1])
    store.close()


asyncio.run(scheduler_tests())


# ---------------------------------------------------------------- 6
print("\n=== 6. 명령 ===")
write_env(TMP / ".env", {"TELEGRAM_BOT_TOKEN": "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456",
                         "TELEGRAM_CHAT_ID": "55"})
FakeScheduler = SimpleNamespace(
    stats=SimpleNamespace(uptime_seconds=lambda: 1.0, requests=0, failures=0,
                          new_items=0, price_drops=0),
    keyword_names=[], reload_keywords=lambda kws: None,
)
seen = SeenStore(TMP / "cmd.db")
seen.record("예시 키워드", [item(1, name="<b>위험</b> & 이름"), item(2)])
core = CommandCore(load(), Controls(), FakeScheduler, seen)
run = core.dispatch

listed_before = run("/list")
check("무시·차단이 없으면 /list 는 예전 그대로", "무시 중인" not in listed_before and "차단한" not in listed_before)

check("빈 목록 안내", "무시 중인 상품이 없습니다" in run("/mute"))
reply = run("/mute https://jp.mercari.com/item/m00000000001")
check("주소만 붙여넣어도 무시", "더 알리지 않습니다" in reply, reply[:80])
check("이름의 태그는 이스케이프", "&lt;b&gt;위험&lt;/b&gt; &amp; 이름" in reply, reply[:120])
check("되돌리는 법 안내", "/mute del m00000000001" in reply)
check("또 하면 이미", "이미 무시 중" in run("/mute add m00000000001"))
check("목록에 번호와 함께", "m00000000001" in run("/mute"))
check("기록에 없는 상품도 받음 (번호로 표시)", "m77777777777" in run("/mute add m77777777777"))
check("이상한 값은 안내", "못 읽었습니다" in run("/mute add !!!"))
check("모르는 동작은 사용법", "모르는 사용법" in run("/mute 이상해"))
check("풀기", "다시 알립니다" in run("/mute del m00000000001"))
check("없는 것 풀기", "무시 중인 상품이 아닙니다" in run("/mute del m00000000001"))

reply = run(f"/block add {SELLER} m00000000002")
check("버튼처럼 상품까지 주면 메모를 남김", "c108 学マス ふもふも #2" in reply, reply[:200])
check("프로필 링크", f"user/profile/{SELLER}" in reply)
check("또 하면 이미", "이미 차단한" in run(f"/block {SELLER}"))
check("프로필 주소로도", "차단했습니다" in run(f"/block https://jp.mercari.com/user/profile/{OTHER}"))
check("목록", SELLER in run("/block") and OTHER in run("/block"))
check("상품 번호를 판매자로 주면 안내", "모르는 사용법" in run("/block m00000000001"))

listed = run("/list")
check("/list 에 무시·차단 요약", "무시 중인 상품 1건" in listed and "차단한 판매자 2명" in listed, listed[-200:])
check("/help 에 새 명령", "/mute" in run("/help") and "/block" in run("/help"))
check("풀기", "차단을 풀었습니다" in run(f"/block del {OTHER}"))

# ---------------------------------------------------------------- 7
print("\n=== 7. 텔레그램 — 알림에 버튼 ===")


class Captured:
    def __init__(self):
        self.calls = []

    async def post(self, url, data=None, **_):
        self.calls.append((url.rsplit("/", 1)[-1], data or {}))
        return SimpleNamespace(json=lambda: {"ok": True})

    async def aclose(self):
        pass


async def telegram_tests():
    tg = TelegramNotifier("123:abc", "55")
    tg._client = Captured()

    def markup_of(alert):
        return json.loads(tg._client.calls[-1][1].get("reply_markup") or "null")

    await tg.send(alerts.new_item(item(1), "k", None))
    mk = markup_of(None)
    check("신규 알림에 버튼 둘", mk and len(mk["inline_keyboard"][0]) == 2, mk)
    check("버튼 데이터가 표식 그대로",
          [b["callback_data"] for b in mk["inline_keyboard"][0]]
          == ["mute:m00000000001", f"block:{SELLER}:m00000000001"], mk)

    await tg.send(alerts.price_drop(item(1, 5000), "k", 6800, None))
    check("인하 알림에도 버튼", markup_of(None) is not None)

    for label, alert in [
        ("묶음", alerts.batch([item(n) for n in range(5)], "k", None)),
        ("공지", alerts.notice("t", "b")),
        ("명령 응답", alerts.raw("<b>x</b>", only="telegram")),
    ]:
        await tg.send(alert)
        check(f"{label}에는 버튼 없음", "reply_markup" not in tg._client.calls[-1][1])

    await tg.send(alerts.raw("ok", only="telegram", actions=(("↩ 되돌리기", "unmute:m00000000001"),)))
    check("되돌리기 답장에는 버튼", markup_of(None)["inline_keyboard"][0][0]["callback_data"]
          == "unmute:m00000000001")
    check("render() 는 여전히 (본문, 사진)", len(tg.render(alerts.new_item(item(1), "k", None))) == 2)


asyncio.run(telegram_tests())

# ---------------------------------------------------------------- 8
print("\n=== 8. 텔레그램 — 버튼 누름 받기 ===")


class Q:
    def __init__(self):
        self.items = []

    def put(self, a):
        self.items.append(a)


async def callback_tests():
    seen2 = SeenStore(TMP / "cb.db")
    seen2.record("k", [item(5)])
    core2 = CommandCore(load(), Controls(), FakeScheduler, seen2)
    q = Q()
    tgc = TelegramCommands(load(), core2, q)
    await tgc._client.aclose()
    tgc._client = Captured()

    def query(data, chat="55"):
        return {"id": "q1", "data": data, "message": {"chat": {"id": int(chat)}}}

    await tgc._on_callback(query("mute:m00000000005"))
    answered = [c for c in tgc._client.calls if c[0] == "answerCallbackQuery"]
    check("누르면 반드시 답함 (로딩 표시 멈춤)", len(answered) == 1, tgc._client.calls)
    check("토스트에 태그 없음", "<" not in answered[0][1]["text"], answered[0][1])
    check("실제로 무시됨", seen2.silenced(["m00000000005"], [])[0] == {"m00000000005"})
    check("답장은 텔레그램에만", q.items and q.items[-1].only == "telegram")
    check("답장에 되돌리기 버튼", q.items[-1].actions == (("↩ 되돌리기", "unmute:m00000000005"),))

    await tgc._on_callback(query("unmute:m00000000005"))
    check("되돌리기 누르면 풀림", seen2.silenced(["m00000000005"], [])[0] == set())
    check("되돌린 답장엔 버튼 없음", q.items[-1].actions == ())

    before = len(q.items)
    await tgc._on_callback(query("mute:m00000000005", chat="99999"))
    check("남의 채팅에서 누르면 실행 안 함", seen2.silenced(["m00000000005"], [])[0] == set())
    check("남에게도 답은 함", tgc._client.calls[-1][0] == "answerCallbackQuery")
    check("남에게 답장 안 보냄", len(q.items) == before)

    await tgc._on_callback(query("rm:-rf"))
    check("모르는 버튼은 실행 안 하고 답만", len(q.items) == before
          and "쓰지 않는" in tgc._client.calls[-1][1]["text"])

    # 받는 쪽 설정
    from meralarm.commands import ALLOWED_UPDATES
    check("버튼 누름을 받도록 요청", "callback_query" in ALLOWED_UPDATES)
    check("메시지도 계속 받음", {"message", "edited_message"} <= set(ALLOWED_UPDATES))
    seen2.close()


asyncio.run(callback_tests())

# ---------------------------------------------------------------- 9
print("\n=== 9. 디스코드 — 버튼 ===")
write_env(TMP / ".env", {"DISCORD_BOT_TOKEN": "MTIz.x.y",
                         "DISCORD_CHANNEL_ID": "7", "DISCORD_OWNER_ID": "9"})
bot = DiscordBot("x", 7, 9, CommandCore(load(), Controls(), FakeScheduler, seen))

view = bot._view(item_actions(item(1)))
ids = [c.custom_id for c in view.children]
check("버튼 둘", len(ids) == 2, ids)
check("custom_id 앞머리", all(i.startswith(ACTION_PREFIX) for i in ids), ids)
check("차단은 빨간 버튼", view.children[1].item.style == discord.ButtonStyle.danger
      if hasattr(view.children[1], "item") else view.children[1].style == discord.ButtonStyle.danger)
check("버튼이 없으면 view 도 없음", bot._view(()) is None)

pattern = bot._Action.__discord_ui_compiled_template__
for cid in ids + [ACTION_PREFIX + f"mute:{SHOPS_ID}", ACTION_PREFIX + f"unblock:{SELLER}"]:
    m = pattern.fullmatch(cid)
    check(f"재시작 후에도 알아봄 {cid[:36]}", m is not None and action_to_command(m["token"]) is not None)
check("남의 버튼은 안 붙잡음", pattern.fullmatch("other:mute:m00000000001") is None)


class Interaction:
    def __init__(self, uid=9):
        self.user = SimpleNamespace(id=uid)
        self.sent = []
        self.response = SimpleNamespace(send_message=self._send)

    async def _send(self, text, ephemeral=False):
        self.sent.append((text, ephemeral))


async def discord_tests():
    calls = []

    async def fake_run(interaction, text, actions=()):
        calls.append((text, actions))

    bot._run = fake_run
    await bot._on_action(Interaction(), "mute:m00000000001")
    check("버튼 → 명령", calls[-1][0] == "/mute add m00000000001", calls)
    check("버튼 → 되돌리기 붙임", calls[-1][1] == (("↩ 되돌리기", "unmute:m00000000001"),))

    it = Interaction()
    n = len(calls)
    await bot._on_action(it, "rm:-rf")
    check("모르는 버튼은 실행 안 함", len(calls) == n)
    check("본인에게만 안내", it.sent and it.sent[0][1] is True, it.sent)

    mute = bot._tree.get_command("mute")
    block = bot._tree.get_command("block")
    for cmd, kwargs, want in [
        (mute, {}, "/mute"),
        (mute, {"item": "m00000000001"}, "/mute add m00000000001"),
        (mute, {"action": app_commands.Choice(name="되돌리기", value="del"), "item": "m1x"},
         "/mute del m1x"),
        (block, {}, "/block"),
        (block, {"seller": SELLER}, f"/block add {SELLER}"),
        (block, {"action": app_commands.Choice(name="되돌리기", value="del"), "seller": SELLER},
         f"/block del {SELLER}"),
    ]:
        await cmd.callback(Interaction(), **kwargs)
        check(f"/{cmd.name} {kwargs and list(kwargs.values())[-1]!s:<14} → {want}", calls[-1][0] == want, calls[-1])


asyncio.run(discord_tests())

seen.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 54}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
