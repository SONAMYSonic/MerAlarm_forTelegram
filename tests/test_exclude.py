"""전역 제외어 검증."""

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
shutil.copy(REPO / "config.example.yaml", CONFIG)

from meralarm import commands as cmdmod  # noqa: E402
from meralarm import filters  # noqa: E402
from meralarm.commands import CommandCore  # noqa: E402
from meralarm.config import ConfigError, load  # noqa: E402
from meralarm.config_store import KeywordStore  # noqa: E402
from meralarm.control import Controls  # noqa: E402
from meralarm.models import Item  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402
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


write_env(TMP / ".env", {"TELEGRAM_BOT_TOKEN": "1:x" * 20, "TELEGRAM_CHAT_ID": "55"})


def item(name, price=1000, i=0):
    return Item(
        id=f"m{i:011d}", name=name, price=price, thumbnail="",
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
        FakeScheduler.applied = keywords


# ---------------------------------------------------------------- 1
print("\n=== 1. config.yaml 의 전역 exclude 가 모든 키워드에 합쳐진다 ===")
CONFIG.write_text(
    "exclude:\n"
    "  - ジャンク\n"
    "  - まとめ\n"
    "\n"
    "keywords:\n"
    "  - name: A\n"
    "    query: A\n"
    "    exclude: [セット]\n"
    "  - B\n",
    encoding="utf-8",
)
cfg = load()
a, b = cfg.keywords
check("전역이 Config 에 있음", cfg.exclude == ("ジャンク", "まとめ"), cfg.exclude)
check("상세형에 합쳐짐", set(a.exclude) == {"ジャンク", "まとめ", "セット"}, a.exclude)
check("간단형에도 적용됨", set(b.exclude) == {"ジャンク", "まとめ"}, b.exclude)
check("키워드 제외어가 전역을 덮어쓰지 않음", "ジャンク" in a.exclude)

print("\n=== 2. 실제로 걸러지는지 ===")
check("전역 제외어에 걸림", not filters.matches(item("シャニマス ジャンク品"), a))
check("전역 제외어에 걸림 (간단형)", not filters.matches(item("まとめ売り 10点"), b))
check("키워드 제외어도 그대로", not filters.matches(item("アクスタ セット"), a))
check("해당 없으면 통과", filters.matches(item("シャニマス アクスタ 単品"), a))
check("대소문자 무시", not filters.matches(item("JUNK ジャンク"), a))

print("\n=== 3. 중복은 한 번만 ===")
CONFIG.write_text(
    "exclude: [まとめ]\nkeywords:\n  - name: A\n    query: A\n    exclude: [まとめ, セット]\n",
    encoding="utf-8",
)
kw = load().keywords[0]
check("같은 말이 두 번 안 들어감", sorted(kw.exclude) == ["まとめ", "セット"], kw.exclude)

print("\n=== 4. 빈 항목이 상품을 삼키지 않는다 (예전 버그) ===")
CONFIG.write_text("exclude:\n  - \n  - ジャンク\nkeywords:\n  - A\n", encoding="utf-8")
kw = load().keywords[0]
check("빈 줄은 걸러짐", kw.exclude == ("ジャンク",), kw.exclude)
check("none 든 상품이 안 사라짐", filters.matches(item("Pokemon none card"), kw))

print("\n=== 5. 목록이 아니면 거절 ===")
CONFIG.write_text("exclude: まとめ\nkeywords:\n  - A\n", encoding="utf-8")
try:
    load()
    check("문자열이면 거절", False, "통과해버림")
except ConfigError as e:
    check("문자열이면 거절", "목록" in str(e), str(e))

# ---------------------------------------------------------------- 6
print("\n=== 6. 명령어로 넣고 빼기 ===")
shutil.copy(REPO / "config.example.yaml", CONFIG)
seen = SeenStore(TMP / "seen.db")
seen.record("예시 키워드", [
    item("シャニマス まとめ売り アクスタ 6点", 9000, 1),
    item("シャニマス アクリルスタンド 単品", 2000, 2),
    item("ポケカ まとめ 30枚", 5000, 3),
])
core = CommandCore(load(), Controls(), FakeScheduler(), seen)


def run(text):
    return core.dispatch(text)


check("처음엔 비어 있음", "전역 제외어가 없습니다" in run("/exclude"), run("/exclude")[:60])

reply = run("/exclude add まとめ")
check("미리보기가 뜸", "확인해 주세요" in reply, reply[:100])
check("걸리는 건수를 알려줌", "2건" in reply, reply)
check("예시 상품을 보여줌", "まとめ売り" in reply, reply)
check("아직 안 들어감", "まとめ" not in KeywordStore(CONFIG).global_excludes())

reply = run("/exclude yes")
check("확인하면 들어감", "넣었습니다" in reply, reply[:80])
check("파일에 적힘", KeywordStore(CONFIG).global_excludes() == ["まとめ"])
check("스케줄러에 반영됨", "まとめ" in FakeScheduler.applied[0].exclude, FakeScheduler.applied[0].exclude)

print("\n=== 7. 걸리는 게 없으면 바로 넣는다 ===")
reply = run("/exclude add 존재하지않는말123")
check("확인 없이 바로", "넣었습니다" in reply, reply[:80])
check("파일에 둘 다", len(KeywordStore(CONFIG).global_excludes()) == 2)

print("\n=== 8. 확인 흐름의 가장자리 ===")
check("확인할 게 없으면 알려줌", "확인할 것이 없습니다" in run("/exclude yes"))

# 걸리는 상품이 있어야 확인 대기 상태가 된다. 없으면 바로 적용된다(7번 참고).
pending_reply = run("/exclude add アクリルスタンド")
check("확인 대기로 들어감", "확인해 주세요" in pending_reply, pending_reply[:80])
core._pending = cmdmod.Pending(
    "exclude", ("アクリルスタンド",), None,
    asked_at=time.monotonic() - cmdmod.PENDING_TTL_SEC - 1,
)
check("만료로 판정됨", core._pending.expired)
check("시간 지나면 취소", "시간이 지나" in run("/exclude yes"))
check("취소됐으면 안 들어감", "アクリルスタンド" not in KeywordStore(CONFIG).global_excludes())
check("한 번 쓴 확인은 재사용 안 됨", "확인할 것이 없습니다" in run("/exclude yes"))

print("\n=== 9. 빼기 ===")
reply = run("/exclude del まとめ")
check("뺐다고 알려줌", "뺐습니다" in reply, reply[:60])
check("파일에서 사라짐", "まとめ" not in KeywordStore(CONFIG).global_excludes())
check("없는 말은 알려줌", "없던 말" in run("/exclude del 없는말"))

print("\n=== 10. 입력 검증 ===")
for text, want in [
    ("/exclude add", "모든 키워드에"),
    ("/exclude 이상한동작 x", "모르는 사용법"),
    ("/exclude del", "뺄 말을"),
    ("/exclude add " + "가" * 60, "너무 깁니다"),
    ("/exclude add " + " ".join(f"w{i}" for i in range(25)), "20개까지"),
]:
    reply = run(text)
    check(f"{text[:34]:<34} → 안내", want in reply, reply[:80])

reply = run("/exclude add -ハイフン")
check("-단어 로 써도 받아줌", "ハイフン" in KeywordStore(CONFIG).global_excludes())

print("\n=== 11. 목록과 설정에 보인다 ===")
run("/exclude add ジャンク")
run("/exclude yes")
listed = run("/list")
check("/list 에 전역 제외어 절이 있음", "전역 제외어" in listed, listed[-150:])
check("/list 에 실제 말이 보임", "ジャンク" in listed)

core._cfg = load()
shown = run("/config")
check("/config 에 전역 제외어", "전역 제외어" in shown and "ジャンク" in shown, shown[:400])

print("\n=== 12. 주석이 살아남는다 ===")
text = CONFIG.read_text(encoding="utf-8")
check("전역 제외어 설명 주석이 붙음", "모든 키워드에 함께 적용되는 제외어" in text, text[:300])
check("원래 주석도 그대로", "MerAlarm 설정" in text)
check("exclude 가 keywords 앞에", text.index("exclude:") < text.index("keywords:"))

print("\n=== 13. 설정이 깨지면 되돌린다 ===")
before = CONFIG.read_text(encoding="utf-8")
store = KeywordStore(CONFIG)


def always_fails():
    raise ConfigError("일부러 실패")


try:
    store.add_global_excludes(["테스트"], validate=always_fails)
    check("검증 실패 시 예외", False, "조용히 통과")
except Exception as e:
    check("검증 실패 시 예외", "되돌렸습니다" in str(e), str(e)[:60])
check("파일이 원래대로", CONFIG.read_text(encoding="utf-8") == before)

seen.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 52}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
