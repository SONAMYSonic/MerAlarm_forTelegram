"""이미 본 상품을 SQLite 에 기록해 중복 알림을 막는다.

메루카리 검색은 정렬 순서를 신뢰할 수 없다. SORT_CREATED_TIME 은 최초 출품 시각이
아니라 최근 갱신 시각 기준이라 3년 전 상품도 상위에 올라오고, 그 순서마저 완전한
내림차순이 아니다. 따라서 "마지막 확인 시각 이후만" 같은 컷오프는 성립하지 않으며
매 회차 item_id 를 전수 대조하는 수밖에 없다.

가격도 함께 저장해 다음 회차와 비교한다. 재출품이나 설명 수정만으로도 상품이
상위로 올라오므로, 가격이 실제로 내려갔을 때만 알려야 노이즈가 생기지 않는다.
"""

import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path

from .models import Item
from .text import fold

# SQLite 의 바인딩 변수 개수 제한에 걸리지 않도록 조회를 나눠 던진다.
_CHUNK = 400

SCHEMA = """
CREATE TABLE IF NOT EXISTS items (
    keyword    TEXT    NOT NULL,
    item_id    TEXT    NOT NULL,
    name       TEXT    NOT NULL,
    price      INTEGER NOT NULL,
    first_seen TEXT    NOT NULL,
    last_seen  TEXT    NOT NULL,
    PRIMARY KEY (keyword, item_id)
);

CREATE TABLE IF NOT EXISTS keywords (
    keyword   TEXT PRIMARY KEY,
    seeded_at TEXT NOT NULL
);

-- 어떤 키워드로 잡혔든 "이미 알린 상품"은 여기에 한 번만 남는다.
-- items 는 키워드별로 나뉘어 있어서, 한 상품이 여러 키워드에 걸리면
-- 같은 물건으로 알림이 여러 번 간다. 그것을 막는 것이 이 표의 역할이다.
CREATE TABLE IF NOT EXISTS notified (
    item_id     TEXT PRIMARY KEY,
    price       INTEGER NOT NULL,
    keyword     TEXT    NOT NULL,
    notified_at TEXT    NOT NULL
);
"""


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


class SeenStore:
    def __init__(self, path: Path) -> None:
        self._db = sqlite3.connect(path)
        self._db.executescript(SCHEMA)
        self._db.commit()

    def is_seeded(self, keyword: str) -> bool:
        """이 키워드가 최초 1회 적재를 마쳤는지.

        마치기 전까지는 알림을 보내지 않는다. 그러지 않으면 실행하자마자
        기존 매물 120건이 한꺼번에 날아온다. 키워드를 나중에 추가했을 때도
        그 키워드에 대해서만 같은 방식으로 동작한다.
        """
        return (
            self._db.execute(
                "SELECT 1 FROM keywords WHERE keyword = ?", (keyword,)
            ).fetchone()
            is not None
        )

    def mark_seeded(self, keyword: str) -> None:
        self._db.execute(
            "INSERT OR IGNORE INTO keywords (keyword, seeded_at) VALUES (?, ?)",
            (keyword, _now()),
        )
        self._db.commit()

    def known_prices(self, keyword: str, item_ids: list[str]) -> dict[str, int]:
        """기록에 있는 상품의 마지막 확인 가격. 없는 상품은 결과에 담기지 않는다."""
        known: dict[str, int] = {}
        for start in range(0, len(item_ids), _CHUNK):
            chunk = item_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            known.update(
                self._db.execute(
                    f"SELECT item_id, price FROM items "
                    f"WHERE keyword = ? AND item_id IN ({placeholders})",
                    (keyword, *chunk),
                )
            )
        return known

    def record(self, keyword: str, items: list[Item]) -> None:
        """본 상품을 기록한다. 이미 있으면 가격과 최종 확인 시각을 갱신한다."""
        if not items:
            return
        now = _now()
        self._db.executemany(
            """
            INSERT INTO items (keyword, item_id, name, price, first_seen, last_seen)
            VALUES (?, ?, ?, ?, ?, ?)
            ON CONFLICT (keyword, item_id) DO UPDATE SET
                price     = excluded.price,
                name      = excluded.name,
                last_seen = excluded.last_seen
            """,
            [(keyword, i.id, i.name, i.price, now, now) for i in items],
        )
        self._db.commit()

    def matching_names(self, word: str, limit: int = 3) -> tuple[int, int, list[str]]:
        """제외어 후보가 지금 추적 중인 상품에 몇 건이나 걸리는지 미리 본다.

        전역 제외어는 한 단어만 잘못 넣어도 **모든 키워드의 알림이 조용히 죽는다.**
        무엇이 사라질지 먼저 보여주고 확인을 받기 위한 것이다. 검색을 새로 하지
        않고 이미 기록해 둔 이름만 훑으므로 곧바로 답이 나온다.

        돌려주는 값은 (걸리는 상품 수, 추적 중인 전체 수, 예시 이름).

        SQL 의 LIKE 대신 파이썬으로 훑는다. LIKE 는 전각/반각을 가리므로 **미리보기와
        실제 필터의 결과가 달라진다.** "37건이 걸립니다" 라고 해놓고 다른 수가
        걸리면 확인을 받는 의미가 없다. 상품 천여 건이라 훑어도 순식간이다.
        """
        needle = fold(word)
        matched: set[str] = set()
        every: set[str] = set()
        samples: list[str] = []
        for item_id, name in self._db.execute("SELECT item_id, name FROM items"):
            every.add(item_id)
            if item_id in matched or needle not in fold(name):
                continue
            matched.add(item_id)
            if len(samples) < limit:
                samples.append(name)
        return len(matched), len(every), samples

    def notified_prices(self, item_ids: list[str]) -> dict[str, int]:
        """이미 알린 상품과 그때 알린 가격. 키워드를 가리지 않는다."""
        result: dict[str, int] = {}
        for start in range(0, len(item_ids), _CHUNK):
            chunk = item_ids[start : start + _CHUNK]
            placeholders = ",".join("?" * len(chunk))
            result.update(
                self._db.execute(
                    f"SELECT item_id, price FROM notified WHERE item_id IN ({placeholders})",
                    chunk,
                )
            )
        return result

    def mark_notified(self, keyword: str, item: Item) -> None:
        self._db.execute(
            """
            INSERT INTO notified (item_id, price, keyword, notified_at)
            VALUES (?, ?, ?, ?)
            ON CONFLICT (item_id) DO UPDATE SET
                price       = excluded.price,
                keyword     = excluded.keyword,
                notified_at = excluded.notified_at
            """,
            (item.id, item.price, keyword, _now()),
        )
        self._db.commit()

    def purge(self, keep_days: int) -> tuple[int, int]:
        """오래 보이지 않은 기록을 지운다. (items, notified) 삭제 건수를 돌려준다.

        팔렸거나 내려간 상품은 검색에 다시 나오지 않으므로 기록만 쌓인다.
        아직 팔리지 않은 상품은 매 회차 last_seen 이 갱신되어 지워지지 않는다.

        keep_days 는 나이 필터(max_age_days)보다 넉넉해야 한다. 그보다 짧으면
        아직 감시 대상인 상품의 기록을 지워 신규로 다시 알리게 된다.
        """
        cutoff = (datetime.now(timezone.utc) - timedelta(days=keep_days)).isoformat(
            timespec="seconds"
        )
        # 두 표 모두 _now() 가 만든 같은 형식의 UTC 문자열이라 사전순 비교가 성립한다.
        items = self._db.execute("DELETE FROM items WHERE last_seen < ?", (cutoff,)).rowcount
        notified = self._db.execute(
            "DELETE FROM notified WHERE notified_at < ?", (cutoff,)
        ).rowcount
        self._db.commit()
        return items, notified

    def count(self, keyword: str) -> int:
        return self._db.execute(
            "SELECT COUNT(*) FROM items WHERE keyword = ?", (keyword,)
        ).fetchone()[0]

    def close(self) -> None:
        self._db.close()
