"""Phase 0 검증: mercapi가 2026년 현재에도 메루카리 검색을 수행하는지 확인한다.

확인 항목
  1. DPoP 인증이 통과되어 검색 응답이 오는가
  2. 신착순(created_time desc) 정렬이 먹는가
  3. 알림에 필요한 필드(id, 제목, 가격, 썸네일, 출품시각)를 뽑을 수 있는가
"""

import asyncio
import time

from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData as S

KEYWORD = "ポケモンカード"


async def main() -> None:
    m = Mercapi()

    t0 = time.perf_counter()
    results = await m.search(
        KEYWORD,
        sort_by=S.SortBy.SORT_CREATED_TIME,
        sort_order=S.SortOrder.ORDER_DESC,
        status=[S.Status.STATUS_ON_SALE],
    )
    elapsed = time.perf_counter() - t0

    print(f"[OK] 검색 성공 · {elapsed:.2f}초 · 총 {results.meta.num_found}건 매칭")
    print(f"     1페이지 {len(results.items)}건 수신\n")

    print("--- 첫 상품의 전체 필드 ---")
    first = results.items[0]
    for k, v in vars(first).items():
        if k.startswith("_"):
            continue
        print(f"  {k}: {str(v)[:90]}")

    print("\n--- 신착순 정렬 확인 (상위 5건) ---")
    for it in results.items[:5]:
        print(f"  {it.updated} | ¥{it.price:>8,} | {it.id_} | {it.name[:38]}")


if __name__ == "__main__":
    asyncio.run(main())
