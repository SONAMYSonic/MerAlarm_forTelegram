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

DISCORD_API = "https://discord.com/api/v10"
# 권한 18432 = 메시지 보내기(2048) + 링크 임베드(16384). 딱 이만큼만 달라고 한다.
DISCORD_INVITE = (
    "https://discord.com/oauth2/authorize"
    "?client_id={app_id}&scope=bot+applications.commands&permissions=18432"
)
# 글을 쓸 수 있는 채널 종류. 0 은 일반 텍스트, 5 는 공지 채널.
TEXT_CHANNELS = (0, 5)

DISCORD_KEYS = (
    "DISCORD_WEBHOOK_URL",
    "DISCORD_BOT_TOKEN",
    "DISCORD_CHANNEL_ID",
    "DISCORD_OWNER_ID",
)

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
    """알림 받을 곳이 하나도 없을 때만 마법사를 띄운다.

    예전에는 텔레그램이 없으면 무조건 띄웠다. 디스코드만 쓰는 사람에게는 켤 때마다
    설정을 다시 하라고 하는 꼴이 된다.
    """
    env = read_env(path)
    telegram = env.get("TELEGRAM_BOT_TOKEN") and env.get("TELEGRAM_CHAT_ID")
    discord_bot = env.get("DISCORD_BOT_TOKEN") and env.get("DISCORD_CHANNEL_ID")
    return not (telegram or discord_bot or env.get("DISCORD_WEBHOOK_URL"))


def write_env(path: Path, values: dict[str, str]) -> None:
    def get(key: str) -> str:
        return values.get(key, "") or ""

    path.write_text(
        "# MerAlarm 설정. 이 파일에는 봇 토큰이 들어 있으니 남에게 보내지 마세요.\n"
        "# 다시 설정하려면 프로그램을 --setup 옵션으로 실행하세요.\n"
        "\n"
        "# --- 텔레그램 ---\n"
        f"TELEGRAM_BOT_TOKEN={get('TELEGRAM_BOT_TOKEN')}\n"
        f"TELEGRAM_CHAT_ID={get('TELEGRAM_CHAT_ID')}\n"
        "\n"
        "# --- 디스코드 (선택) ---\n"
        "# 웹훅은 알림만, 봇은 알림과 명령어를 모두 합니다.\n"
        "# 둘 다 적혀 있으면 봇만 씁니다. 안 그러면 같은 알림이 두 번 옵니다.\n"
        f"DISCORD_WEBHOOK_URL={get('DISCORD_WEBHOOK_URL')}\n"
        f"DISCORD_BOT_TOKEN={get('DISCORD_BOT_TOKEN')}\n"
        f"DISCORD_CHANNEL_ID={get('DISCORD_CHANNEL_ID')}\n"
        "# 이 사람만 명령을 쓸 수 있습니다.\n"
        f"DISCORD_OWNER_ID={get('DISCORD_OWNER_ID')}\n",
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
    _say("      디스코드만 쓰실 거라면 빈 칸으로 건너뛰셔도 됩니다.")
    _say()
    _say("  1. 텔레그램에서 @BotFather 를 검색해 대화를 엽니다")
    _say("  2. /newbot 을 보냅니다")
    _say("  3. 봇 이름을 정합니다 (아무거나. 예: 메루카리 알리미)")
    _say("  4. 봇 아이디를 정합니다 (반드시 bot 으로 끝나야 합니다)")
    _say("  5. 123456789:AAE... 처럼 생긴 토큰을 받습니다")
    _say()

    for attempt in range(5):
        try:
            token = input("  토큰을 붙여넣고 Enter (건너뛰려면 빈 칸): ").strip()
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


def _bot_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bot {token}"}


def _pick(items: list[dict], label: str) -> dict | None:
    if len(items) == 1:
        return items[0]
    _say(f"\n  {label}을 고르세요.")
    for i, item in enumerate(items, 1):
        _say(f"    {i}. {item['name']}")
    try:
        answer = input(f"  번호 (1~{len(items)}): ").strip()
    except (EOFError, KeyboardInterrupt):
        return None
    if answer.isdigit() and 1 <= int(answer) <= len(items):
        return items[int(answer) - 1]
    _say("  → 번호를 못 읽었습니다.")
    return None


async def _check_bot_token(client: httpx.AsyncClient, token: str) -> dict | None:
    try:
        response = await client.get(
            f"{DISCORD_API}/users/@me", headers=_bot_headers(token), timeout=15
        )
    except Exception:
        return None
    return response.json() if response.status_code == 200 else None


async def _wait_for_guild(client: httpx.AsyncClient, token: str) -> dict | None:
    """봇이 서버에 초대될 때까지 기다린다. 최대 3분."""
    for _ in range(60):
        try:
            response = await client.get(
                f"{DISCORD_API}/users/@me/guilds", headers=_bot_headers(token), timeout=15
            )
            guilds = response.json() if response.status_code == 200 else []
        except (KeyboardInterrupt, asyncio.CancelledError):
            return None
        except Exception:
            guilds = []
        if guilds:
            return _pick(guilds, "봇이 들어간 서버")
        await asyncio.sleep(3)
    _say("  → 시간이 초과됐습니다. 초대 주소를 다시 열어주세요.")
    return None


async def _pick_channel(client: httpx.AsyncClient, token: str, guild_id: str) -> dict | None:
    try:
        response = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}/channels",
            headers=_bot_headers(token),
            timeout=15,
        )
        channels = response.json() if response.status_code == 200 else []
    except Exception:
        channels = []
    text = sorted(
        (c for c in channels if c.get("type") in TEXT_CHANNELS),
        key=lambda c: c.get("position", 0),
    )
    if not text:
        _say("  → 글을 쓸 수 있는 채널을 찾지 못했습니다.")
        return None
    return _pick(text, "알림 받을 채널")


async def _guild_owner(
    client: httpx.AsyncClient, token: str, guild_id: str
) -> tuple[str, str]:
    """(사용자 번호, 보여줄 이름). 서버를 직접 만들었다면 그게 본인이다."""
    try:
        response = await client.get(
            f"{DISCORD_API}/guilds/{guild_id}", headers=_bot_headers(token), timeout=15
        )
        owner_id = str(response.json()["owner_id"])
    except Exception:
        return "", ""
    try:
        who = await client.get(
            f"{DISCORD_API}/users/{owner_id}", headers=_bot_headers(token), timeout=15
        )
        body = who.json()
        return owner_id, body.get("global_name") or body.get("username") or ""
    except Exception:
        return owner_id, ""


def _confirm_owner(owner_id: str, owner_name: str) -> str:
    _say("\n  명령을 쓸 수 있는 사람을 정합니다.")
    if owner_id:
        _say(f"    서버를 만든 {owner_name or owner_id} 님으로 하겠습니다.")
        _say("    본인이 맞으면 그냥 Enter 를 누르세요.")
    _say("    다른 분이라면 그 사람의 사용자 ID 를 넣으세요.")
    _say("    (디스코드 설정 → 고급 → 개발자 모드를 켠 뒤, 이름 우클릭 → 사용자 ID 복사)")
    try:
        answer = input("  사용자 ID (그대로 두려면 Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        return owner_id
    if not answer:
        return owner_id
    if not answer.isdigit():
        _say("  → 숫자가 아닙니다. 서버를 만든 사람으로 두겠습니다.")
        return owner_id
    return answer


async def _bot_test(client: httpx.AsyncClient, token: str, channel_id: str) -> bool:
    try:
        response = await client.post(
            f"{DISCORD_API}/channels/{channel_id}/messages",
            headers=_bot_headers(token),
            json={
                "content": (
                    "🔔 **MerAlarm 연결됐습니다.**\n"
                    "프로그램을 시작하면 `/help` 같은 명령을 쓸 수 있습니다."
                )
            },
            timeout=20,
        )
    except Exception:
        return False
    return response.status_code in (200, 201)


async def _ask_bot(client: httpx.AsyncClient) -> dict[str, str] | None:
    _say()
    _say("  봇 만들기")
    _say("    1. https://discord.com/developers/applications 를 엽니다")
    _say("    2. 오른쪽 위 New Application → 이름을 짓고 만듭니다")
    _say("    3. 왼쪽 메뉴에서 Bot → Reset Token → Yes → 나온 값을 복사합니다")
    _say("       (토큰은 그때 한 번만 보입니다. 놓치면 다시 Reset 하세요)")
    _say()
    _say("  ※ 봇 토큰은 비밀번호와 같습니다. 남에게 보내지 마세요.")
    _say()

    me = None
    for _ in range(5):
        try:
            token = input("  봇 토큰을 붙여넣고 Enter (그만두려면 빈 칸): ").strip()
        except (EOFError, KeyboardInterrupt):
            return None
        if not token:
            return None
        # 개발자 포털에서 "Bot xxxx" 를 통째로 복사해 오는 일이 흔하다.
        token = token.removeprefix("Bot ").strip()
        _say("  → 확인 중...")
        me = await _check_bot_token(client, token)
        if me:
            break
        _say("  → 디스코드가 이 토큰을 거부했습니다.")
        _say("     Application ID 나 Public Key 말고 Bot 탭의 토큰인지 확인해 주세요.")
    if not me:
        return None
    _say(f"  → 확인됐습니다: {me.get('username', '')}\n")

    _say("  이제 봇을 내 서버에 초대합니다.")
    _say("  아래 주소를 브라우저 주소창에 붙여넣고, 서버를 고른 뒤 승인하세요.")
    _say()
    _say("    " + DISCORD_INVITE.format(app_id=me["id"]))
    _say()
    _say("  기다리는 중... (그만두려면 Ctrl+C)")

    guild = await _wait_for_guild(client, token)
    if not guild:
        return None
    _say(f"  → 들어갔습니다: {guild['name']}")

    channel = await _pick_channel(client, token, guild["id"])
    if not channel:
        return None

    owner_id = _confirm_owner(*await _guild_owner(client, token, guild["id"]))
    if not owner_id:
        _say("  → 사용자 ID 가 없으면 명령을 쓸 수 없습니다. 봇 설정을 중단합니다.\n")
        return None

    if await _bot_test(client, token, channel["id"]):
        _say(f"\n  → #{channel['name']} 에 시험 삼아 보냈습니다. 디스코드를 확인해 주세요.\n")
    else:
        _say(f"\n  → #{channel['name']} 에 글을 쓰지 못했습니다.")
        _say("     그 채널에서 봇에게 '메시지 보내기' 권한이 있는지 확인해 주세요.\n")

    return {
        "DISCORD_BOT_TOKEN": token,
        "DISCORD_CHANNEL_ID": str(channel["id"]),
        "DISCORD_OWNER_ID": owner_id,
    }


async def _ask_webhook(client: httpx.AsyncClient) -> str:
    _say()
    _say("  웹훅 만들기")
    _say("    1. 알림 받을 채널의 톱니(채널 편집)를 누릅니다")
    _say("    2. 연동 → 웹후크 → 새 웹후크")
    _say("    3. '웹후크 URL 복사' 를 누릅니다")
    _say()
    try:
        url = input("  웹후크 URL (그만두려면 빈 칸): ").strip()
    except (EOFError, KeyboardInterrupt):
        return ""
    if not url:
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


async def _setup_discord(client: httpx.AsyncClient, existing: dict[str, str]) -> dict[str, str]:
    """선택 사항. 건너뛰어도 텔레그램만으로 완전히 동작한다."""
    kept = {key: existing.get(key, "") for key in DISCORD_KEYS}
    empty = {key: "" for key in DISCORD_KEYS}
    current = "봇" if kept["DISCORD_BOT_TOKEN"] else ("웹훅" if kept["DISCORD_WEBHOOK_URL"] else "")

    _say("[2/3] 디스코드 (선택)")
    _say()
    if current:
        _say(f"  지금은 {current} 으로 설정돼 있습니다. 그대로 두려면 Enter 를 누르세요.")
        _say()
    _say("  1. 안 받음")
    _say("  2. 웹훅 — URL 하나만 붙여넣으면 알림이 옵니다 (1분, 쉬움)")
    _say("  3. 봇   — 알림에 더해 디스코드에서도 /add 같은 명령을 씁니다 (3~5분)")
    _say()
    try:
        answer = input("  번호 (그대로 두려면 Enter): ").strip()
    except (EOFError, KeyboardInterrupt):
        return kept

    if not answer:
        _say()
        return kept
    if answer == "1":
        _say()
        return empty
    if answer == "2":
        return {**empty, "DISCORD_WEBHOOK_URL": await _ask_webhook(client)}
    if answer == "3":
        got = await _ask_bot(client)
        if got:
            # 봇이 있으면 웹훅은 쓰이지 않는다. 남겨두면 나중에 왜 안 쓰이는지 헷갈린다.
            return {**empty, **got}
        _say("  → 봇 설정을 마치지 못했습니다. 디스코드는 건너뜁니다.\n")
        return empty

    _say("  → 번호를 못 읽었습니다. 디스코드는 그대로 두겠습니다.\n")
    return kept


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
        keyword = ""

    from .config_store import EXAMPLE_KEYWORD, KeywordStore

    store = KeywordStore(config_path)

    if not keyword:
        # 설정에는 키워드가 최소 하나 있어야 하므로 예시를 남긴다. 그대로 두면
        # 원하지도 않는 상품 알림이 오므로 무엇을 해야 하는지 알려준다.
        try:
            if EXAMPLE_KEYWORD in store.names():
                _say(f"\n  ⚠ '{EXAMPLE_KEYWORD}' 가 그대로 남아 있습니다.")
                _say("     텔레그램에서 /add 로 원하는 키워드를 넣고")
                _say("     /del 로 예시를 지워주세요.\n")
        except Exception:
            pass
        return

    try:
        store.add(keyword)
        _say(f"  → '{keyword}' 를 추가했습니다.")
        # 사용자가 자기 키워드를 넣었으면 예시는 치운다. 안 그러면 관심도 없는
        # 상품 알림이 계속 온다.
        names = store.names()
        if EXAMPLE_KEYWORD in names and len(names) > 1:
            store.remove(names.index(EXAMPLE_KEYWORD) + 1)
            _say("  → 예시 키워드는 지웠습니다.")
        _say()
    except Exception as e:
        _say(f"  → 추가하지 못했습니다({e}). 나중에 /add 로 넣어주세요.\n")


# ---- 전체 흐름 ----


async def run(env_path: Path, config_path: Path) -> bool:
    _say()
    _say(LINE)
    _say("  MerAlarm 첫 설정")
    _say(LINE)
    _say()
    _say("알림 받을 곳이 하나는 있어야 합니다. 텔레그램이나 디스코드 중 하나면 됩니다.")
    _say("텔레그램이 가장 간단하고, 3분이면 끝납니다.")
    _say()

    existing = read_env(env_path)
    async with httpx.AsyncClient() as client:
        token = chat_id = ""
        result = await _ask_token(client, existing.get("TELEGRAM_BOT_TOKEN", ""))
        if result is None:
            _say("  → 텔레그램은 건너뜁니다.\n")
        else:
            token, username = result
            chat_id = existing.get("TELEGRAM_CHAT_ID", "")
            if not chat_id:
                chat_id = await _wait_for_chat(client, token, username) or ""
            if not chat_id:
                # 토큰만 있고 상대가 없으면 아무 데도 못 보낸다. 반쪽으로 두면
                # 나중에 실행할 때 설정 오류로 막힌다.
                _say("  → 대화 상대를 찾지 못해 텔레그램은 건너뜁니다.\n")
                token = ""

        discord = await _setup_discord(client, existing)

        if not (token and chat_id) and not any(discord.values()):
            _say(LINE)
            _say("  알림 받을 곳이 하나도 없어 설정을 중단했습니다.")
            _say("  텔레그램이나 디스코드 중 하나는 설정해 주세요.")
            _say(LINE)
            _say()
            return False

        write_env(
            env_path,
            {"TELEGRAM_BOT_TOKEN": token, "TELEGRAM_CHAT_ID": chat_id, **discord},
        )
        if token and chat_id:
            await _send_test(client, token, chat_id)

    _ask_keyword(config_path)

    _say(LINE)
    _say("  설정이 끝났습니다. 감시를 시작합니다.")
    _say(LINE)
    _say()
    return True
