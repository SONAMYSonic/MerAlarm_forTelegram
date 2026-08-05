"""config.yaml 과 .env 를 읽어 검증된 설정 객체로 만든다.

설정 오류는 실행 직후에 사람이 읽을 수 있는 메시지로 끝내야 한다. 무인 운영이라
잘못된 값으로 몇 시간 돌다가 조용히 아무 알림도 안 오는 상황이 최악이다.
"""

import shutil
import sys
from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml


def _root() -> Path:
    """설정과 데이터를 둘 기준 폴더.

    exe 로 묶으면 `__file__` 은 실행할 때마다 새로 풀리는 임시 폴더를 가리킨다.
    그것을 기준으로 삼으면 설정 파일을 못 찾고, 찾더라도 종료 시 사라진다.
    묶인 상태에서는 실행 파일이 놓인 폴더를 쓴다.
    """
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent.parent


ROOT = _root()


class ConfigError(RuntimeError):
    """설정이 없거나 값이 잘못됐을 때. 메시지를 그대로 사용자에게 보여준다."""


@dataclass(frozen=True, slots=True)
class NightMode:
    enabled: bool
    start: time
    end: time
    interval_sec: int

    def applies(self, now: time) -> bool:
        if not self.enabled:
            return False
        if self.start <= self.end:
            return self.start <= now < self.end
        return now >= self.start or now < self.end  # 자정을 넘는 구간


@dataclass(frozen=True, slots=True)
class PollConfig:
    default_interval_sec: int
    jitter_ratio: float
    min_request_gap_sec: float
    night: NightMode


@dataclass(frozen=True, slots=True)
class BackoffConfig:
    fail_threshold: int
    max_interval_sec: int
    recover_after: int


@dataclass(frozen=True, slots=True)
class StoreConfig:
    keep_days: int


@dataclass(frozen=True, slots=True)
class NotifyConfig:
    show_krw: bool
    price_drop: bool
    batch_threshold: int
    heartbeat_hour: int | None
    error_alert: bool
    # 키워드가 따로 지정하지 않았을 때 쓰이는 기본값. 표시용으로도 쓴다.
    max_age_days: int | None
    max_bump_days: int | None


@dataclass(frozen=True, slots=True)
class KeywordConfig:
    name: str
    query: str
    interval_sec: int
    price_min: int | None
    price_max: int | None
    exclude: tuple[str, ...]
    conditions: tuple[int, ...]
    shipping_payers: tuple[int, ...]
    # 출품한 지 이만큼 지난 상품은 알리지 않는다. None 이면 제한 없음.
    max_age_days: int | None
    # 출품 후 이만큼 지나서 갱신된 상품(끌어올린 것)은 신규로 알리지 않는다.
    max_bump_days: int | None


@dataclass(frozen=True, slots=True)
class Config:
    poll: PollConfig
    backoff: BackoffConfig
    notify: NotifyConfig
    store: StoreConfig
    keywords: tuple[KeywordConfig, ...]
    # 채널은 하나만 있어도 된다. 비어 있는 쪽은 그냥 쓰지 않는다.
    telegram_token: str
    telegram_chat_id: str
    discord_webhook: str
    # 봇은 알림과 명령을 모두 한다. 토큰이 있으면 웹훅 대신 이쪽을 쓴다.
    discord_bot_token: str
    discord_channel_id: int
    discord_owner_id: int
    config_path: Path
    db_path: Path
    log_path: Path
    fx_cache_path: Path

    @property
    def has_telegram(self) -> bool:
        return bool(self.telegram_token and self.telegram_chat_id)

    @property
    def has_discord_bot(self) -> bool:
        return bool(self.discord_bot_token and self.discord_channel_id)


SETUP_HINT = (
    "        콘솔 창에서 아래를 실행하면 차례로 안내해 드립니다.\n"
    "            python -m meralarm --setup"
)


def ensure_writable(root: Path = ROOT) -> None:
    """설정과 기록을 남길 수 있는 자리인지 먼저 확인한다.

    압축을 풀지 않고 zip 안에서 바로 실행하거나 Program Files 같은 곳에 두면
    파일을 못 만든다. 그대로 두면 PermissionError 가 그대로 튀어나와 사용자는
    무엇이 잘못됐는지 알 수 없다.
    """
    probe = root / ".write_test"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
    except OSError:
        raise ConfigError(
            f"이 폴더에 파일을 만들 수 없습니다.\n"
            f"        {root}\n\n"
            "        압축을 풀지 않고 zip 안에서 바로 실행하지 않았는지,\n"
            "        Program Files 처럼 권한이 필요한 곳에 두지 않았는지 확인하세요.\n"
            "        바탕화면이나 문서 폴더로 옮기면 해결됩니다."
        ) from None


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError("알림을 받을 곳이 설정되지 않았습니다.\n" + SETUP_HINT)
    env: dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, value = line.split("=", 1)
            env[key.strip()] = value.strip()
    return env


def _parse_time(value: Any, where: str) -> time:
    try:
        hour, minute = str(value).split(":")
        return time(int(hour), int(minute))
    except (ValueError, TypeError):
        raise ConfigError(f'{where} 는 "HH:MM" 형식이어야 합니다. 받은 값: {value!r}') from None


def _int_list(value: Any, where: str, allowed: range) -> tuple[int, ...]:
    if value is None:
        return ()
    if not isinstance(value, list):
        raise ConfigError(f"{where} 는 목록이어야 합니다. 받은 값: {value!r}")
    result = []
    for entry in value:
        if not isinstance(entry, int) or entry not in allowed:
            raise ConfigError(
                f"{where} 에는 {allowed.start}~{allowed.stop - 1} 사이 숫자만 올 수 있습니다. "
                f"받은 값: {entry!r}"
            )
        result.append(entry)
    return tuple(result)


def _positive_int(value: Any, where: str, minimum: int) -> int:
    if not isinstance(value, int) or value < minimum:
        raise ConfigError(f"{where} 는 {minimum} 이상의 정수여야 합니다. 받은 값: {value!r}")
    return value


def _optional_age(value: Any, where: str, minimum: int = 1) -> int | None:
    """일수 설정. `minimum=0` 은 끌어올림처럼 0이 뜻을 갖는 항목에 쓴다."""
    if value is None:
        return None
    if not isinstance(value, int) or value < minimum:
        raise ConfigError(
            f"{where} 는 {minimum} 이상의 정수 또는 null 이어야 합니다. 받은 값: {value!r}"
        )
    return value


def _parse_keyword(
    raw: Any,
    index: int,
    default_interval: int,
    default_max_age: int | None,
    default_max_bump: int | None,
) -> KeywordConfig:
    where = f"keywords[{index}]"

    if isinstance(raw, str):  # 간단형: 키워드 문자열 한 줄
        query = raw.strip()
        if not query:
            raise ConfigError(f"{where} 가 비어 있습니다.")
        return KeywordConfig(
            name=query,
            query=query,
            interval_sec=default_interval,
            price_min=None,
            price_max=None,
            exclude=(),
            conditions=(),
            shipping_payers=(),
            max_age_days=default_max_age,
            max_bump_days=default_max_bump,
        )

    if not isinstance(raw, dict):
        raise ConfigError(f"{where} 는 문자열이거나 항목 묶음이어야 합니다. 받은 값: {raw!r}")

    query = str(raw.get("query") or raw.get("name") or "").strip()
    if not query:
        raise ConfigError(f"{where} 에 query 가 없습니다.")

    price_min = raw.get("price_min")
    price_max = raw.get("price_max")
    for label, value in (("price_min", price_min), ("price_max", price_max)):
        if value is not None and (not isinstance(value, int) or value < 0):
            raise ConfigError(f"{where}.{label} 은 0 이상의 정수여야 합니다. 받은 값: {value!r}")
    if price_min is not None and price_max is not None and price_min > price_max:
        raise ConfigError(f"{where} 의 price_min 이 price_max 보다 큽니다.")

    exclude = raw.get("exclude") or []
    if not isinstance(exclude, list):
        raise ConfigError(f"{where}.exclude 는 목록이어야 합니다.")

    return KeywordConfig(
        name=str(raw.get("name") or query).strip(),
        query=query,
        interval_sec=_positive_int(
            raw.get("interval_sec", default_interval), f"{where}.interval_sec", 10
        ),
        price_min=price_min,
        price_max=price_max,
        # 빈 목록 항목("- " 만 쓴 줄)은 None 으로 읽힌다. 걸러내지 않으면 문자열
        # "None" 이 제외어로 들어가 제목에 none 이 든 상품이 조용히 사라진다.
        exclude=tuple(
            str(w).strip() for w in exclude if w is not None and str(w).strip()
        ),
        conditions=_int_list(raw.get("conditions"), f"{where}.conditions", range(1, 7)),
        shipping_payers=_int_list(
            raw.get("shipping_payers"), f"{where}.shipping_payers", range(1, 4)
        ),
        max_age_days=_optional_age(
            raw.get("max_age_days", default_max_age), f"{where}.max_age_days"
        ),
        max_bump_days=_optional_age(
            raw.get("max_bump_days", default_max_bump), f"{where}.max_bump_days", minimum=0
        ),
    )


def _snowflake(value: str, where: str) -> int:
    """디스코드의 채널·사용자 번호. 숫자만 들어 있다."""
    if not value.isdigit():
        raise ConfigError(f"{where} 는 숫자여야 합니다. 받은 값: {value!r}\n" + SETUP_HINT)
    return int(value)


def _channels(env: dict[str, str]) -> tuple[str, str, str, str, int, int]:
    """알림 채널 설정을 읽고 검증한다.

    예전에는 텔레그램이 없으면 무조건 거절했다. 디스코드 봇 하나로도 알림과 명령이
    모두 되므로 지금은 **하나도 없을 때만** 거절한다.
    """
    telegram_token = env.get("TELEGRAM_BOT_TOKEN", "").strip()
    telegram_chat_id = env.get("TELEGRAM_CHAT_ID", "").strip()
    # 반쪽만 있으면 조용히 텔레그램을 빼먹지 말고 무엇이 빠졌는지 알려준다.
    if bool(telegram_token) != bool(telegram_chat_id):
        missing = (
            "대화 상대(TELEGRAM_CHAT_ID)" if telegram_token else "봇 토큰(TELEGRAM_BOT_TOKEN)"
        )
        raise ConfigError(f"텔레그램 {missing} 이(가) 비어 있습니다.\n" + SETUP_HINT)

    webhook = env.get("DISCORD_WEBHOOK_URL", "").strip()
    bot_token = env.get("DISCORD_BOT_TOKEN", "").strip()
    channel_id = owner_id = 0
    if bot_token:
        raw_channel = env.get("DISCORD_CHANNEL_ID", "").strip()
        raw_owner = env.get("DISCORD_OWNER_ID", "").strip()
        if not raw_channel:
            raise ConfigError(
                "디스코드 봇 토큰은 있는데 보낼 채널(DISCORD_CHANNEL_ID)이 없습니다.\n"
                + SETUP_HINT
            )
        if not raw_owner:
            raise ConfigError(
                "디스코드 봇을 조작할 사람(DISCORD_OWNER_ID)이 없습니다.\n"
                "        이 값이 없으면 서버에 있는 누구나 감시 설정을 바꿀 수 있습니다.\n"
                + SETUP_HINT
            )
        channel_id = _snowflake(raw_channel, "DISCORD_CHANNEL_ID")
        owner_id = _snowflake(raw_owner, "DISCORD_OWNER_ID")

    if not telegram_token and not bot_token and not webhook:
        raise ConfigError("알림을 받을 곳이 하나도 없습니다.\n" + SETUP_HINT)

    return telegram_token, telegram_chat_id, webhook, bot_token, channel_id, owner_id


def load() -> Config:
    ensure_writable()
    env = _read_env(ROOT / ".env")
    token, chat_id, webhook, bot_token, channel_id, owner_id = _channels(env)

    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        # 배포판에서 실수로 지웠거나 처음 받은 상태일 수 있다. 예시본이 옆에 있으면
        # 그것으로 시작한다. 없으면 무엇이 없는지 분명히 알려준다.
        example = ROOT / "config.example.yaml"
        if example.exists():
            shutil.copy(example, config_path)
        else:
            raise ConfigError(
                f"설정 파일이 없습니다: {config_path}\n"
                "        내려받은 폴더에서 config.yaml 을 지우지 않았는지 확인하세요."
            )
    try:
        raw = yaml.safe_load(config_path.read_text(encoding="utf-8")) or {}
    except yaml.YAMLError as e:
        raise ConfigError(f"config.yaml 을 읽을 수 없습니다: {e}") from None

    poll_raw = raw.get("poll") or {}
    default_interval = _positive_int(
        poll_raw.get("default_interval_sec", 60), "poll.default_interval_sec", 10
    )
    jitter = float(poll_raw.get("jitter_ratio", 0.2))
    if not 0 <= jitter < 1:
        raise ConfigError("poll.jitter_ratio 는 0 이상 1 미만이어야 합니다.")

    night_raw = poll_raw.get("night_mode") or {}
    night = NightMode(
        enabled=bool(night_raw.get("enabled", False)),
        start=_parse_time(night_raw.get("start", "02:00"), "poll.night_mode.start"),
        end=_parse_time(night_raw.get("end", "07:00"), "poll.night_mode.end"),
        interval_sec=_positive_int(
            night_raw.get("interval_sec", 180), "poll.night_mode.interval_sec", 10
        ),
    )

    notify_raw = raw.get("notify") or {}
    default_max_age = _optional_age(
        notify_raw.get("max_age_days"), "notify.max_age_days"
    )
    # 0 이면 "출품 당일에 갱신된 것까지만 새 매물로 친다"는 뜻이라 유효하다.
    default_max_bump = _optional_age(
        notify_raw.get("max_bump_days"), "notify.max_bump_days", minimum=0
    )

    keywords_raw = raw.get("keywords") or []
    if not isinstance(keywords_raw, list) or not keywords_raw:
        raise ConfigError("config.yaml 의 keywords 가 비어 있습니다.")
    keywords = tuple(
        _parse_keyword(entry, i, default_interval, default_max_age, default_max_bump)
        for i, entry in enumerate(keywords_raw)
    )
    names = [k.name for k in keywords]
    if len(set(names)) != len(names):
        raise ConfigError(f"keywords 의 name 이 중복됩니다: {names}")

    backoff_raw = raw.get("backoff") or {}
    store_raw = raw.get("store") or {}
    keep_days = _positive_int(store_raw.get("keep_days", 60), "store.keep_days", 7)

    # 기록을 나이 제한보다 먼저 지우면, 아직 감시 대상인 상품을 신규로 다시 알린다.
    ages = [k.max_age_days for k in keywords if k.max_age_days is not None]
    if ages and keep_days <= max(ages):
        raise ConfigError(
            f"store.keep_days({keep_days})는 가장 긴 max_age_days({max(ages)})보다 "
            f"커야 합니다. 그렇지 않으면 아직 감시 중인 상품의 기록이 지워져 "
            f"이미 알린 상품을 신규로 다시 알립니다."
        )

    heartbeat_hour = notify_raw.get("heartbeat_hour")
    if heartbeat_hour is not None and (
        not isinstance(heartbeat_hour, int) or not 0 <= heartbeat_hour <= 23
    ):
        raise ConfigError("notify.heartbeat_hour 는 0~23 또는 null 이어야 합니다.")

    data_dir = ROOT / "data"
    data_dir.mkdir(exist_ok=True)
    (ROOT / "logs").mkdir(exist_ok=True)

    return Config(
        poll=PollConfig(
            default_interval_sec=default_interval,
            jitter_ratio=jitter,
            min_request_gap_sec=float(poll_raw.get("min_request_gap_sec", 3)),
            night=night,
        ),
        backoff=BackoffConfig(
            fail_threshold=_positive_int(
                backoff_raw.get("fail_threshold", 3), "backoff.fail_threshold", 1
            ),
            max_interval_sec=_positive_int(
                backoff_raw.get("max_interval_sec", 900), "backoff.max_interval_sec", 10
            ),
            recover_after=_positive_int(
                backoff_raw.get("recover_after", 5), "backoff.recover_after", 1
            ),
        ),
        notify=NotifyConfig(
            show_krw=bool(notify_raw.get("show_krw", True)),
            price_drop=bool(notify_raw.get("price_drop", True)),
            batch_threshold=_positive_int(
                notify_raw.get("batch_threshold", 5), "notify.batch_threshold", 2
            ),
            heartbeat_hour=heartbeat_hour,
            error_alert=bool(notify_raw.get("error_alert", True)),
            max_age_days=default_max_age,
            max_bump_days=default_max_bump,
        ),
        store=StoreConfig(keep_days=keep_days),
        keywords=keywords,
        telegram_token=token,
        telegram_chat_id=chat_id,
        discord_webhook=webhook,
        discord_bot_token=bot_token,
        discord_channel_id=channel_id,
        discord_owner_id=owner_id,
        config_path=config_path,
        db_path=data_dir / "seen.db",
        log_path=ROOT / "logs" / "meralarm.log",
        fx_cache_path=data_dir / "fx.json",
    )
