"""알림 끄기 — 상품 무시와 판매자 차단.

    /mute     이 상품은 더 알리지 마 (값을 내려도)
    /block    이 판매자의 상품은 어느 키워드에서든 알리지 마

둘 다 **알림만 끈다. 가격은 계속 기록한다.** 기록까지 멈추면 그 상품이 검색에서
안 보이는 것처럼 낡아 지워지고, 나중에 무시를 풀었을 때 옛 매물이 "신규"로
쏟아진다. 거르는 자리도 그래서 스케줄러의 알림 후보 단계다(`_drop_silenced`).

## 버튼

신규·인하 알림에 버튼이 붙는다. 버튼에는 짧은 표식을 담는다.

    mute:m12345678901             이 상품 무시
    block:964893670:m12345678901  이 판매자 차단 (뒤의 상품은 메모용)
    unmute:m12345678901           되돌리기
    unblock:964893670             되돌리기

누르면 채널(텔레그램·디스코드)이 표식을 그대로 돌려주고, 여기서 명령어로 바꿔
`CommandCore` 에 넘긴다. 표식의 모양과 뜻을 이 파일 한 곳에만 두는 이유는,
채널마다 따로 해석하면 한쪽만 고치는 일이 생기기 때문이다.

텔레그램 콜백 데이터는 64바이트, 디스코드 custom_id 는 100자까지다. 실측 최장이
Shops 상품의 38바이트라 넉넉하다. 번호는 영숫자만 받는다 — 표식은 우리가 만든
버튼에서만 오지만, 엉뚱한 글자가 섞여 명령어가 되는 일은 애초에 막아둔다.
"""

import re
from html import escape

# 상품 번호. 개인 판매는 m+숫자 12자, Shops 는 영문 대소문자 섞인 22자다.
# **대소문자를 바꾸면 안 된다** — Shops 번호는 대소문자가 다르면 다른 상품이다.
_ITEM_ID = r"[A-Za-z0-9]{6,40}"
# 판매자 번호. 개인이든 Shops 든 숫자 9자였다.
_SELLER_ID = r"\d{3,20}"

_ITEM_URL = re.compile(r"mercari\.com/(?:item|shops/product)/(" + _ITEM_ID + r")")
_SELLER_URL = re.compile(r"mercari\.com/user/profile/(" + _SELLER_ID + r")")

ADD = {"add", "추가", "넣기"}
DEL = {"del", "delete", "remove", "삭제", "제거", "빼기"}

# 목록이 아무리 길어도 이만큼만 보여준다. 텔레그램 한 통이 4096자다.
LIST_LIMIT = 30


def parse_item(text: str) -> str | None:
    """상품 주소나 번호에서 상품 번호만 꺼낸다. 못 읽으면 None."""
    text = text.strip().strip("<>")
    if m := _ITEM_URL.search(text):
        return m.group(1)
    if re.fullmatch(_ITEM_ID, text):
        return text
    return None


def parse_seller(text: str) -> str | None:
    """판매자 주소나 번호에서 판매자 번호만 꺼낸다. 못 읽으면 None."""
    text = text.strip().strip("<>")
    if m := _SELLER_URL.search(text):
        return m.group(1)
    if re.fullmatch(_SELLER_ID, text):
        return text
    return None


# ---- 버튼 표식 ----


def item_actions(item) -> tuple[tuple[str, str], ...]:
    """신규·인하 알림에 붙일 버튼. (보이는 글자, 표식).

    판매자 번호가 숫자가 아니면 차단 버튼은 뺀다. 수집기가 번호를 못 받아 와
    "None" 같은 것이 들어 있으면, 누르는 순간 엉뚱한 것을 막게 된다.
    """
    actions = [("🔇 이 상품 무시", f"mute:{item.id}")]
    if item.seller_id.isdigit():
        actions.append(("🚫 판매자 차단", f"block:{item.seller_id}:{item.id}"))
    return tuple(actions)


def action_to_command(token: str) -> str | None:
    """버튼 표식을 명령어로 바꾼다. 모르는 모양이면 None — 아무것도 하지 않는다."""
    if m := re.fullmatch(r"mute:(" + _ITEM_ID + r")", token):
        return f"/mute add {m[1]}"
    if m := re.fullmatch(r"unmute:(" + _ITEM_ID + r")", token):
        return f"/mute del {m[1]}"
    if m := re.fullmatch(r"block:(" + _SELLER_ID + r"):(" + _ITEM_ID + r")", token):
        return f"/block add {m[1]} {m[2]}"
    if m := re.fullmatch(r"unblock:(" + _SELLER_ID + r")", token):
        return f"/block del {m[1]}"
    return None


def undo_actions(token: str) -> tuple[tuple[str, str], ...]:
    """버튼으로 무시·차단한 뒤 그 답장에 붙일 되돌리기 버튼.

    한 번 눌러 알림이 꺼지는 만큼, 잘못 눌렀을 때도 한 번에 되돌릴 수 있어야
    한다. 이 프로그램에서 가장 무서운 것은 알림이 **조용히** 죽는 것이다.
    """
    if m := re.fullmatch(r"mute:(" + _ITEM_ID + r")", token):
        return (("↩ 되돌리기", f"unmute:{m[1]}"),)
    if m := re.fullmatch(r"block:(" + _SELLER_ID + r"):" + _ITEM_ID, token):
        return (("↩ 되돌리기", f"unblock:{m[1]}"),)
    return ()


def seller_url(seller_id: str) -> str:
    return f"https://jp.mercari.com/user/profile/{seller_id}"


# ---- 명령 ----


class SilenceCommands:
    """`/mute` 와 `/block`. 답장은 다른 명령과 같이 텔레그램 HTML 로 쓴다.

    `commands.CommandCore` 가 이것을 불러 쓴다. 명령 파일이 1,100줄을 넘어
    기능별로 나누기 시작한 첫 조각이다.
    """

    def __init__(self, store) -> None:
        # 기록 저장소가 없으면 무시·차단을 담을 곳이 없다. 실제 실행에서는 늘 있다.
        self._store = store

    def summary(self) -> str | None:
        """`/list` 아래에 붙일 한 줄. 무시도 차단도 없으면 None.

        "왜 이 상품이 안 오지" 의 답이 여기 있을 수 있다. 제외어처럼 보이는 곳에
        두어야 찾는다.
        """
        if self._store is None:
            return None
        muted, blocked = len(self._store.muted()), len(self._store.blocked())
        if not muted and not blocked:
            return None
        parts = []
        if muted:
            parts.append(f"🔇 무시 중인 상품 {muted}건 (/mute)")
        if blocked:
            parts.append(f"🚫 차단한 판매자 {blocked}명 (/block)")
        return "\n".join(parts)

    # ---- /mute ----

    def mute(self, argument: str) -> str:
        if self._store is None:
            return "⚠️ 기록 저장소가 없어 쓸 수 없습니다."
        parts = argument.split()
        if not parts:
            return self._mute_list()
        head = parts[0]
        if head.lower() in ADD:
            return self._mute_add(parts[1:])
        if head.lower() in DEL:
            return self._mute_del(parts[1:])
        # "/mute 상품주소" 처럼 바로 붙여넣어도 넣기로 받아준다. delete·remove 는
        # 상품 번호 모양(영숫자 6자 이상)에도 맞지만, 동작 이름을 위에서 먼저 보므로
        # 여기까지 오지 않는다.
        if parse_item(head):
            return self._mute_add(parts)
        return f"모르는 사용법입니다: <code>{escape(head)}</code>\n\n" + self._mute_usage()

    @staticmethod
    def _mute_usage() -> str:
        return (
            "<b>상품 무시</b> — 이 상품은 값을 내려도 더 알리지 않습니다.\n"
            "알림 아래의 <b>🔇 이 상품 무시</b> 버튼을 누르는 게 가장 쉽습니다.\n\n"
            "<code>/mute</code> — 무시 중인 목록\n"
            "<code>/mute 상품주소</code> — 무시하기\n"
            "<code>/mute del 상품번호</code> — 되돌리기\n\n"
            "판매완료된 상품은 알아서 정리됩니다."
        )

    def _mute_list(self) -> str:
        rows = self._store.muted()
        if not rows:
            return "무시 중인 상품이 없습니다.\n\n" + self._mute_usage()
        lines = [f"<b>무시 중인 상품</b> {len(rows)}건 (값을 내려도 알리지 않음)", ""]
        for n, (item_id, name, _) in enumerate(rows[:LIST_LIMIT], 1):
            lines.append(f"{n}. {escape(name[:44])}")
            lines.append(f"    <code>{escape(item_id)}</code>")
        if len(rows) > LIST_LIMIT:
            lines.append(f"\n<i>외 {len(rows) - LIST_LIMIT}건</i>")
        lines += [
            "",
            "되돌리려면 <code>/mute del 상품번호</code>",
            "판매완료된 상품은 알아서 정리됩니다.",
        ]
        return "\n".join(lines)

    def _mute_add(self, words: list[str]) -> str:
        if not words:
            return self._mute_usage()
        item_id = parse_item(words[0])
        if item_id is None:
            return (
                f"상품 번호를 못 읽었습니다: <code>{escape(words[0][:60])}</code>\n"
                "알림의 상품 주소를 그대로 붙여넣거나 <code>m12345678901</code> 처럼 적어주세요."
            )
        name = self._store.name_of(item_id) or item_id
        if not self._store.mute(item_id, name):
            return f"이미 무시 중입니다: <b>{escape(name[:60])}</b>"
        return (
            f"🔇 더 알리지 않습니다: <b>{escape(name[:60])}</b>\n"
            "값을 내려도 조용합니다. 가격 기록은 계속하므로 되돌리면 그때부터 다시 알립니다.\n"
            f"되돌리려면 <code>/mute del {escape(item_id)}</code>"
        )

    def _mute_del(self, words: list[str]) -> str:
        if not words:
            return "뺄 상품 번호를 적어주세요. 예: <code>/mute del m12345678901</code>"
        item_id = parse_item(words[0])
        if item_id is None:
            return f"상품 번호를 못 읽었습니다: <code>{escape(words[0][:60])}</code>"
        name = self._store.unmute(item_id)
        if name is None:
            return f"무시 중인 상품이 아닙니다: <code>{escape(item_id)}</code>"
        return f"🔔 다시 알립니다: <b>{escape(name[:60])}</b>"

    # ---- /block ----

    def block(self, argument: str) -> str:
        if self._store is None:
            return "⚠️ 기록 저장소가 없어 쓸 수 없습니다."
        parts = argument.split()
        if not parts:
            return self._block_list()
        head = parts[0]
        if head.lower() in ADD:
            return self._block_add(parts[1:])
        if head.lower() in DEL:
            return self._block_del(parts[1:])
        if parse_seller(head):
            return self._block_add(parts)
        return f"모르는 사용법입니다: <code>{escape(head)}</code>\n\n" + self._block_usage()

    @staticmethod
    def _block_usage() -> str:
        return (
            "<b>판매자 차단</b> — 이 판매자의 상품은 어느 키워드에서든 알리지 않습니다.\n"
            "알림 아래의 <b>🚫 판매자 차단</b> 버튼을 누르는 게 가장 쉽습니다.\n\n"
            "<code>/block</code> — 차단한 목록\n"
            "<code>/block 판매자주소</code> — 차단하기\n"
            "<code>/block del 판매자번호</code> — 되돌리기"
        )

    def _block_list(self) -> str:
        rows = self._store.blocked()
        if not rows:
            return "차단한 판매자가 없습니다.\n\n" + self._block_usage()
        lines = [f"<b>차단한 판매자</b> {len(rows)}명", ""]
        for n, (seller_id, note, _) in enumerate(rows[:LIST_LIMIT], 1):
            lines.append(
                f'{n}. <a href="{seller_url(seller_id)}">{escape(seller_id)}</a>'
            )
            if note:
                lines.append(f"    차단할 때 보던 상품: {escape(note[:40])}")
        if len(rows) > LIST_LIMIT:
            lines.append(f"\n<i>외 {len(rows) - LIST_LIMIT}명</i>")
        lines += ["", "되돌리려면 <code>/block del 판매자번호</code>"]
        return "\n".join(lines)

    def _block_add(self, words: list[str]) -> str:
        if not words:
            return self._block_usage()
        seller_id = parse_seller(words[0])
        if seller_id is None:
            return (
                f"판매자 번호를 못 읽었습니다: <code>{escape(words[0][:60])}</code>\n"
                "판매자 프로필 주소를 붙여넣거나 숫자만 적어주세요."
            )
        # 버튼으로 막을 때는 보고 있던 상품이 함께 온다. 나중에 왜 막았는지 알 수
        # 있게 그 이름을 남긴다. 직접 칠 때는 없어도 된다.
        note = ""
        if len(words) > 1 and (item_id := parse_item(words[1])):
            note = self._store.name_of(item_id) or ""
        if not self._store.block(seller_id, note):
            return f"이미 차단한 판매자입니다: <code>{escape(seller_id)}</code>"
        lines = [
            f'🚫 판매자를 차단했습니다: <a href="{seller_url(seller_id)}">{escape(seller_id)}</a>',
            "이 판매자의 상품은 어느 키워드에서든 알리지 않습니다.",
        ]
        if note:
            lines.append(f"차단할 때 보던 상품: {escape(note[:60])}")
        lines.append(f"되돌리려면 <code>/block del {escape(seller_id)}</code>")
        return "\n".join(lines)

    def _block_del(self, words: list[str]) -> str:
        if not words:
            return "풀 판매자 번호를 적어주세요. 예: <code>/block del 964893670</code>"
        seller_id = parse_seller(words[0])
        if seller_id is None:
            return f"판매자 번호를 못 읽었습니다: <code>{escape(words[0][:60])}</code>"
        if self._store.unblock(seller_id) is None:
            return f"차단한 판매자가 아닙니다: <code>{escape(seller_id)}</code>"
        return f"🔔 차단을 풀었습니다: <code>{escape(seller_id)}</code>\n이 판매자의 상품도 다시 알립니다."
