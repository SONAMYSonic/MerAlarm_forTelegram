"""/add 의 제외어 문법 검증. 애매한 입력을 정말로 거절하는지가 핵심."""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm import filters
from meralarm.commands import CommandCore, ParseError, parse_add
from meralarm.config import load
from meralarm.control import Controls
from meralarm.models import Item
from meralarm.scheduler import Scheduler
from meralarm.store import SeenStore

# 실제 config.yaml 을 건드리지 않는다. 예전에는 고쳤다가 finally 에서 되돌렸는데,
# 도중에 프로세스가 끊기면 사용자 설정에 테스트 흔적이 남는 구조였다.
import meralarm.config as _cfgmod  # noqa: E402

_ROOT = Path(tempfile.mkdtemp())
_cfgmod.ROOT = _ROOT
(_ROOT / ".env").write_text(
    "TELEGRAM_BOT_TOKEN=123456789:AAEtesttesttesttesttesttesttest000\nTELEGRAM_CHAT_ID=55\n",
    encoding="utf-8",
)
(_ROOT / "config.yaml").write_text(
    (REPO / "config.example.yaml").read_text(encoding="utf-8")
    + "  - name: 두번째\n    query: 두번째\n",
    encoding="utf-8",
)
CONFIG = _ROOT / "config.yaml"
BACKUP = Path(tempfile.mkdtemp()) / "backup.yaml"
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def accepts(text, expect_query, expect_ex):
    try:
        q, e = parse_add(text)
    except ParseError as err:
        check(f"{text!r}", False, f"거절됨: {err}")
        return
    check(f"{text!r} → {q!r} {e}", q == expect_query and e == expect_ex,
          f"got {q!r} {e}")


def rejects(text, why):
    try:
        q, e = parse_add(text)
        check(f"{text!r} 거절", False, f"통과해버림 → {q!r} {e}")
    except ParseError as err:
        check(f"{text!r} 거절 · {why}", True)
        print(f"        └ {str(err).splitlines()[0]}")


class FakeQueue:
    def __init__(self): self.messages = []
    def put(self, m): self.messages.append(m)


async def main():
    shutil.copy(CONFIG, BACKUP)
    original = CONFIG.read_text(encoding="utf-8")

    print("\n=== 1. 정상 입력 ===")
    accepts("芹沢あさひ", "芹沢あさひ", ())
    accepts("芹沢あさひ -セット", "芹沢あさひ", ("セット",))
    accepts("芹沢あさひ -セット -まとめ", "芹沢あさひ", ("セット", "まとめ"))
    accepts("芹沢あさひ -セット,まとめ", "芹沢あさひ", ("セット", "まとめ"))
    accepts("芹沢あさひ -セット,まとめ -詰め合わせ", "芹沢あさひ",
            ("セット", "まとめ", "詰め合わせ"))
    accepts("ガンプラ MG -ジャンク", "ガンプラ MG", ("ジャンク",))
    accepts("  芹沢あさひ   -セット  ", "芹沢あさひ", ("セット",))
    accepts("芹沢あさひ -セット -セット", "芹沢あさひ", ("セット",))
    accepts("Re-ZERO", "Re-ZERO", ())

    print("\n=== 2. 애매하면 거절 ===")
    rejects("芹沢あさひ -セット まとめ", "대시 빠뜨림 (가장 흔한 실수)")
    rejects("-セット", "키워드 없음")
    rejects("-セット 芹沢あさひ", "제외어가 먼저")
    rejects("芹沢あさひ -", "대시만 있음")
    rejects("芹沢あさひ -セット,", "쉼표 뒤가 빔")
    rejects("芹沢あさひ -セット, まとめ", "쉼표 뒤 띄어쓰기")
    rejects("芹沢あさひ -,セット", "쉼표 앞이 빔")
    rejects("芹沢あさひ --セット", "대시 두 개")
    rejects("", "빈 입력")
    rejects("芹沢あさひ -" + "가" * 60, "제외어 길이 초과")
    rejects("芹沢あさひ " + " ".join(f"-{i}" for i in range(25)), "제외어 개수 초과")

    print("\n=== 3. 실제 추가와 필터 동작 ===")
    try:
        cfg = load()
        controls = Controls()
        controls.bind(asyncio.get_running_loop())
        store = SeenStore(Path(tempfile.mkdtemp()) / "t.db")
        sched = Scheduler(cfg, None, store, FakeQueue(), controls)
        cmd = CommandCore(cfg, controls, sched)

        reply = cmd.dispatch("/add 樋口円香 -セット,まとめ -ジャンク")
        check("추가 성공", "추가했습니다" in reply, reply[:70])
        check("응답에 제외어 표시", "セット" in reply and "ジャンク" in reply)

        fresh = load()
        added = next(k for k in fresh.keywords if k.name == "樋口円香")
        check("설정에 제외어 3개 저장", added.exclude == ("セット", "まとめ", "ジャンク"),
              f"got {added.exclude}")

        def item(name):
            from datetime import datetime
            return Item(id="m1", name=name, price=1000, thumbnail="",
                        created=datetime.now(), updated=datetime.now(),
                        condition_id=1, shipping_payer_id=2, seller_id="s")

        check("제외어 걸린 상품 차단", not filters.matches(item("樋口円香 セット"), added))
        check("제외어 없는 상품 통과", filters.matches(item("樋口円香 アクリルスタンド"), added))

        listed = cmd.dispatch("/list")
        check("/list 에 제외어 표시", "제외:" in listed and "ジャンク" in listed, listed[:120])

        reply = cmd.dispatch("/add 樋口円香 -別の")
        check("중복은 거절하고 안내", "이미 감시 중" in reply and "/del" in reply)

        bad = cmd.dispatch("/add 樋口円香2 -セット まとめ")
        check("대시 빠뜨리면 추가 안 함", "-가 빠진" in bad, bad[:70])
        check("잘못된 입력은 설정에 안 적힘",
              "樋口円香2" not in CONFIG.read_text(encoding="utf-8"))

        idx = [k.name for k in load().keywords].index("樋口円香") + 1
        cmd.dispatch(f"/del {idx}")
        store.close()
    finally:
        shutil.copy(BACKUP, CONFIG)
        print(f"\nconfig.yaml 원복: "
              f"{'OK' if CONFIG.read_text(encoding='utf-8') == original else 'FAILED'}")

    print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)


asyncio.run(main())
