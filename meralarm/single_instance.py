"""같은 폴더에서 두 번 켜지는 것을 막는다.

두 개가 동시에 돌면 알림이 두 번 오고, 텔레그램 명령은 서로 뺏어가 응답이
오락가락한다. 처음 쓰는 사람은 창이 안 보인다고 다시 눌러보기 쉬워서 실제로
자주 일어난다.

파일 잠금을 쓴다. 프로세스가 죽으면 운영체제가 잠금을 알아서 풀어주므로,
강제 종료되거나 정전이 나도 잠금이 남아 다음 실행을 막는 일이 없다.
"""

import sys
from pathlib import Path


class AlreadyRunning(RuntimeError):
    pass


class SingleInstance:
    def __init__(self, path: Path) -> None:
        self._path = path
        self._handle = None

    def acquire(self) -> None:
        try:
            handle = open(self._path, "a+")
        except OSError as e:
            # 잠금 파일을 못 만드는 상황이면 중복 실행 검사만 건너뛴다.
            # 이것 때문에 프로그램 자체가 안 켜지는 편이 더 나쁘다.
            print(f"[알림] 중복 실행 검사를 건너뜁니다: {e}", file=sys.stderr)
            return

        try:
            if sys.platform == "win32":
                import msvcrt

                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            handle.close()
            raise AlreadyRunning from None

        self._handle = handle

    def release(self) -> None:
        if self._handle is None:
            return
        try:
            if sys.platform == "win32":
                import msvcrt

                self._handle.seek(0)
                msvcrt.locking(self._handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(self._handle.fileno(), fcntl.LOCK_UN)
        except OSError:
            pass
        finally:
            self._handle.close()
            self._handle = None
