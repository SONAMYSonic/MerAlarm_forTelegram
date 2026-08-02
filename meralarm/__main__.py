"""MerAlarm 진입점.

    python -m meralarm

메루카리를 주기적으로 검색해 처음 보는 상품과 가격이 내려간 상품을 텔레그램으로 알린다.
끄는 방법은 세 가지다. 트레이 아이콘의 "종료", 콘솔에서 Ctrl+C, 작업 스케줄러 중지.
"""

import asyncio
import logging
import sys
from logging.handlers import RotatingFileHandler

from .commands import CommandListener
from .config import Config, ConfigError, ensure_writable, load
from .single_instance import AlreadyRunning, SingleInstance
from .control import Controls
from .notifiers.discord import DiscordNotifier
from .notifiers.queue import SendQueue
from .notifiers.telegram import TelegramNotifier
from . import setup_wizard
from .scheduler import Scheduler
from .sources.mercapi_source import MercapiSource
from .store import SeenStore
from .tray import Tray

log = logging.getLogger("meralarm")


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


async def run(cfg: Config) -> None:
    controls = Controls()
    controls.bind(asyncio.get_running_loop())

    store = SeenStore(cfg.db_path)
    notifier = TelegramNotifier(cfg.telegram_token, cfg.telegram_chat_id)
    notifiers = [notifier]
    if cfg.discord_webhook:
        notifiers.append(DiscordNotifier(cfg.discord_webhook))
    queue = SendQueue(notifiers)
    scheduler = Scheduler(cfg, MercapiSource(), store, queue, controls)
    tray = Tray(controls, cfg.log_path, [k.name for k in cfg.keywords])
    tray.start()
    log.info("알림 채널: %s", " · ".join(queue.channels))

    commands = CommandListener(cfg, controls, scheduler, queue)

    sender = asyncio.create_task(queue.run(), name="sender")
    watcher = asyncio.create_task(scheduler.run(), name="watcher")
    stopper = asyncio.create_task(controls.wait_stop(), name="stopper")
    listener = asyncio.create_task(commands.run(), name="commands")

    try:
        # 감시가 끝나거나(예외) 종료 요청이 오거나 둘 중 먼저인 쪽을 따른다.
        done, _ = await asyncio.wait(
            [watcher, stopper], return_when=asyncio.FIRST_COMPLETED
        )
        for task in done:
            task.result()  # 감시 쪽에서 터졌다면 여기서 다시 던져진다
    finally:
        for task in (watcher, stopper, listener):
            task.cancel()
        # 큐를 먼저 비운 뒤에 전송 작업을 내린다. 순서가 바뀌면 남은 알림이 사라진다.
        await queue.drain(timeout=15)
        sender.cancel()
        tray.stop()
        await commands.close()
        for target in notifiers:
            await target.close()
        store.close()
        log.info("종료했습니다")


def main() -> None:
    from .config import ROOT

    env_path = ROOT / ".env"
    forced = "--setup" in sys.argv

    try:
        ensure_writable()
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
