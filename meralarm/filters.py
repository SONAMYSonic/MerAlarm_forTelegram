"""키워드별 필터.

필터는 중복 제거보다 먼저 적용한다. 그래야 조건에서 벗어나 있던 상품이 나중에
조건 안으로 들어오면(예: 상한가 위였다가 가격이 내려오면) 새 상품처럼 잡힌다.
"""

from .config import KeywordConfig
from .models import Item


def matches(item: Item, kw: KeywordConfig, *, ignore_age: bool = False) -> bool:
    """조건에 맞는 상품인가.

    나이 제한은 **"신규라고 알릴지"에만** 쓰는 조건이다. 목적은 "모르던 옛날 상품을
    새로 나온 것처럼 알리지 말자"는 것이지 "오래된 상품은 쳐다보지도 말자"가 아니다.

    그래서 추적 대상을 고를 때는 `ignore_age=True` 로 나이를 빼고 본다. 오래된
    상품도 가격을 기록해 두어야, 나중에 값을 내렸을 때 알아챌 수 있다.
    """
    # 검색이 갱신 시각 순이라 오래된 상품도 순위가 출렁이며 상위로 떠오른다.
    # 그러면 우리 눈에는 "처음 본 상품"이라 신규로 잡히므로, 출품한 지 오래된
    # 것은 아예 후보에서 뺀다.
    if not ignore_age and kw.max_age_days is not None and item.age_days > kw.max_age_days:
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


def apply(items: list[Item], kw: KeywordConfig, *, ignore_age: bool = False) -> list[Item]:
    """조건에 맞는 상품만 남긴다."""
    return [item for item in items if matches(item, kw, ignore_age=ignore_age)]
