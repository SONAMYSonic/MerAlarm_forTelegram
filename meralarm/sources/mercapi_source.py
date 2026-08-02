"""mercapi 기반 수집기 (주력).

mercapi 가 메루카리 내부 API 의 DPoP 서명을 대신 처리해준다. 이 프로젝트에서
기술적으로 가장 어려운 부분이 이 라이브러리 안에 들어 있다.
"""

import logging

import httpx
from mercapi import Mercapi
from mercapi.requests.search import SearchRequestData as S

from ..models import Item

log = logging.getLogger(__name__)

BLOCKED_CODES = (403, 429)


async def _raise_on_block(response: httpx.Response) -> None:
    """차단 응답을 명시적인 예외로 바꾼다.

    mercapi 는 상태 코드를 보지 않고 곧바로 json() 을 부른다. 그래서 429 나 403 이
    와도 호출부에는 JSON 파싱 오류로만 보이고, 그러면 스케줄러가 이것을 차단으로
    인식하지 못해 주기 자동 감속이 영영 동작하지 않는다.
    """
    if response.status_code in BLOCKED_CODES:
        await response.aread()
        raise httpx.HTTPStatusError(
            f"메루카리가 {response.status_code} 로 응답했습니다",
            request=response.request,
            response=response,
        )


class MercapiSource:
    name = "mercapi"

    def __init__(self) -> None:
        self._api = Mercapi()
        self._install_block_detection()

    def _install_block_detection(self) -> None:
        # mercapi 의 내부 클라이언트에 훅을 건다. 라이브러리가 바뀌어 붙이지 못하게
        # 되더라도 수집 자체는 계속되어야 하므로 경고만 남기고 넘어간다.
        client = getattr(self._api, "_client", None)
        if isinstance(client, httpx.AsyncClient):
            client.event_hooks["response"].append(_raise_on_block)
        else:
            log.warning(
                "mercapi 내부 클라이언트를 찾지 못해 차단 감지를 붙이지 못했습니다. "
                "429/403 응답이 와도 주기 자동 감속이 동작하지 않습니다."
            )

    async def search(self, keyword: str) -> list[Item]:
        results = await self._api.search(
            keyword,
            sort_by=S.SortBy.SORT_CREATED_TIME,
            sort_order=S.SortOrder.ORDER_DESC,
            status=[S.Status.STATUS_ON_SALE],
        )
        return [
            Item(
                id=r.id_,
                name=r.name,
                price=r.price,
                thumbnail=r.thumbnails[0] if r.thumbnails else "",
                created=r.created,
                updated=r.updated,
                condition_id=r.item_condition_id,
                shipping_payer_id=r.shipping_payer_id,
                seller_id=str(r.seller_id),
                item_type=r.item_type,
            )
            for r in results.items
        ]
