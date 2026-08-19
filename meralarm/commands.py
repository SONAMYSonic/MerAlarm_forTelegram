"""봇 명령어.

화면 없는 서버에서는 트레이 아이콘을 쓸 수 없어, 설정을 바꾸려면 SSH 로 들어가
config.yaml 을 고치고 재시작해야 한다. 그 대신 알림을 받는 그 자리에서 키워드를
넣고 빼고 잠시 멈출 수 있게 한다.

두 갈래로 나뉘어 있다.

    CommandCore       무엇을 하는지. 어느 채널에서 왔는지 모른다
    TelegramCommands  텔레그램에서 받아오는 방법 (롱 폴링)

디스코드 쪽 받아오기는 `notifiers/discord_bot.py` 에 있고 같은 코어를 쓴다.
명령 하나를 늘리려면 코어만 고치면 두 채널에 동시에 생긴다.

**어느 채널이든 주인이 보낸 것만 처리한다.** 봇은 검색이나 서버 목록으로
노출되므로 누구나 말을 걸 수 있고, 그대로 두면 남이 내 감시 설정을 바꾼다.
"""

import asyncio
import logging
import re
import time
from dataclasses import dataclass, field
from html import escape

import httpx

from .config import Config, ConfigError
from .config_store import KeywordStore, KeywordStoreError
from .control import Controls
from .alerts import raw
from .notifiers.queue import SendQueue
from .notifiers.telegram import API
from .scheduler import Scheduler
from .store import SeenStore
from .text import fold

log = logging.getLogger(__name__)

POLL_TIMEOUT = 30
DURATION = re.compile(r"^(\d+)\s*([smh]?)$", re.IGNORECASE)

# 제외어·필수어를 넣겠다고 물어본 뒤 이만큼 지나면 잊는다. 한참 전에 하다 만 것이
# 뒤늦게 확정되면 무엇을 승인했는지 모르게 된다.
PENDING_TTL_SEC = 300


@dataclass(frozen=True, slots=True)
class Pending:
    """확인을 기다리는 동안만 들고 있는 것.

    전에는 튜플이었는데 항목이 둘에서 넷으로 늘어나는 동안 자리로만 구분됐다.
    `_pending[0]` 이 종류인지 낱말인지는 읽는 사람이 기억해야 했고, 늘릴 때마다
    꺼내 쓰는 곳을 전부 찾아 고쳐야 했다.

    시각은 만들 때 저절로 찍힌다. 부르는 쪽이 `time.monotonic()` 을 기억할 일이
    아니고, 빠뜨리면 영영 안 만료되는 확인이 남는다.
    """

    # "exclude" 또는 "require". 물어본 것과 다른 쪽으로 확인하면 받지 않는다.
    kind: str
    words: tuple[str, ...]
    # None 이면 전역, 숫자면 그 번호의 키워드. 확인은 물어본 범위를 그대로 따른다.
    index: int | None
    asked_at: float = field(default_factory=time.monotonic)

    @property
    def expired(self) -> bool:
        return time.monotonic() - self.asked_at > PENDING_TTL_SEC


HELP = """<b>MerAlarm 명령어</b>

/status — 상태 보기
/list — 감시 중인 키워드
/add <i>키워드</i> — 키워드 추가
/del <i>번호</i> — 키워드 삭제
/exclude — 제외어 (이 말이 있으면 안 알림)
/require — 필수 포함어 (이 말이 없으면 안 알림)
/config — 현재 설정 전부 보기
/set <i>항목 값</i> — 설정 바꾸기
/pause <i>[30m]</i> — 잠시 멈춤 (시간 생략 시 무기한)
/resume — 다시 시작
/help — 이 도움말

<b>제외어 붙이기</b>
제목에 그 말이 들어가면 알리지 않습니다.

<code>/add 樋口円香 -セット -まとめ</code>
<code>/add 樋口円香 -セット,まとめ</code>
<code>/add ガンプラ MG -ジャンク</code>

제외어에는 <b>모두 -를 붙여야</b> 합니다. 하나라도 빠지면 키워드의 일부인지
제외어인지 알 수 없어 추가하지 않고 알려드립니다.

<b>제외어 고치기</b>
넣은 뒤에도 바꿀 수 있습니다. 지웠다 다시 넣지 않아도 됩니다.

<code>/exclude</code> — 모든 키워드에 적용되는 목록
<code>/exclude add まとめ</code> — 모든 키워드에 넣기
<code>/exclude del まとめ</code> — 빼기

<code>/exclude 2</code> — 2번 키워드의 제외어 보기
<code>/exclude 2 add セット</code> — 2번 키워드에만 넣기
<code>/exclude 2 del セット</code> — 2번에서 빼기

<b>엉뚱한 상품이 잔뜩 걸릴 때</b>
검색어가 짧으면 그 말이 다른 낱말 속에 박혀서도 걸립니다.
(<code>ストレイライト</code> → <code>エクストレイル</code> 닛산 차, <code>ストレイキッズ</code> 아이돌)

이럴 때는 제외어로 하나씩 막는 것보다 <b>있어야 할 말</b>을 정하는 편이 확실합니다.

<code>/require 2 add シャニマス アイドルマスター</code>

이 중 하나라도 제목에 있어야 알립니다. 없으면 안 알립니다.

넣기 전에 지금 추적 중인 상품 몇 건이 걸리는지 먼저 보여드립니다.
제외어는 대소문자도 전각/반각도 가리지 않습니다.
(<code>C108</code> 로 적으면 <code>c108</code> 도 <code>Ｃ１０８</code> 도 걸립니다)

<b>설정 바꾸기</b>

<code>/set interval 60</code> — 감시 주기(초)
<code>/set age 7</code> — 출품 7일 이내만
<code>/set bump 0</code> — 끌어올린 상품은 신규로 안 침
<code>/set krw off</code> — 원화 환산 끄기
<code>/set 2 price_max 50000</code> — 2번 키워드만 가격 상한

<b>age 와 bump 의 차이</b>
age 는 "출품한 지 며칠 됐나", bump 는 "출품하고 며칠 뒤에 갱신됐나"입니다.
5일 전에 올려두고 그대로인 물건은 age 5 · bump 0 (늦게 발견한 새 매물),
5일 전에 올리고 오늘 끌어올린 물건은 age 5 · bump 5 (이미 있던 물건)입니다.
둘 다 <b>신규 알림에만</b> 적용되고, 걸러져도 값을 내리면 알려드립니다.

항목 목록은 <code>/set</code> 만 쳐도 나옵니다.

키워드를 추가하면 첫 회차는 조용히 목록만 담고 그다음부터 알립니다."""


class ParseError(ValueError):
    """사용자에게 그대로 보여줄 안내 문구를 담는다."""


def parse_add(argument: str) -> tuple[str, tuple[str, ...]]:
    """`/add` 인자를 키워드와 제외어로 나눈다.

        芹沢あさひ                       → ("芹沢あさひ", ())
        芹沢あさひ -セット -まとめ        → ("芹沢あさひ", ("セット", "まとめ"))
        芹沢あさひ -セット,まとめ         → ("芹沢あさひ", ("セット", "まとめ"))
        ガンプラ MG -ジャンク            → ("ガンプラ MG", ("ジャンク",))

    애매한 입력은 추측하지 않고 거절한다. 특히 `키워드 -제외1 제외2` 처럼 대시를
    빠뜨린 경우, 뒤엣것을 키워드로 붙이면 조용히 엉뚱한 감시가 등록된다.
    """
    tokens = argument.split()
    if not tokens:
        raise ParseError("키워드가 비어 있습니다.")
    if tokens[0].startswith("-"):
        raise ParseError("키워드를 먼저 쓰고 그 뒤에 -제외어를 붙여주세요.")

    # 첫 번째 -토큰이 나오는 곳까지가 키워드다. 키워드에 공백이 있어도 된다.
    split_at = next((i for i, t in enumerate(tokens) if t.startswith("-")), len(tokens))
    query = " ".join(tokens[:split_at])

    excludes: list[str] = []
    for token in tokens[split_at:]:
        if not token.startswith("-"):
            raise ParseError(
                f"'{token}' 앞에 -가 빠진 것 같습니다.\n"
                f"제외어는 모두 -를 붙여야 합니다. 예: <code>/add {query} -A -B</code>"
            )
        body = token[1:]
        if body.startswith("-"):
            raise ParseError("-는 하나만 붙여주세요.")
        if not body:
            raise ParseError("-만 있고 제외어가 없습니다.")
        if body.endswith(","):
            # 쉼표 뒤에 띄어쓴 경우다. 뒤엣말이 제외어인지 키워드인지 알 수 없다.
            raise ParseError(
                "쉼표 뒤에 띄어쓰면 안 됩니다.\n"
                f"<code>{token},다음말</code> 처럼 붙여 쓰거나 "
                f"<code>{token[:-1]} -다음말</code> 처럼 각각 -를 붙여주세요."
            )
        for part in body.split(","):
            part = part.strip()
            if not part:
                raise ParseError("제외어 사이가 비어 있습니다. 쉼표를 확인해주세요.")
            if len(part) > 50:
                raise ParseError("제외어가 너무 깁니다. 50자 이내로 써주세요.")
            if part not in excludes:
                excludes.append(part)

    if not query:
        raise ParseError("키워드가 없습니다. 제외어만으로는 감시할 수 없습니다.")
    if len(excludes) > 20:
        raise ParseError("제외어가 너무 많습니다. 20개 이내로 써주세요.")
    return query, tuple(excludes)


OFF_WORDS = {"off", "없음", "해제", "none", "null", "-"}


def _switch(text: str) -> bool:
    lowered = text.lower()
    if lowered in {"on", "켜기", "true", "1", "yes"}:
        return True
    if lowered in OFF_WORDS | {"false", "0", "no", "끄기"}:
        return False
    raise ParseError("on 또는 off 로 써주세요.")


def _ranged(label: str, low: int, high: int, allow_off: bool = False):
    def parse(text: str):
        if allow_off and text.lower() in OFF_WORDS:
            return None
        if not text.lstrip("-").isdigit():
            raise ParseError(
                f"{label} 은(는) 숫자여야 합니다."
                + (" 해제하려면 off 라고 쓰세요." if allow_off else "")
            )
        value = int(text)
        if not low <= value <= high:
            raise ParseError(f"{label} 은(는) {low}~{high} 사이여야 합니다. 받은 값: {value}")
        return value

    return parse


# 사용자에게 보이는 이름 → (config.yaml 경로, 설명, 값 해석기)
GLOBAL_SETTINGS = {
    "interval": (("poll", "default_interval_sec"), "감시 주기(초)", _ranged("주기", 10, 3600)),
    "gap": (("poll", "min_request_gap_sec"), "요청 간 최소 간격(초)", _ranged("간격", 1, 60)),
    "age": (("notify", "max_age_days"), "출품 경과일 제한(일)", _ranged("경과일", 1, 3650, True)),
    "bump": (("notify", "max_bump_days"), "끌어올림 허용(일)", _ranged("일수", 0, 3650, True)),
    "batch": (("notify", "batch_threshold"), "묶음 요약 기준(건)", _ranged("기준", 2, 100)),
    "krw": (("notify", "show_krw"), "원화 환산", _switch),
    "drop": (("notify", "price_drop"), "가격 인하 알림", _switch),
    "night": (("poll", "night_mode", "enabled"), "심야 자동 감속", _switch),
    "heartbeat": (("notify", "heartbeat_hour"), "생존 보고 시각(시)", _ranged("시각", 0, 23, True)),
    "keep": (("store", "keep_days"), "기록 보관 기간(일)", _ranged("보관 기간", 7, 3650)),
}

# 키워드별 설정. off 를 주면 항목을 지워 전역값을 따르거나 제한을 없앤다.
KEYWORD_SETTINGS = {
    "interval": ("interval_sec", "감시 주기(초)", _ranged("주기", 10, 3600, True)),
    "price_min": ("price_min", "최저 가격(엔)", _ranged("가격", 0, 100_000_000, True)),
    "price_max": ("price_max", "최고 가격(엔)", _ranged("가격", 0, 100_000_000, True)),
    "age": ("max_age_days", "출품 경과일 제한(일)", _ranged("경과일", 1, 3650, True)),
    "bump": ("max_bump_days", "끌어올림 허용(일)", _ranged("일수", 0, 3650, True)),
}


def _parse_duration(text: str) -> float | None:
    """`30m` `2h` `90s` `15`(분) → 초. 못 읽으면 None."""
    match = DURATION.match(text.strip())
    if not match:
        return None
    value, unit = int(match.group(1)), match.group(2).lower()
    if value <= 0:
        return None
    return value * {"s": 1, "m": 60, "h": 3600, "": 60}[unit]


def _humanize(seconds: float) -> str:
    minutes = int(seconds // 60)
    if minutes >= 60:
        return f"{minutes // 60}시간 {minutes % 60}분" if minutes % 60 else f"{minutes // 60}시간"
    return f"{minutes}분" if minutes else f"{int(seconds)}초"


class CommandCore:
    """명령을 해석하고 실행한다. 어느 채널에서 온 명령인지는 모른다.

    답장은 텔레그램 HTML 로 쓴다. 디스코드로 갈 때는 `notifiers/markup.py` 가
    마크다운으로 바꾼다. 채널마다 문구를 따로 두면 한쪽만 고치는 일이 생긴다.
    """

    def __init__(
        self,
        cfg: Config,
        controls: Controls,
        scheduler: Scheduler,
        seen: SeenStore | None = None,
    ) -> None:
        self._cfg = cfg
        self._controls = controls
        self._scheduler = scheduler
        self._store = KeywordStore(cfg.config_path)
        # 전역 제외어를 넣기 전에 무엇이 걸리는지 세어 보려고 쓴다. 없어도
        # 동작은 하되 미리보기만 생략된다.
        self._seen = seen
        # 확인을 기다리는 동안만 들고 있는다. 확인이 끝나거나 시간이 지나면 비운다.
        self._pending: Pending | None = None

    def dispatch(self, text: str) -> str:
        """명령 한 줄을 받아 답장을 돌려준다.

        여기서 예외를 삼키는 것은 채널을 붙일 때마다 같은 try 를 다시 쓰지
        않기 위해서다. 명령 하나가 터졌다고 수신 자체가 멈추면 안 된다.
        """
        try:
            return self._route(text)
        except Exception:
            log.exception("명령 처리 중 오류")
            return "⚠️ 처리 중 오류가 났습니다. 로그를 확인하세요."

    def _route(self, text: str) -> str:
        parts = text.split(maxsplit=1)
        # 그룹에서는 /add@봇이름 형태로 온다.
        command = parts[0].split("@")[0].lower()
        argument = parts[1].strip() if len(parts) > 1 else ""

        handlers = {
            "/help": lambda _: HELP,
            "/start": lambda _: HELP,
            "/status": self._status,
            "/list": self._list,
            "/add": self._add,
            "/del": self._delete,
            "/set": self._set,
            "/config": self._config,
            "/exclude": self._exclude,
            "/require": self._require,
            "/pause": self._pause,
            "/resume": self._resume,
        }
        handler = handlers.get(command)
        if handler is None:
            return f"모르는 명령입니다: {escape(command)}\n/help 로 사용법을 보세요."
        return handler(argument)

    def _status(self, _: str) -> str:
        stats = self._scheduler.stats
        hours = stats.uptime_seconds() / 3600
        if self._controls.paused:
            remain = self._controls.resume_in
            state = f"⏸ 일시정지 ({_humanize(remain)} 뒤 재개)" if remain else "⏸ 일시정지"
        else:
            state = "🔍 감시 중"
        from . import __version__

        return (
            f"<b>{state}</b>  <i>v{__version__}</i>\n\n"
            f"가동 {hours:.1f}시간\n"
            f"키워드 {len(self._scheduler.keyword_names)}개 · 기본 주기 "
            f"{self._cfg.poll.default_interval_sec}초\n"
            f"요청 {stats.requests:,}회 (실패 {stats.failures})\n"
            f"신규 알림 {stats.new_items}건 · 가격 인하 {stats.price_drops}건"
        )

    def _list(self, _: str) -> str:
        try:
            entries = self._store.entries()
            shared = self._store.global_excludes()
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        lines = []
        for i, (name, excludes) in enumerate(entries, 1):
            lines.append(f"{i}. <b>{escape(name)}</b>")
            try:
                must = self._store.keyword_words(i, "require")[1]
            except KeywordStoreError:
                must = []
            if must:
                lines.append(
                    "    필수: " + ", ".join(f"<code>{escape(w)}</code>" for w in must)
                )
            if excludes:
                lines.append("    제외: " + ", ".join(f"<code>{escape(w)}</code>" for w in excludes))
        text = "<b>감시 중인 키워드</b>\n\n" + "\n".join(lines)

        # 전역 제외어는 여기 안 보이면 "왜 이 상품이 안 오지" 의 답을 찾을 수 없다.
        if shared:
            text += "\n\n<b>전역 제외어</b> (모든 키워드에 적용)\n" + " ".join(
                f"<code>{escape(w)}</code>" for w in shared
            )
        return text + "\n\n/del 번호 로 지울 수 있습니다."

    def _add(self, argument: str) -> str:
        if not argument:
            return (
                "사용법: <code>/add 키워드 -제외어 -제외어</code>\n\n"
                "예:\n"
                "<code>/add 樋口円香</code>\n"
                "<code>/add 樋口円香 -セット -まとめ</code>\n"
                "<code>/add 樋口円香 -セット,まとめ</code>"
            )
        try:
            query, excludes = parse_add(argument)
        except ParseError as e:
            return f"⚠️ {e}"

        try:
            added = self._store.add(query, excludes, validate=self._validator())
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        if not self._reload():
            return f"✅ {escape(added)} 를 설정에 적었지만 반영에 실패했습니다. 로그를 확인하세요."

        lines = [f"✅ <b>{escape(added)}</b> 추가했습니다."]
        if excludes:
            lines.append("제외어: " + ", ".join(f"<code>{escape(w)}</code>" for w in excludes))
        lines.append("\n첫 회차는 조용히 목록만 담고 그다음부터 알립니다.")
        return "\n".join(lines)

    def _delete(self, argument: str) -> str:
        if not argument.isdigit():
            return "사용법: <code>/del 번호</code>\n/list 로 번호를 확인하세요."
        try:
            removed = self._store.remove(int(argument), validate=self._validator())
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"
        self._reload()
        return f"🗑 <b>{escape(removed)}</b> 감시를 중단했습니다."

    # ---- 제외어 ----
    #
    # 전역(모든 키워드)과 키워드별을 같은 명령으로 다룬다. 번호를 앞에 붙이면
    # 그 키워드에만 걸린다 — `/set 2 price_max 50000` 과 같은 번호 체계다.

    def _exclude(self, argument: str) -> str:
        parts = argument.split()
        if not parts:
            return self._exclude_show()

        index = None
        if parts[0].isdigit():
            index = int(parts[0])
            parts = parts[1:]
            if not parts:
                return self._exclude_show(index)

        action, words = parts[0].lower(), parts[1:]
        if action in {"add", "추가"}:
            return self._exclude_add(words, index)
        if action in {"del", "delete", "remove", "삭제", "제거"}:
            return self._exclude_remove(words, index)
        if action in {"yes", "y", "확인"}:
            # 확인은 물어본 곳의 범위를 그대로 따른다. 번호를 다시 적어도 무시한다.
            return self._exclude_confirm()
        return f"모르는 사용법입니다: <code>{escape(action)}</code>\n\n" + self._exclude_usage()

    @staticmethod
    def _exclude_usage() -> str:
        return (
            "<b>제외어</b> — 제목에 그 말이 들어 있으면 알리지 않습니다.\n\n"
            "<b>모든 키워드에</b>\n"
            "<code>/exclude</code> — 지금 목록\n"
            "<code>/exclude add まとめ ジャンク</code> — 넣기\n"
            "<code>/exclude del まとめ</code> — 빼기\n\n"
            "<b>한 키워드에만</b> (번호는 /list 기준)\n"
            "<code>/exclude 2</code> — 2번 키워드의 제외어\n"
            "<code>/exclude 2 add セット</code> — 2번에만 넣기\n"
            "<code>/exclude 2 del セット</code> — 2번에서 빼기"
        )

    def _exclude_show(self, index: int | None = None) -> str:
        try:
            if index is None:
                words = self._store.global_excludes()
                if not words:
                    return "전역 제외어가 없습니다.\n\n" + self._exclude_usage()
                listed = " ".join(f"<code>{escape(w)}</code>" for w in words)
                return (
                    f"<b>전역 제외어</b> {len(words)}개\n{listed}\n\n"
                    "모든 키워드에 함께 적용됩니다.\n"
                    "빼려면 <code>/exclude del 단어</code>"
                )

            name, words = self._store.keyword_excludes(index)
            shared = self._store.global_excludes()
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        lines = [f"<b>{escape(name)}</b> 의 제외어"]
        lines.append(
            " ".join(f"<code>{escape(w)}</code>" for w in words) if words else "<i>없음</i>"
        )
        if shared:
            # 여기 안 보이면 "이 키워드엔 적은 적 없는데 왜 안 오지" 를 풀 수 없다.
            lines += [
                "",
                "여기에 더해 <b>전역 제외어</b>도 적용됩니다",
                " ".join(f"<code>{escape(w)}</code>" for w in shared),
            ]
        try:
            must = self._store.keyword_words(index, "require")[1]
        except KeywordStoreError:
            must = []
        if must:
            lines += [
                "",
                "<b>필수 포함어</b> (이 중 하나는 제목에 있어야 함)",
                " ".join(f"<code>{escape(w)}</code>" for w in must),
            ]
        lines += ["", f"<code>/exclude {index} add 단어</code> 로 넣습니다."]
        return "\n".join(lines)

    @staticmethod
    def _clean_words(words: list[str]) -> list[str]:
        cleaned: list[str] = []
        for word in words:
            # /add 가 -단어 문법이라 습관적으로 붙여 쓰기 쉽다. 받아준다.
            if word.startswith("-"):
                word = word[1:]
            word = word.strip()
            if not word:
                raise ParseError("빈 제외어가 있습니다.")
            if len(word) > 50:
                raise ParseError("제외어가 너무 깁니다. 50자 이내로 써주세요.")
            if word not in cleaned:
                cleaned.append(word)
        if len(cleaned) > 20:
            raise ParseError("한 번에 20개까지만 넣을 수 있습니다.")
        return cleaned

    def _exclude_add(self, words: list[str], index: int | None = None) -> str:
        if not words:
            return self._exclude_usage()
        try:
            cleaned = self._clean_words(words)
            having = {
                fold(w)
                for w in (
                    self._store.global_excludes()
                    if index is None
                    else self._store.keyword_excludes(index)[1]
                )
            }
        except ParseError as e:
            return f"⚠️ {e}"
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        fresh = [w for w in cleaned if fold(w) not in having]
        if not fresh:
            return "이미 전부 들어 있습니다."

        warning = self._preview(fresh, index)
        if warning is None:
            return self._apply_excludes(fresh, index)
        self._pending = Pending("exclude", tuple(fresh), index)
        return warning

    def _preview(self, words: list[str], index: int | None = None) -> str | None:
        """넣으면 무엇이 사라지는지 미리 보여준다. 걸리는 게 없으면 None.

        제외어는 한 단어만 잘못 넣어도 **알림이 조용히 죽는다.** 예전에 빈 항목이
        문자열 "None" 으로 읽혀 상품을 삼킨 적도 있다. 사라질 것을 먼저 보여준다.
        """
        if self._seen is None:
            return None
        try:
            keyword = "" if index is None else self._store.keyword_excludes(index)[0]
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        lines: list[str] = []
        hits_total = tracked = 0
        for word in words:
            hits, tracked, samples = self._seen.matching_names(
                word, keyword=keyword or None
            )
            hits_total += hits
            if not hits:
                lines.append(f"· <code>{escape(word)}</code> — 지금 걸리는 상품 없음")
                continue
            lines.append(f"· <code>{escape(word)}</code> — <b>{hits}건</b>")
            for name in samples:
                lines.append(f"    {escape(name[:44])}")

        if not hits_total:
            return None
        where = f"<b>{escape(keyword)}</b> 가 잡아온" if keyword else "추적 중인"
        return (
            f"⚠️ 넣기 전에 확인해 주세요. {where} {tracked:,}건 기준입니다.\n\n"
            + "\n".join(lines)
            + "\n\n<b>이 상품들은 앞으로 알림이 오지 않습니다.</b>\n"
            "넣으시려면 <code>/exclude yes</code>"
        )

    def _confirm(self, kind: str) -> str:
        """`/exclude yes` 와 `/require yes` 가 함께 쓴다."""
        pending = self._pending
        if pending is None or pending.kind != kind:
            return f"확인할 것이 없습니다. <code>/{kind} add 단어</code> 부터 해주세요."
        # 한 번 쓴 확인은 되살아나지 않는다. 만료됐더라도 여기서 비운다.
        self._pending = None
        if pending.expired:
            return f"시간이 지나 취소했습니다. <code>/{kind} add 단어</code> 를 다시 해주세요."
        if kind == "exclude":
            return self._apply_excludes(list(pending.words), pending.index)
        return self._apply_requires(list(pending.words), pending.index)

    def _exclude_confirm(self) -> str:
        return self._confirm("exclude")

    def _apply_excludes(self, words: list[str], index: int | None = None) -> str:
        try:
            if index is None:
                name = ""
                added = self._store.add_global_excludes(words, validate=self._validator())
            else:
                name, added = self._store.add_keyword_excludes(
                    index, words, validate=self._validator()
                )
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"
        if not added:
            return "이미 전부 들어 있습니다."

        self._reload()
        listed = " ".join(f"<code>{escape(w)}</code>" for w in added)
        if index is None:
            return (
                f"✅ 전역 제외어에 넣었습니다: {listed}\n"
                "모든 키워드에 바로 적용됩니다. 빼려면 <code>/exclude del 단어</code>"
            )
        return (
            f"✅ <b>{escape(name)}</b> 의 제외어에 넣었습니다: {listed}\n"
            f"이 키워드에만 적용됩니다. 빼려면 <code>/exclude {index} del 단어</code>"
        )

    def _exclude_remove(self, words: list[str], index: int | None = None) -> str:
        if not words:
            sample = "/exclude del まとめ" if index is None else f"/exclude {index} del セット"
            return f"뺄 말을 적어주세요. 예: <code>{escape(sample)}</code>"

        removed: list[str] = []
        missing: list[str] = []
        name = ""
        for word in words:
            try:
                if index is None:
                    removed.append(
                        self._store.remove_global_exclude(word, validate=self._validator())
                    )
                else:
                    name, taken = self._store.remove_keyword_exclude(
                        index, word, validate=self._validator()
                    )
                    removed.append(taken)
            except KeywordStoreError:
                missing.append(word)
        if removed:
            self._reload()

        lines = []
        if removed:
            where = "전역 제외어" if index is None else f"<b>{escape(name)}</b>"
            lines.append(
                f"🗑 {where} 에서 뺐습니다: "
                + " ".join(f"<code>{escape(w)}</code>" for w in removed)
            )
            lines.append("이 말이 든 상품도 다시 알림 대상이 됩니다.")
        if missing:
            lines.append(
                "원래 없던 말: " + " ".join(f"<code>{escape(w)}</code>" for w in missing)
            )
        return "\n".join(lines)

    # ---- 필수 포함어 ----
    #
    # 제외어와 방향이 반대다. "이 말이 있으면 빼기" 가 아니라 "이 말이 없으면 빼기".
    #
    # 메루카리 검색은 제목만 보지 않고 설명문까지 뒤지며, 짧은 말은 다른 낱말 속에
    # 박혀서도 걸린다. `ストレイライト` 로 찾으면 `エクストレイル`(닛산 차),
    # `ハイウエストレイヤード`(옷), `ストレイキッズ`(아이돌)가 함께 온다. 실측으로
    # 120건 중 제목에 그 말이 있는 것은 13건뿐이었고, 제외어를 13개나 걸어도 쓰레기
    # 10건이 남았다. 반대로 "있어야 할 말" 하나를 두니 쓰레기가 0이 됐다.
    #
    # 전역으로는 두지 않는다. 모든 키워드에 같은 말을 요구할 일은 드물고, 한 번
    # 잘못 넣으면 모든 알림이 한꺼번에 죽는다.

    def _require(self, argument: str) -> str:
        parts = argument.split()
        if not parts:
            return self._require_show()

        if parts[0].lower() in {"yes", "y", "확인"}:
            return self._confirm("require")

        if not parts[0].isdigit():
            return (
                "필수 포함어는 <b>키워드마다</b> 정합니다. 번호를 앞에 적어주세요.\n\n"
                + self._require_usage()
            )
        index, parts = int(parts[0]), parts[1:]
        if not parts:
            return self._require_show(index)

        action, words = parts[0].lower(), parts[1:]
        if action in {"add", "추가"}:
            return self._require_add(words, index)
        if action in {"del", "delete", "remove", "삭제", "제거"}:
            return self._require_remove(words, index)
        return f"모르는 사용법입니다: <code>{escape(action)}</code>\n\n" + self._require_usage()

    @staticmethod
    def _require_usage() -> str:
        return (
            "<b>필수 포함어</b> — 제목에 이 말이 <b>없으면</b> 알리지 않습니다.\n"
            "여러 개면 그중 <b>하나만 맞아도</b> 통과합니다.\n\n"
            "<code>/require</code> — 전체 목록\n"
            "<code>/require 2</code> — 2번 키워드의 필수 포함어\n"
            "<code>/require 2 add シャニマス アイドルマスター</code> — 넣기\n"
            "<code>/require 2 del シャニマス</code> — 빼기\n\n"
            "검색어가 짧아 엉뚱한 상품이 잔뜩 걸릴 때 씁니다. "
            "제외어로 하나씩 막는 것보다 확실합니다."
        )

    def _require_show(self, index: int | None = None) -> str:
        try:
            if index is not None:
                name, words = self._store.keyword_words(index, "require")
                if not words:
                    return (
                        f"<b>{escape(name)}</b> 에는 필수 포함어가 없습니다.\n"
                        f"제목을 따지지 않고 검색에 걸린 것을 모두 봅니다.\n\n"
                        f"<code>/require {index} add 단어</code> 로 넣습니다."
                    )
                listed = " ".join(f"<code>{escape(w)}</code>" for w in words)
                return (
                    f"<b>{escape(name)}</b> 의 필수 포함어\n{listed}\n\n"
                    f"이 중 하나라도 제목에 있어야 알립니다.\n"
                    f"빼려면 <code>/require {index} del 단어</code>"
                )

            entries = self._store.entries()
            rows = []
            for i in range(1, len(entries) + 1):
                name, words = self._store.keyword_words(i, "require")
                shown = (
                    " ".join(f"<code>{escape(w)}</code>" for w in words)
                    if words
                    else "<i>없음</i>"
                )
                rows.append(f"{i}. <b>{escape(name)}</b> — {shown}")
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        return (
            "<b>필수 포함어</b> (제목에 없으면 알리지 않음)\n\n"
            + "\n".join(rows)
            + "\n\n<code>/require 번호 add 단어</code> 로 넣습니다."
        )

    def _require_add(self, words: list[str], index: int) -> str:
        if not words:
            return self._require_usage()
        try:
            cleaned = self._clean_words(words)
            name, having = self._store.keyword_words(index, "require")
        except ParseError as e:
            return f"⚠️ {e}"
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        known = {fold(w) for w in having}
        fresh = [w for w in cleaned if fold(w) not in known]
        if not fresh:
            return "이미 전부 들어 있습니다."

        warning = self._require_preview(name, having + fresh, fresh, index)
        if warning is None:
            return self._apply_requires(fresh, index)
        self._pending = Pending("require", tuple(fresh), index)
        return warning

    def _require_preview(
        self, name: str, combined: list[str], fresh: list[str], index: int
    ) -> str | None:
        """넣으면 무엇만 남는지 보여준다. 셀 것이 없으면 None.

        제외어보다 훨씬 위험하다. 제외어는 걸리는 것만 빼지만 필수 포함어는
        **걸리지 않는 것을 전부 뺀다.** 한 글자만 틀려도 그 키워드가 통째로 조용해진다.
        """
        if self._seen is None:
            return None
        kept, total, keep_samples, drop_samples = self._seen.require_preview(name, combined)
        if not total:
            return None

        listed = " ".join(f"<code>{escape(w)}</code>" for w in fresh)
        lines = [
            f"⚠️ 넣기 전에 확인해 주세요. {listed}",
            "",
            f"<b>{escape(name)}</b> 가 잡아온 {total:,}건 중 "
            f"<b>{kept}건만 남고 {total - kept}건이 사라집니다.</b>",
        ]
        if keep_samples:
            lines.append("")
            lines.append("남는 것")
            lines += [f"    {escape(n[:44])}" for n in keep_samples]
        if drop_samples:
            lines.append("")
            lines.append("사라지는 것")
            lines += [f"    {escape(n[:44])}" for n in drop_samples]
        if not kept:
            lines.append("")
            lines.append("<b>남는 것이 하나도 없습니다.</b> 말이 맞는지 다시 봐주세요.")
        lines.append("")
        lines.append("넣으시려면 <code>/require yes</code>")
        return "\n".join(lines)

    def _apply_requires(self, words: list[str], index: int) -> str:
        try:
            name, added = self._store.add_keyword_words(
                index, "require", words, validate=self._validator()
            )
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"
        if not added:
            return "이미 전부 들어 있습니다."

        self._reload()
        listed = " ".join(f"<code>{escape(w)}</code>" for w in added)
        return (
            f"✅ <b>{escape(name)}</b> 의 필수 포함어에 넣었습니다: {listed}\n"
            f"이제 제목에 이 중 하나가 있어야 알립니다. "
            f"빼려면 <code>/require {index} del 단어</code>"
        )

    def _require_remove(self, words: list[str], index: int) -> str:
        if not words:
            return f"뺄 말을 적어주세요. 예: <code>/require {index} del シャニマス</code>"

        removed: list[str] = []
        missing: list[str] = []
        name = ""
        for word in words:
            try:
                name, taken = self._store.remove_keyword_word(
                    index, "require", word, validate=self._validator()
                )
                removed.append(taken)
            except KeywordStoreError:
                missing.append(word)
        if removed:
            self._reload()

        lines = []
        if removed:
            lines.append(
                f"🗑 <b>{escape(name)}</b> 의 필수 포함어에서 뺐습니다: "
                + " ".join(f"<code>{escape(w)}</code>" for w in removed)
            )
            try:
                left = self._store.keyword_words(index, "require")[1]
            except KeywordStoreError:
                left = []
            if not left:
                lines.append("이제 제목을 따지지 않고 검색에 걸린 것을 모두 봅니다.")
        if missing:
            lines.append(
                "원래 없던 말: " + " ".join(f"<code>{escape(w)}</code>" for w in missing)
            )
        return "\n".join(lines)

    def _set(self, argument: str) -> str:
        tokens = argument.split()
        if len(tokens) < 2:
            return self._set_usage()

        # 첫 토큰이 숫자면 그 번호의 키워드 설정이다. /del 과 같은 번호 체계.
        if tokens[0].isdigit():
            if len(tokens) < 3:
                return self._set_usage()
            return self._set_keyword(int(tokens[0]), tokens[1].lower(), tokens[2])
        return self._set_global(tokens[0].lower(), tokens[1])

    def _set_usage(self) -> str:
        globals_ = " ".join(f"<code>{k}</code>" for k in GLOBAL_SETTINGS)
        per_kw = " ".join(f"<code>{k}</code>" for k in KEYWORD_SETTINGS)
        return (
            "사용법\n"
            "<code>/set 항목 값</code> — 전체에 적용\n"
            "<code>/set 번호 항목 값</code> — 그 키워드에만 적용\n\n"
            f"전체: {globals_}\n"
            f"키워드별: {per_kw}\n\n"
            "예\n"
            "<code>/set interval 60</code>\n"
            "<code>/set age 7</code>\n"
            "<code>/set krw off</code>\n"
            "<code>/set 2 price_max 50000</code>\n"
            "<code>/set 2 price_max off</code>\n\n"
            "현재 값은 /config 로 봅니다."
        )

    def _set_global(self, key: str, raw: str) -> str:
        spec = GLOBAL_SETTINGS.get(key)
        if spec is None:
            return f"모르는 항목입니다: <code>{escape(key)}</code>\n\n" + self._set_usage()
        path, label, parse = spec
        try:
            value = parse(raw)
        except ParseError as e:
            return f"⚠️ {e}"

        try:
            self._store.set_global(path, value, validate=self._validator())
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        self._reload()
        return f"✅ <b>{label}</b> → {self._show(value)}"

    def _set_keyword(self, index: int, key: str, raw: str) -> str:
        spec = KEYWORD_SETTINGS.get(key)
        if spec is None:
            allowed = " ".join(f"<code>{k}</code>" for k in KEYWORD_SETTINGS)
            return f"키워드별로 바꿀 수 있는 항목: {allowed}"
        field, label, parse = spec
        try:
            value = parse(raw)
        except ParseError as e:
            return f"⚠️ {e}"

        try:
            name = self._store.set_keyword(index, field, value, validate=self._validator())
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        self._reload()
        return f"✅ <b>{escape(name)}</b> · {label} → {self._show(value)}"

    @staticmethod
    def _show(value) -> str:
        if value is None:
            return "<i>지정 안 함</i>"
        if isinstance(value, bool):
            return "켜짐" if value else "꺼짐"
        return f"<b>{value:,}</b>" if isinstance(value, int) else f"<b>{value}</b>"

    def _config(self, _: str) -> str:
        try:
            from .config import load

            cfg = load()
        except ConfigError as e:
            return f"⚠️ 설정을 읽지 못했습니다: {escape(str(e))}"

        night = cfg.poll.night
        lines = [
            "<b>전체 설정</b>",
            f"· 감시 주기 <b>{cfg.poll.default_interval_sec}초</b> "
            f"(요청 간격 {cfg.poll.min_request_gap_sec:g}초)",
            f"· 출품 경과일 제한 {self._show(cfg.notify.max_age_days)}"
            + ("일" if cfg.notify.max_age_days is not None else ""),
            f"· 끌어올림 허용 {self._show(cfg.notify.max_bump_days)}"
            + ("일" if cfg.notify.max_bump_days is not None else ""),
            f"· 묶음 요약 기준 <b>{cfg.notify.batch_threshold}건</b>",
            f"· 원화 환산 {self._show(cfg.notify.show_krw)}"
            f" · 인하 알림 {self._show(cfg.notify.price_drop)}",
            f"· 심야 감속 {self._show(night.enabled)}"
            + (f" ({night.start:%H:%M}~{night.end:%H:%M} {night.interval_sec}초)"
               if night.enabled else ""),
            f"· 생존 보고 {self._show(cfg.notify.heartbeat_hour)}"
            + ("시" if cfg.notify.heartbeat_hour is not None else ""),
            f"· 기록 보관 <b>{cfg.store.keep_days}일</b>",
        ]
        if cfg.exclude:
            lines.append(
                "· 전역 제외어 " + " ".join(f"<code>{escape(w)}</code>" for w in cfg.exclude)
            )
        lines += ["", "<b>키워드</b>"]
        for i, kw in enumerate(cfg.keywords, 1):
            detail = []
            if kw.price_min is not None or kw.price_max is not None:
                low = f"¥{kw.price_min:,}" if kw.price_min is not None else ""
                high = f"¥{kw.price_max:,}" if kw.price_max is not None else ""
                detail.append(f"가격 {low}~{high}")
            if kw.interval_sec != cfg.poll.default_interval_sec:
                detail.append(f"주기 {kw.interval_sec}초")
            if kw.require:
                detail.append("필수 " + ", ".join(kw.require))
            # 전역 제외어는 위에 따로 보여줬으므로 여기서는 이 키워드에만 적은 것만.
            own = [w for w in kw.exclude if w not in cfg.exclude]
            if own:
                detail.append("제외 " + ", ".join(own))
            lines.append(f"{i}. <b>{escape(kw.name)}</b>")
            if detail:
                lines.append("    " + escape(" · ".join(detail)))

        lines.append("\n바꾸려면 /set 을 쓰세요.")
        return "\n".join(lines)

    def _validator(self):
        from .config import load

        return load

    def _pause(self, argument: str) -> str:
        if not argument:
            self._controls.pause()
            return "⏸ 멈췄습니다. /resume 으로 다시 시작합니다."
        seconds = _parse_duration(argument)
        if seconds is None:
            return "시간을 못 읽었습니다. <code>/pause 30m</code> <code>/pause 2h</code> 처럼 써주세요."
        self._controls.pause(seconds)
        return f"⏸ {_humanize(seconds)} 동안 멈춥니다. 그 뒤 자동으로 재개됩니다."

    def _resume(self, _: str) -> str:
        if not self._controls.paused:
            return "이미 감시 중입니다."
        self._controls.resume()
        return "▶️ 다시 시작했습니다."

    # ---- 설정 반영 ----

    def _reload(self) -> bool:
        """바뀐 config.yaml 을 다시 읽어 스케줄러에 적용한다. 재시작 없이 반영된다."""
        try:
            from .config import load

            fresh = load()
        except ConfigError as e:
            log.error("설정을 다시 읽지 못했습니다: %s", e)
            return False
        self._scheduler.reload_keywords(fresh.keywords)
        return True


class TelegramCommands:
    """텔레그램에서 명령을 받아 코어에 넘기고 답장을 큐에 넣는다.

    받아오는 방법만 텔레그램의 것이고, 무엇을 하는지는 전부 `CommandCore` 에 있다.
    """

    def __init__(self, cfg: Config, core: CommandCore, queue: SendQueue) -> None:
        self._cfg = cfg
        self._core = core
        self._queue = queue
        self._offset = 0
        # 알림 전송과 별개의 연결을 쓴다. 롱 폴링이 전송을 붙잡고 있으면 안 된다.
        self._client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 15)

    async def run(self) -> None:
        log.info("텔레그램 명령어 수신 대기 시작. /help 로 사용법을 볼 수 있습니다")
        await self._skip_backlog()
        while True:
            try:
                updates = await self._fetch()
            except Exception as e:
                # 같은 토큰으로 두 곳에서 동시에 받으면 텔레그램이 한쪽을 끊는다.
                # 흔한 실수라 원문을 그대로 남겨야 원인을 알 수 있다.
                hint = ""
                if "conflict" in str(e).lower():
                    hint = " — 같은 봇을 두 곳에서 돌리고 있지 않은지 확인하세요"
                log.warning("명령어 수신 실패: %s%s. 10초 뒤 재시도", e, hint)
                await asyncio.sleep(10)
                continue
            for update in updates:
                self._on_update(update)

    async def _skip_backlog(self) -> None:
        """켜져 있지 않던 동안 쌓인 메시지는 버린다. 옛 명령이 뒤늦게 실행되면 곤란하다."""
        try:
            updates = await self._fetch(timeout=0)
            if updates:
                log.info("대기 중이던 메시지 %d건을 건너뜁니다", len(updates))
        except Exception:
            pass

    async def _fetch(self, timeout: int = POLL_TIMEOUT) -> list[dict]:
        response = await self._client.get(
            API.format(token=self._cfg.telegram_token, method="getUpdates"),
            params={"offset": self._offset, "timeout": timeout},
        )
        body = response.json()
        if not body.get("ok"):
            raise RuntimeError(body.get("description", "알 수 없는 오류"))
        updates = body.get("result", [])
        if updates:
            self._offset = updates[-1]["update_id"] + 1
        return updates

    def _on_update(self, update: dict) -> None:
        message = update.get("message") or update.get("edited_message")
        if not message:
            return
        text = (message.get("text") or "").strip()
        if not text.startswith("/"):
            return

        sender = str(message.get("chat", {}).get("id", ""))
        if sender != str(self._cfg.telegram_chat_id):
            log.warning("허용되지 않은 chat_id(%s)의 명령을 무시했습니다: %s", sender, text[:40])
            return

        log.info("텔레그램 명령 수신: %s", text[:60])
        # 명령 응답은 물어본 곳에만 간다. 디스코드에 텔레그램 명령 결과가 뜨면
        # 왜 뜨는지 알 수 없다.
        self._queue.put(raw(self._core.dispatch(text), only="telegram"))

    async def close(self) -> None:
        await self._client.aclose()
