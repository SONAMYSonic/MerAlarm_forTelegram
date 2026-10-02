"""한쪽에서 바꾼 설정이 다른 쪽에도 반영되는지."""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
shutil.copy(REPO / "config.example.yaml", TMP / "config.yaml")

from meralarm.commands import CommandCore, TelegramCommands  # noqa: E402
from meralarm.config import load  # noqa: E402
from meralarm.control import Controls  # noqa: E402
from meralarm.notifiers.discord_bot import DiscordBot  # noqa: E402
from meralarm.notifiers.queue import SendQueue  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


write_env(
    TMP / ".env",
    {
        "TELEGRAM_BOT_TOKEN": "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456",
        "TELEGRAM_CHAT_ID": "55",
        "DISCORD_BOT_TOKEN": "MTIz.x.y",
        "DISCORD_CHANNEL_ID": "7",
        "DISCORD_OWNER_ID": "9",
    },
)


class FakeScheduler:
    """설정이 실제로 다시 실린 횟수를 센다."""

    stats = SimpleNamespace(
        uptime_seconds=lambda: 3600.0, requests=1, failures=0, new_items=0, price_drops=0
    )
    keyword_names = []
    reloads = 0
    last = None

    def reload_keywords(self, keywords):
        FakeScheduler.reloads += 1
        FakeScheduler.last = [k.name for k in keywords]


# ---- 실제 조립과 똑같이: 코어 하나를 두 채널이 함께 쓴다 ----
cfg = load()
controls = Controls()
sched = FakeScheduler()
core = CommandCore(cfg, controls, sched)

queue = SendQueue([])
telegram = TelegramCommands(cfg, core, queue)
discord_bot = DiscordBot("MTIz.x.y", 7, 9, core)


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.deferred = False

    async def send_message(self, content, ephemeral=False):
        self.sent.append(content)

    async def defer(self):
        self.deferred = True


class FakeInteraction:
    def __init__(self):
        self.user = SimpleNamespace(id=9)
        self.response = FakeResponse()
        self.followup = SimpleNamespace(sent=[])

        async def send(content):
            self.followup.sent.append(content)

        self.followup.send = send


def via_discord(text):
    it = FakeInteraction()
    asyncio.run(discord_bot._run(it, text))
    return "".join(it.followup.sent)


def via_telegram(text):
    before = queue.pending
    telegram._on_update({"message": {"chat": {"id": 55}, "text": text}})
    assert queue.pending == before + 1
    return queue._queue.get_nowait().body


def yaml_text():
    return (TMP / "config.yaml").read_text(encoding="utf-8")


print("\n=== 1. 디스코드에서 키워드를 넣으면 텔레그램에서도 보이나 ===")
added = via_discord("/add 디스코드에서추가")
check("디스코드가 추가했다고 답함", "추가했습니다" in added, added[:80])
check("설정 파일에 실제로 적힘", "디스코드에서추가" in yaml_text())
listed = via_telegram("/list")
check("텔레그램 /list 에 보임", "디스코드에서추가" in listed, listed[:120])

print("\n=== 2. 텔레그램에서 넣으면 디스코드에서도 보이나 ===")
via_telegram("/add 텔레그램에서추가")
listed = via_discord("/list")
check("디스코드 /list 에 보임", "텔레그램에서추가" in listed, listed[:120])

print("\n=== 3. 설정값도 양쪽에 반영되나 ===")
via_discord("/set interval 47")
shown = via_telegram("/config")
check("디스코드가 바꾼 주기를 텔레그램이 봄", "47초" in shown, shown[:160])

via_telegram("/set bump 3")
shown = via_discord("/config")
check("텔레그램이 바꾼 bump 를 디스코드가 봄", "3" in shown and "끌어올림" in shown)

print("\n=== 4. 감시 엔진에도 즉시 실리나 (재시작 없이) ===")
before = FakeScheduler.reloads
via_discord("/add 즉시반영확인")
check("디스코드 명령이 스케줄러를 다시 실음", FakeScheduler.reloads == before + 1)
check("새 키워드가 감시 목록에 있음", "즉시반영확인" in (FakeScheduler.last or []),
      FakeScheduler.last)

print("\n=== 5. 일시정지는 프로그램 전체에 걸리나 ===")
via_discord("/pause")
check("디스코드가 멈추면 전체가 멈춤", controls.paused)
resumed = via_telegram("/resume")
check("텔레그램이 풀면 전체가 풀림", not controls.paused and "다시 시작" in resumed)

print("\n=== 6. 답장은 물어본 곳에만 간다 ===")
via_telegram("/status")
check("텔레그램 답장은 큐가 비어야 정상(이미 꺼냄)", queue.pending == 0)
telegram._on_update({"message": {"chat": {"id": 55}, "text": "/status"}})
alert = queue._queue.get_nowait()
check("텔레그램 답장은 telegram 에만", alert.only == "telegram")
it = FakeInteraction()
asyncio.run(discord_bot._run(it, "/status"))
check("디스코드 답장은 큐를 안 거침", queue.pending == 0 and it.followup.sent)

print("\n=== 7. 삭제도 양쪽에서 ===")


def names(listing: str) -> list[str]:
    """텔레그램 쪽 `1. <b>이름</b>` 줄에서 이름만 뽑는다."""
    out = []
    for line in listing.splitlines():
        line = line.strip()
        if line and line[0].isdigit() and ". <b>" in line:
            out.append(line.split("<b>", 1)[1].split("</b>", 1)[0])
    return out


before_names = names(via_telegram("/list"))
check("목록을 읽어냄", len(before_names) >= 2, before_names)
check("디스코드가 넣은 것이 목록에 있음", "디스코드에서추가" in before_names, before_names)

target = before_names.index("디스코드에서추가") + 1
removed = via_telegram(f"/del {target}")
check("텔레그램이 지웠다고 답함", "중단했습니다" in removed, removed[:80])

after_discord = via_discord("/list")
after_names = names(via_telegram("/list"))
check(
    "디스코드 목록에서도 사라짐",
    "디스코드에서추가" not in after_discord,
    after_discord[:150],
)
check("키워드 수가 하나 줄어듦", len(after_names) == len(before_names) - 1,
      f"{before_names} → {after_names}")
check("설정 파일에서도 사라짐", "디스코드에서추가" not in yaml_text())
check("남은 키워드는 그대로", "텔레그램에서추가" in after_discord)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 50}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
