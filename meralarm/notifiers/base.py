"""알림 채널 규약.

`sources/base.py` 의 짝이다. 저쪽이 "상품을 어디서 가져오는가"라면 이쪽은
"알림을 어디로 내보내는가"다. 둘 다 갈아끼울 수 있어야 하므로 규약만 정해 둔다.

지금 이 규약을 지키는 것은 셋이다.

    TelegramNotifier   notifiers/telegram.py
    DiscordNotifier    notifiers/discord_webhook.py   웹훅
    DiscordBot         notifiers/discord_bot.py       봇(명령도 받는다)

셋은 서로를 모르고 `SendQueue` 도 어느 것이 왔는지 모른다. 그래서 채널을 하나
더 붙이는 일이 파일 하나 추가로 끝난다.

`Protocol` 이라 상속하지 않는다. 이름·`send`·`close` 를 갖추면 그것으로 채널이다.
"""

from typing import Protocol

from ..alerts import Alert


class Notifier(Protocol):
    # 큐가 `Alert.only` 와 견주는 이름. 명령 응답을 물어본 채널로만 돌려보낼 때 쓴다.
    name: str

    async def send(self, alert: Alert) -> bool:
        """보냈으면 True.

        예외를 밖으로 던져도 되지만 False 를 돌려주는 편이 낫다. 채널이 여럿일 때
        **하나라도 성공하면 본 것으로 기록하므로**, 실패를 조용히 삼키면 다른
        채널로는 갔는데 안 간 것으로 남거나 그 반대가 된다.
        """
        ...

    async def close(self) -> None:
        """종료할 때 불린다. 열어 둔 연결을 닫는다.

        규약에 넣어 둔 이유는, 빠뜨려도 종료하는 순간에야 터지기 때문이다.
        받아둘 게 없으면 그냥 아무것도 하지 않으면 된다.
        """
        ...
