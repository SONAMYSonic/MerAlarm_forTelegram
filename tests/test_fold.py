"""제외어가 대소문자·전각/반각을 가리지 않는지."""
import shutil, sys, tempfile, time
from datetime import datetime
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import meralarm.config as cfgmod
TMP = Path(tempfile.mkdtemp()); cfgmod.ROOT = TMP
CONFIG = TMP / "config.yaml"

from meralarm import filters                       # noqa: E402
from meralarm.config import load                   # noqa: E402
from meralarm.models import Item                   # noqa: E402
from meralarm.setup_wizard import write_env        # noqa: E402
from meralarm.store import SeenStore               # noqa: E402
from meralarm.text import fold                     # noqa: E402

write_env(TMP / ".env", {"TELEGRAM_BOT_TOKEN": "1:x" * 20, "TELEGRAM_CHAT_ID": "55"})
ok = fail = 0
def check(label, cond, detail=""):
    global ok, fail
    if cond: ok += 1; print(f"  [OK] {label}")
    else: fail += 1; print(f"  [NG] {label} {detail}")

def item(name):
    return Item(id="m1", name=name, price=1000, thumbnail="",
                created=datetime.now(), updated=datetime.now(),
                condition_id=1, shipping_payer_id=2, seller_id="s")

def kw_with(word):
    CONFIG.write_text(f"exclude: ['{word}']\nkeywords:\n  - A\n", encoding="utf-8")
    return load().keywords[0]

print("\n=== 1. 대소문자 ===")
for excl, title in [
    ("C108", "シャニマス c108 アクスタ"),
    ("c108", "シャニマス C108 アクスタ"),
    ("PSA", "psa10 鑑定品"),
    ("psa", "PSA10 鑑定品"),
    ("Ver", "オフレコVER. 1/8"),
]:
    check(f"제외어 {excl!r} → 제목 {title[:18]!r}", not filters.matches(item(title), kw_with(excl)))

print("\n=== 2. 전각 영숫자 (실제 상품명에 있음) ===")
for excl, title in [
    ("B664", "Ｂ664　ヴァイスシュヴァルツ アイドルマスター"),
    ("b664", "Ｂ664　ヴァイスシュヴァルツ"),
    ("Ｂ664", "B664 ヴァイス"),
    ("vol.2", "ちびぐるみ vol.２　和泉愛依"),
    ("3枚", "SR★（スーパーレア★）３枚セット"),
]:
    check(f"제외어 {excl!r} → {title[:20]!r}", not filters.matches(item(title), kw_with(excl)))

print("\n=== 3. 반각 가타카나·장음 (실제 상품명에 있음) ===")
for excl, title in [
    ("アルター", "アルタｰ 黛冬優子 オフレコVer."),
    ("アクスタ", "ｱｸｽﾀ 6点セット"),
]:
    check(f"제외어 {excl!r} → {title[:18]!r}", not filters.matches(item(title), kw_with(excl)))

print("\n=== 4. 안 걸려야 할 것은 그대로 통과 ===")
kw = kw_with("ジャンク")
for title in ["シャニマス アクスタ 単品", "C109 カード", "美品 未開封"]:
    check(f"통과: {title[:18]!r}", filters.matches(item(title), kw))

print("\n=== 5. 미리보기가 필터와 같은 답을 낸다 ===")
seen = SeenStore(TMP / "s.db")
names = [
    "Ｂ664　ヴァイスシュヴァルツ アイドルマスター",
    "B664 別の商品",
    "アルタｰ 黛冬優子 オフレコVer.",
    "シャニマス アクスタ 単品",
    "c108 グッズ",
]
seen.record("A", [Item(id=f"m{i}", name=n, price=1000, thumbnail="",
                       created=datetime.now(), updated=datetime.now(),
                       condition_id=1, shipping_payer_id=2, seller_id="s")
                  for i, n in enumerate(names)])
for word in ["B664", "b664", "Ｂ664", "アルター", "C108", "ジャンク"]:
    hits, total, _ = seen.matching_names(word)
    kw = kw_with(word)
    real = sum(1 for n in names if not filters.matches(item(n), kw))
    check(f"{word:<8} 미리보기 {hits}건 = 실제 {real}건", hits == real, f"{hits} vs {real}")

print("\n=== 6. 속도 ===")
big = [Item(id=f"m{i}", name=f"シャニマス 商品名 その{i} アクリルスタンド {i}", price=1000,
            thumbnail="", created=datetime.now(), updated=datetime.now(),
            condition_id=1, shipping_payer_id=2, seller_id="s") for i in range(120)]
kw = kw_with("ジャンク")
start = time.perf_counter()
for _ in range(100):
    filters.apply(big, kw)
per = (time.perf_counter() - start) / 100 * 1000
check(f"120건 필터 1회 {per:.2f}ms", per < 20, f"{per:.2f}ms")

print("\n=== 7. fold 자체 ===")
check("전각 → 반각", fold("Ｂ664") == "b664", fold("Ｂ664"))
check("반각 가나 → 전각", fold("ｱｸｽﾀ") == "アクスタ", fold("ｱｸｽﾀ"))
check("전각 공백 → 보통 공백", fold("まとめ　売り") == "まとめ 売り")
check("한글은 그대로", fold("아크릴") == "아크릴")
check("일본어 한자 그대로", fold("黛冬優子") == "黛冬優子")

seen.close(); shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
