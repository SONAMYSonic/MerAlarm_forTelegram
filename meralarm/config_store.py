"""봇 명령으로 `config.yaml` 의 keywords 목록을 고쳐 쓴다.

사람이 써 둔 주석과 서식이 남아야 한다. PyYAML 로 읽고 쓰면 주석이 통째로
날아가므로 왕복 편집을 지원하는 ruamel.yaml 을 쓴다. 설정을 읽어 들이는 쪽
(config.py)은 그대로 PyYAML 을 쓴다 — 읽기만 할 때는 그게 더 간단하다.
"""

import io
import os
from pathlib import Path

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

    def _save(self, yaml: YAML, data) -> None:
        # 임시 파일에 쓰고 바꿔치기한다. 쓰는 도중에 죽어도 원본이 남는다.
        buf = io.StringIO()
        yaml.dump(data, buf)
        tmp = self._path.with_suffix(".yaml.tmp")
        tmp.write_text(buf.getvalue(), encoding="utf-8")
        os.replace(tmp, self._path)

    def names(self) -> list[str]:
        _, data = self._load()
        return [_name_of(entry) for entry in data["keywords"]]

    def add(self, query: str, excludes: tuple[str, ...] = ()) -> str:
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
        self._save(yaml, data)
        return query

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

    def remove(self, index: int) -> str:
        """1부터 세는 번호로 지운다. /list 에 보이는 번호와 맞춘다."""
        yaml, data = self._load()
        count = len(data["keywords"])
        if count <= 1:
            raise KeywordStoreError("마지막 키워드는 지울 수 없습니다. 감시할 것이 없어집니다.")
        if not 1 <= index <= count:
            raise KeywordStoreError(f"번호는 1~{count} 사이여야 합니다.")

        removed = _name_of(data["keywords"][index - 1])
        del data["keywords"][index - 1]
        self._save(yaml, data)
        return removed
