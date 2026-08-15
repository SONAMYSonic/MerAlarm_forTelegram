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
from ruamel.yaml.comments import CommentedSeq


# 배포판이 처음 담고 나오는 자리표시 키워드. 설정에는 키워드가 최소 하나 있어야
# 해서 비워둘 수 없다. 사용자가 자기 키워드를 넣으면 이것은 치운다.
EXAMPLE_KEYWORD = "예시 키워드"


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

    # ---- 전역 제외어 ----

    def global_excludes(self) -> list[str]:
        _, data = self._load()
        return [str(w).strip() for w in (data.get("exclude") or []) if w is not None and str(w).strip()]

    def _exclude_seq(self, data):
        """전역 제외어 목록을 꺼낸다. 없으면 keywords 바로 앞에 만들어 준다.

        맨 뒤에 붙이면 긴 keywords 목록 아래에 파묻혀 사람이 못 찾는다.
        """
        current = data.get("exclude")
        if isinstance(current, CommentedSeq):
            return current
        seq = CommentedSeq(current or [])
        if "exclude" in data:
            data["exclude"] = seq
            return seq

        keys = list(data.keys())
        position = keys.index("keywords") if "keywords" in keys else len(keys)
        data.insert(position, "exclude", seq)
        data.yaml_set_comment_before_after_key(
            "exclude",
            before=(
                "모든 키워드에 함께 적용되는 제외어.\n"
                "키워드별 exclude 를 덮어쓰지 않고 거기에 더해진다.\n"
                "제목에 이 말이 들어 있으면 어느 키워드로 잡혔든 알리지 않는다."
            ),
        )
        return seq

    def add_global_excludes(
        self, words: list[str], validate: Callable[[], object] | None = None
    ) -> list[str]:
        """이미 있는 말은 건너뛰고, 새로 들어간 것만 돌려준다."""
        yaml, data = self._load()
        seq = self._exclude_seq(data)
        having = {str(w).strip().lower() for w in seq if w is not None}

        added = []
        for word in words:
            word = word.strip()
            if not word or word.lower() in having:
                continue
            seq.append(word)
            having.add(word.lower())
            added.append(word)

        if not added:
            return []
        self._save(yaml, data, validate)
        return added

    def remove_global_exclude(
        self, word: str, validate: Callable[[], object] | None = None
    ) -> str:
        yaml, data = self._load()
        seq = data.get("exclude")
        target = word.strip().lower()
        index = next(
            (i for i, w in enumerate(seq or []) if w is not None and str(w).strip().lower() == target),
            None,
        )
        if index is None:
            raise KeywordStoreError(f"'{word}' 는 전역 제외어에 없습니다.")
        removed = str(seq[index]).strip()
        del seq[index]
        self._save(yaml, data, validate)
        return removed

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
