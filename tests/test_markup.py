"""HTML → 디스코드 마크다운 변환 검증."""

import sys

from pathlib import Path
REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

from meralarm.notifiers.markup import LIMIT, chunks, from_html

ok = fail = 0


def check(label, got, want):
    global ok, fail
    if got == want:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label}\n       기대 {want!r}\n       실제 {got!r}")


print("\n=== 1. 기본 태그 ===")
check("굵게", from_html("<b>굵게</b>"), "**굵게**")
check("기울임", from_html("<i>기울임</i>"), "*기울임*")
check("코드", from_html("<code>/set age 7</code>"), "`/set age 7`")
check("취소선", from_html("<s>¥5,000</s>"), "~~¥5,000~~")
check("링크", from_html('<a href="https://x/1">상품 보기</a>'), "[상품 보기](https://x/1)")

print("\n=== 2. HTML 엔티티가 원래 글자로 ===")
check("앰퍼샌드", from_html("A &amp; B"), "A & B")
check("꺾쇠", from_html("&lt;태그&gt;"), "<태그>")

print("\n=== 3. 사용자 글이 서식으로 읽히지 않는다 ===")
check("별표", from_html("<b>A*B</b>"), "**A\\*B**")
check("밑줄", from_html("키워드_이름"), "키워드\\_이름")
check("물결", from_html("가격 ¥100~¥500"), "가격 ¥100\\~¥500")
check("파이프", from_html("A||B"), "A\\|\\|B")
check("역슬래시", from_html("C:\\경로"), "C:\\\\경로")

print("\n=== 4. 링크 글자의 대괄호 ===")
check(
    "대괄호 이스케이프",
    from_html('<a href="https://x/1">[PSA10] 카드</a>'),
    "[\\[PSA10\\] 카드](https://x/1)",
)

print("\n=== 5. 코드 구간 안은 그대로 ===")
check("별표 안 건드림", from_html("<code>a*b</code>"), "`a*b`")
check("백틱은 바꿔서 구간 안 깨짐", from_html("<code>a`b</code>"), "`aˋb`")

print("\n=== 6. 실제 응답 모양 ===")
real = (
    "<b>🔍 감시 중</b>  <i>v1.1.2</i>\n\n"
    "가동 12.3시간\n"
    "키워드 3개 · 기본 주기 30초\n"
    '<a href="https://jp.mercari.com/item/m1">🛒 상품 보기</a>'
)
got = from_html(real)
check(
    "상태 응답",
    got,
    "**🔍 감시 중**  *v1.1.2*\n\n"
    "가동 12.3시간\n"
    "키워드 3개 · 기본 주기 30초\n"
    "[🛒 상품 보기](https://jp.mercari.com/item/m1)",
)

print("\n=== 7. 실제 HELP 와 /config 가 통과하는지 ===")
from meralarm.commands import HELP

converted = from_html(HELP)
check("HELP 에 태그 잔재 없음", "<b>" in converted or "</" in converted, False)
print(f"       HELP {len(HELP)}자 → {len(converted)}자, {len(chunks(converted))}통")

print("\n=== 8. 길면 나눠 보낸다 ===")
short = "짧은 글"
check("짧으면 한 통", chunks(short), [short])

long_text = "\n".join(f"{i}. 키워드 이름 {i}" for i in range(400))
parts = chunks(long_text)
check("모든 조각이 한도 이내", all(len(p) <= LIMIT for p in parts), True)
check("내용이 하나도 안 사라짐", "\n".join(parts), long_text)
print(f"       {len(long_text)}자 → {len(parts)}통")

one_line = "가" * 5000
parts = chunks(one_line)
check("줄바꿈 없는 긴 줄도 쪼갬", all(len(p) <= LIMIT for p in parts), True)
check("긴 줄 내용 보존", "".join(parts), one_line)

print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
