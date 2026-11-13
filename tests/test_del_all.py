"""`/exclude del all` · `/require 2 del all` 검증 — 목록을 한 번에 비우기.

콜라보 카페가 끝나 アイキュー スイパラ スイーツパラダイス 를 한꺼번에 빼고 싶었던
일에서 나왔다. 확인할 것은 셋이다.

- 명령이 말한 범위만 비우는가 (전역이면 전역만, 번호면 그 키워드만)
- 잘못 뺐을 때 답장의 명령을 붙여넣어 그대로 되돌릴 수 있는가
- 사람이 config.yaml 에 써 둔 주석이 함께 사라지지 않는가
"""

import asyncio
import html
import re
import shutil
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as cfgmod  # noqa: E402

TMP = Path(tempfile.mkdtemp())
cfgmod.ROOT = TMP
CONFIG = TMP / "config.yaml"

from discord import app_commands  # noqa: E402

from meralarm.commands import HELP, CommandCore  # noqa: E402
from meralarm.config import load  # noqa: E402
from meralarm.config_store import KeywordStore, KeywordStoreError  # noqa: E402
from meralarm.control import Controls  # noqa: E402
from meralarm.notifiers.discord_bot import DiscordBot  # noqa: E402
from meralarm.setup_wizard import write_env  # noqa: E402
from meralarm.store import SeenStore  # noqa: E402

write_env(TMP / ".env", {"TELEGRAM_BOT_TOKEN": "1:x" * 20, "TELEGRAM_CHAT_ID": "55"})
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


class FakeScheduler:
    stats = SimpleNamespace(
        uptime_seconds=lambda: 1.0, requests=0, failures=0, new_items=0, price_drops=0
    )
    keyword_names = []
    applied = None

    def reload_keywords(self, keywords):
        FakeScheduler.applied = {k.name: k for k in keywords}


# 사람이 손으로 주석을 단 설정. 목록 바로 뒤의 주석은 ruamel 이 목록의 마지막 항목에
# 매달기 때문에, 목록을 비우면 다음 항목의 주석까지 사라지기 쉽다.
LAYOUT = """\
# MerAlarm 설정

poll:
  default_interval_sec: 30

# 모든 키워드에 함께 적용되는 제외어.
exclude:
  - C108
  - アイキュー  # 콜라보 끝나면 뺄 것

# 감시할 키워드. 이 주석은 keywords 앞에 남아야 한다.
keywords:
  - name: ストレイライト
    query: ストレイライト
    exclude:
      - LED
    require:
      - ストレイライト
      - シャニマス
    # price_max 위 주석
    price_max: 999999
  - name: 黛冬優子
    query: 黛冬優子
    require:
      - シャニマス

  # 마지막 키워드 앞 주석
  - name: 和泉愛依
    query: 和泉愛依
    exclude:
      - まとめ
"""


def text():
    return CONFIG.read_text(encoding="utf-8")


def right_before(comment, line):
    """comment 줄 바로 다음 줄이 line 으로 시작하는가."""
    lines = text().splitlines()
    return any(
        a.strip() == comment and b.strip().startswith(line) for a, b in zip(lines, lines[1:])
    )


def reset():
    CONFIG.write_text(LAYOUT, encoding="utf-8")


store = KeywordStore(CONFIG)

print("=== 1. 저장소 — 전역 제외어 비우기 ===")
reset()
removed = store.clear_global_excludes(validate=load)
check("뺀 말을 돌려줌", removed == ["C108", "アイキュー"], removed)
check("빈 목록으로 남김 (키와 위 주석은 그대로)", "exclude: []" in text(), text()[:200])
check("위 설명 주석", right_before("# 모든 키워드에 함께 적용되는 제외어.", "exclude: []"))
check("다음 키의 주석이 안 사라짐", right_before("# 감시할 키워드. 이 주석은 keywords 앞에 남아야 한다.", "keywords:"))
check("빠진 말의 줄끝 주석은 함께 빠짐", "콜라보 끝나면" not in text())
cfg = load()
check("설정이 그대로 읽힘 · 전역 비었음", cfg.exclude == (), cfg.exclude)
kws = {k.name: k for k in cfg.keywords}
check("키워드별 제외어는 그대로", "LED" in kws["ストレイライト"].exclude and "まとめ" in kws["和泉愛依"].exclude)
before = CONFIG.read_bytes()
check("다시 비우면 빈 목록", store.clear_global_excludes(validate=load) == [])
check("할 것이 없으면 파일을 안 건드림", CONFIG.read_bytes() == before)

print("\n=== 2. 저장소 — 키워드 목록 비우기 ===")
reset()
name, removed = store.clear_keyword_words(1, "require", validate=load)
check("이름과 뺀 말", (name, removed) == ("ストレイライト", ["ストレイライト", "シャニマス"]), (name, removed))
check("빈 require 항목을 남기지 않음", "require" not in store._load()[1]["keywords"][0])
check("다음 키의 주석이 안 사라짐", right_before("# price_max 위 주석", "price_max: 999999"))
name, removed = store.clear_keyword_words(2, "require", validate=load)
check("키워드 끝의 목록", (name, removed) == ("黛冬優子", ["シャニマス"]), (name, removed))
check("다음 키워드의 주석이 안 사라짐", right_before("# 마지막 키워드 앞 주석", "- name: 和泉愛依"))
check("그 사이 빈 줄도 남음", "\n\n  # 마지막 키워드 앞 주석" in text(), repr(text()[-160:]))
name, removed = store.clear_keyword_words(3, "exclude", validate=load)
check("파일 끝의 목록", (name, removed) == ("和泉愛依", ["まとめ"]), (name, removed))
check("다른 목록은 그대로", store.keyword_words(1, "exclude")[1] == ["LED"])
check("전역도 그대로", store.global_excludes() == ["C108", "アイキュー"])
before = CONFIG.read_bytes()
check("없는 목록은 빈 결과", store.clear_keyword_words(1, "require", validate=load) == ("ストレイライト", []))
check("할 것이 없으면 파일을 안 건드림", CONFIG.read_bytes() == before)
try:
    store.clear_keyword_words(9, "exclude", validate=load)
    check("없는 번호는 거절", False, "통과해버림")
except KeywordStoreError as e:
    check("없는 번호는 거절", "1~3" in str(e), str(e))
load()
check("다 비운 뒤에도 설정이 읽힘", True)

CONFIG.write_text("keywords:\n  - 芹沢あさひ\n  - 黛冬優子\n", encoding="utf-8")
before = CONFIG.read_bytes()
check("간단형 키워드는 비울 것이 없음", store.clear_keyword_words(1, "exclude", validate=load) == ("芹沢あさひ", []))
check("간단형을 상세형으로 바꿔 쓰지 않음", CONFIG.read_bytes() == before)

print("\n=== 3. 마지막 말 하나만 빼도 주석이 남는다 ===")
# 비우기를 만들면서 같은 구멍이 하나씩 빼기에도 있던 것을 함께 막았다.
reset()
check("전역 마지막 말", store.remove_global_exclude("アイキュー", validate=load) == "アイキュー")
check("  남은 말은 그대로", store.global_excludes() == ["C108"])
check("  다음 키의 주석이 남음", right_before("# 감시할 키워드. 이 주석은 keywords 앞에 남아야 한다.", "keywords:"))
store.remove_global_exclude("C108", validate=load)
check("전역 마지막 하나까지", "exclude: []" in text() and right_before("# 감시할 키워드. 이 주석은 keywords 앞에 남아야 한다.", "keywords:"))
store.remove_keyword_word(1, "require", "シャニマス", validate=load)
check("키워드 목록의 마지막 말", store.keyword_words(1, "require")[1] == ["ストレイライト"])
check("  다음 키의 주석이 남음", right_before("# price_max 위 주석", "price_max: 999999"))
store.remove_keyword_word(2, "require", "シャニマス", validate=load)
check("키워드 목록이 비면 항목째", "require" not in store._load()[1]["keywords"][1])
check("  다음 키워드의 주석이 남음", right_before("# 마지막 키워드 앞 주석", "- name: 和泉愛依"))
load()

print("\n=== 4. 배포판 견본으로 왕복하면 원래대로 ===")
# 윈도우에서 견본은 CRLF 다(write_text). 줄바꿈을 맞춰 읽기 전에는 넣는 순간 목록 뒤
# 빈 줄이 사라져 이 왕복이 한 줄씩 어긋났다.
example = (REPO / "config.example.yaml").read_text(encoding="utf-8")
CONFIG.write_text(example, encoding="utf-8")
store.add_global_excludes(["C108", "アイキュー", "スイパラ"], validate=load)
check("넣으면 바뀜", text() != example)
check("  목록 뒤 빈 줄이 남음", "  - スイパラ\n\n# 검색어가 짧으면" in text(), text()[3300:3500])
store.clear_global_excludes(validate=load)
check("전역을 비우면 견본과 한 글자도 안 다름", text() == example)
store.add_keyword_words(1, "require", ["ポケカ", "ポケモン"], validate=load)
store.clear_keyword_words(1, "require", validate=load)
check("필수 포함어도 넣었다 비우면 그대로", text() == example)

print("\n=== 5. 명령 — /exclude del all ===")
reset()
seen = SeenStore(TMP / "s.db")
core = CommandCore(load(), Controls(), FakeScheduler(), seen)


def run(command):
    return core.dispatch(command)


def restore_commands(reply):
    """답장에 붙은 되돌리는 명령. 텔레그램에서 눌러 복사하는 그 글자 그대로."""
    return [html.unescape(c) for c in re.findall(r"<code>(/(?:exclude|require) [^<]*add [^<]*)</code>", reply)]


reply = run("/exclude del all")
check("몇 개를 뺐는지", "전역 제외어 2개를 전부 뺐습니다" in reply, reply[:120])
check("뺀 말을 보여줌", "C108" in reply and "アイキュー" in reply)
check("다시 알림 대상이 된다고 알림", "다시 알림 대상" in reply)
check("키워드별 제외어는 남았다고 알림", "키워드마다 따로 넣은 제외어는 그대로" in reply, reply[-260:])
check("되돌리는 명령", restore_commands(reply) == ["/exclude add C108 アイキュー"], restore_commands(reply))
check("스케줄러에 반영", all("C108" not in k.exclude for k in FakeScheduler.applied.values()))
check("키워드별 것은 스케줄러에도 남음", "LED" in FakeScheduler.applied["ストレイライト"].exclude)
check("또 하면 없다고", run("/exclude del all") == "전역 제외어가 없습니다.", run("/exclude del all"))

for command in restore_commands(reply):
    back = run(command)
check("답장의 명령으로 되돌아옴", store.global_excludes() == ["C108", "アイキュー"], back[:120])

for word in ("전부", "모두", "ALL", "All"):
    reply = run(f"/exclude del {word}")
    check(f"/exclude del {word} 도 전부 빼기", "전부 뺐습니다" in reply and store.global_excludes() == [], reply[:80])
    run("/exclude add C108 アイキュー")

reply = run("/exclude del all C108")
check("다른 말과 섞이면 낱말로 읽음", "전부 뺐습니다" not in reply and "원래 없던 말" in reply, reply[:160])
check("  C108 만 빠짐", store.global_excludes() == ["アイキュー"], store.global_excludes())
run("/exclude add C108")

print("\n=== 6. 명령 — /exclude 번호 del all ===")
reply = run("/exclude 1 del all")
check("그 키워드만", "ストレイライト</b> 의 제외어 1개를 전부 뺐습니다" in reply, reply[:120])
check("전역은 그대로", sorted(store.global_excludes()) == ["C108", "アイキュー"])
check("다른 키워드는 그대로", store.keyword_words(3, "exclude")[1] == ["まとめ"])
check("되돌리는 명령에 번호", restore_commands(reply) == ["/exclude 1 add LED"], restore_commands(reply))
check("키워드별 안내는 안 붙음", "키워드마다" not in reply)
check("또 하면 없다고", "의 제외어가 없습니다" in run("/exclude 1 del all"))
check("없는 번호", "1~3" in run("/exclude 9 del all"), run("/exclude 9 del all"))
run("/exclude 1 add LED")

print("\n=== 7. 명령 — /require 번호 del all ===")
reply = run("/require 1 del all")
check("몇 개를 뺐는지", "필수 포함어 2개를 전부 뺐습니다" in reply, reply[:120])
check("이제 다 본다고 알림", "검색에 걸린 것을 모두 봅니다" in reply)
check("되돌리는 명령", restore_commands(reply) == ["/require 1 add ストレイライト シャニマス"], restore_commands(reply))
check("스케줄러에 반영", FakeScheduler.applied["ストレイライト"].require == ())
check("다른 키워드는 그대로", store.keyword_words(2, "require")[1] == ["シャニマス"])
check("또 하면 없다고", "에는 필수 포함어가 없습니다" in run("/require 1 del all"))
for command in restore_commands(reply):
    run(command)
check("답장의 명령으로 되돌아옴", store.keyword_words(1, "require")[1] == ["ストレイライト", "シャニマス"])
check("번호 없이는 안내", "번호를 앞에" in run("/require del all"))
for command, want in [("/require 1 del", "/require 1 del all"), ("/exclude del", "/exclude del all"), ("/exclude 2 del", "/exclude 2 del all")]:
    check(f"{command:<16} → 전부 빼기도 안내", want in run(command), run(command))

print("\n=== 8. 되돌리는 명령은 한 번에 넣을 수 있는 만큼 끊는다 ===")
words = [f"말{n:02}" for n in range(25)]
store.add_global_excludes(words, validate=load)
total = len(store.global_excludes())
reply = run("/exclude del all")
commands = restore_commands(reply)
check("두 줄로 나뉨", len(commands) == 2, commands)
check("한 줄에 20개까지", all(len(c.split()) - 2 <= 20 for c in commands), [len(c.split()) for c in commands])
for command in commands:
    back = run(command)
check("두 줄을 다 붙이면 전부 돌아옴", len(store.global_excludes()) == total, (len(store.global_excludes()), total, back[:80]))
run("/exclude del all")

# 띄어쓰기가 든 말은 손으로 적은 설정에만 있다. 명령에 섞으면 두 낱말로 쪼개진다.
CONFIG.write_text(LAYOUT.replace("  - C108\n", "  - C108\n  - まとめ 売り\n"), encoding="utf-8")
reply = run("/exclude del all")
check("띄어쓴 말도 빠짐", store.global_excludes() == [], store.global_excludes())
check("되돌리는 명령에는 안 섞음", restore_commands(reply) == ["/exclude add C108 アイキュー"], restore_commands(reply))
check("따로 알림", "config.yaml 에 직접" in reply and "まとめ 売り" in reply, reply[-200:])

print("\n=== 9. 도움말 ===")
check("/help 에 전역", "/exclude del all" in HELP)
check("/help 에 키워드별", "/exclude 2 del all" in HELP)
check("/help 에 필수 포함어", "/require 2 del all" in HELP)
check("/help 가 텔레그램 한 통에 들어감", len(HELP) < 4096, len(HELP))
reset()
core = CommandCore(load(), Controls(), FakeScheduler(), seen)
run("/exclude del all")
check("/exclude 사용법에도", "/exclude del all" in run("/exclude"), run("/exclude")[-300:])
check("/require 사용법에도", "/require 2 del all" in run("/require add x"))

print("\n=== 10. 디스코드 슬래시 명령 ===")
bot = DiscordBot("MTIz.x.y", 7, 9, core)
calls = []


async def fake_run(interaction, command, actions=()):
    calls.append(command)


bot._run = fake_run
CLEAR = app_commands.Choice(name="전부 빼기", value="clear")
DEL = app_commands.Choice(name="빼기", value="del")
for name in ("exclude", "require"):
    cmd = bot._tree.get_command(name)
    action = next(p for p in cmd.parameters if p.name == "action")
    values = [c.value for c in action.choices]
    check(f"/{name} 에 '전부 빼기'", "clear" in values and len(values) <= 25, values)


async def slash():
    exclude = bot._tree.get_command("exclude")
    require = bot._tree.get_command("require")
    for cmd, kwargs, want in [
        (exclude, {"action": CLEAR}, "/exclude del all"),
        (exclude, {"action": CLEAR, "number": 2}, "/exclude 2 del all"),
        (exclude, {"action": CLEAR, "words": "まとめ"}, "/exclude del all"),
        (exclude, {"action": DEL, "words": "まとめ"}, "/exclude del まとめ"),
        (exclude, {}, "/exclude"),
        (exclude, {"number": 2}, "/exclude 2"),
        (require, {"action": CLEAR, "number": 1}, "/require 1 del all"),
        (require, {"action": DEL, "number": 1, "words": "シャニマス"}, "/require 1 del シャニマス"),
        (require, {"number": 1}, "/require 1"),
    ]:
        await cmd.callback(SimpleNamespace(), **kwargs)
        shown = {k: (v.value if isinstance(v, app_commands.Choice) else v) for k, v in kwargs.items()}
        check(f"/{cmd.name} {shown} → {want}", calls[-1] == want, calls[-1])


asyncio.run(slash())

print("\n=== 11. 윈도우 줄바꿈 — 고쳐 쓸 때 빈 줄이 불어나지 않는다 ===")
# 배포판 견본은 CRLF 로 나간다. ruamel 이 주석만 \r\n 째 들고 있다가 텍스트 모드로
# 쓰이면서 주석마다 \r\r\n 이 생겼다. 이 파일의 1~4절도 처음엔 그것 때문에 실패했다.


def breaks(raw):
    return raw.count(b"\r\r\n"), raw.count(b"\r\n"), raw.count(b"\n")


def four_edits(newline):
    """같은 고쳐 쓰기를 네 번. 줄바꿈만 다른 두 파일이 같은 결과가 나와야 한다."""
    CONFIG.write_bytes(LAYOUT.replace("\n", newline).encode("utf-8"))
    store.clear_global_excludes(validate=load)
    store.add_global_excludes(["C108"], validate=load)
    store.remove_global_exclude("C108", validate=load)
    store.clear_keyword_words(1, "require", validate=load)
    return CONFIG.read_bytes()


on_windows = four_edits("\r\n")
on_server = four_edits("\n")
doubled, crlf, lf = breaks(on_windows)
check("CRLF 파일: 네 번 고쳐 써도 \\r\\r\\n 없음", doubled == 0, doubled)
check("CRLF 파일은 CRLF 그대로", crlf == lf, (crlf, lf))
check("LF 파일은 LF 그대로 (서버)", b"\r" not in on_server)
check("줄바꿈만 다르고 내용은 똑같음", on_windows.replace(b"\r\n", b"\n") == on_server)
check("  주석도 제자리", right_before("# 감시할 키워드. 이 주석은 keywords 앞에 남아야 한다.", "keywords:")
      and right_before("# price_max 위 주석", "price_max: 999999"))

# 이미 \r\r\n 이 박힌 파일. 그 자리는 빈 줄로 굳히고 더 불어나지 않게 한다.
CONFIG.write_bytes(LAYOUT.replace("\n", "\r\n").replace("설정\r\n", "설정\r\r\n").encode("utf-8"))
store.add_global_excludes(["スイパラ"], validate=load)
check("이미 생긴 \\r\\r\\n 도 정리", breaks(CONFIG.read_bytes())[0] == 0, breaks(CONFIG.read_bytes()))
load()

before = CONFIG.read_bytes()


def broken():
    raise ValueError("일부러 낸 오류")


try:
    store.clear_global_excludes(validate=broken)
    check("검증에 실패하면 되돌림", False, "통과해버림")
except KeywordStoreError:
    check("검증에 실패하면 원래 바이트 그대로", CONFIG.read_bytes() == before)

seen.close()
shutil.rmtree(TMP, ignore_errors=True)
print(f"\n{'=' * 54}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
