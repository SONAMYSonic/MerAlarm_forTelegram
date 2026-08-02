"""Phase 0 검증 (2/2): 텔레그램 봇으로 실제 상품 알림이 오는지 확인한다.

사전 준비
  1. @BotFather 에서 /newbot 으로 봇 생성 → 토큰 발급
  2. 만든 봇과 대화를 시작하고 아무 메시지나 전송
  3. .env.example 을 .env 로 복사하고 TELEGRAM_BOT_TOKEN 을 채움

TELEGRAM_CHAT_ID 는 비워두면 getUpdates 로 자동 탐지한다.
"""

import asyncio
import os
import sys
from pathlib import Path

import httpx
from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData as S

ENV_PATH = Path(__file__).with_name(".env")
API = "https://api.telegram.org/bot{token}/{method}"

KEYWORD = "ポケモンカード"


def load_env() -> dict[str, str]:
    if not ENV_PATH.exists():
        sys.exit(f"[중단] {ENV_PATH} 가 없습니다. .env.example 을 복사해서 만드세요.")
    env = {}
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            k, v = line.split("=", 1)
            env[k.strip()] = v.strip()
    if not env.get("TELEGRAM_BOT_TOKEN"):
        sys.exit("[중단] .env 의 TELEGRAM_BOT_TOKEN 이 비어 있습니다.")
    return env


async def resolve_chat_id(client: httpx.AsyncClient, token: str) -> str:
    """봇에게 온 메시지에서 chat_id 를 찾아낸다."""
    r = await client.get(API.format(token=token, method="getUpdates"))
    r.raise_for_status()
    updates = r.json().get("result", [])
    for u in reversed(updates):
        chat = (u.get("message") or u.get("channel_post") or {}).get("chat")
        if chat:
            print(f"[OK] chat_id 자동 탐지: {chat['id']} ({chat.get('first_name') or chat.get('title')})")
            return str(chat["id"])
    sys.exit(
        "[중단] chat_id 를 찾지 못했습니다.\n"
        "       텔레그램에서 봇을 검색해 대화를 열고 아무 메시지나 보낸 뒤 다시 실행하세요."
    )


def build_caption(item, keyword: str) -> str:
    """실제 알림에 쓸 메시지 포맷의 프로토타입."""
    tag = keyword.replace(" ", "")
    url = f"https://jp.mercari.com/item/{item.id_}"
    krw = round(item.price * 9.5)  # 임시 환율. Phase 2 에서 실시간 조회로 교체
    return (
        f"🆕 <b>#{tag}</b>\n\n"
        f"{item.name}\n\n"
        f"💴 <b>¥{item.price:,}</b>  (약 ₩{krw:,})\n"
        f"🕒 {item.created}\n\n"
        f'<a href="{url}">🛒 상품 보기</a>'
    )


async def main() -> None:
    env = load_env()
    token = env["TELEGRAM_BOT_TOKEN"]

    async with httpx.AsyncClient(timeout=20) as client:
        r = await client.get(API.format(token=token, method="getMe"))
        if not r.json().get("ok"):
            sys.exit(f"[중단] 토큰이 유효하지 않습니다: {r.text}")
        print(f"[OK] 봇 확인: @{r.json()['result']['username']}")

        chat_id = env.get("TELEGRAM_CHAT_ID") or await resolve_chat_id(client, token)

        print(f"[..] 메루카리에서 '{KEYWORD}' 최신 상품을 가져오는 중")
        results = await Mercapi().search(
            KEYWORD,
            sort_by=S.SortBy.SORT_CREATED_TIME,
            sort_order=S.SortOrder.ORDER_DESC,
            status=[S.Status.STATUS_ON_SALE],
        )
        item = results.items[0]
        print(f"[OK] 최신 상품: {item.name[:40]} (¥{item.price:,})")

        print("[..] 텔레그램으로 전송 중")
        r = await client.post(
            API.format(token=token, method="sendPhoto"),
            data={
                "chat_id": chat_id,
                "photo": item.thumbnails[0],
                "caption": build_caption(item, KEYWORD),
                "parse_mode": "HTML",
            },
        )
        if r.json().get("ok"):
            print("[OK] 전송 성공. 텔레그램을 확인하세요.")
            if not env.get("TELEGRAM_CHAT_ID"):
                print(f"\n     .env 에 아래 줄을 채워두면 다음부터 탐지를 건너뜁니다:\n"
                      f"     TELEGRAM_CHAT_ID={chat_id}")
        else:
            sys.exit(f"[실패] {r.text}")


if __name__ == "__main__":
    asyncio.run(main())
