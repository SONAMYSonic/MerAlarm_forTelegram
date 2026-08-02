"""config.yaml 과 .env 를 읽어 검증된 설정 객체로 만든다.

설정 오류는 실행 직후에 사람이 읽을 수 있는 메시지로 끝내야 한다. 무인 운영이라
잘못된 값으로 몇 시간 돌다가 조용히 아무 알림도 안 오는 상황이 최악이다.
"""

from dataclasses import dataclass
from datetime import time
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parent.parent


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
class NotifyConfig:
    show_krw: bool
    price_drop: bool
    batch_threshold: int
    heartbeat_hour: int | None
    error_alert: bool


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


@dataclass(frozen=True, slots=True)
class Config:
    poll: PollConfig
    backoff: BackoffConfig
    notify: NotifyConfig
    keywords: tuple[KeywordConfig, ...]
    telegram_token: str
    telegram_chat_id: str
    config_path: Path
    db_path: Path
    log_path: Path
    fx_cache_path: Path


def _read_env(path: Path) -> dict[str, str]:
    if not path.exists():
        raise ConfigError(f"{path} 가 없습니다. .env.example 을 복사해서 만드세요.")
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


def _optional_age(value: Any, where: str) -> int | None:
    if value is None:
        return None
    if not isinstance(value, int) or value < 1:
        raise ConfigError(f"{where} 는 1 이상의 정수 또는 null 이어야 합니다. 받은 값: {value!r}")
    return value


def _parse_keyword(
    raw: Any, index: int, default_interval: int, default_max_age: int | None
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
    )


def load() -> Config:
    env = _read_env(ROOT / ".env")
    token = env.get("TELEGRAM_BOT_TOKEN")
    chat_id = env.get("TELEGRAM_CHAT_ID")
    if not token:
        raise ConfigError(".env 의 TELEGRAM_BOT_TOKEN 이 비어 있습니다.")
    if not chat_id:
        raise ConfigError(
            ".env 의 TELEGRAM_CHAT_ID 가 비어 있습니다. telegram_check.py 를 실행하면 찾아줍니다."
        )

    config_path = ROOT / "config.yaml"
    if not config_path.exists():
        raise ConfigError(f"{config_path} 가 없습니다.")
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

    keywords_raw = raw.get("keywords") or []
    if not isinstance(keywords_raw, list) or not keywords_raw:
        raise ConfigError("config.yaml 의 keywords 가 비어 있습니다.")
    keywords = tuple(
        _parse_keyword(entry, i, default_interval, default_max_age)
        for i, entry in enumerate(keywords_raw)
    )
    names = [k.name for k in keywords]
    if len(set(names)) != len(names):
        raise ConfigError(f"keywords 의 name 이 중복됩니다: {names}")

    backoff_raw = raw.get("backoff") or {}

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
        ),
        keywords=keywords,
        telegram_token=token,
        telegram_chat_id=chat_id,
        config_path=config_path,
        db_path=data_dir / "seen.db",
        log_path=ROOT / "logs" / "meralarm.log",
        fx_cache_path=data_dir / "fx.json",
    )
