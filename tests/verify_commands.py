"""텔레그램 봇 명령어 검증. 실제 config.yaml 을 쓰고 끝나면 원복한다."""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm.commands import CommandCore, TelegramCommands, _humanize, _parse_duration
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
BACKUP = Path(tempfile.mkdtemp()) / "config.backup.yaml"

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
    def __init__(self):
        self.messages = []

    def put(self, message):
        self.messages.append(message)


async def main():
    shutil.copy(CONFIG, BACKUP)
    original = CONFIG.read_text(encoding="utf-8")
    comments_before = len([l for l in original.splitlines() if l.strip().startswith("#")])

    try:
        cfg = load()
        controls = Controls()
        controls.bind(asyncio.get_running_loop())
        store = SeenStore(Path(tempfile.mkdtemp()) / "t.db")
        queue = FakeQueue()
        sched = Scheduler(cfg, None, store, queue, controls)
        cmd = CommandCore(cfg, controls, sched)
        tg = TelegramCommands(cfg, cmd, queue)

        print("\n=== 1. 시간 파싱 ===")
        check("30m → 1800초", _parse_duration("30m") == 1800)
        check("2h → 7200초", _parse_duration("2h") == 7200)
        check("90s → 90초", _parse_duration("90s") == 90)
        check("단위 없으면 분", _parse_duration("15") == 900)
        check("이상한 값은 None", _parse_duration("abc") is None)
        check("0 이하는 None", _parse_duration("0") is None)
        check("표시 변환", _humanize(7200) == "2시간" and _humanize(1800) == "30분",
              f"{_humanize(7200)} / {_humanize(1800)}")

        print("\n=== 2. 조회 명령 ===")
        check("/help", "명령어" in cmd.dispatch("/help"))
        check("/status 상태 표시", "감시 중" in cmd.dispatch("/status"))
        listed = cmd.dispatch("/list")
        check("/list 에 키워드 3개", listed.count("\n1.") + listed.count("\n2.") + listed.count("\n3.") >= 3
              or all(n in listed for n in sched.keyword_names), listed[:80])
        check("모르는 명령 안내", "모르는 명령" in cmd.dispatch("/nonsense"))

        print("\n=== 3. 일시정지 ===")
        check("/pause", "멈췄" in cmd.dispatch("/pause") and controls.paused)
        check("/resume", "다시 시작" in cmd.dispatch("/resume") and not controls.paused)
        cmd.dispatch("/pause 30m")
        check("시한부 정지", controls.paused and 1700 < controls.resume_in <= 1801,
              f"남은 {controls.resume_in}")
        check("/status 에 남은 시간", "뒤 재개" in cmd.dispatch("/status"))
        controls.resume()
        check("잘못된 시간 안내", "못 읽었" in cmd.dispatch("/pause 어제"))

        print("\n=== 4. 키워드 추가 ===")
        before = len(sched.keyword_names)
        reply = cmd.dispatch("/add 樋口円香")
        check("추가 응답", "추가했습니다" in reply, reply[:60])
        check("스케줄러에 즉시 반영", len(sched.keyword_names) == before + 1,
              f"{before} → {len(sched.keyword_names)}")
        check("config.yaml 에 기록", "樋口円香" in CONFIG.read_text(encoding="utf-8"))
        check("중복 추가 거부", "이미 감시 중" in cmd.dispatch("/add 樋口円香"))
        check("빈 인자 안내", "사용법" in cmd.dispatch("/add"))

        print("\n=== 5. 주석 보존 ===")
        after = CONFIG.read_text(encoding="utf-8")
        comments_after = len([l for l in after.splitlines() if l.strip().startswith("#")])
        check("주석이 그대로", comments_after == comments_before,
              f"{comments_before} → {comments_after}")
        check("기존 설정 유지", "max_age_days: 30" in after)

        print("\n=== 6. 키워드 삭제 ===")
        target = sched.keyword_names.index("樋口円香") + 1
        reply = cmd.dispatch(f"/del {target}")
        check("삭제 응답", "중단했습니다" in reply, reply[:60])
        check("스케줄러에서 제거", "樋口円香" not in sched.keyword_names)
        check("config.yaml 에서 제거", "樋口円香" not in CONFIG.read_text(encoding="utf-8"))
        check("범위 밖 번호 거부", "번호는" in cmd.dispatch("/del 99"))
        check("숫자 아니면 안내", "사용법" in cmd.dispatch("/del 하나"))

        print("\n=== 7. 보안 — 남의 chat_id 는 무시 ===")
        queue.messages.clear()
        tg._on_update({"message": {"chat": {"id": 99999999}, "text": "/add 침입자"}})
        check("응답하지 않음", len(queue.messages) == 0, f"got {len(queue.messages)}")
        check("설정도 안 바뀜", "침입자" not in CONFIG.read_text(encoding="utf-8"))

        tg._on_update({"message": {"chat": {"id": int(cfg.telegram_chat_id)}, "text": "/list"}})
        check("본인 chat_id 는 응답", len(queue.messages) == 1)
        await tg.close()
        store.close()

    finally:
        shutil.copy(BACKUP, CONFIG)
        restored = CONFIG.read_text(encoding="utf-8")
        print(f"\nconfig.yaml 원복: {'OK' if restored == original else 'FAILED'}")

    print(f"\n{'=' * 44}\n통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)


asyncio.run(main())
