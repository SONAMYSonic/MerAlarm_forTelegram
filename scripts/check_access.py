"""이 컴퓨터에서 메루카리 API 가 되는지 확인한다.

새 서버(Oracle Cloud·라즈베리파이 등)에 올리기 전에 먼저 돌려본다. 데이터센터
IP 는 차단될 수 있는데, 전부 배포한 뒤에 알면 시간 낭비다.

    python scripts/check_access.py
"""

import asyncio
import sys
import time

import httpx

try:
    from mercapi import Mercapi
    from mercapi.requests.search import SearchRequestData as S
except ImportError:
    sys.exit("mercapi 가 없습니다. 먼저 setup.sh 또는 setup.ps1 을 실행하세요.")

KEYWORD = "ポケモンカード"
TRIES = 5
GAP_SEC = 3


async def show_network(client: httpx.AsyncClient) -> None:
    print("=== 이 서버의 네트워크 ===")
    try:
        info = (await client.get("https://ipinfo.io/json", timeout=10)).json()
    except Exception as e:
        print(f"  IP 정보를 가져오지 못했습니다 ({type(e).__name__}). 계속 진행합니다.\n")
        return
    print(f"  IP      : {info.get('ip')}")
    print(f"  위치    : {info.get('country')} {info.get('region', '')} {info.get('city', '')}")
    print(f"  통신사  : {info.get('org')}")
    org = (info.get("org") or "").lower()
    if any(word in org for word in ("oracle", "amazon", "google", "azure", "microsoft", "ovh")):
        print("  ※ 데이터센터 IP 입니다. 가정용 회선보다 차단 확률이 높습니다.")
    print()


async def probe() -> int:
    api = Mercapi()
    ok = 0
    blocked = 0
    print(f"=== 메루카리 검색 {TRIES}회 시도 ===")

    for i in range(1, TRIES + 1):
        start = time.perf_counter()
        try:
            res = await api.search(
                KEYWORD,
                sort_by=S.SortBy.SORT_CREATED_TIME,
                sort_order=S.SortOrder.ORDER_DESC,
                status=[S.Status.STATUS_ON_SALE],
            )
        except httpx.HTTPStatusError as e:
            code = e.response.status_code
            blocked += code in (403, 429)
            print(f"  {i}/{TRIES}  실패 · HTTP {code}")
        except Exception as e:
            print(f"  {i}/{TRIES}  실패 · {type(e).__name__}: {str(e)[:70]}")
        else:
            ok += 1
            print(f"  {i}/{TRIES}  성공 · {time.perf_counter() - start:.2f}초 · {len(res.items)}건")
        if i < TRIES:
            await asyncio.sleep(GAP_SEC)

    print()
    print("=" * 46)
    if ok == TRIES:
        print("판정: 정상. 이 서버에서 돌려도 됩니다.")
        return 0
    if ok == 0 and blocked:
        print("판정: 차단됨. 이 서버에서는 쓸 수 없습니다.")
        print("      가정용 회선(라즈베리파이 등)을 쓰거나 폴백 수집기가 필요합니다.")
        return 2
    if ok == 0:
        print("판정: 전부 실패했지만 차단 신호(403/429)는 아닙니다.")
        print("      네트워크나 방화벽 설정을 먼저 확인하세요.")
        return 3
    print(f"판정: 불안정 ({ok}/{TRIES} 성공). 주기를 길게 잡고 며칠 지켜보세요.")
    return 1


async def main() -> None:
    async with httpx.AsyncClient() as client:
        await show_network(client)
    sys.exit(await probe())


if __name__ == "__main__":
    asyncio.run(main())
