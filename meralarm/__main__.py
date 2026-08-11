"""MerAlarm 진입점.

    python -m meralarm

메루카리를 주기적으로 검색해 처음 보는 상품과 가격이 내려간 상품을 텔레그램으로 알린다.
끄는 방법은 세 가지다. 트레이 아이콘의 "종료", 콘솔에서 Ctrl+C, 작업 스케줄러 중지.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from .commands import CommandCore, TelegramCommands
from .config import Config, ConfigError, ensure_config, ensure_writable, load
from .single_instance import AlreadyRunning, SingleInstance
from .control import Controls
from .notifiers.discord_bot import DiscordBot
from .notifiers.discord_webhook import DiscordNotifier
from .notifiers.queue import SendQueue
from .notifiers.telegram import TelegramNotifier
from . import setup_wizard
from . import __version__
from .scheduler import Scheduler
from .sources.mercapi_source import MercapiSource
from .store import SeenStore
from .tray import Tray

log = logging.getLogger("meralarm")


def use_utf8() -> None:
    """글자 하나 때문에 프로그램이 죽는 일을 막는다.

    출력을 파일로 돌리면(`MerAlarm.exe > log.txt`) 파이썬은 윈도우 기본 코드
    페이지로 쓰려 한다. 한국어 윈도우는 cp949 이고 거기 없는 글자(예: — )를
    만나면 UnicodeEncodeError 로 **그 자리에서 종료된다.** 콘솔에 직접 띄울
    때는 안 나던 것이 로그로 남기려는 순간 터지므로 알아채기도 어렵다.
    """
    for stream in (sys.stdout, sys.stderr):
        if stream is None:
            continue
        try:
            stream.reconfigure(encoding="utf-8", errors="replace")
        except Exception:
            pass  # pythonw 처럼 붙일 수 없는 환경이면 그냥 둔다


def setup_logging(cfg: Config) -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%Y-%m-%d %H:%M:%S")
    file_handler = RotatingFileHandler(
        cfg.log_path, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    file_handler.setFormatter(fmt)
    handlers: list[logging.Handler] = [file_handler]

    # pythonw.exe 로 띄우면 표준 출력이 없다. 그때 콘솔 핸들러를 붙이면 터진다.
    if sys.stderr is not None:
        console = logging.StreamHandler()
        console.setFormatter(fmt)
        handlers.append(console)

    logging.basicConfig(level=logging.INFO, handlers=handlers)

    # httpx 는 요청 URL 을 통째로 INFO 로 찍는다. 텔레그램 API 는 토큰이 URL 경로에
    # 들어가므로 그대로 두면 봇 토큰이 로그 파일에 평문으로 쌓인다.
    logging.getLogger("httpx").setLevel(logging.WARNING)


def build_channels(cfg: Config, core: CommandCore) -> tuple[list, DiscordBot | None]:
    """설정에 맞는 알림 채널을 만든다. (채널 목록, 디스코드 봇)

    **봇과 웹훅이 둘 다 적혀 있으면 봇만 쓴다.** 둘 다 보내면 같은 알림이 두 번
    온다. 그래서 조건문이 아니라 elif 로 두어 구조상 함께 켜질 수 없게 했다.
    """
    channels: list = []
    if cfg.has_telegram:
        channels.append(TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id))

    bot = None
    if cfg.has_discord_bot:
        if cfg.discord_webhook:
            log.info("디스코드 봇이 있으므로 웹훅은 쓰지 않습니다 (알림이 두 번 가지 않도록)")
        bot = DiscordBot(
            cfg.discord_bot_token, cfg.discord_channel_id, cfg.discord_owner_id, core
        )
        channels.append(bot)
    elif cfg.discord_webhook:
        channels.append(DiscordNotifier(cfg.discord_webhook))

    return channels, bot


async def run(cfg: Config) -> None:
    controls = Controls()
    controls.bind(asyncio.get_running_loop())

    store = SeenStore(cfg.db_path)
    # 채널 → 큐 → 스케줄러 → 명령 코어 → 디스코드 봇 순으로 서로를 필요로 한다.
    # 고리를 끊으려고 큐를 먼저 비워둔 채 만들고 채널은 나중에 단다.
    queue = SendQueue([])
    scheduler = Scheduler(cfg, MercapiSource(), store, queue, controls)
    core = CommandCore(cfg, controls, scheduler)
    channels, bot = build_channels(cfg, core)
    for channel in channels:
        queue.add(channel)

    tray = Tray(controls, cfg.log_path, [k.name for k in cfg.keywords])
    tray.start()
    log.info("MerAlarm %s · 알림 채널: %s", __version__, " · ".join(queue.channels))

    telegram_commands = TelegramCommands(cfg, core, queue) if cfg.has_telegram else None

    sender = asyncio.create_task(queue.run(), name="sender")
    watcher = asyncio.create_task(scheduler.run(), name="watcher")
    stopper = asyncio.create_task(controls.wait_stop(), name="stopper")
    # 명령 수신은 있으면 좋은 것이지 감시의 전제가 아니다. 없어도 그대로 돈다.
    listener = (
        asyncio.create_task(telegram_commands.run(), name="telegram-commands")
        if telegram_commands is not None
        else None
    )
    # 봇은 명령만 받는 게 아니라 알림이 나가는 통로이기도 하다. 따로 다룬다.
    bot_task = asyncio.create_task(bot.run(), name="discord-bot") if bot is not None else None

    try:
        # 감시가 끝나거나(예외) 종료 요청이 오거나 둘 중 먼저인 쪽을 따른다.
        done, _ = await asyncio.wait(
            [watcher, stopper], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()  # 감시 쪽에서 터졌다면 여기서 다시 던져진다
    finally:
        for task in (watcher, stopper, listener):
            if task is not None:
                task.cancel()
        # 큐를 먼저 비운 뒤에 전송 작업을 내린다. 순서가 바뀌면 남은 알림이 사라진다.
        await queue.drain(timeout=15)
        sender.cancel()
        # 봇 연결은 큐를 다 비운 뒤에 내린다. 먼저 끊으면 남은 알림이 못 나간다.
        if bot_task is not None:
            bot_task.cancel()
        tray.stop()
        if telegram_commands is not None:
            await telegram_commands.close()
        for target in queue.notifiers:
            await target.close()
        store.close()
        log.info("종료했습니다")


def main() -> None:
    from .config import ROOT

    use_utf8()
    env_path = ROOT / ".env"
    forced = "--setup" in sys.argv

    try:
        ensure_writable()
        # 배포판에는 config.yaml 이 없다. 설정 마법사가 키워드를 적어 넣으려면
        # 그 전에 파일이 있어야 하므로 여기서 미리 만들어 둔다.
        ensure_config(ROOT / "config.yaml", ROOT / "config.example.yaml")
    except ConfigError as e:
        sys.exit(f"[설정 오류] {e}")

    lock = SingleInstance(ROOT / ".meralarm.lock")
    try:
        lock.acquire()
    except AlreadyRunning:
        sys.exit(
            "이미 실행 중입니다.\n"
            "        작업 표시줄 오른쪽 아래의 종 모양 아이콘을 확인하세요.\n"
            "        (안 보이면 ^ 를 눌러 숨겨진 아이콘을 펼치세요)"
        )

    if forced or setup_wizard.needs_setup(env_path):
        if setup_wizard.can_prompt():
            asyncio.run(setup_wizard.run(env_path, ROOT / "config.yaml"))
        elif forced:
            sys.exit("설정 마법사는 콘솔 창에서 실행해야 합니다.")
        # 마법사를 건너뛰었거나 중간에 그만뒀다면 아래 load() 가 무엇이 없는지
        # 알려주고 끝난다. 여기서 곧바로 종료하면 그 안내를 못 보게 된다.

    try:
        cfg = load()
    except ConfigError as e:
        sys.exit(f"[설정 오류] {e}")

    setup_logging(cfg)
    try:
        asyncio.run(run(cfg))
    except KeyboardInterrupt:
        log.info("Ctrl+C 로 종료합니다")
    finally:
        lock.release()


if __name__ == "__main__":
    main()
