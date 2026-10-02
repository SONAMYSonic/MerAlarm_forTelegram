"""키워드별 제외어를 명령으로 고칠 수 있는지."""

import asyncio
import shutil
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
CONFIG = TMP / "config.yaml"

from meralarm.commands import CommandCore  # noqa: E402
from meralarm.config import load  # noqa: E402
from meralarm.config_store import KeywordStore  # noqa: E402
from meralarm.control import Controls  # noqa: E402
from meralarm.models import Item  # noqa: E402
from meralarm.notifiers.discord_bot import DiscordBot  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402
from meralarm.store import SeenStore  # noqa: E402

write_env(TMP / ".env", {"TELEGRAM_BOT_TOKEN": "1:x" * 20, "TELEGRAM_CHAT_ID": "55"})
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


class FakeScheduler:
    stats = SimpleNamespace(
        uptime_seconds=lambda: 1.0, requests=0, failures=0, new_items=0, price_drops=0
    )
    keyword_names = []
    applied = None

    def reload_keywords(self, keywords):
        FakeScheduler.applied = {k.name: k.exclude for k in keywords}


def item(name, i):
    return Item(
        id=f"m{i}", name=name, price=1000, thumbnail="",
        created=datetime.now(), updated=datetime.now(),
        condition_id=1, shipping_payer_id=2, seller_id="s",
    )


CONFIG.write_text(
    "keywords:\n"
    "  - name: 芹沢あさひ\n"
    "    query: 芹沢あさひ\n"
    "    exclude: [セット]\n"
    "  - 黛冬優子\n",  # 간단형(문자열). 조건을 담을 자리가 없다
    encoding="utf-8",
)

seen = SeenStore(TMP / "s.db")
seen.record("芹沢あさひ", [item("芹沢あさひ アクスタ セット", 1), item("芹沢あさひ 単品", 2)])
seen.record("黛冬優子", [item("黛冬優子 まとめ売り", 3), item("黛冬優子 缶バッジ", 4)])

core = CommandCore(load(), Controls(), FakeScheduler(), seen)
store = KeywordStore(CONFIG)


def run(text):
    return core.dispatch(text)


print("\n=== 1. 키워드별 제외어 보기 ===")
shown = run("/exclude 1")
check("이름이 보임", "芹沢あさひ" in shown, shown[:80])
check("그 키워드 제외어가 보임", "セット" in shown, shown[:120])
check("없으면 없다고", "없음" in run("/exclude 2"), run("/exclude 2")[:80])
check("범위 밖 번호는 거절", "1~2" in run("/exclude 9"), run("/exclude 9")[:60])

print("\n=== 2. 넣기 ===")
reply = run("/exclude 2 add まとめ")
check("미리보기가 뜸", "확인해 주세요" in reply, reply[:90])
check("그 키워드 기준으로 셈", "黛冬優子" in reply and "1건" in reply, reply[:160])
check("아직 안 들어감", "まとめ" not in store.keyword_excludes(2)[1])

reply = run("/exclude yes")
check("확인하면 들어감", "넣었습니다" in reply and "黛冬優子" in reply, reply[:90])
check("파일에 적힘", store.keyword_excludes(2)[1] == ["まとめ"])
check("간단형이 상세형으로 바뀜", "name: 黛冬優子" in CONFIG.read_text(encoding="utf-8"))
check("다른 키워드는 그대로", store.keyword_excludes(1)[1] == ["セット"])
check("스케줄러에 반영", FakeScheduler.applied["黛冬優子"] == ("まとめ",), FakeScheduler.applied)

print("\n=== 3. 전역과 섞이지 않는다 ===")
run("/exclude add ジャンク")
check("전역은 전역대로", store.global_excludes() == ["ジャンク"])
check("키워드 파일에는 전역이 안 적힘", store.keyword_excludes(2)[1] == ["まとめ"])
core._cfg = load()
merged = {k.name: set(k.exclude) for k in core._cfg.keywords}
check("실제 필터에는 합쳐짐", merged["黛冬優子"] == {"ジャンク", "まとめ"}, merged)
check("1번도 전역을 물려받음", merged["芹沢あさひ"] == {"ジャンク", "セット"}, merged)

shown = run("/exclude 2")
check("키워드 보기에 전역도 안내됨", "전역 제외어" in shown and "ジャンク" in shown, shown[:200])

print("\n=== 4. 빼기 ===")
reply = run("/exclude 2 del まとめ")
check("뺐다고 알려줌", "뺐습니다" in reply and "黛冬優子" in reply, reply[:80])
check("파일에서 사라짐", store.keyword_excludes(2)[1] == [])
check("빈 exclude 항목이 안 남음", "exclude" not in CONFIG.read_text(encoding="utf-8").split("黛冬優子")[1])
check("전역은 그대로", store.global_excludes() == ["ジャンク"])
check("없는 말은 알려줌", "원래 없던 말" in run("/exclude 1 del 없는말"))

print("\n=== 5. 전각/대소문자도 그대로 통한다 ===")
run("/exclude 1 add B664")
run("/exclude yes") if core._pending else None
check("넣힘", [w.lower() for w in store.keyword_excludes(1)[1]].count("b664") == 1,
      store.keyword_excludes(1)[1])
check("전각으로 빼도 빠짐", "뺐습니다" in run("/exclude 1 del Ｂ664"), run("/exclude 1 del Ｂ664"))

print("\n=== 6. 사용법 안내 ===")
for text, want in [
    ("/exclude 1 이상한동작 x", "모르는 사용법"),
    ("/exclude 1 add", "한 키워드에만"),
    ("/exclude 1 del", "뺄 말을"),
]:
    reply = run(text)
    check(f"{text:<26} → 안내", want in reply, reply[:80])

print("\n=== 7. 디스코드 슬래시 명령 ===")
bot = DiscordBot("MTIz.x.y", 7, 9, core)
cmd = next(c for c in bot._tree.get_commands() if c.name == "exclude")
opts = {o.name: o for o in cmd.parameters}
check("옵션 3개", set(opts) == {"action", "words", "number"}, list(opts))
check("전부 선택 사항", all(not o.required for o in cmd.parameters))
check("number 는 정수", opts["number"].type.name == "integer", opts["number"].type)


class FakeInteraction:
    def __init__(self):
        self.user = SimpleNamespace(id=9)
        self.sent = []
        self.response = SimpleNamespace(
            defer=self._defer, send_message=self._deny
        )
        self.followup = SimpleNamespace(send=self._send)

    async def _defer(self):
        pass

    async def _deny(self, content, ephemeral=False):
        self.sent.append(content)

    async def _send(self, content):
        self.sent.append(content)


sent = []
original = core.dispatch
core.dispatch = lambda text: sent.append(text) or "ok"
for kwargs, want in [
    ({}, "/exclude"),
    ({"number": 2}, "/exclude 2"),
]:
    it = FakeInteraction()
    verb = "list"
    scope = f"{kwargs['number']} " if "number" in kwargs else ""
    asyncio.run(bot._run(it, f"/exclude {scope}".rstrip()))
    check(f"목록 보기 {kwargs} → {want!r}", sent[-1] == want, sent[-1])
core.dispatch = original

seen.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 50}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
