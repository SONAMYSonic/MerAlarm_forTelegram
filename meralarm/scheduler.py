"""폴링 엔진.

키워드마다 다음 실행 시각을 따로 들고 있다가 만기된 것만 돌린다. 하나의 주기에
모든 키워드를 몰아서 요청하면 트래픽이 톱니처럼 튀므로 시작 시점을 주기 안에
고르게 흩어두고, 매 회차 지터를 섞어 고정 패턴이 남지 않게 한다.
"""

import asyncio
import logging
import random
import time
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

from . import alerts, filters, fx
from .config import Config, KeywordConfig
from .control import Controls
from .models import Item
from .notifiers.queue import SendQueue
from .ratelimit import RateLimiter
from .sources.base import ItemSource
from .store import SeenStore

log = logging.getLogger(__name__)


def _is_blocked(exc: BaseException) -> bool:
    """요청이 막힌 것으로 볼 수 있는 응답인가."""
    return (
        isinstance(exc, httpx.HTTPStatusError)
        and exc.response.status_code in (403, 429)
    )


@dataclass
class KeywordState:
    cfg: KeywordConfig
    next_run: float = 0.0
    # 차단이 감지되면 늘어나는 배수. 1.0 이면 설정한 주기 그대로.
    penalty: float = 1.0
    fails: int = 0
    successes: int = 0
    alerted: bool = False


@dataclass
class Stats:
    started: datetime = field(default_factory=datetime.now)
    new_items: int = 0
    price_drops: int = 0
    requests: int = 0
    failures: int = 0

    def uptime_seconds(self) -> float:
        return (datetime.now() - self.started).total_seconds()


class Scheduler:
    def __init__(
        self,
        cfg: Config,
        source: ItemSource,
        store: SeenStore,
        queue: SendQueue,
        controls: Controls | None = None,
    ) -> None:
        self._cfg = cfg
        self._source = source
        self._store = store
        self._queue = queue
        self._controls = controls or Controls()
        self._limiter = RateLimiter(cfg.poll.min_request_gap_sec)
        self._states = [KeywordState(cfg=k) for k in cfg.keywords]
        self._krw_rate: float | None = None
        self._rate_date: date | None = None
        self._heartbeat_date: date | None = None
        self._purge_date: date | None = None
        self.stats = Stats()

    # ---- 바깥에서 조작하는 부분 ----

    @property
    def keyword_names(self) -> list[str]:
        return [state.cfg.name for state in self._states]

    def reload_keywords(self, keywords) -> None:
        """설정이 바뀐 뒤 재시작 없이 반영한다.

        이미 돌고 있던 키워드는 실행 시각과 백오프 상태를 그대로 물려받는다.
        그러지 않으면 키워드 하나 추가할 때마다 전체 감시 리듬이 초기화된다.
        """
        existing = {state.cfg.name: state for state in self._states}
        gap = self._cfg.poll.min_request_gap_sec
        now = time.monotonic()

        states = []
        added = []
        for kw in keywords:
            state = existing.get(kw.name)
            if state is not None:
                state.cfg = kw
            else:
                # 새 키워드는 곧 한 번 돌려 목록을 담는다. 서로 겹치지 않게 띄운다.
                state = KeywordState(cfg=kw, next_run=now + gap * (len(added) + 1))
                added.append(kw.name)
            states.append(state)

        removed = [name for name in existing if name not in {k.name for k in keywords}]
        self._states = states
        log.info(
            "키워드 갱신: 총 %d개%s%s",
            len(states),
            f" · 추가 {added}" if added else "",
            f" · 제거 {removed}" if removed else "",
        )

    # ---- 주기 계산 ----

    def _base_interval(self, state: KeywordState) -> float:
        night = self._cfg.poll.night
        if night.applies(datetime.now().time()):
            # 심야에는 늦추기만 한다. 키워드가 더 느리게 설정돼 있으면 그쪽을 존중한다.
            return max(night.interval_sec, state.cfg.interval_sec)
        return state.cfg.interval_sec

    def _effective_interval(self, state: KeywordState) -> float:
        raw = self._base_interval(state) * state.penalty
        return min(raw, self._cfg.backoff.max_interval_sec)

    def _stagger(self) -> None:
        now = time.monotonic()
        count = len(self._states)
        if not count:
            return
        for index, state in enumerate(self._states):
            state.next_run = now + self._effective_interval(state) * index / count

    def _schedule_next(self, state: KeywordState) -> None:
        ratio = self._cfg.poll.jitter_ratio
        jitter = 1 + random.uniform(-ratio, ratio)
        state.next_run = time.monotonic() + self._effective_interval(state) * jitter

    # ---- 신규·인하 판정 ----

    def _diff(
        self, kw: KeywordConfig, items: list[Item]
    ) -> tuple[list[Item], list[tuple[Item, int]]]:
        known = self._store.known_prices(kw.name, [i.id for i in items])
        new = [i for i in items if i.id not in known]
        drops = [
            (i, known[i.id]) for i in items if i.id in known and i.price < known[i.id]
        ]
        return new, drops

    def _on_notified(self, keyword: str, items: list[Item]) -> None:
        """전송에 성공한 뒤에만 불린다."""
        self._store.record(keyword, items)
        for item in items:
            self._store.mark_notified(keyword, item)

    def _drop_duplicates(
        self,
        kw: KeywordConfig,
        ledger: dict[str, int],
        new: list[Item],
        drops: list[tuple[Item, int]],
    ) -> tuple[list[Item], list[tuple[Item, int]]]:
        """다른 키워드로 이미 알린 상품을 걸러낸다.

        키워드 세 개가 같은 작품의 캐릭터라면 한 상품에 둘 이상이 함께 나오는 일이
        흔하다. items 표는 키워드별로 나뉘어 있어 그대로 두면 같은 물건으로 알림이
        두세 번 간다.
        """
        if not ledger:
            return new, drops

        suppressed = 0
        kept_new = []
        for item in new:
            if item.id in ledger:
                suppressed += 1
            else:
                kept_new.append(item)

        kept_drops = []
        for item, old_price in drops:
            announced = ledger.get(item.id)
            if announced is None:
                kept_drops.append((item, old_price))
            elif item.price < announced:
                # 사용자가 마지막으로 본 가격을 기준으로 보여줘야 하락폭이 맞는다.
                kept_drops.append((item, announced))
            else:
                suppressed += 1

        if suppressed:
            log.info("[%s] 다른 키워드에서 이미 알린 %d건 건너뜀", kw.name, suppressed)
        return kept_new, kept_drops

    def _enqueue_new(self, kw: KeywordConfig, new: list[Item]) -> None:
        # 오래된 것부터 보내야 알림 순서가 시간 흐름과 맞는다.
        new = sorted(new, key=lambda i: i.created)

        if len(new) >= self._cfg.notify.batch_threshold:
            self._queue.put(
                alerts.batch(
                    new,
                    kw.name,
                    self._krw_rate,
                    on_sent=lambda items=tuple(new): self._on_notified(kw.name, list(items)),
                )
            )
            log.info("[%s] 신규 %d건 → 묶음 요약 1건", kw.name, len(new))
            return

        for item in new:
            self._queue.put(
                alerts.new_item(
                    item,
                    kw.name,
                    self._krw_rate,
                    # 전송에 성공한 뒤에 기록한다. 먼저 기록하면 전송 전에 죽었을 때
                    # 본 것으로 남아 그 상품은 영영 알림이 오지 않는다.
                    on_sent=lambda i=item: self._on_notified(kw.name, [i]),
                )
            )

    def _enqueue_drops(self, kw: KeywordConfig, drops: list[tuple[Item, int]]) -> None:
        for item, old_price in drops:
            self._queue.put(
                alerts.price_drop(
                    item,
                    kw.name,
                    old_price,
                    self._krw_rate,
                    on_sent=lambda i=item: self._on_notified(kw.name, [i]),
                )
            )
            log.info(
                "[%s] 가격 인하: ¥%s → ¥%s %s",
                kw.name,
                f"{old_price:,}",
                f"{item.price:,}",
                item.name[:36],
            )

    # ---- 한 키워드 처리 ----

    async def _sold_count(self, kw: KeywordConfig) -> int | None:
        """판매완료 건수. 수집기가 못 세면 None.

        `ItemSource` 규약에는 넣지 않았다. 진단에만 쓰는 것이라 폴백 수집기가
        이것 하나 때문에 못 붙으면 손해다. 있으면 쓰고 없으면 건너뛴다.
        """
        lookup = getattr(self._source, "search_sold", None)
        if lookup is None:
            return None
        try:
            await self._limiter.acquire()
            return len(await lookup(kw.query))
        except Exception:
            log.debug("[%s] 판매완료 조회 실패", kw.name, exc_info=True)
            return None

    async def _seed_empty(self, kw: KeywordConfig, found: int) -> None:
        """담을 것이 없어도 최초 적재를 마친 것으로 표시하고, **한 번만** 알린다.

        표시해 두지 않으면 나중에 처음으로 잡힌 상품이 "최초 적재" 대상이 되어
        기록만 되고 알림이 나가지 않는다. 즉 **0건으로 시작한 키워드는 첫 매물을
        반드시 놓친다.** 아직 안 나온 물건을 미리 걸어두는 쓰임에서 가장 치명적인데,
        그 쓰임이야말로 이 프로그램을 쓰는 이유다.

        알리는 이유는 따로 있다. 겉으로는 "조용하다"가 세 가지를 다 뜻한다 —
        검색어가 틀렸거나, 지금 다 팔렸거나, 조건이 너무 빡빡하거나. 손볼 곳이
        저마다 다른데 사용자에게는 똑같이 보인다. 그래서 어느 쪽인지 말해준다.

        `found` 는 검색에 걸린 건수다. 0 이면 검색 자체가 비었고, 0 보다 크면
        걸리긴 했는데 조건이 전부 걸러냈다는 뜻이다.
        """
        if self._store.is_seeded(kw.name):
            return
        self._store.mark_seeded(kw.name)
        name = kw.name

        if found:
            log.info("[%s] %d건이 잡혔지만 조건에 맞는 것이 없습니다", name, found)
            self._queue.put(
                alerts.notice(
                    "🔎 조건에 맞는 상품이 없습니다",
                    f"'{name}' 로 {found}건이 잡혔지만 조건에 맞는 것이 없습니다.\n"
                    f"/exclude 로 제외어와 필수 포함어를, /config 로 가격·상태 조건을"
                    f" 확인해 보세요.\n"
                    f"조건에 맞는 상품이 올라오면 알려드리겠습니다.",
                )
            )
            return

        sold = await self._sold_count(kw)

        if sold:
            # 검색어는 맞다. 지금 다 팔렸을 뿐이니 기다리면 된다.
            log.info("[%s] 판매중 0건 · 판매완료 %d건. 재출품을 기다립니다", name, sold)
            self._queue.put(
                alerts.notice(
                    "🔎 지금은 살 수 있는 것이 없습니다",
                    f"'{name}' 로 판매중인 상품이 없습니다.\n"
                    f"다만 판매완료가 {sold}건 있으니 검색어는 맞습니다.\n"
                    f"다시 올라오면 알려드리겠습니다.",
                )
            )
            return

        if sold is None:
            log.warning("[%s] 지금 잡히는 상품이 없습니다", name)
            self._queue.put(
                alerts.notice(
                    "🔎 지금 잡히는 상품이 없습니다",
                    f"'{name}' 로 판매중인 상품이 없습니다.\n"
                    f"새로 올라오면 알려드리겠습니다.",
                )
            )
            return

        # 판매중도 판매완료도 0. 그 말로 팔린 물건이 아예 없다는 뜻이라
        # 검색어 쪽을 의심하는 편이 맞다.
        log.warning("[%s] 판매중도 판매완료도 0건. 검색어를 확인하세요", name)
        self._queue.put(
            alerts.notice(
                "⚠️ 검색어를 확인해 주세요",
                f"'{name}' 은 판매중도 판매완료도 0건입니다.\n"
                f"메루카리에서 그대로 쳐서 나오는지 확인해 보세요.\n"
                f"낱말을 띄어 쓰면 <b>둘 다 든</b> 상품만 찾습니다 — 각각은 흔해도"
                f" 함께 든 물건이 없으면 0건이 됩니다.",
            )
        )

    async def _poll(self, state: KeywordState) -> None:
        kw = state.cfg
        await self._limiter.acquire()
        items = await self._source.search(kw.query)
        self.stats.requests += 1

        if not items:
            await self._seed_empty(kw, 0)
            return

        total = len(items)

        # 출품 경과일·끌어올림은 "신규라고 알릴지"를 정하는 조건이지 "쳐다볼지"가
        # 아니다. 걸러질 상품도 가격을 기록해 두어야 값을 내렸을 때 알아챈다.
        items = filters.apply(items, kw, ignore_freshness=True)
        if not items:
            await self._seed_empty(kw, total)
            return

        if not self._store.is_seeded(kw.name):
            self._store.record(kw.name, items)
            self._store.mark_seeded(kw.name)
            log.info(
                "[%s] 최초 적재 %d건 완료 (전체 %d건). 다음 회차부터 알립니다",
                kw.name,
                len(items),
                total,
            )
            return

        new, drops = self._diff(kw, items)
        if not self._cfg.notify.price_drop:
            drops = []
        ledger = self._store.notified_prices([i.id for i in items])
        new, drops = self._drop_duplicates(kw, ledger, new, drops)

        # 처음 본 상품 중 오래됐거나 끌어올린 것은 신규로 알리지 않는다. 갱신만으로
        # 상위에 떠오른 것을 새 물건이라고 알리면 거짓말이 되기 때문이다. 대신
        # 기록은 남겨서, 나중에 값을 내리면 그때 알린다.
        alertable = [i for i in new if filters.matches(i, kw)]
        quiet = len(new) - len(alertable)
        new = alertable

        # 알림 대상이 아닌 상품은 곧바로 기록을 갱신한다. 가격이 오른 경우도 여기
        # 포함되며, 그래야 다음 인하를 오른 가격 기준으로 계산한다.
        pending = {i.id for i in new} | {i.id for i, _ in drops}
        self._store.record(kw.name, [i for i in items if i.id not in pending])

        if not new and not drops:
            log.info(
                "[%s] 변화 없음 (%d/%d건 확인%s)",
                kw.name,
                len(items),
                total,
                f" · 오래된 신규 {quiet}건 조용히 기록" if quiet else "",
            )
            return

        if new:
            self.stats.new_items += len(new)
            log.info("[%s] 신규 %d건 발견", kw.name, len(new))
            self._enqueue_new(kw, new)
        if drops:
            self.stats.price_drops += len(drops)
            self._enqueue_drops(kw, drops)

    async def _run_keyword(self, state: KeywordState) -> None:
        try:
            await self._poll(state)
        except Exception as e:
            state.fails += 1
            state.successes = 0
            self.stats.failures += 1

            if _is_blocked(e):
                state.penalty = min(
                    state.penalty * 2,
                    self._cfg.backoff.max_interval_sec / max(state.cfg.interval_sec, 1),
                )
                log.warning(
                    "[%s] 차단으로 보이는 응답. 주기를 %.0f초로 늘립니다",
                    state.cfg.name,
                    self._effective_interval(state),
                )
            else:
                log.exception("[%s] 폴링 실패 (연속 %d회)", state.cfg.name, state.fails)

            if (
                self._cfg.notify.error_alert
                and not state.alerted
                and state.fails >= self._cfg.backoff.fail_threshold
            ):
                state.alerted = True
                self._queue.put(
                    alerts.notice(
                        "⚠️ 감시 오류",
                        f"'{state.cfg.name}' 수집이 {state.fails}회 연속 실패했습니다.\n"
                        f"원인: {type(e).__name__}: {e}\n"
                        f"현재 주기: {self._effective_interval(state):.0f}초\n"
                        f"계속 재시도합니다.",
                    )
                )
        else:
            state.fails = 0
            state.successes += 1
            if state.alerted:
                state.alerted = False
                self._queue.put(
                    alerts.notice(
                        "✅ 감시 복구", f"'{state.cfg.name}' 수집이 정상으로 돌아왔습니다."
                    )
                )
            if state.penalty > 1 and state.successes >= self._cfg.backoff.recover_after:
                state.penalty = max(1.0, state.penalty / 2)
                state.successes = 0
                log.info(
                    "[%s] 주기를 %.0f초로 되돌립니다",
                    state.cfg.name,
                    self._effective_interval(state),
                )
        finally:
            self._schedule_next(state)

    # ---- 주변 작업 ----

    async def _refresh_rate(self) -> None:
        if not self._cfg.notify.show_krw:
            return
        today = date.today()
        if self._rate_date == today:
            return
        rate = await fx.jpy_to_krw(self._cfg.fx_cache_path)
        if rate is None:
            # 오늘 것으로 표시해 두면 환율 서버가 잠깐 죽었을 때 하루 종일
            # 원화 표시가 사라진다. 성공했을 때만 오늘 몫을 마쳤다고 본다.
            return
        self._krw_rate = rate
        self._rate_date = today

    def _maybe_purge(self) -> None:
        """하루 한 번 오래된 기록을 정리한다. 안 하면 DB가 계속 자란다."""
        today = date.today()
        if self._purge_date == today:
            return
        self._purge_date = today
        try:
            items, notified = self._store.purge(self._cfg.store.keep_days)
        except Exception:
            log.exception("기록 정리 실패. 감시는 계속합니다")
            return
        if items or notified:
            log.info(
                "%d일 넘게 보이지 않은 기록 정리: 상품 %d건 · 알림 원장 %d건",
                self._cfg.store.keep_days,
                items,
                notified,
            )

    def _maybe_heartbeat(self) -> None:
        hour = self._cfg.notify.heartbeat_hour
        if hour is None:
            return
        now = datetime.now()
        if now.hour != hour or self._heartbeat_date == now.date():
            return
        self._heartbeat_date = now.date()

        uptime = now - self.stats.started
        hours = uptime.total_seconds() / 3600
        self._queue.put(
            alerts.notice(
                "💓 MerAlarm 정상 동작",
                f"가동 {hours:.0f}시간\n"
                f"감시 키워드 {len(self._states)}개\n"
                f"요청 {self.stats.requests:,}회 (실패 {self.stats.failures}회)\n"
                f"신규 알림 {self.stats.new_items}건 · 가격 인하 {self.stats.price_drops}건",
            )
        )

    # ---- 메인 루프 ----

    async def run(self) -> None:
        self._stagger()
        log.info(
            "감시 시작 · 키워드 %s · 기본 주기 %d초",
            [k.name for k in self._cfg.keywords],
            self._cfg.poll.default_interval_sec,
        )
        while True:
            if self._controls.check_auto_resume():
                log.info("일시정지 시한이 끝나 감시를 재개합니다")
            if self._controls.paused:
                # 멈춰 있는 동안은 요청을 보내지 않는다. 만기된 회차를 그때그때
                # 뒤로 밀어야 재개 순간에 밀린 요청이 한꺼번에 몰리지 않는다.
                now = time.monotonic()
                for state in self._states:
                    if state.next_run <= now:
                        self._schedule_next(state)
                await asyncio.sleep(1)
                continue

            await self._refresh_rate()
            self._maybe_purge()
            self._maybe_heartbeat()

            now = time.monotonic()
            due = [s for s in self._states if s.next_run <= now]
            if not due:
                sleep_for = min(s.next_run for s in self._states) - now
                # 상한을 두어 심야 전환과 heartbeat 판정이 제때 돌아가게 한다.
                await asyncio.sleep(min(max(sleep_for, 0.1), 5))
                continue

            for state in sorted(due, key=lambda s: s.next_run):
                await self._run_keyword(state)
