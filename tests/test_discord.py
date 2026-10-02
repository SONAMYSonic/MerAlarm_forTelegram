"""디스코드 봇 붙이기 검증.

실제 .env 를 절대 건드리지 않는다. 임시 폴더에 가짜 설정을 만들어 쓴다.
"""

import asyncio
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod

# 실제 설정을 읽기 전에 뿌리를 임시 폴더로 돌린다. 이 줄이 먼저 와야 한다.
TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
shutil.copy(REPO / "config.example.yaml", TMP / "config.yaml")

from meralarm import alerts  # noqa: E402
from meralarm.__main__ import build_channels  # noqa: E402
from meralarm.commands import CommandCore, TelegramCommands  # noqa: E402
from meralarm.config import ConfigError, load  # noqa: E402
from meralarm.control import Controls  # noqa: E402
from meralarm.notifiers import markup  # noqa: E402
from meralarm.notifiers.discord_bot import DiscordBot  # noqa: E402
from meralarm.notifiers.discord_embed import DESCRIPTION_LIMIT, build  # noqa: E402
from meralarm.notifiers.discord_webhook import DiscordNotifier  # noqa: E402
from meralarm.notifiers.queue import SendQueue  # noqa: E402
from meralarm.notifiers.telegram import TelegramNotifier  # noqa: E402
from meralarm.setup_wizard import needs_setup, read_env, write_env  # noqa: E402

ok = fail = 0

TG = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"
# 진짜 디스코드 토큰 모양으로 적으면 GitHub 이 비밀값으로 보고 푸시를 막는다.
# 앱은 토큰 모양을 따지지 않으니 짧은 가짜로 충분하다.
BOT = "MTIz.x.y"
HOOK = "https://discord.com/api/webhooks/1/xxxx"


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def env(**kw):
    return {k: v for k, v in kw.items() if v}


def write(**kw):
    write_env(TMP / ".env", kw)


# ---------------------------------------------------------------- 1
print("\n=== 1. 채널 설정 검증 ===")

cases = [
    ("텔레그램만", env(TELEGRAM_BOT_TOKEN=TG, TELEGRAM_CHAT_ID="55"), None),
    ("웹훅만", env(DISCORD_WEBHOOK_URL=HOOK), None),
    (
        "봇만",
        env(DISCORD_BOT_TOKEN=BOT, DISCORD_CHANNEL_ID="7", DISCORD_OWNER_ID="9"),
        None,
    ),
    (
        "셋 다",
        env(
            TELEGRAM_BOT_TOKEN=TG,
            TELEGRAM_CHAT_ID="55",
            DISCORD_WEBHOOK_URL=HOOK,
            DISCORD_BOT_TOKEN=BOT,
            DISCORD_CHANNEL_ID="7",
            DISCORD_OWNER_ID="9",
        ),
        None,
    ),
    ("아무것도 없음", {}, "하나도 없습니다"),
    ("텔레그램 반쪽", env(TELEGRAM_BOT_TOKEN=TG), "TELEGRAM_CHAT_ID"),
    ("chat_id 만", env(TELEGRAM_CHAT_ID="55"), "TELEGRAM_BOT_TOKEN"),
    ("봇에 채널 없음", env(DISCORD_BOT_TOKEN=BOT, DISCORD_OWNER_ID="9"), "DISCORD_CHANNEL_ID"),
    ("봇에 주인 없음", env(DISCORD_BOT_TOKEN=BOT, DISCORD_CHANNEL_ID="7"), "DISCORD_OWNER_ID"),
    (
        "채널 번호가 숫자 아님",
        env(DISCORD_BOT_TOKEN=BOT, DISCORD_CHANNEL_ID="일반채널", DISCORD_OWNER_ID="9"),
        "숫자",
    ),
]

for label, values, want_error in cases:
    try:
        cfgmod._channels(values)
        got = None
    except ConfigError as e:
        got = str(e)
    if want_error is None:
        check(label, got is None, f"거절당함: {got}")
    else:
        check(label, got is not None and want_error in got, f"실제: {got}")

# ---------------------------------------------------------------- 2
print("\n=== 2. 마법사가 다시 뜨는 조건 ===")
path = TMP / ".env"
for label, values, want in [
    ("아무것도 없으면 뜬다", {}, True),
    ("텔레그램 있으면 안 뜬다", {"TELEGRAM_BOT_TOKEN": TG, "TELEGRAM_CHAT_ID": "55"}, False),
    ("웹훅만 있어도 안 뜬다", {"DISCORD_WEBHOOK_URL": HOOK}, False),
    ("봇만 있어도 안 뜬다", {"DISCORD_BOT_TOKEN": BOT, "DISCORD_CHANNEL_ID": "7"}, False),
    ("텔레그램 반쪽이면 뜬다", {"TELEGRAM_BOT_TOKEN": TG}, True),
]:
    write_env(path, values)
    check(label, needs_setup(path) is want)

print("\n=== 3. .env 왕복 ===")
full = {
    "TELEGRAM_BOT_TOKEN": TG,
    "TELEGRAM_CHAT_ID": "55",
    "DISCORD_WEBHOOK_URL": HOOK,
    "DISCORD_BOT_TOKEN": BOT,
    "DISCORD_CHANNEL_ID": "7",
    "DISCORD_OWNER_ID": "9",
}
write_env(path, full)
back = read_env(path)
check("쓴 값이 그대로 읽힌다", all(back.get(k) == v for k, v in full.items()), back)
check("주석은 값으로 안 읽힌다", "#" not in "".join(back.keys()))

# ---------------------------------------------------------------- 4
print("\n=== 4. 봇이 있으면 웹훅은 쓰지 않는다 ===")


class FakeScheduler:
    stats = SimpleNamespace(
        uptime_seconds=lambda: 3600.0, requests=10, failures=0, new_items=2, price_drops=1
    )
    keyword_names = ["가", "나"]

    def reload_keywords(self, keywords):
        pass


controls = Controls()
core_for_build = None


def channels_for(**values):
    write(**values)
    cfg = load()
    global core_for_build
    core_for_build = CommandCore(cfg, controls, FakeScheduler())
    names, bot = build_channels(cfg, core_for_build)
    return [type(n).__name__ for n in names], bot


names, bot = channels_for(
    TELEGRAM_BOT_TOKEN=TG,
    TELEGRAM_CHAT_ID="55",
    DISCORD_WEBHOOK_URL=HOOK,
    DISCORD_BOT_TOKEN=BOT,
    DISCORD_CHANNEL_ID="7",
    DISCORD_OWNER_ID="9",
)
check("봇+웹훅 → 웹훅 빠짐", names == ["TelegramNotifier", "DiscordBot"], names)
check("봇 객체가 돌아옴", bot is not None)

names, bot = channels_for(TELEGRAM_BOT_TOKEN=TG, TELEGRAM_CHAT_ID="55", DISCORD_WEBHOOK_URL=HOOK)
check("봇 없으면 웹훅 씀", names == ["TelegramNotifier", "DiscordNotifier"], names)
check("봇 없음", bot is None)

names, bot = channels_for(DISCORD_BOT_TOKEN=BOT, DISCORD_CHANNEL_ID="7", DISCORD_OWNER_ID="9")
check("텔레그램 없이 봇만", names == ["DiscordBot"], names)

names, _ = channels_for(TELEGRAM_BOT_TOKEN=TG, TELEGRAM_CHAT_ID="55")
check("텔레그램만", names == ["TelegramNotifier"], names)

check(
    "채널 이름은 둘 다 discord",
    DiscordNotifier(HOOK).name == DiscordBot(BOT, 7, 9, core_for_build).name == "discord",
)

# ---------------------------------------------------------------- 5
print("\n=== 5. 슬래시 명령 등록 ===")
write(DISCORD_BOT_TOKEN=BOT, DISCORD_CHANNEL_ID="7", DISCORD_OWNER_ID="9")
cfg = load()
core = CommandCore(cfg, controls, FakeScheduler())
bot = DiscordBot(BOT, 7, 9, core)

registered = {c.name for c in bot._tree.get_commands()}
want = {"status", "list", "config", "help", "add", "del", "set", "exclude", "require",
        "mute", "block", "pause", "resume"}
check(f"명령 {len(registered)}개 등록", registered == want, f"차이 {registered ^ want}")
check("이름이 전부 소문자", all(n == n.lower() for n in registered))
check("이름이 32자 이내", all(1 <= len(n) <= 32 for n in registered))

for command in bot._tree.get_commands():
    desc_ok = 1 <= len(command.description) <= 100
    check(f"/{command.name} 설명 길이", desc_ok, repr(command.description))

# ---------------------------------------------------------------- 6
print("\n=== 6. 주인만 명령을 쓸 수 있다 ===")


class FakeResponse:
    def __init__(self):
        self.sent = []
        self.deferred = False

    async def send_message(self, content, ephemeral=False):
        self.sent.append((content, ephemeral))

    async def defer(self):
        self.deferred = True


class FakeFollowup:
    def __init__(self):
        self.sent = []

    async def send(self, content):
        self.sent.append(content)


class FakeInteraction:
    def __init__(self, user_id):
        self.user = SimpleNamespace(id=user_id)
        self.response = FakeResponse()
        self.followup = FakeFollowup()


async def run_as(user_id, text):
    interaction = FakeInteraction(user_id)
    await bot._run(interaction, text)
    return interaction


stranger = asyncio.run(run_as(1234, "/add 몰래추가"))
check("남이 치면 거절", stranger.response.sent and "주인만" in stranger.response.sent[0][0])
check("거절은 본인에게만 보이게", stranger.response.sent[0][1] is True)
check("거절이면 실행 안 함", not stranger.response.deferred)
check("설정 파일 안 바뀜", "몰래추가" not in (TMP / "config.yaml").read_text(encoding="utf-8"))

owner = asyncio.run(run_as(9, "/status"))
check("주인이면 먼저 시간을 번다", owner.response.deferred)
check("답장이 온다", len(owner.followup.sent) == 1, owner.followup.sent)
check("마크다운으로 바뀌어 있다", "<b>" not in owner.followup.sent[0])

# ---------------------------------------------------------------- 7
print("\n=== 7. 모든 명령이 디스코드에서 돈다 ===")
commands = [
    "/help",
    "/status",
    "/list",
    "/config",
    "/set",
    "/set interval 45",
    "/set age 7",
    "/set bump 0",
    "/set krw off",
    "/set 1 price_max 50000",
    "/set 1 price_max off",
    "/set 없는항목 1",
    "/add 테스트키워드 -제외1 -제외2",
    "/add 나쁜입력 -제외1 제외2",
    "/pause 30m",
    "/resume",
    "/모르는명령",
]
for text in commands:
    interaction = asyncio.run(run_as(9, text))
    parts = interaction.followup.sent
    body = "".join(parts)
    good = (
        interaction.response.deferred
        and parts
        and all(0 < len(p) <= markup.LIMIT for p in parts)
        and "<b>" not in body
        and "</" not in body
        and "처리 중 오류" not in body
    )
    check(f"{text:<28} → {len(parts)}통 {len(body):>4}자", good, body[:120])

check("추가한 키워드가 실제로 적혔다", "테스트키워드" in (TMP / "config.yaml").read_text(encoding="utf-8"))
check("애매한 입력은 안 적혔다", "나쁜입력" not in (TMP / "config.yaml").read_text(encoding="utf-8"))

deleted = asyncio.run(run_as(9, "/del 1"))
check("/del 도 된다", deleted.followup.sent and "중단" in deleted.followup.sent[0])

# ---------------------------------------------------------------- 8
print("\n=== 8. 텔레그램은 그대로 동작한다 (회귀) ===")
write(TELEGRAM_BOT_TOKEN=TG, TELEGRAM_CHAT_ID="55")
tg_cfg = load()
tg_core = CommandCore(tg_cfg, controls, FakeScheduler())
queue = SendQueue([TelegramNotifier(TG, "55")])
listener = TelegramCommands(tg_cfg, tg_core, queue)

listener._on_update({"message": {"chat": {"id": 55}, "text": "/status"}})
check("주인 명령이 큐에 들어감", queue.pending == 1)

listener._on_update({"message": {"chat": {"id": 999}, "text": "/add 남의명령"}})
check("남의 chat_id 는 무시", queue.pending == 1)

listener._on_update({"message": {"chat": {"id": 55}, "text": "안녕"}})
check("명령이 아니면 무시", queue.pending == 1)

alert = queue._queue.get_nowait()
check("텔레그램에만 간다", alert.only == "telegram")
check("텔레그램은 HTML 그대로", "<b>" in alert.body, alert.body[:80])

telegram = TelegramNotifier(TG, "55")
text, photo = telegram.render(alert)
check("텔레그램 렌더가 안 깨짐", text == alert.body and photo is None)

# ---------------------------------------------------------------- 9
print("\n=== 9. 임베드 (옮긴 뒤 회귀) ===")
from datetime import datetime  # noqa: E402

from meralarm.models import Item  # noqa: E402


def item(name="상품", price=3000, i=0):
    return Item(
        id=f"m{i:011d}",
        name=name,
        price=price,
        thumbnail="http://x/t.jpg",
        created=datetime.now(),
        updated=datetime.now(),
        condition_id=1,
        shipping_payer_id=2,
        seller_id="s",
    )


NAME = "シャイニーカラーズ わくドキくじ 黛冬優子 アクリルスタンド 未開封"

for label, alert in [
    ("신규", alerts.new_item(item(NAME), "黛冬優子", 9.05)),
    ("인하", alerts.price_drop(item(NAME), "黛冬優子", 9999, 9.05)),
    ("공지", alerts.notice("제목", "본문")),
]:
    embed = build(alert)
    check(f"{label} 임베드에 None 없음", None not in embed.values(), embed)

for count in (5, 40, 120, 400):
    embed = build(alerts.batch([item(NAME, 3000 + i, i) for i in range(count)], "kw", 9.05))
    size = len(embed["description"])
    check(f"묶음 {count:3}건 → 본문 {size:4}자", size <= DESCRIPTION_LIMIT)

big = build(alerts.batch([item(NAME, 3000, i) for i in range(400)], "kw", 9.05))
check("잘릴 때 생략 안내", "생략했습니다" in big["description"], big["description"][-80:])
check("전체 건수는 제목에", big["title"] == "신규 400건")

print("\n=== 10. 봇이 안 붙었으면 알림을 붙잡지 않는다 ===")
stuck = DiscordBot(BOT, 7, 9, core)
stuck._failed = True
check("연결 실패 상태면 즉시 실패 반환", asyncio.run(stuck.send(alerts.notice("가", "나"))) is False)

shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 52}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
