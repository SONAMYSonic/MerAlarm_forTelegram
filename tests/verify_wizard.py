"""설정 마법사 검증. 실제 텔레그램 API 로 토큰 확인까지 한다."""

import asyncio
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import httpx

from meralarm import setup_wizard as w

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


# 진짜 토큰이 있을 때만 텔레그램에 실제로 물어본다. 없는 컴퓨터에서는 건너뛴다.
REAL = w.read_env(REPO / ".env") if (REPO / ".env").exists() else {}


async def main():
    tmp = Path(tempfile.mkdtemp())

    print("\n=== 1. 설정 필요 여부 판정 ===")
    empty = tmp / "none.env"
    check("파일 없으면 필요", w.needs_setup(empty))

    half = tmp / "half.env"
    half.write_text("TELEGRAM_BOT_TOKEN=123\nTELEGRAM_CHAT_ID=\n", encoding="utf-8")
    check("chat_id 비면 필요", w.needs_setup(half))

    full = tmp / "full.env"
    full.write_text("TELEGRAM_BOT_TOKEN=123\nTELEGRAM_CHAT_ID=456\n", encoding="utf-8")
    check("둘 다 있으면 불필요", not w.needs_setup(full))

    commented = tmp / "cmt.env"
    commented.write_text("# TELEGRAM_BOT_TOKEN=xxx\nTELEGRAM_BOT_TOKEN=a\nTELEGRAM_CHAT_ID=b\n",
                         encoding="utf-8")
    check("주석 줄은 무시", w.read_env(commented)["TELEGRAM_BOT_TOKEN"] == "a")

    print("\n=== 2. 토큰 형태 검사 ===")
    good = "123456789:AAEabcdefghijklmnopqrstuvwxyz0123456"
    check("정상 토큰 통과", bool(w.TOKEN_SHAPE.match(good)))
    for bad, why in [("", "빈 값"), ("abc", "형태 아님"), ("123:short", "너무 짧음"),
                     ("나는토큰", "한글"), ("123456789", "콜론 없음")]:
        check(f"거절: {why}", not w.TOKEN_SHAPE.match(bad))

    print("\n=== 3. 실제 텔레그램 API 로 토큰 확인 ===")
    async with httpx.AsyncClient() as client:
        real_token = REAL.get("TELEGRAM_BOT_TOKEN", "")
        if real_token:
            username = await w._check_token(client, real_token)
            check("진짜 토큰은 봇 이름을 돌려줌", bool(username), f"got {username}")
            if username:
                print(f"        └ @{username}")
        else:
            print("  [--] .env 가 없어 진짜 토큰 확인은 건너뜀")
        bogus = await w._check_token(client, "123456789:AAEfakefakefakefakefakefakefake000")
        check("가짜 토큰은 None", bogus is None, f"got {bogus}")

    print("\n=== 4. .env 쓰기 ===")
    out = tmp / "written.env"
    w.write_env(out, {"TELEGRAM_BOT_TOKEN": "999:TOKEN", "TELEGRAM_CHAT_ID": "12345"})
    text = out.read_text(encoding="utf-8")
    check("토큰 기록", "TELEGRAM_BOT_TOKEN=999:TOKEN" in text)
    check("chat_id 기록", "TELEGRAM_CHAT_ID=12345" in text)
    check("안내 주석 포함", "--setup" in text and "남에게 보내지 마세요" in text)
    check("다시 읽으면 설정 불필요", not w.needs_setup(out))

    print("\n=== 5. 대화형 여부 판정 ===")
    check("판정 함수가 예외 없이 동작", isinstance(w.can_prompt(), bool))
    print(f"        └ 지금 환경: {'대화형' if w.can_prompt() else '비대화형(정상)'}")

    print("\n=== 6. 설정 없을 때의 안내 문구 ===")
    from meralarm.config import ConfigError, _read_env
    try:
        _read_env(tmp / "missing.env")
        check("없는 .env 는 오류", False, "예외가 안 남")
    except ConfigError as e:
        check("--setup 안내 포함", "--setup" in str(e), str(e)[:60])
        print(f"        └ {str(e).splitlines()[0]}")

    print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
    sys.exit(1 if fail else 0)


asyncio.run(main())
