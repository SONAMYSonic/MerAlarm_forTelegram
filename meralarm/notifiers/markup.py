"""텔레그램 HTML 로 쓰인 글을 디스코드 마크다운으로 바꾼다.

명령 응답은 한 벌만 쓰고 채널마다 다르게 그린다. 두 벌을 따로 쓰면 한쪽만 고치는
일이 반드시 생긴다. 기준을 텔레그램 HTML 로 잡은 것은 그쪽이 이미 그렇게 쓰여
있기 때문이다. 옮기는 김에 새로 쓰면 잘 돌던 것이 깨진다.

    <b>굵게</b>        →  **굵게**
    <i>기울임</i>      →  *기울임*
    <code>코드</code>  →  `코드`
    <s>취소선</s>      →  ~~취소선~~
    <a href="U">T</a>  →  [T](U)
"""

from html.parser import HTMLParser

# 디스코드 메시지 한 통에 들어가는 길이. 넘기면 통째로 거절당한다.
LIMIT = 2000

# 디스코드가 서식 기호로 읽는 글자. 키워드나 상품명에 들어 있으면 글자가
# 굵어지거나 아예 사라진다. 일본어 키워드에는 잘 없지만 남이 쓰면 반드시 걸린다.
SPECIAL = "\\*_~`|"

_MARKS = {
    "b": "**",
    "strong": "**",
    "i": "*",
    "em": "*",
    "code": "`",
    "s": "~~",
    "del": "~~",
}


def escape(text: str) -> str:
    """평범한 글이 서식으로 읽히지 않게 한다."""
    for char in SPECIAL:
        text = text.replace(char, "\\" + char)
    return text


class _Converter(HTMLParser):
    def __init__(self) -> None:
        # &amp; &lt; 같은 것을 알아서 되돌려준다. 텔레그램에 보낼 때 escape() 로
        # 감싼 것들이라 여기서 풀어야 원래 글자가 된다.
        super().__init__(convert_charrefs=True)
        self.out: list[str] = []
        self._code = 0
        self._link = 0
        self._href = ""

    def handle_starttag(self, tag: str, attrs: list) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href", "") or ""
            self._link += 1
            self.out.append("[")
            return
        mark = _MARKS.get(tag)
        if mark is None:
            return
        if tag == "code":
            self._code += 1
        self.out.append(mark)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a":
            if self._link:
                self._link -= 1
                self.out.append(f"]({self._href})")
            return
        mark = _MARKS.get(tag)
        if mark is None:
            return
        if tag == "code":
            self._code = max(0, self._code - 1)
        self.out.append(mark)

    def handle_data(self, data: str) -> None:
        if self._code:
            # 코드 구간 안에서는 역슬래시 탈출이 통하지 않는다. 백틱이 들어오면
            # 구간이 거기서 끊겨 뒤가 통째로 깨지므로 비슷한 글자로 바꾼다.
            self.out.append(data.replace("`", "ˋ"))
            return
        text = escape(data)
        if self._link:
            # 링크 글자에 대괄호가 있으면 [글](주소) 구조가 깨진다.
            text = text.replace("[", "\\[").replace("]", "\\]")
        self.out.append(text)


def from_html(text: str) -> str:
    parser = _Converter()
    parser.feed(text)
    parser.close()
    return "".join(parser.out)


def chunks(text: str, limit: int = LIMIT) -> list[str]:
    """한 통에 안 들어가면 여러 통으로 나눈다.

    잘라서 버리지 않고 나눠 보낸다. `/config` 처럼 키워드가 늘수록 길어지는
    응답은 언제 한도를 넘을지 알 수 없고, 넘은 줄 모르고 잘리면 설정을 잘못
    읽게 된다.
    """
    if len(text) <= limit:
        return [text]

    parts: list[str] = []
    current = ""
    for line in text.split("\n"):
        # 한 줄이 그 자체로 한도를 넘으면 그 줄만 통째로 쪼갠다.
        while len(line) > limit:
            if current:
                parts.append(current)
                current = ""
            parts.append(line[:limit])
            line = line[limit:]
        if not current:
            current = line
        elif len(current) + 1 + len(line) <= limit:
            current += "\n" + line
        else:
            parts.append(current)
            current = line
    if current:
        parts.append(current)
    return parts
