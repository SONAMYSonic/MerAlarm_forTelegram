"""일반 사용자용 안전장치 검증."""

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import meralarm.config as c
from meralarm.config_store import EXAMPLE_KEYWORD, KeywordStore
from meralarm.single_instance import AlreadyRunning, SingleInstance

SRC = REPO
ok = fail = 0


def check(label, cond, detail=""):
    global ok, fail
    if cond:
        ok += 1
        print(f"  [OK] {label}")
    else:
        fail += 1
        print(f"  [NG] {label} {detail}")


print("\n=== 1. 중복 실행 차단 ===")
tmp = Path(tempfile.mkdtemp())
lock_path = tmp / ".lock"
first = SingleInstance(lock_path)
first.acquire()
check("첫 번째는 잠금 획득", first._handle is not None)

second = SingleInstance(lock_path)
try:
    second.acquire()
    check("두 번째는 거부되어야 함", False, "잠겼는데 통과함")
except AlreadyRunning:
    check("두 번째는 거부됨", True)

first.release()
third = SingleInstance(lock_path)
try:
    third.acquire()
    check("놓아주면 다시 잡힘", True)
    third.release()
except AlreadyRunning:
    check("놓아주면 다시 잡힘", False, "여전히 잠김")

print("\n=== 2. 쓰기 안 되는 폴더 안내 ===")
ro = Path(tempfile.mkdtemp()) / "ro"
ro.mkdir()
subprocess.run(["icacls", str(ro), "/inheritance:r"], capture_output=True)
subprocess.run(["icacls", str(ro), "/grant:r", f"{os.environ['USERNAME']}:(RX)"],
               capture_output=True)
try:
    c.ensure_writable(ro)
    check("쓰기 불가를 잡아냄", False, "통과해버림")
except c.ConfigError as e:
    msg = str(e)
    check("쓰기 불가를 잡아냄", True)
    check("원인 안내 포함", "압축을 풀지 않고" in msg and "바탕화면" in msg)
    print(f"        └ {msg.splitlines()[0]}")
subprocess.run(["icacls", str(ro), "/grant", f"{os.environ['USERNAME']}:(F)"], capture_output=True)

good = Path(tempfile.mkdtemp())
try:
    c.ensure_writable(good)
    check("쓰기 되는 폴더는 통과", True)
except c.ConfigError:
    check("쓰기 되는 폴더는 통과", False)
check("검사 파일은 남지 않음", not (good / ".write_test").exists())

print("\n=== 3. 예시 키워드 자동 정리 ===")
work = Path(tempfile.mkdtemp())
shutil.copy(SRC / "config.example.yaml", work / "config.yaml")
store = KeywordStore(work / "config.yaml")
check("배포본에 예시가 있음", EXAMPLE_KEYWORD in store.names(), f"{store.names()}")

store.add("내키워드")
names = store.names()
check("사용자 키워드 추가됨", "내키워드" in names)
store.remove(names.index(EXAMPLE_KEYWORD) + 1)
after = store.names()
check("예시가 지워짐", EXAMPLE_KEYWORD not in after, f"{after}")
check("사용자 것만 남음", after == ["내키워드"], f"{after}")

print("\n=== 4. 마법사가 실제로 그렇게 하는지 ===")
work2 = Path(tempfile.mkdtemp())
shutil.copy(SRC / "config.example.yaml", work2 / "config.yaml")
import io
from unittest.mock import patch

from meralarm import setup_wizard as w

with patch("builtins.input", return_value="樋口円香"):
    with patch("sys.stdout", new=io.StringIO()):
        w._ask_keyword(work2 / "config.yaml")
result = KeywordStore(work2 / "config.yaml").names()
check("마법사가 예시를 치움", result == ["樋口円香"], f"{result}")

work3 = Path(tempfile.mkdtemp())
shutil.copy(SRC / "config.example.yaml", work3 / "config.yaml")
buf = io.StringIO()
with patch("builtins.input", return_value=""):
    with patch("sys.stdout", new=buf):
        w._ask_keyword(work3 / "config.yaml")
kept = KeywordStore(work3 / "config.yaml").names()
check("건너뛰면 예시는 남김", kept == [EXAMPLE_KEYWORD], f"{kept}")
check("남았다고 경고함", "그대로 남아" in buf.getvalue(), buf.getvalue()[:80])

print(f"\n{'=' * 46}\n통과 {ok} · 실패 {fail}")
sys.exit(1 if fail else 0)
