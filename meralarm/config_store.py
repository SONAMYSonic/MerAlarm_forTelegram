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
from ruamel.yaml.comments import CommentedMap, CommentedSeq
from ruamel.yaml.error import CommentMark
from ruamel.yaml.tokens import CommentToken

from .text import fold


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


# ---- 목록에서 말을 뺄 때 남의 주석 지키기 ----
#
# ruamel 은 블록 목록 바로 뒤에 오는 빈 줄과 주석 — 실은 **다음 항목**을 설명하는
# 것 — 을 그 목록의 마지막 항목에 매단다. 그래서 마지막 말을 빼거나 목록을 비우면
# 다음 항목의 주석까지 함께 사라진다. 빼기 전에 떼어 두었다가 남는 자리에 다시 잇는다.


def _take_tail(seq) -> str | None:
    """마지막 항목에 매달린 '그 뒤 줄들'을 떼어 낸다. 옮길 것이 없으면 None.

    돌려주는 글은 줄바꿈(빠지는 항목 줄의 끝)으로 시작한다. 그 항목 자신의 줄끝
    주석은 항목과 함께 버린다.
    """
    if not isinstance(seq, CommentedSeq) or not len(seq):
        return None
    slot = seq.ca.items.get(len(seq) - 1)
    token = slot[0] if slot else None
    if token is None:
        return None
    slot[0] = None
    text = token.value
    if text.startswith("#"):
        text = text[text.index("\n"):] if "\n" in text else "\n"
    return text if text.strip() or text.count("\n") > 1 else None


def _put_after(node, key, text: str) -> None:
    """node[key] 의 마지막 줄 뒤에 잇는다. 값이 블록 목록·매핑이면 그 끝으로 들어간다."""
    value = node[key]
    if isinstance(value, (CommentedSeq, CommentedMap)):
        if not len(value):
            # `[]` 뒤에는 매달 자리가 없다. 억지로 달면 파일이 깨진다. 드문 모양이라 버린다.
            return
        if not value.fa.flow_style():
            last = len(value) - 1 if isinstance(value, CommentedSeq) else list(value)[-1]
            _put_after(value, last, text)
            return
    # 목록 항목은 0번 자리, 매핑 값은 2번 자리가 "이 줄 뒤" 주석이다.
    index = 0 if isinstance(node, CommentedSeq) else 2
    slot = node.ca.items.setdefault(key, [None, None, None, None])
    if slot[index] is None:
        slot[index] = CommentToken(text, CommentMark(0), None)
    else:
        # 이미 줄끝 주석이 있으면 그것이 줄을 끝낸다. 우리 글의 첫 줄바꿈은 뺀다.
        slot[index].value += text[1:]


def _put_before(mapping, key, text: str) -> None:
    """mapping 의 key 바로 앞에 줄마다 넣는다. 글에 든 들여쓰기를 그대로 쓴다.

    비운 목록은 `[]` 한 줄이 되거나 아예 지워져 뒤에 주석을 매달 항목이 없다. ruamel 도
    `[]` 뒤의 주석은 다음 키 앞에 둔다(배포판 config.example.yaml 의 전역 exclude 가
    그 모양이다).
    """
    slot = mapping.ca.items.setdefault(key, [None, [], None, None])
    if slot[1] is None:
        slot[1] = []
    slot[1][:0] = [CommentToken(line + "\n", CommentMark(0), None) for line in text[1:].splitlines()]


def _delete_items(seq, positions) -> str | None:
    """seq 에서 positions 번째 항목들을 뺀다.

    마지막 항목이 빠지면 거기 매달린 뒤 주석을 새 마지막 항목으로 옮긴다. 목록이 통째로
    비면 옮길 곳이 없으니 그 글을 돌려준다 — 놓을 자리는 부르는 쪽이 안다.
    """
    tail = _take_tail(seq) if len(seq) - 1 in positions else None
    # 앞에서부터 지우면 뒤 번호가 당겨진다. 음수 번호는 ruamel 이 주석 자리를 잘못 옮긴다.
    for position in sorted(positions, reverse=True):
        del seq[position]
    if tail and len(seq):
        _put_after(seq, len(seq) - 1, tail)
        return None
    return tail


class KeywordStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def _load(self):
        yaml = _yaml()
        # 줄바꿈을 \n 으로 맞춰 읽는다. CRLF 파일을 그대로 주면 ruamel 이 주석을 \r\n 째
        # 들고 있다가, 쓸 때 주석마다 빈 줄을 만들거나(윈도우) 목록 뒤 빈 줄을 먹는다.
        # 원래 줄바꿈으로 되돌려 쓰는 것은 _save 가 한다.
        data = yaml.load(self._path.read_text(encoding="utf-8-sig"))
        if data is None or "keywords" not in data:
            raise KeywordStoreError("config.yaml 에서 keywords 를 찾지 못했습니다.")
        return yaml, data

    def _save(self, yaml: YAML, data, validate: Callable[[], object] | None = None) -> None:
        """설정 파일을 바꿔 쓴다.

        `validate` 를 주면 새 내용으로 실제 설정 로드를 해보고, 실패하면 원본으로
        되돌린다. 이게 없으면 봇 명령 한 번으로 파일이 깨져 **다음 재시작 때 아예
        켜지지 않는** 상태가 될 수 있다. 무인 운영에서는 치명적이다.
        """
        original = self._path.read_bytes()

        buf = io.StringIO()
        yaml.dump(data, buf)
        # 읽을 때 \n 으로 맞췄으니(_load) 원래 파일이 주로 쓰던 줄바꿈으로 되돌린다.
        # 바이트째 쓴다. 텍스트 모드로 쓰면 윈도우가 \n 을 \r\n 으로 바꾸는데, 예전엔
        # 주석만 \r\n 째 들고 있어서 주석마다 \r\r\n — 메모장에서 빈 줄 — 이 생겼다.
        text = buf.getvalue()
        if original.count(b"\r\n") * 2 > original.count(b"\n"):
            text = text.replace("\n", "\r\n")

        # 임시 파일에 쓰고 바꿔치기한다. 쓰는 도중에 죽어도 원본이 남는다.
        tmp = self._path.with_suffix(".yaml.tmp")
        tmp.write_bytes(text.encode("utf-8"))
        os.replace(tmp, self._path)

        if validate is None:
            return
        try:
            validate()
        except Exception as e:
            self._path.write_bytes(original)
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

    def _entry(self, data, index: int):
        """1부터 세는 번호로 키워드 항목을 꺼낸다. /list 에 보이는 번호와 맞춘다.

        간단형(문자열 한 줄)은 조건을 담을 수 없으므로 상세형으로 바꿔준다.
        바꾸기만 하고 저장은 부르는 쪽이 한다 — 도중에 거절하면 파일은 그대로다.
        """
        count = len(data["keywords"])
        if not 1 <= index <= count:
            raise KeywordStoreError(f"번호는 1~{count} 사이여야 합니다.")
        entry = data["keywords"][index - 1]
        if isinstance(entry, str):
            entry = {"name": entry.strip(), "query": entry.strip()}
            data["keywords"][index - 1] = entry
        return entry

    def set_keyword(
        self,
        index: int,
        key: str,
        value: Any,
        validate: Callable[[], object] | None = None,
    ) -> str:
        """1부터 세는 번호로 키워드 항목을 고친다. 값이 None 이면 항목을 지운다."""
        yaml, data = self._load()
        entry = self._entry(data, index)
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
        having = {fold(str(w).strip()) for w in seq if w is not None}

        added = []
        for word in words:
            word = word.strip()
            if not word or fold(word) in having:
                continue
            seq.append(word)
            having.add(fold(word))
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
        target = fold(word.strip())
        index = next(
            (i for i, w in enumerate(seq or []) if w is not None and fold(str(w).strip()) == target),
            None,
        )
        if index is None:
            raise KeywordStoreError(f"'{word}' 는 전역 제외어에 없습니다.")
        removed = str(seq[index]).strip()
        self._drop_global(data, [index])
        self._save(yaml, data, validate)
        return removed

    def clear_global_excludes(self, validate: Callable[[], object] | None = None) -> list[str]:
        """전역 제외어를 전부 뺀다. 뺀 말을 돌려준다 — 되돌리는 명령을 만들 때 쓴다.

        목록은 `[]` 로 남긴다. 키까지 지우면 그 위에 붙은 설명 주석이 함께 사라진다.
        """
        yaml, data = self._load()
        seq = data.get("exclude")
        words = [str(w).strip() for w in (seq or []) if w is not None and str(w).strip()]
        if not words:
            return []
        self._drop_global(data, range(len(seq)))
        self._save(yaml, data, validate)
        return words

    @staticmethod
    def _drop_global(data, positions) -> None:
        tail = _delete_items(data["exclude"], positions)
        keys = list(data)
        after = keys.index("exclude") + 1
        if tail and after < len(keys):
            _put_before(data, keys[after], tail)

    # ---- 키워드별 낱말 목록 (exclude / require) ----
    #
    # 둘은 뜻만 반대일 뿐 다루는 방법이 같다. 한 벌로 두어야 한쪽만 고치는 일이 없다.

    LABELS = {"exclude": "제외어", "require": "필수 포함어"}

    def keyword_words(self, index: int, field: str) -> tuple[str, list[str]]:
        """(키워드 이름, 그 키워드의 낱말 목록)."""
        _, data = self._load()
        count = len(data["keywords"])
        if not 1 <= index <= count:
            raise KeywordStoreError(f"번호는 1~{count} 사이여야 합니다.")
        entry = data["keywords"][index - 1]
        if isinstance(entry, str):
            return entry.strip(), []
        words = [
            str(w).strip()
            for w in (entry.get(field) or [])
            if w is not None and str(w).strip()
        ]
        return _name_of(entry), words

    def add_keyword_words(
        self,
        index: int,
        field: str,
        words: list[str],
        validate: Callable[[], object] | None = None,
    ) -> tuple[str, list[str]]:
        """(키워드 이름, 새로 들어간 말). 이미 있는 말은 건너뛴다."""
        yaml, data = self._load()
        entry = self._entry(data, index)
        seq = entry.get(field)
        if not isinstance(seq, CommentedSeq):
            seq = CommentedSeq(
                [w for w in (seq or []) if w is not None and str(w).strip()]
            )
            entry[field] = seq
        having = {fold(str(w).strip()) for w in seq if w is not None}

        added = []
        for word in words:
            word = word.strip()
            if not word or fold(word) in having:
                continue
            seq.append(word)
            having.add(fold(word))
            added.append(word)

        if not added:
            return _name_of(entry), []
        self._save(yaml, data, validate)
        return _name_of(entry), added

    def remove_keyword_word(
        self,
        index: int,
        field: str,
        word: str,
        validate: Callable[[], object] | None = None,
    ) -> tuple[str, str]:
        yaml, data = self._load()
        entry = self._entry(data, index)
        seq = entry.get(field) or []
        target = fold(word.strip())
        position = next(
            (
                i
                for i, w in enumerate(seq)
                if w is not None and fold(str(w).strip()) == target
            ),
            None,
        )
        if position is None:
            raise KeywordStoreError(
                f"'{word}' 는 {_name_of(entry)} 의 "
                f"{self.LABELS.get(field, field)}에 없습니다."
            )
        removed = str(seq[position]).strip()
        self._drop_words(entry, field, [position])
        self._save(yaml, data, validate)
        return _name_of(entry), removed

    def clear_keyword_words(
        self, index: int, field: str, validate: Callable[[], object] | None = None
    ) -> tuple[str, list[str]]:
        """(키워드 이름, 뺀 말). 그 키워드의 낱말 목록을 전부 뺀다."""
        yaml, data = self._load()
        entry = self._entry(data, index)
        seq = entry.get(field)
        words = [str(w).strip() for w in (seq or []) if w is not None and str(w).strip()]
        if not words:
            return _name_of(entry), []
        self._drop_words(entry, field, range(len(seq)))
        self._save(yaml, data, validate)
        return _name_of(entry), words

    @staticmethod
    def _drop_words(entry, field: str, positions) -> None:
        seq = entry[field]
        tail = _delete_items(seq, positions)
        if len(seq):
            return
        # 다 빼면 항목 자체를 지운다. 빈 목록을 남기면 "- " 만 있는 줄이 되어
        # 예전에 상품을 삼켰던 그 모양으로 이어질 수 있다.
        keys = list(entry)
        at = keys.index(field)
        entry.pop(field, None)
        if not tail:
            return
        # 뒤따르던 주석은 다음 키 앞에 둔다. 이 목록이 맨 끝이었으면 앞 키 뒤에 잇는다.
        if at + 1 < len(keys):
            _put_before(entry, keys[at + 1], tail)
        elif at > 0:
            _put_after(entry, keys[at - 1], tail)

    # 예전 이름. 부르는 곳이 읽기 쉬우라고 남겨둔다.

    def keyword_excludes(self, index: int) -> tuple[str, list[str]]:
        return self.keyword_words(index, "exclude")

    def add_keyword_excludes(
        self, index: int, words: list[str], validate: Callable[[], object] | None = None
    ) -> tuple[str, list[str]]:
        return self.add_keyword_words(index, "exclude", words, validate)

    def remove_keyword_exclude(
        self, index: int, word: str, validate: Callable[[], object] | None = None
    ) -> tuple[str, str]:
        return self.remove_keyword_word(index, "exclude", word, validate)

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
