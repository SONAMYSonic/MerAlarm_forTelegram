"""나이·끌어올림·가격·제외어 필터 회귀.

전역 제외어를 넣으면서 `_parse_keyword` 를 손댔다. 그 길로 함께 지나가는
조건들이 예전과 똑같이 동작하는지 다시 덮는다.
"""

import shutil
import sys
import tempfile
from datetime import datetime, timedelta
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
CONFIG = TMP / "config.yaml"

from meralarm import filters  # noqa: E402
from meralarm.config import ConfigError, load  # noqa: E402
from meralarm.models import TYPE_SHOPS, Item  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402

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


def item(name="상품", price=1000, age=0, bump=0, cond=1, payer=2, shops=False):
    created = datetime.now() - timedelta(days=age)
    return Item(
        id="m1", name=name, price=price, thumbnail="",
        created=created, updated=created + timedelta(days=bump),
        condition_id=cond, shipping_payer_id=payer, seller_id="s",
        item_type=TYPE_SHOPS if shops else "ITEM_TYPE_MERCARI",
    )


def write(text):
    CONFIG.write_text(text, encoding="utf-8")
    return load()


print("\n=== 1. 전역 기본값 상속 ===")
cfg = write(
    "notify:\n  max_age_days: 30\n  max_bump_days: 0\n"
    "poll:\n  default_interval_sec: 45\n"
    "keywords:\n  - A\n  - name: B\n    query: B\n    max_age_days: 7\n    interval_sec: 90\n"
)
a, b = cfg.keywords
check("age 를 물려받음", a.max_age_days == 30, a.max_age_days)
check("bump 를 물려받음", a.max_bump_days == 0, a.max_bump_days)
check("주기를 물려받음", a.interval_sec == 45, a.interval_sec)
check("키워드가 age 를 덮어씀", b.max_age_days == 7, b.max_age_days)
check("키워드가 주기를 덮어씀", b.interval_sec == 90, b.interval_sec)
check("덮어써도 bump 는 전역대로", b.max_bump_days == 0, b.max_bump_days)

print("\n=== 2. age 와 bump ===")
kw = write("notify:\n  max_age_days: 30\n  max_bump_days: 0\nkeywords:\n  - A\n").keywords[0]
check("오늘 출품 → 알림", filters.matches(item(age=0, bump=0), kw))
check("5일 전 출품, 그대로 → 알림", filters.matches(item(age=5, bump=0), kw))
check("5일 전 출품, 오늘 끌어올림 → 안 알림", not filters.matches(item(age=5, bump=5), kw))
check("40일 전 출품 → 안 알림", not filters.matches(item(age=40, bump=0), kw))
check("추적할 때는 나이를 안 본다", filters.matches(item(age=400, bump=9), kw, ignore_freshness=True))

kw = write("notify:\n  max_age_days: null\n  max_bump_days: null\nkeywords:\n  - A\n").keywords[0]
check("null 이면 제한 없음", filters.matches(item(age=900, bump=800), kw))

print("\n=== 3. 가격·상태·배송비 ===")
kw = write(
    "keywords:\n  - name: A\n    query: A\n    price_min: 1000\n    price_max: 5000\n"
    "    conditions: [1, 2]\n    shipping_payers: [2]\n"
).keywords[0]
check("하한 미만 제외", not filters.matches(item(price=999), kw))
check("하한 경계 통과", filters.matches(item(price=1000), kw))
check("상한 경계 통과", filters.matches(item(price=5000), kw))
check("상한 초과 제외", not filters.matches(item(price=5001), kw))
check("허용 상태 통과", filters.matches(item(price=2000, cond=2), kw))
check("불허 상태 제외", not filters.matches(item(price=2000, cond=4), kw))
check("배송비 부담자 다름 → 제외", not filters.matches(item(price=2000, payer=1), kw))

print("\n=== 4. 잘못된 설정은 거절 ===")
for label, text, want in [
    ("keywords 없음", "poll:\n  default_interval_sec: 30\n", "keywords"),
    ("price_min > price_max", "keywords:\n  - name: A\n    query: A\n    price_min: 900\n    price_max: 100\n", "price_min"),
    ("주기가 너무 짧음", "poll:\n  default_interval_sec: 3\nkeywords:\n  - A\n", "10 이상"),
    ("이름 중복", "keywords:\n  - A\n  - A\n", "중복"),
    ("conditions 범위 밖", "keywords:\n  - name: A\n    query: A\n    conditions: [9]\n", "1~6"),
    ("keep_days 가 age 보다 짧음", "store:\n  keep_days: 10\nnotify:\n  max_age_days: 30\nkeywords:\n  - A\n", "keep_days"),
    ("heartbeat 범위 밖", "notify:\n  heartbeat_hour: 99\nkeywords:\n  - A\n", "0~23"),
]:
    try:
        write(text)
        check(label, False, "통과해버림")
    except ConfigError as e:
        check(label, want in str(e), str(e)[:70])

print("\n=== 5. 전역 제외어가 다른 조건과 함께 ===")
cfg = write(
    "exclude: [ジャンク]\n"
    "notify:\n  max_age_days: 30\n  max_bump_days: 0\n"
    "keywords:\n  - name: A\n    query: A\n    price_max: 5000\n    exclude: [セット]\n"
)
kw = cfg.keywords[0]
check("전역+키워드 둘 다 살아있음", set(kw.exclude) == {"ジャンク", "セット"}, kw.exclude)
check("전역 제외어에 걸림", not filters.matches(item("ジャンク品 アクスタ"), kw))
check("키워드 제외어에 걸림", not filters.matches(item("アクスタ セット"), kw))
check("제외어는 추적할 때도 적용된다", not filters.matches(item("ジャンク品"), kw, ignore_freshness=True))
check("가격 조건도 그대로", not filters.matches(item("アクスタ", price=9000), kw))
check("아무 데도 안 걸리면 통과", filters.matches(item("アクスタ 単品", price=3000), kw))

print("\n=== 6. apply 가 목록을 거른다 ===")
items = [
    item("정상 상품", price=2000),
    item("ジャンク 상품", price=2000),
    item("비싼 상품", price=9000),
    item("セット 상품", price=2000),
]
kept = filters.apply(items, kw)
check("4건 중 1건만 남음", len(kept) == 1, [i.name for i in kept])
check("남은 것이 정상 상품", kept[0].name == "정상 상품")

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
