"""/set 과 /config 검증. 잘못된 값이 설정 파일을 깨뜨리지 않는지가 핵심."""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm.commands import CommandCore
from meralarm.config import load
from meralarm.control import Controls
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


class FakeQueue:
    def __init__(self): self.messages = []
    def put(self, m): self.messages.append(m)


async def main():
    shutil.copy(CONFIG, BACKUP)
    original = CONFIG.read_text(encoding="utf-8")
    comments_before = len([l for l in original.splitlines() if l.strip().startswith("#")])

    try:
        cfg = load()
        controls = Controls()
        controls.bind(asyncio.get_running_loop())
        store = SeenStore(Path(tempfile.mkdtemp()) / "t.db")
        sched = Scheduler(cfg, None, store, FakeQueue(), controls)
        cmd = CommandCore(cfg, controls, sched)

        print("\n=== 1. 전역 설정 ===")
        check("주기 변경", "60" in cmd.dispatch("/set interval 60"))
        check("실제로 반영", load().poll.default_interval_sec == 60)

        check("스위치 끄기", "꺼짐" in cmd.dispatch("/set krw off"))
        check("실제로 반영", load().notify.show_krw is False)
        cmd.dispatch("/set krw on")
        check("스위치 켜기", load().notify.show_krw is True)

        check("나이 제한 해제", "지정 안 함" in cmd.dispatch("/set age off"))
        check("null 로 반영", load().notify.max_age_days is None)
        cmd.dispatch("/set age 30")
        check("나이 제한 복구", load().notify.max_age_days == 30)

        print("\n=== 2. 키워드별 설정 ===")
        reply = cmd.dispatch("/set 1 price_max 50000")
        check("가격 상한 설정", "50,000" in reply, reply[:60])
        check("해당 키워드만 반영", load().keywords[0].price_max == 50000
              and load().keywords[1].price_max is None)

        cmd.dispatch("/set 1 price_max off")
        check("해제하면 사라짐", load().keywords[0].price_max is None)

        cmd.dispatch("/set 2 interval 90")
        check("키워드별 주기", load().keywords[1].interval_sec == 90)
        cmd.dispatch("/set 2 interval off")
        check("해제하면 전역값 상속",
              load().keywords[1].interval_sec == load().poll.default_interval_sec)

        print("\n=== 3. 잘못된 입력 거절 ===")
        check("모르는 항목", "모르는 항목" in cmd.dispatch("/set 없는항목 1"))
        check("숫자 아님", "숫자여야" in cmd.dispatch("/set interval 빠르게"))
        check("범위 밖(너무 짧음)", "10~3600" in cmd.dispatch("/set interval 3"))
        check("스위치에 숫자 아님", "on 또는 off" in cmd.dispatch("/set krw 아마도"))
        check("키워드 번호 범위 밖", "번호는" in cmd.dispatch("/set 99 price_max 100"))
        check("인자 부족은 사용법", "사용법" in cmd.dispatch("/set interval"))
        check("빈 인자는 사용법", "사용법" in cmd.dispatch("/set"))

        print("\n=== 4. 안전장치 — 깨지는 설정은 되돌린다 ===")
        before = CONFIG.read_text(encoding="utf-8")
        # keep_days(60) 보다 큰 age 는 검증에서 걸려야 한다
        reply = cmd.dispatch("/set age 100")
        check("모순된 설정 거절", "되돌렸습니다" in reply, reply[:80])
        check("파일이 원래대로", CONFIG.read_text(encoding="utf-8") == before)
        check("설정이 여전히 읽힘", load().notify.max_age_days == 30)

        print("\n=== 5. 주석 보존 ===")
        after = CONFIG.read_text(encoding="utf-8")
        comments_after = len([l for l in after.splitlines() if l.strip().startswith("#")])
        check("주석이 그대로", comments_after == comments_before,
              f"{comments_before} → {comments_after}")

        print("\n=== 6. /config 표시 ===")
        shown = cmd.dispatch("/config")
        check("전역 설정 표시", "감시 주기" in shown and "기록 보관" in shown)
        check("키워드 표시", all(k.name in shown for k in load().keywords))
        check("바꾸는 법 안내", "/set" in shown)
        print("\n" + shown.replace("<b>", "").replace("</b>", "")
              .replace("<i>", "").replace("</i>", ""))
        store.close()
    finally:
        shutil.copy(BACKUP, CONFIG)
        restored = CONFIG.read_text(encoding="utf-8")
        print(f"\nconfig.yaml 원복: {'OK' if restored == original else 'FAILED'}")

    print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)


asyncio.run(main())
