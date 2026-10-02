"""포트 규약을 실제로 지키는지 본다.

`Notifier` 와 `ItemSource` 는 `Protocol` 이라 상속하지 않는다. 그래서 타입 체커가
없으면 아무도 안 지켜도 조용하다. 채널을 하나 더 붙이면서 `close()` 를 빠뜨리면
종료하는 순간에야 터진다 — 몇 시간 돌린 뒤에.

여기서는 실행 시점에 이름·인자·async 여부까지 맞춰 본다. 새 채널을 만들면
이 파일에 한 줄 추가하는 것으로 규약 검사가 따라온다.
"""

import inspect
import sys
from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm.notifiers.base import Notifier  # noqa: E402
from meralarm.notifiers.discord_bot import DiscordBot  # noqa: E402
from meralarm.notifiers.discord_webhook import DiscordNotifier  # noqa: E402
from meralarm.notifiers.telegram import TelegramNotifier  # noqa: E402
from meralarm.sources.base import ItemSource  # noqa: E402
from meralarm.sources.mercapi_source import MercapiSource  # noqa: E402

ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


def members(protocol) -> tuple[list[str], list[str]]:
    """(메서드 이름들, 데이터 속성 이름들)."""
    names = [n for n in protocol.__protocol_attrs__ if not n.startswith("_")]
    methods = [n for n in names if callable(getattr(protocol, n, None))]
    return sorted(methods), sorted(n for n in names if n not in methods)


def conforms(cls, protocol) -> None:
    """cls 가 protocol 을 구조적으로 만족하는가."""
    methods, attributes = members(protocol)
    label = f"{cls.__name__} → {protocol.__name__}"

    for name in attributes:
        check(f"{label} · {name} 있음", hasattr(cls, name))

    for name in methods:
        want = getattr(protocol, name)
        got = getattr(cls, name, None)
        if got is None:
            check(f"{label} · {name}() 있음", False, "없음")
            continue
        check(f"{label} · {name}() 있음", True)

        # await 로 부르는 자리다. 보통 함수를 두면 코루틴이 아니라 함수 객체가
        # 돌아와 조용히 아무 일도 안 일어난다.
        check(
            f"{label} · {name}() async",
            inspect.iscoroutinefunction(got) == inspect.iscoroutinefunction(want),
            f"규약 {inspect.iscoroutinefunction(want)} · 실제 {inspect.iscoroutinefunction(got)}",
        )

        want_args = list(inspect.signature(want).parameters)
        got_args = list(inspect.signature(got).parameters)
        check(f"{label} · {name}{tuple(want_args)} 인자 일치", want_args == got_args, got_args)


print("=== 1. Protocol 이 뭘 요구하는지 ===")
methods, attributes = members(Notifier)
check("Notifier 가 요구하는 메서드", methods == ["close", "send"], methods)
check("Notifier 가 요구하는 속성", attributes == ["name"], attributes)
check("ItemSource 가 요구하는 메서드", members(ItemSource)[0] == ["search"], members(ItemSource))

print("\n=== 2. 알림 채널 세 개가 규약을 지키는가 ===")
for cls in (TelegramNotifier, DiscordNotifier, DiscordBot):
    conforms(cls, Notifier)

print("\n=== 3. 수집기가 규약을 지키는가 ===")
conforms(MercapiSource, ItemSource)

print("\n=== 4. 채널 이름이 서로 구분되는가 ===")
# 큐는 alert.only 를 이름으로 견준다. 이름이 비어 있거나 겹치면 명령 응답이
# 엉뚱한 곳으로 가거나 아무 데도 안 간다.
names = {cls.__name__: cls.name for cls in (TelegramNotifier, DiscordNotifier, DiscordBot)}
check("모두 이름이 있음", all(names.values()), names)
check("텔레그램은 telegram", names["TelegramNotifier"] == "telegram", names)
# 웹훅과 봇은 둘 다 discord 다. 구조상 함께 켜지지 않으므로 겹쳐도 된다
# (__main__.build_channels 의 elif). 오히려 같아야 alert.only="discord" 가
# 어느 쪽이 켜져 있든 똑같이 닿는다.
check(
    "웹훅과 봇은 같은 이름을 쓴다",
    names["DiscordNotifier"] == names["DiscordBot"] == "discord",
    names,
)

print("\n=== 5. 규약을 어기면 잡히는가 (검사 자체가 도는지) ===")


class 없는채널:
    name = "broken"

    async def send(self, alert) -> bool:  # close 를 빠뜨렸다
        return True


before = fail
conforms(없는채널, Notifier)
check("close 를 빠뜨린 채널이 걸러짐", fail > before, "그냥 통과해버림")
fail = before  # 일부러 낸 실패는 합계에서 뺀다

print(f"\n{'=' * 54}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
