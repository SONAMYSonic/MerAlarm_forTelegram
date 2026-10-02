"""필수 포함어 검증. 실제로 겪은 ストレイライト 사례로 확인한다."""

import shutil
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
CONFIG = TMP / "config.yaml"

from meralarm import commands as cmdmod  # noqa: E402
from meralarm import filters  # noqa: E402
from meralarm.commands import CommandCore  # noqa: E402
from meralarm.config import ConfigError, load  # noqa: E402
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


def item(name, i=0):
    return Item(
        id=f"m{i}", name=name, price=1000, thumbnail="",
        created=datetime.now(), updated=datetime.now(),
        condition_id=1, shipping_payer_id=2, seller_id="s",
    )


class FakeScheduler:
    stats = SimpleNamespace(
        uptime_seconds=lambda: 1.0, requests=0, failures=0, new_items=0, price_drops=0
    )
    keyword_names = []
    applied = None

    def reload_keywords(self, keywords):
        FakeScheduler.applied = {k.name: k for k in keywords}


# 실제로 걸려서 알림이 왔던 제목들
JUNK = [
    "Stray Kids(ストレイキッズ・スキズ・SKZ) ペンライト 2世代",
    "straykids skzoo ペンライトカバー Ver.2 フォクシニー",
    "【GYDA】ハイウエストレイヤードデザイン2WAYデニムパンツ",
    "エクストレイル メガネホルダー T33系 サングラスホルダー (X-TRAIL)ts",
    "日産ローグ、エクストレイル、T30、T31 フォグライトベゼルカバー",
    "文豪ストレイドッグス★ライトノベル 9巻セット★朝霧カフカ",
    "BANDAI SPIRITS MG ガンダムアストレイレッドフレーム",
    "ステンドグラス　ミニトレイ 小物入れ　ガラストレイ　茶グリーン",
]
REAL = [
    "ストレイライト缶バッジセット",
    "ストレイライト ペンライト2nd",
    "【箔押し】シャニマス ぱしゃこれ ストレイライト 芹沢あさひ",
    "アイドルマスターシャイニーカラーズ プレイマット ストレイライト 未開封",
    "シャニマス シャイニーカラーズ 和泉愛依 グッズセット",
]

print("\n=== 1. 설정 파일에서 읽힌다 ===")
CONFIG.write_text(
    "keywords:\n"
    "  - name: ストレイライト\n"
    "    query: ストレイライト\n"
    "    require: [ストレイライト, シャニマス, アイドルマスター, シャイニーカラーズ]\n"
    "  - 黛冬優子\n",
    encoding="utf-8",
)
cfg = load()
kw, plain = cfg.keywords
check("require 가 읽힘", len(kw.require) == 4, kw.require)
check("안 적으면 비어 있음", plain.require == (), plain.require)

print("\n=== 2. 실제 쓰레기가 전부 걸러진다 ===")
kept_junk = [n for n in JUNK if filters.matches(item(n), kw)]
check(f"쓰레기 {len(JUNK)}건 → {len(kept_junk)}건", not kept_junk, kept_junk)

print("\n=== 3. 진짜 물건은 전부 통과한다 ===")
lost = [n for n in REAL if not filters.matches(item(n), kw)]
check(f"진짜 {len(REAL)}건 전부 통과", not lost, lost)

print("\n=== 4. 하나라도 맞으면 통과 (OR) ===")
check("첫 말만 맞아도", filters.matches(item("ストレイライト 缶バッジ"), kw))
check("마지막 말만 맞아도", filters.matches(item("シャイニーカラーズ アクスタ"), kw))
check("하나도 안 맞으면 제외", not filters.matches(item("ポケモンカード リザードン"), kw))

print("\n=== 5. 추적할 때도 적용된다 ===")
check(
    "관심 없는 물건은 아예 안 본다",
    not filters.matches(item("エクストレイル LED"), kw, ignore_freshness=True),
)

print("\n=== 6. 제외어와 함께 쓸 때 ===")
CONFIG.write_text(
    "exclude: [ジャンク]\n"
    "keywords:\n"
    "  - name: A\n"
    "    query: A\n"
    "    require: [シャニマス]\n"
    "    exclude: [セット]\n",
    encoding="utf-8",
)
kw2 = load().keywords[0]
check("필수 통과 + 제외 안 걸림", filters.matches(item("シャニマス アクスタ 単品"), kw2))
check("필수는 통과했지만 제외에 걸림", not filters.matches(item("シャニマス アクスタ セット"), kw2))
check("필수는 통과했지만 전역 제외에 걸림", not filters.matches(item("シャニマス ジャンク品"), kw2))
check("제외에 안 걸려도 필수가 없으면 제외", not filters.matches(item("ポケカ 単品"), kw2))

print("\n=== 7. 목록이 아니면 거절 ===")
CONFIG.write_text("keywords:\n  - name: A\n    query: A\n    require: シャニマス\n", encoding="utf-8")
try:
    load()
    check("문자열이면 거절", False, "통과해버림")
except ConfigError as e:
    check("문자열이면 거절", "require" in str(e) and "목록" in str(e), str(e)[:70])

# ------------------------------------------------------------------ 명령어
print("\n=== 8. 명령으로 넣기 ===")
CONFIG.write_text(
    "keywords:\n  - name: ストレイライト\n    query: ストレイライト\n  - 黛冬優子\n",
    encoding="utf-8",
)
seen = SeenStore(TMP / "s.db")
seen.record("ストレイライト", [item(n, i) for i, n in enumerate(JUNK + REAL)])
core = CommandCore(load(), Controls(), FakeScheduler(), seen)
store = KeywordStore(CONFIG)


def run(text):
    return core.dispatch(text)


check("처음엔 없다고", "없습니다" in run("/require 1"), run("/require 1")[:70])

reply = run("/require 1 add ストレイライト シャニマス アイドルマスター シャイニーカラーズ")
check("미리보기가 뜸", "확인해 주세요" in reply, reply[:90])
check("남는 수와 사라지는 수", "5건만 남고 8건이 사라집니다" in reply, reply[:250])
def sample_lines(text, header):
    """'남는 것' / '사라지는 것' 아래에 붙은 예시 줄만 뽑는다."""
    body = text.split(header, 1)[1] if header in text else ""
    out = []
    for line in body.splitlines()[1:]:
        if not line.startswith("    "):
            break
        out.append(line.strip())
    return out


kept_shown = sample_lines(reply, "남는 것")
drop_shown = sample_lines(reply, "사라지는 것")
check("남는 예시가 붙음", len(kept_shown) == 3, kept_shown)
check("남는 예시는 전부 진짜 물건", all(any(n.startswith(x[:20]) for n in REAL) for x in kept_shown), kept_shown)
check("사라지는 예시가 붙음", len(drop_shown) == 3, drop_shown)
check("사라지는 예시는 전부 쓰레기", all(any(n.startswith(x[:20]) for n in JUNK) for x in drop_shown), drop_shown)
check("아직 안 들어감", store.keyword_words(1, "require")[1] == [])

reply = run("/require yes")
check("확인하면 들어감", "넣었습니다" in reply, reply[:80])
check("파일에 적힘", len(store.keyword_words(1, "require")[1]) == 4)
check("스케줄러에 반영", len(FakeScheduler.applied["ストレイライト"].require) == 4)

print("\n=== 9. 말을 더하면 조건이 느슨해진다 (OR) ===")
reply = run("/require 1 add 절대없는말")
check("남는 수가 그대로", "5건만 남고" in reply, reply[:200])
run("/require yes")
check("들어감", "절대없는말" in store.keyword_words(1, "require")[1])

print("\n=== 10. 빼기 ===")
reply = run("/require 1 del 절대없는말")
check("뺐다고 알려줌", "뺐습니다" in reply, reply[:80])
check("없는 말은 알려줌", "원래 없던 말" in run("/require 1 del 없는말"))

for w in ["ストレイライト", "シャニマス", "アイドルマスター", "シャイニーカラーズ"]:
    run(f"/require 1 del {w}")
check("다 빼면 조건 없음 안내", "따지지 않고" in run("/require 1"), run("/require 1")[:90])
check("빈 require 항목이 안 남음", "require" not in CONFIG.read_text(encoding="utf-8"))

print("\n=== 10-2. 전부 사라질 때 경고한다 ===")
# 조건이 하나도 없는 상태에서 아무것도 안 맞는 말을 넣으면 그 키워드가 통째로 조용해진다.
reply = run("/require 1 add 절대없는말")
check("0건 남는다고 알림", "0건만 남고 13건이 사라집니다" in reply, reply[:220])
check("남는 게 없다고 경고", "남는 것이 하나도 없습니다" in reply, reply[-260:])
run("/require yes")
check("경고해도 넣기는 넣는다", store.keyword_words(1, "require")[1] == ["절대없는말"])
run("/require 1 del 절대없는말")

# 그 키워드로 받아온 상품이 아직 없으면 셀 것이 없어 곧바로 넣는다
reply = run("/require 2 add シャニマス")
check("기록이 없으면 미리보기 없이 바로", "넣었습니다" in reply, reply[:80])
run("/require 2 del シャニマス")

print("\n=== 11. 사용법·검증 ===")
for text, want in [
    ("/require", "필수 포함어"),
    ("/require add シャニマス", "번호를 앞에"),
    ("/require 1 이상한동작 x", "모르는 사용법"),
    ("/require 1 add", "필수 포함어"),
    ("/require 1 del", "뺄 말을"),
    ("/require 9 add x", "1~2"),
    ("/require yes", "확인할 것이 없습니다"),
]:
    reply = run(text)
    check(f"{text:<30} → 안내", want in reply, reply[:90])

print("\n=== 12. 확인은 종류를 가린다 ===")
run("/require 1 add シャニマス")
check("require 대기 중에 exclude yes 는 안 먹음", "확인할 것이 없습니다" in run("/exclude yes"))
check("그래도 require yes 는 먹음", "넣었습니다" in run("/require yes"), "")
run("/require 1 del シャニマス")

print("\n=== 13. 만료 ===")
run("/require 1 add シャニマス")
core._pending = cmdmod.Pending(
    "require", ("シャニマス",), 1,
    asked_at=time.monotonic() - cmdmod.PENDING_TTL_SEC - 1,
)
check("만료로 판정됨", core._pending.expired)
check("시간 지나면 취소", "시간이 지나" in run("/require yes"))
check("취소됐으면 안 들어감", store.keyword_words(1, "require")[1] == [])

print("\n=== 14. 목록에 보인다 ===")
run("/require 1 add シャニマス")
run("/require yes")
listed = run("/list")
check("/list 에 필수 표시", "필수:" in listed and "シャニマス" in listed, listed[:220])
shown = run("/exclude 1")
check("/exclude 1 에도 안내", "필수 포함어" in shown, shown[:220])
core._cfg = load()
check("/config 에도", "필수" in run("/config"), run("/config")[:400])

print("\n=== 15. 디스코드 슬래시 명령 ===")
bot = DiscordBot("MTIz.x.y", 7, 9, core)
names = {c.name for c in bot._tree.get_commands()}
check("/require 등록", "require" in names, names)
cmd = next(c for c in bot._tree.get_commands() if c.name == "require")
opts = {o.name for o in cmd.parameters}
check("옵션 3개", opts == {"number", "action", "words"}, opts)
check("설명 100자 이내", len(cmd.description) <= 100, cmd.description)

seen.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 52}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
