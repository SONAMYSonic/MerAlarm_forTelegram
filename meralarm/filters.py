"""키워드별 필터.

조건은 두 갈래다.

- **늘 적용되는 것** — 가격, 제외어, 상품 상태, 배송비 부담자.
  아예 관심 없는 물건을 걸러낸다.
- **신규 알림에만 적용되는 것** — 출품 경과일(age), 끌어올림(bump).
  "새로 나온 물건이라고 알릴지"를 정할 뿐, 추적을 그만두라는 뜻이 아니다.
  걸러진 상품도 가격은 기록해 두었다가 값을 내리면 그때 알린다.
"""

from .config import KeywordConfig
from .models import Item


def matches(item: Item, kw: KeywordConfig, *, ignore_freshness: bool = False) -> bool:
    """조건에 맞는 상품인가.

    `ignore_freshness=True` 는 추적 대상을 고를 때 쓴다. 오래됐거나 끌어올린
    상품도 가격은 따라가야, 나중에 값을 내렸을 때 알아챌 수 있다.
    """
    if not ignore_freshness:
        # 메루카리 검색은 최근 갱신 순이라 옛날 상품도 위로 떠오른다. 그것을
        # 새로 나온 물건이라고 알리면 거짓말이 된다.
        if kw.max_age_days is not None and item.age_days > kw.max_age_days:
            return False
        # 출품하고 한참 뒤에 갱신된 것은 끌어올린 것이지 새 매물이 아니다.
        # 출품 경과일과는 다른 조건이다 — 5일 전에 올려두고 그대로인 물건은
        # 우리가 늦게 발견한 새 매물이므로 이 조건에는 걸리지 않는다.
        if kw.max_bump_days is not None and item.bump_days > kw.max_bump_days:
            return False

    if kw.price_min is not None and item.price < kw.price_min:
        return False
    if kw.price_max is not None and item.price > kw.price_max:
        return False
    if kw.conditions and item.condition_id not in kw.conditions:
        return False
    if kw.shipping_payers and item.shipping_payer_id not in kw.shipping_payers:
        return False
    name = item.name.lower()
    return not any(word.lower() in name for word in kw.exclude)


def apply(items: list[Item], kw: KeywordConfig, *, ignore_freshness: bool = False) -> list[Item]:
    """조건에 맞는 상품만 남긴다."""
    return [item for item in items if matches(item, kw, ignore_freshness=ignore_freshness)]
