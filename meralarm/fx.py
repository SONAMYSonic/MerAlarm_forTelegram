"""엔화→원화 환율. 하루 1회만 조회하고 파일에 캐싱한다.

환율은 부가 정보라 실패해도 알림 자체를 막아서는 안 된다. 조회에 실패하면
캐시된 값을, 그것도 없으면 None 을 돌려주고 호출부가 원화 표시를 생략한다.
"""

import json
import logging
from datetime import date
from pathlib import Path

import httpx

API_URL = "https://open.er-api.com/v6/latest/JPY"

log = logging.getLogger(__name__)


async def jpy_to_krw(cache_path: Path) -> float | None:
    today = date.today().isoformat()

    cached: dict = {}
    if cache_path.exists():
        try:
            cached = json.loads(cache_path.read_text(encoding="utf-8"))
            if cached.get("date") == today:
                return float(cached["rate"])
        except (ValueError, KeyError):
            log.warning("환율 캐시가 손상되어 무시합니다: %s", cache_path)

    try:
        async with httpx.AsyncClient(timeout=10) as client:
            response = await client.get(API_URL)
            response.raise_for_status()
            rate = float(response.json()["rates"]["KRW"])
    except Exception as e:  # 네트워크·형식 오류 모두 부가 기능 실패로 처리
        stale = cached.get("rate")
        log.warning("환율 조회 실패(%s). %s", e, f"캐시값 {stale} 사용" if stale else "원화 표시 생략")
        return float(stale) if stale else None

    cache_path.write_text(
        json.dumps({"date": today, "rate": rate}, ensure_ascii=False), encoding="utf-8"
    )
    log.info("환율 갱신: 1 JPY = %.4f KRW", rate)
    return rate
