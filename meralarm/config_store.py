"""봇 명령으로 `config.yaml` 의 keywords 목록을 고쳐 쓴다.

사람이 써 둔 주석과 서식이 남아야 한다. PyYAML 로 읽고 쓰면 주석이 통째로
날아가므로 왕복 편집을 지원하는 ruamel.yaml 을 쓴다. 설정을 읽어 들이는 쪽
(config.py)은 그대로 PyYAML 을 쓴다 — 읽기만 할 때는 그게 더 간단하다.
"""

import io
import os
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ruamel.yaml import YAML


class KeywordStoreError(RuntimeError):
    """사용자에게 그대로 보여줄 수 있는 메시지를 담는다."""


def _yaml() -> YAML:
    yaml = YAML()
    yaml.preserve_quotes = True
    yaml.indent(mapping=2, sequence=4, offset=2)
    return yaml


def _name_of(entry) -> str:
    """간단형(문자열)과 상세형(매핑) 모두에서 표시 이름을 뽑는다."""
    if isinstance(entry, str):
        return entry.strip()
    return str(entry.get("name") or entry.get("query") or "").strip()


class KeywordStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self):
        yaml = _yaml()
        data = yaml.load(self._path)
        if data is None or "keywords" not in data:
            raise KeywordStoreError("config.yaml 에서 keywords 를 찾지 못했습니다.")
        return yaml, data

    def _save(self, yaml: YAML, data, validate: Callable[[], object] | None = None) -> None:
        """설정 파일을 바꿔 쓴다.

        `validate` 를 주면 새 내용으로 실제 설정 로드를 해보고, 실패하면 원본으로
        되돌린다. 이게 없으면 봇 명령 한 번으로 파일이 깨져 **다음 재시작 때 아예
        켜지지 않는** 상태가 될 수 있다. 무인 운영에서는 치명적이다.
        """
        original = self._path.read_text(encoding="utf-8")

        # 임시 파일에 쓰고 바꿔치기한다. 쓰는 도중에 죽어도 원본이 남는다.
        buf = io.StringIO()
        yaml.dump(data, buf)
        tmp = self._path.with_suffix(".yaml.tmp")
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(tmp, self._path)

        if validate is None:
            return
        try:
            validate()
        except Exception as e:
            self._path.write_text(original, encoding="utf-8")
            raise KeywordStoreError(f"설정이 올바르지 않아 되돌렸습니다.\n{e}") from None

    def names(self) -> list[str]:
        _, data = self._load()
        return [_name_of(entry) for entry in data["keywords"]]

    def add(
        self,
        query: str,
        excludes: tuple[str, ...] = (),
        validate: Callable[[], object] | None = None,
    ) -> str:
        query = query.strip()
        if not query:
            raise KeywordStoreError("키워드가 비어 있습니다.")
        if len(query) > 100:
            raise KeywordStoreError("키워드가 너무 깁니다. 100자 이내로 써주세요.")

        yaml, data = self._load()
        if any(_name_of(e) == query for e in data["keywords"]):
            raise KeywordStoreError(
                f"'{query}' 는 이미 감시 중입니다.\n"
                f"조건을 바꾸려면 /del 로 지우고 다시 추가하세요."
            )

        entry = {"name": query, "query": query}
        if excludes:
            entry["exclude"] = list(excludes)
        data["keywords"].append(entry)
        self._save(yaml, data, validate)
        return query

    def set_global(
        self, path: tuple[str, ...], value: Any, validate: Callable[[], object] | None = None
    ) -> None:
        """`("notify", "max_age_days")` 처럼 중첩 경로에 값을 넣는다.

        값이 None 이어도 항목을 지우지 않고 `null` 을 적는다. 지우면 그 항목에 딸린
        설명 주석까지 함께 사라져, 껐다 켜는 것만으로 파일이 점점 빈약해진다.
        전역 설정은 모두 null 을 "지정 안 함"으로 읽으므로 동작도 같다.
        """
        yaml, data = self._load()
        node = data
        for key in path[:-1]:
            if key not in node or node[key] is None:
                node[key] = {}
            node = node[key]
        node[path[-1]] = value
        self._save(yaml, data, validate)

    def set_keyword(
        self,
        index: int,
        key: str,
        value: Any,
        validate: Callable[[], object] | None = None,
    ) -> str:
        """1부터 세는 번호로 키워드 항목을 고친다. 값이 None 이면 항목을 지운다."""
        yaml, data = self._load()
        count = len(data["keywords"])
        if not 1 <= index <= count:
            raise KeywordStoreError(f"번호는 1~{count} 사이여야 합니다.")

        entry = data["keywords"][index - 1]
        if isinstance(entry, str):
            # 간단형(문자열)은 조건을 담을 수 없다. 상세형으로 바꿔준다.
            entry = {"name": entry.strip(), "query": entry.strip()}
            data["keywords"][index - 1] = entry

        if value is None:
            entry.pop(key, None)
        else:
            entry[key] = value
        self._save(yaml, data, validate)
        return _name_of(entry)

    def entries(self) -> list[tuple[str, list[str]]]:
        """표시용 (이름, 제외어) 목록. 간단형 키워드에는 제외어가 없다."""
        _, data = self._load()
        result = []
        for entry in data["keywords"]:
            if isinstance(entry, str):
                result.append((entry.strip(), []))
            else:
                excludes = [str(w) for w in (entry.get("exclude") or []) if w is not None]
                result.append((_name_of(entry), excludes))
        return result

    def remove(self, index: int, validate: Callable[[], object] | None = None) -> str:
        """1부터 세는 번호로 지운다. /list 에 보이는 번호와 맞춘다."""
        yaml, data = self._load()
        count = len(data["keywords"])
        if count <= 1:
            raise KeywordStoreError("마지막 키워드는 지울 수 없습니다. 감시할 것이 없어집니다.")
        if not 1 <= index <= count:
            raise KeywordStoreError(f"번호는 1~{count} 사이여야 합니다.")

        removed = _name_of(data["keywords"][index - 1])
        del data["keywords"][index - 1]
        self._save(yaml, data, validate)
        return removed
