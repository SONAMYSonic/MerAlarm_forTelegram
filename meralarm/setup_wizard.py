"""첫 실행 설정 마법사.

파일을 열어 토큰을 붙여넣으라는 안내는 개발자에게만 통한다. 프로그램을 켜면
무엇을 해야 하는지 차례로 물어보고, 받은 값이 진짜 되는지 그 자리에서 확인한다.

화면이 없는 환경(systemd, pythonw)에서는 아예 시작하지 않는다. 입력을 기다리다
멈춰버리면 서비스가 조용히 죽은 것처럼 보인다.
"""

import asyncio
import re
import sys
from pathlib import Path

import httpx

API = "https://api.telegram.org/bot{token}/{method}"
TOKEN_SHAPE = re.compile(r"^\d{6,}:[A-Za-z0-9_-]{30,}$")

LINE = "─" * 52


def _say(text: str = "") -> None:
    print(text, flush=True)


def can_prompt() -> bool:
    """사람이 답할 수 있는 환경인가."""
    return (
        sys.stdin is not None
        and sys.stdout is not None
        and hasattr(sys.stdin, "isatty")
        and sys.stdin.isatty()
    )


def read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    values = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            values[key.strip()] = value.strip()
    return values


def needs_setup(path: Path) -> bool:
    env = read_env(path)
    return not env.get("TELEGRAM_BOT_TOKEN") or not env.get("TELEGRAM_CHAT_ID")


def write_env(path: Path, token: str, chat_id: str, discord: str = "") -> None:
    path.write_text(
        "# MerAlarm 설정. 이 파일에는 봇 토큰이 들어 있으니 남에게 보내지 마세요.\n"
        "# 다시 설정하려면 프로그램을 --setup 옵션으로 실행하세요.\n"
        "\n"
        f"TELEGRAM_BOT_TOKEN={token}\n"
        f"TELEGRAM_CHAT_ID={chat_id}\n"
        "\n"
        "# (선택) 디스코드에도 함께 보내려면 웹후크 URL 을 넣으세요.\n"
        f"DISCORD_WEBHOOK_URL={discord}\n",
        encoding="utf-8",
    )
    try:
        path.chmod(0o600)  # 리눅스에서 남이 못 읽게. 윈도우에서는 무시된다.
    except OSError:
        pass


# ---- 단계별 ----


async def _ask_token(client: httpx.AsyncClient, known: str = "") -> tuple[str, str] | None:
    if known:
        username = await _check_token(client, known)
        if username:
            _say(f"저장된 봇을 확인했습니다: @{username}\n")
            return known, username
        _say("저장된 토큰이 더 이상 통하지 않습니다. 다시 발급받아 주세요.\n")

    _say("[1/3] 텔레그램 봇 만들기")
    _say()
    _say("  1. 텔레그램에서 @BotFather 를 검색해 대화를 엽니다")
    _say("  2. /newbot 을 보냅니다")
    _say("  3. 봇 이름을 정합니다 (아무거나. 예: 메루카리 알리미)")
    _say("  4. 봇 아이디를 정합니다 (반드시 bot 으로 끝나야 합니다)")
    _say("  5. 123456789:AAE... 처럼 생긴 토큰을 받습니다")
    _say()

    for attempt in range(5):
        try:
            token = input("  토큰을 붙여넣고 Enter (그만두려면 빈 칸): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not token:
            return None
        if not TOKEN_SHAPE.match(token):
            _say("  → 토큰 형태가 아닙니다. 숫자:영문 형태 전체를 붙여넣어 주세요.")
            continue

        _say("  → 확인 중...")
        username = await _check_token(client, token)
        if username:
            _say(f"  → 확인됐습니다: @{username}\n")
            return token, username
        _say("  → 텔레그램이 이 토큰을 거부했습니다. 다시 확인해 주세요.")
    return None


async def _check_token(client: httpx.AsyncClient, token: str) -> str | None:
    try:
        response = await client.get(API.format(token=token, method="getMe"), timeout=15)
        body = response.json()
    except Exception:
        return None
    return body["result"]["username"] if body.get("ok") else None


async def _wait_for_chat(client: httpx.AsyncClient, token: str, username: str) -> str | None:
    _say("[2/3] 봇에게 말 걸기")
    _say()
    _say(f"  텔레그램에서 @{username} 을 열고 아무 메시지나 보내주세요.")
    _say("  (하단의 시작/START 버튼을 눌러도 됩니다)")
    _say()
    _say("  봇은 먼저 말을 걸 수 없어서 이 과정이 필요합니다.")
    _say("  기다리는 중... (그만두려면 Ctrl+C)")

    # 예전에 온 메시지는 건너뛴다. 며칠 전 대화로 엉뚱한 상대를 잡으면 안 된다.
    offset = 0
    try:
        first = await client.get(
            API.format(token=token, method="getUpdates"), params={"timeout": 0}, timeout=20
        )
        updates = first.json().get("result", [])
        if updates:
            offset = updates[-1]["update_id"] + 1
    except Exception:
        pass

    for _ in range(30):  # 최대 약 5분
        try:
            response = await client.get(
                API.format(token=token, method="getUpdates"),
                params={"offset": offset, "timeout": 10},
                timeout=25,
            )
            updates = response.json().get("result", [])
        except (KeyboardInterrupt, asyncio.CancelledError):
            return None
        except Exception:
            await asyncio.sleep(3)
            continue

        for update in updates:
            message = update.get("message") or update.get("edited_message")
            chat = (message or {}).get("chat")
            if chat:
                name = chat.get("first_name") or chat.get("title") or ""
                _say(f"  → 찾았습니다: {name} (chat_id {chat['id']})\n")
                return str(chat["id"])
        if updates:
            offset = updates[-1]["update_id"] + 1

    _say("  → 시간이 초과됐습니다. 다시 실행해 주세요.")
    return None


async def _ask_discord(client: httpx.AsyncClient, known: str = "") -> str:
    """선택 사항. 건너뛰어도 텔레그램만으로 완전히 동작한다."""
    if known:
        return known

    _say("디스코드에도 같이 받으시겠어요? (선택)")
    _say("  받고 싶으시면 디스코드에서 웹후크 URL 을 만들어 붙여넣으세요.")
    _say("    채널 옆 톱니 → 연동 → 웹후크 → 새 웹후크 → 웹후크 URL 복사")
    _say("  필요 없으면 그냥 Enter 를 누르세요.")
    _say()
    try:
        url = input("  웹후크 URL (건너뛰려면 Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if not url:
        _say()
        return ""
    if "discord.com/api/webhooks/" not in url:
        _say("  → 웹후크 주소가 아닌 것 같습니다. 건너뜁니다.\n")
        return ""

    try:
        response = await client.post(
            url, json={"content": "🔔 MerAlarm 연결됐습니다."}, timeout=20
        )
        if response.status_code in (200, 204):
            _say("  → 확인했습니다. 디스코드를 보세요.\n")
            return url
        _say(f"  → 디스코드가 거부했습니다({response.status_code}). 건너뜁니다.\n")
    except Exception:
        _say("  → 연결하지 못했습니다. 건너뜁니다.\n")
    return ""


async def _send_test(client: httpx.AsyncClient, token: str, chat_id: str) -> bool:
    _say("[3/3] 테스트 알림 보내기")
    try:
        response = await client.post(
            API.format(token=token, method="sendMessage"),
            data={
                "chat_id": chat_id,
                "parse_mode": "HTML",
                "text": (
                    "🔔 <b>MerAlarm 설정 완료</b>\n\n"
                    "이 메시지가 보이면 알림이 정상입니다.\n"
                    "/help 를 보내면 사용법을 볼 수 있습니다."
                ),
            },
            timeout=20,
        )
        ok = response.json().get("ok", False)
    except Exception:
        ok = False
    _say("  → 보냈습니다. 텔레그램을 확인해 주세요.\n" if ok else "  → 전송에 실패했습니다.\n")
    return ok


def _ask_keyword(config_path: Path) -> None:
    _say("감시할 키워드를 하나 정해주세요.")
    _say("  메루카리에서 검색하듯 쓰면 됩니다. 일본어가 잘 잡힙니다.")
    _say("  비워두고 Enter 하면 나중에 텔레그램에서 /add 로 추가할 수 있습니다.")
    _say()
    try:
        keyword = input("  키워드: ").strip()
    except (EOFError, KeyboardInterrupt):
        return
    if not keyword:
        return

    try:
        from .config_store import KeywordStore

        KeywordStore(config_path).add(keyword)
        _say(f"  → '{keyword}' 를 추가했습니다.\n")
    except Exception as e:
        _say(f"  → 추가하지 못했습니다({e}). 나중에 /add 로 넣어주세요.\n")


# ---- 전체 흐름 ----


async def run(env_path: Path, config_path: Path) -> bool:
    _say()
    _say(LINE)
    _say("  MerAlarm 첫 설정")
    _say(LINE)
    _say()
    _say("알림을 받을 텔레그램 봇이 필요합니다. 3분이면 끝납니다.")
    _say()

    existing = read_env(env_path)
    async with httpx.AsyncClient() as client:
        result = await _ask_token(client, existing.get("TELEGRAM_BOT_TOKEN", ""))
        if result is None:
            _say("설정을 중단했습니다.")
            return False
        token, username = result

        chat_id = existing.get("TELEGRAM_CHAT_ID", "")
        if not chat_id:
            chat_id = await _wait_for_chat(client, token, username)
            if not chat_id:
                _say("설정을 중단했습니다.")
                return False

        discord = await _ask_discord(client, existing.get("DISCORD_WEBHOOK_URL", ""))
        write_env(env_path, token, chat_id, discord)
        await _send_test(client, token, chat_id)

    _ask_keyword(config_path)

    _say(LINE)
    _say("  설정이 끝났습니다. 감시를 시작합니다.")
    _say(LINE)
    _say()
    return True
