"""디스코드 봇. 알림도 보내고 명령도 받는다.

웹훅과 다른 점은 **상시 접속**이다. 디스코드에 웹소켓으로 붙어 있으면서 슬래시
명령을 받는다. 나가는 연결이라 공유기 안이든 클라우드든 들어오는 포트를 열 필요가
없다. 텔레그램 롱 폴링과 같은 이치다.

슬래시 명령을 쓰는 이유는 **"메시지 내용 읽기" 특권 권한이 필요 없어서**다.
`!add` 같은 접두사 명령을 받으려면 봇이 채팅을 다 읽어야 하고, 그러려면 개발자
포털에서 특권 권한을 따로 켜야 한다. 슬래시 명령은 디스코드가 "누가 무엇을 어떤
값으로 실행했다"만 알려주므로 채팅을 볼 일이 없다.

여기 있는 것은 받아오고 보내는 방법뿐이다. 명령이 무엇을 하는지는 전부
`commands.CommandCore` 에 있고 텔레그램과 같은 것을 쓴다.
"""

import asyncio
import logging

import discord
from discord import app_commands

from .. import alerts
from ..alerts import Alert
from ..commands import GLOBAL_SETTINGS, KEYWORD_SETTINGS, CommandCore
from . import discord_embed, markup

# 봇이 아직 안 붙었을 때 알림 하나를 얼마나 기다려 줄지. 무한정 기다리면 디스코드가
# 죽었을 때 전송 큐 전체가 그 자리에 선다.
READY_TIMEOUT = 15

log = logging.getLogger(__name__)


class _NoVoiceNoise(logging.Filter):
    """음성 기능 관련 경고만 걸러낸다.

    `discord.Client` 를 만들 때마다 "PyNaCl is not installed" 와 "davey is not
    installed" 를 WARNING 으로 남긴다. 우리는 글만 보내므로 상관없는 이야기인데,
    로그를 열어본 사람은 무언가 잘못된 줄 안다.

    로거 수위를 통째로 올리면 접속·재접속 같은 쓸모 있는 기록까지 사라지므로
    이 두 줄만 집어서 뺀다.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        return "voice will NOT be supported" not in record.getMessage()


logging.getLogger("discord.client").addFilter(_NoVoiceNoise())


def _set_choices() -> list[app_commands.Choice[str]]:
    """`/set` 의 항목 목록을 설정표에서 그대로 만든다.

    여기에 손으로 다시 적으면 항목을 늘릴 때 한쪽만 고치게 된다.
    """
    labels: dict[str, str] = {}
    for key, (_, label, _) in GLOBAL_SETTINGS.items():
        labels[key] = label
    for key, (_, label, _) in KEYWORD_SETTINGS.items():
        labels.setdefault(key, label + " · 키워드 전용")
    # 디스코드는 선택지를 25개까지만 받는다. 지금은 12개지만 넘으면 조용히 거절당한다.
    return [
        app_commands.Choice(name=f"{label} ({key})"[:100], value=key)
        for key, label in list(labels.items())[:25]
    ]


class DiscordBot:
    """전송 채널이면서 명령 수신기다. `SendQueue` 에는 그냥 채널로 보인다."""

    name = "discord"

    def __init__(
        self,
        token: str,
        channel_id: int,
        owner_id: int,
        core: CommandCore,
    ) -> None:
        self._token = token
        self._channel_id = channel_id
        self._owner_id = owner_id
        self._core = core
        self._synced = False
        # 토큰이 틀린 것처럼 다시 시도해도 소용없는 실패. 이때는 기다리지 않고
        # 곧바로 물러나야 다른 채널의 알림까지 늦어지지 않는다.
        self._failed = False

        # 채널 목록만 있으면 된다. 채팅 내용은 보지 않는다.
        intents = discord.Intents.none()
        intents.guilds = True
        self._client = discord.Client(intents=intents)
        self._tree = app_commands.CommandTree(self._client)
        self._register()

    # ---- 접속 ----

    async def run(self) -> None:
        """끊기면 discord.py 가 알아서 다시 붙는다. 여기서 도는 것은 그 바깥이다."""

        @self._client.event
        async def on_ready():
            await self._on_ready()

        try:
            await self._client.start(self._token)
        except asyncio.CancelledError:
            raise
        except discord.LoginFailure:
            self._failed = True
            log.error(
                "디스코드가 봇 토큰을 거부했습니다. 토큰을 다시 발급받아 "
                "--setup 으로 넣어주세요. 다른 채널의 알림은 계속 갑니다."
            )
        except Exception:
            self._failed = True
            # 봇이 죽었다고 메루카리 감시까지 멈추면 본말전도다.
            log.exception("디스코드 봇이 멈췄습니다. 다른 채널의 알림은 계속 갑니다")

    async def _on_ready(self) -> None:
        # 끊겼다 붙을 때마다 다시 불린다. 명령 등록은 한 번이면 된다.
        if self._synced:
            log.info("디스코드에 다시 연결됐습니다")
            return

        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            log.error(
                "디스코드 채널(%s)을 찾을 수 없습니다. 봇을 그 서버에서 내보내지 "
                "않았는지, 채널을 지우지 않았는지 확인하세요.",
                self._channel_id,
            )
            return

        # 서버 단위로 등록하면 곧바로 반영된다. 전역 등록은 최대 1시간이 걸린다.
        try:
            self._tree.copy_global_to(guild=channel.guild)
            await self._tree.sync(guild=channel.guild)
        except discord.HTTPException:
            log.exception("슬래시 명령을 등록하지 못했습니다. 알림은 정상입니다")
            return

        self._synced = True
        log.info(
            "디스코드 봇 연결됨: %s · %s #%s · 명령은 %s 만 사용",
            self._client.user,
            channel.guild.name,
            channel.name,
            self._owner_id,
        )

    async def close(self) -> None:
        if not self._client.is_closed():
            await self._client.close()

    # ---- 알림 보내기 ----

    async def send(self, alert: Alert) -> bool:
        channel = await self._channel()
        if channel is None:
            return False
        try:
            if alert.kind == alerts.RAW:
                # 이미 텔레그램 HTML 로 쓰인 글. 그대로 보내면 태그가 글자로 보인다.
                for part in markup.chunks(markup.from_html(alert.body)):
                    await channel.send(part)
            else:
                await channel.send(embed=discord.Embed.from_dict(discord_embed.build(alert)))
            return True
        except discord.HTTPException as e:
            log.error("디스코드 전송 실패: %s", e)
            return False

    async def _channel(self):
        if self._failed:
            return None
        try:
            await asyncio.wait_for(self._client.wait_until_ready(), READY_TIMEOUT)
        except asyncio.TimeoutError:
            log.warning("디스코드 봇이 아직 연결되지 않아 이번 알림은 건너뜁니다")
            return None
        channel = self._client.get_channel(self._channel_id)
        if channel is None:
            log.error("디스코드 채널(%s)을 찾을 수 없습니다", self._channel_id)
        return channel

    # ---- 명령 받기 ----

    async def _run(self, interaction: discord.Interaction, text: str) -> None:
        if interaction.user.id != self._owner_id:
            log.warning(
                "허용되지 않은 사용자(%s)의 명령을 무시했습니다: %s",
                interaction.user.id,
                text[:40],
            )
            # 본인에게만 보이는 답. 채널에 거절 메시지가 쌓이지 않는다.
            await interaction.response.send_message(
                "이 봇은 주인만 조작할 수 있습니다.", ephemeral=True
            )
            return

        log.info("디스코드 명령 수신: %s", text[:60])
        # 3초 안에 무언가 답하지 않으면 "응답하지 않습니다" 가 뜬다. 설정 파일을
        # 쓰고 다시 검증하는 명령은 그보다 걸릴 수 있어 먼저 시간을 벌어둔다.
        try:
            await interaction.response.defer()
        except discord.HTTPException:
            log.warning("디스코드 응답 시간을 놓쳤습니다: %s", text[:40])
            return

        try:
            reply = self._core.dispatch(text)
            for part in markup.chunks(markup.from_html(reply)):
                await interaction.followup.send(part)
        except Exception:
            log.exception("디스코드 명령 응답 중 오류")
            try:
                await interaction.followup.send("⚠️ 처리 중 오류가 났습니다. 로그를 확인하세요.")
            except discord.HTTPException:
                pass

    def _register(self) -> None:
        tree = self._tree

        @tree.command(name="status", description="잘 돌고 있는지 확인")
        async def status(interaction: discord.Interaction):
            await self._run(interaction, "/status")

        @tree.command(name="list", description="감시 중인 키워드 목록")
        async def list_(interaction: discord.Interaction):
            await self._run(interaction, "/list")

        @tree.command(name="config", description="지금 설정값 전부 보기")
        async def config(interaction: discord.Interaction):
            await self._run(interaction, "/config")

        @tree.command(name="help", description="사용법 보기")
        async def help_(interaction: discord.Interaction):
            await self._run(interaction, "/help")

        @tree.command(name="add", description="감시할 키워드 추가")
        @app_commands.describe(keyword="예: 芹沢あさひ -セット -まとめ (제외어에는 - 를 붙입니다)")
        async def add(interaction: discord.Interaction, keyword: str):
            await self._run(interaction, f"/add {keyword}")

        @tree.command(name="del", description="키워드 삭제")
        @app_commands.describe(number="/list 에 나온 번호")
        async def delete(interaction: discord.Interaction, number: int):
            await self._run(interaction, f"/del {number}")

        @tree.command(name="set", description="설정 바꾸기")
        @app_commands.describe(
            item="바꿀 항목",
            value="새 값. 해제하려면 off",
            number="이 키워드에만 적용할 때 /list 의 번호",
        )
        @app_commands.choices(item=_set_choices())
        async def set_(
            interaction: discord.Interaction,
            item: app_commands.Choice[str],
            value: str,
            number: int | None = None,
        ):
            target = f"{number} " if number is not None else ""
            await self._run(interaction, f"/set {target}{item.value} {value}")

        @tree.command(name="exclude", description="제외어 보기·고치기")
        @app_commands.describe(
            action="무엇을 할지 (비우면 지금 목록을 보여줍니다)",
            words="넣거나 뺄 말. 여러 개면 띄어쓰기로",
            number="이 키워드에만 적용할 때 /list 의 번호. 비우면 모든 키워드",
        )
        @app_commands.choices(
            action=[
                app_commands.Choice(name="목록 보기", value="list"),
                app_commands.Choice(name="넣기", value="add"),
                app_commands.Choice(name="빼기", value="del"),
                app_commands.Choice(name="넣기 확인", value="yes"),
            ]
        )
        async def exclude(
            interaction: discord.Interaction,
            action: app_commands.Choice[str] | None = None,
            words: str | None = None,
            number: int | None = None,
        ):
            # 목록 보기에는 동작 이름을 붙이지 않는다. "list" 를 그대로 넘기면
            # 모르는 사용법이 된다.
            verb = action.value if action else "list"
            scope = f"{number} " if number is not None else ""
            tail = "" if verb == "list" else f"{verb} {words or ''}"
            await self._run(interaction, f"/exclude {scope}{tail}".rstrip())

        @tree.command(name="require", description="필수 포함어 — 이 말이 없으면 안 알림")
        @app_commands.describe(
            number="키워드 번호 (/list 기준). 비우면 전체 목록",
            action="무엇을 할지",
            words="넣거나 뺄 말. 여러 개면 띄어쓰기로",
        )
        @app_commands.choices(
            action=[
                app_commands.Choice(name="목록 보기", value="list"),
                app_commands.Choice(name="넣기", value="add"),
                app_commands.Choice(name="빼기", value="del"),
                app_commands.Choice(name="넣기 확인", value="yes"),
            ]
        )
        async def require(
            interaction: discord.Interaction,
            number: int | None = None,
            action: app_commands.Choice[str] | None = None,
            words: str | None = None,
        ):
            verb = action.value if action else "list"
            if verb == "yes":
                await self._run(interaction, "/require yes")
                return
            scope = f"{number} " if number is not None else ""
            tail = "" if verb == "list" else f"{verb} {words or ''}"
            await self._run(interaction, f"/require {scope}{tail}".rstrip())

        @tree.command(name="pause", description="잠시 멈추기")
        @app_commands.describe(duration="30m, 2h 처럼. 비우면 무기한")
        async def pause(interaction: discord.Interaction, duration: str | None = None):
            await self._run(interaction, f"/pause {duration}" if duration else "/pause")

        @tree.command(name="resume", description="다시 시작")
        async def resume(interaction: discord.Interaction):
            await self._run(interaction, "/resume")
