"""알림 파이프라인 전체가 공유하는 상품 표현."""

from dataclasses import dataclass
from datetime import datetime

# 메루카리 item_condition_id → 표시 문자열
CONDITIONS = {
    1: "새상품·미사용",
    2: "거의 새것",
    3: "눈에 띄는 상처 없음",
    4: "약간 상처·오염 있음",
    5: "상처·오염 있음",
    6: "상태 나쁨",
}

# 메루카리 shipping_payer_id → 표시 문자열
SHIPPING_PAYERS = {
    1: "구매자 부담",
    2: "판매자 부담",
    3: "미정",
}

# 개인 판매 상품. ID 가 m + 숫자 형태이고 /item/ 경로를 쓴다.
TYPE_MERCARI = "ITEM_TYPE_MERCARI"
# 메루카리 Shops(사업자 판매) 상품. ID 가 임의 문자열이고 /shops/product/ 경로를 쓴다.
TYPE_SHOPS = "ITEM_TYPE_BEYOND"


@dataclass(frozen=True, slots=True)
class Item:
    id: str
    name: str
    price: int
    thumbnail: str
    created: datetime
    updated: datetime
    condition_id: int
    shipping_payer_id: int
    seller_id: str
    item_type: str = TYPE_MERCARI

    @property
    def is_shops(self) -> bool:
        return self.item_type == TYPE_SHOPS

    @property
    def age_days(self) -> int:
        """출품한 지 며칠 지났는가.

        메루카리가 주는 시각은 일본 시간이고 한국과 시차가 없어 그대로 비교한다.
        검색은 갱신 시각 순이라 오래된 상품도 상위로 올라오므로, "처음 본 상품"이
        곧 "새로 나온 상품"은 아니다. 그 둘을 구분하는 데 쓴다.
        """
        return max(0, (datetime.now() - self.created).days)

    @property
    def url(self) -> str:
        # Shops 상품을 /item/ 으로 만들면 404 가 뜬다. 검색 결과의 5% 정도가
        # Shops 라서 링크 절반쯤은 멀쩡해 보여도 나머지는 열리지 않는다.
        if self.is_shops:
            return f"https://jp.mercari.com/shops/product/{self.id}"
        return f"https://jp.mercari.com/item/{self.id}"

    @property
    def condition(self) -> str:
        return CONDITIONS.get(self.condition_id, "정보 없음")

    @property
    def shipping_payer(self) -> str:
        return SHIPPING_PAYERS.get(self.shipping_payer_id, "정보 없음")
