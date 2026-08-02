"""수집기 인터페이스.

메루카리 내부 API 는 언제든 막힐 수 있으므로 수집 방식을 갈아끼울 수 있게 해둔다.
Phase 3 에서 Playwright(폴백 2안)와 ZenMarket(폴백 3안)이 같은 규약으로 붙는다.
"""

from typing import Protocol

from ..models import Item


class ItemSource(Protocol):
    name: str

    async def search(self, keyword: str) -> list[Item]:
        """키워드에 해당하는 판매중 상품을 최신순으로 돌려준다."""
        ...
