"""실행 파일용 진입점.

PyInstaller 는 지정한 파일을 최상위 스크립트로 취급한다. `meralarm/__main__.py`
를 직접 넘기면 패키지 밖에서 실행되는 셈이 되어 `from .config import ...` 같은
상대 임포트가 전부 깨진다. 그래서 패키지를 정상적으로 불러오는 얇은 파일을 둔다.

평소에는 `python -m meralarm` 을 그대로 쓰면 되고, 이 파일로 실행해도 똑같다.
"""

from meralarm.__main__ import main

if __name__ == "__main__":
    main()
