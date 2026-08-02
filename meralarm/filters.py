"""키워드별 필터.

필터는 중복 제거보다 먼저 적용한다. 그래야 조건에서 벗어나 있던 상품이 나중에
조건 안으로 들어오면(예: 상한가 위였다가 가격이 내려오면) 새 상품처럼 잡힌다.
"""

from .config import KeywordConfig
from .models import Item


def matches(item: Item, kw: KeywordConfig, *, ignore_age: bool = False) -> bool:
    """조건에 맞는 상품인가.

    `ignore_age` 는 이미 알린 적 있는 상품에만 쓴다. 나이 제한의 목적은 "모르던 옛날
    상품을 신규라고 알리지 말자"는 것이지, "알려드린 상품의 가격 변화를 무시하자"가
    아니다. 둘을 같은 필터로 막으면 30일이 지나는 순간 추적하던 상품을 놓아버린다.
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


def apply(
    items: list[Item], kw: KeywordConfig, *, age_exempt: set[str] | None = None
) -> list[Item]:
    """조건에 맞는 상품만 남긴다.

    `age_exempt` 에 든 item_id 는 나이 제한을 적용하지 않는다. 이미 알린 상품의
    가격 인하를 계속 추적하기 위한 것이다.
    """
    exempt = age_exempt or set()
    return [item for item in items if matches(item, kw, ignore_age=item.id in exempt)]
