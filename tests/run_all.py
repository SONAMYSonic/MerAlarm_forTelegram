"""검증 스크립트를 전부 돌려 건수를 합친다.

    python tests/run_all.py

각 파일은 혼자서도 돈다(python tests/test_filters.py). 일부러 pytest 를 쓰지 않았다 —
파일마다 무엇을 왜 확인하는지 한국어로 줄줄이 찍어 두는 편이, 나중에 다시 열어봤을 때
"이게 왜 실패하지"를 훨씬 빨리 풀게 해준다.

파일 하나가 실제 메루카리·텔레그램에 묻는 것을 빼면 전부 임시 폴더에서 돈다.
**실제 config.yaml 이나 data/ 를 건드리는 것은 없다.**
"""

import os
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent

env = dict(os.environ, PYTHONIOENCODING="utf-8")
files = sorted(HERE.glob("test_*.py")) + sorted(HERE.glob("verify_*.py"))

total = fails = broken = 0
for path in files:
    try:
        result = subprocess.run(
            [sys.executable, str(path)], capture_output=True, text=True,
            encoding="utf-8", errors="replace", env=env, cwd=HERE, timeout=300,
        )
        out = result.stdout + result.stderr
        code = result.returncode
    except subprocess.TimeoutExpired:
        out, code = "시간 초과 (300초)", -1

    match = re.search(r"통과 (\d+) · 실패 (\d+)", out)
    if match:
        passed, failed = int(match.group(1)), int(match.group(2))
        total += passed
        fails += failed
        mark = "OK  " if failed == 0 and code == 0 else "FAIL"
        print(f"  {mark} {path.name:<26} {passed:>4}건" + (f"  실패 {failed}" if failed else ""))
        if failed:
            for line in out.splitlines():
                if "[NG]" in line:
                    print(f"        {line.strip()}")
    else:
        broken += 1
        print(f"  ERR  {path.name:<26} 결과를 못 읽음 (exit {code})")
        print("\n".join(f"        {x}" for x in out.strip().splitlines()[-6:]))

print(f"\n{'=' * 54}")
print(f"통과 {total}건 · 실패 {fails}건 · 못 돌린 파일 {broken}개")
sys.exit(1 if fails or broken else 0)
