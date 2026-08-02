"""텔레그램 봇 명령어.

화면 없는 서버에서는 트레이 아이콘을 쓸 수 없어, 설정을 바꾸려면 SSH 로 들어가
config.yaml 을 고치고 재시작해야 한다. 그 대신 알림을 받는 그 자리에서 키워드를
넣고 빼고 잠시 멈출 수 있게 한다.

받은 메시지는 설정된 chat_id 에서 온 것만 처리한다. 봇 이름은 검색으로 노출되므로
누구나 말을 걸 수 있고, 그대로 두면 남이 내 감시 설정을 바꿀 수 있다.
"""

import asyncio
import logging
import re
from html import escape

import httpx

from .config import Config, ConfigError
from .config_store import KeywordStore, KeywordStoreError
from .control import Controls
from .notifiers.queue import SendQueue
from .notifiers.telegram import API, Message
from .scheduler import Scheduler

log = logging.getLogger(__name__)

POLL_TIMEOUT = 30
DURATION = re.compile(r"^(\d+)\s*([smh]?)$", re.IGNORECASE)

HELP = """<b>MerAlarm 명령어</b>

/status — 상태 보기
/list — 감시 중인 키워드
/add <i>키워드</i> — 키워드 추가
/del <i>번호</i> — 키워드 삭제
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


class CommandListener:
    def __init__(
        self,
        cfg: Config,
        controls: Controls,
        scheduler: Scheduler,
        queue: SendQueue,
    ) -> None:
        self._cfg = cfg
        self._controls = controls
        self._scheduler = scheduler
        self._queue = queue
        self._store = KeywordStore(cfg.config_path)
        self._offset = 0
        # 알림 전송과 별개의 연결을 쓴다. 롱 폴링이 전송을 붙잡고 있으면 안 된다.
        self._client = httpx.AsyncClient(timeout=POLL_TIMEOUT + 15)

    # ---- 롱 폴링 ----

    async def run(self) -> None:
        log.info("명령어 수신 대기 시작. /help 로 사용법을 볼 수 있습니다")
        await self._skip_backlog()
        while True:
            try:
                updates = await self._fetch()
            except Exception as e:
                log.warning("명령어 수신 실패(%s). 10초 뒤 재시도", type(e).__name__)
                await asyncio.sleep(10)
                continue
            for update in updates:
                await self._on_update(update)

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

    async def _on_update(self, update: dict) -> None:
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

        log.info("명령 수신: %s", text[:60])
        try:
            reply = self._dispatch(text)
        except Exception:
            log.exception("명령 처리 중 오류")
            reply = "⚠️ 처리 중 오류가 났습니다. 로그를 확인하세요."
        self._queue.put(Message(text=reply))

    # ---- 명령 처리 ----

    def _dispatch(self, text: str) -> str:
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
        return (
            f"<b>{state}</b>\n\n"
            f"가동 {hours:.1f}시간\n"
            f"키워드 {len(self._scheduler.keyword_names)}개 · 기본 주기 "
            f"{self._cfg.poll.default_interval_sec}초\n"
            f"요청 {stats.requests:,}회 (실패 {stats.failures})\n"
            f"신규 알림 {stats.new_items}건 · 가격 인하 {stats.price_drops}건"
        )

    def _list(self, _: str) -> str:
        try:
            entries = self._store.entries()
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"

        lines = []
        for i, (name, excludes) in enumerate(entries, 1):
            lines.append(f"{i}. <b>{escape(name)}</b>")
            if excludes:
                lines.append("    제외: " + ", ".join(f"<code>{escape(w)}</code>" for w in excludes))
        return "<b>감시 중인 키워드</b>\n\n" + "\n".join(lines) + "\n\n/del 번호 로 지울 수 있습니다."

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
            added = self._store.add(query, excludes)
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
            removed = self._store.remove(int(argument))
        except KeywordStoreError as e:
            return f"⚠️ {escape(str(e))}"
        self._reload()
        return f"🗑 <b>{escape(removed)}</b> 감시를 중단했습니다."

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

    async def close(self) -> None:
        await self._client.aclose()
