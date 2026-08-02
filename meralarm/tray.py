"""작업 표시줄 트레이 아이콘.

`pythonw.exe` 로 띄우면 콘솔 창이 없어서 끌 방법이 마땅치 않다. 트레이 아이콘이
있으면 돌고 있다는 사실이 눈에 보이고, 우클릭 한 번으로 멈추거나 끌 수 있다.

pystray 가 없거나 GUI 가 없는 환경(라즈베리파이 서버 등)이면 조용히 비활성화되고
감시는 그대로 진행된다.
"""

import logging
import os
import subprocess
import sys
import threading
from pathlib import Path

from .control import Controls

log = logging.getLogger(__name__)

RUNNING = (0x2E, 0xA0, 0x43)  # 초록
PAUSED = (0xE0, 0x9F, 0x1E)  # 주황


def _make_icon(color: tuple[int, int, int]):
    from PIL import Image, ImageDraw

    size = 64
    image = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(image)
    draw.ellipse((2, 2, size - 2, size - 2), fill=color)

    # 종 모양. 폰트에 기대면 환경마다 깨지므로 도형으로 그린다.
    white = (255, 255, 255, 255)
    draw.pieslice((18, 16, 46, 44), start=180, end=360, fill=white)
    draw.rectangle((18, 30, 46, 42), fill=white)
    draw.rectangle((14, 42, 50, 46), fill=white)
    draw.ellipse((28, 46, 36, 54), fill=white)
    return image


class Tray:
    """감시 루프와 같은 프로세스 안에서 별도 스레드로 돈다."""

    def __init__(self, controls: Controls, log_path: Path, keywords: list[str]) -> None:
        self._controls = controls
        self._log_path = log_path
        self._keywords = keywords
        self._icon = None
        self._thread: threading.Thread | None = None

    def start(self) -> bool:
        """띄우는 데 성공하면 True. 실패해도 감시는 계속되어야 한다."""
        # 화면 없는 서버(라즈베리파이·VPS)에서는 시도조차 하지 않는다.
        if sys.platform.startswith("linux") and not (
            os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY")
        ):
            log.info("화면이 없는 환경이라 트레이 아이콘 없이 실행합니다.")
            return False

        try:
            import pystray
        except ImportError:
            log.info("pystray 가 없어 트레이 아이콘 없이 실행합니다.")
            return False

        try:
            self._icon = pystray.Icon(
                "meralarm",
                icon=_make_icon(RUNNING),
                title=self._tooltip(),
                menu=pystray.Menu(
                    pystray.MenuItem(self._status_text, None, enabled=False),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem(
                        "일시정지", self._on_toggle, checked=lambda _: self._controls.paused
                    ),
                    pystray.MenuItem("로그 열기", self._on_open_log),
                    pystray.Menu.SEPARATOR,
                    pystray.MenuItem("종료", self._on_quit),
                ),
            )
        except Exception:
            log.warning("트레이 아이콘을 만들지 못해 없이 실행합니다.", exc_info=True)
            return False

        self._thread = threading.Thread(target=self._run_icon, name="tray", daemon=True)
        self._thread.start()
        log.info("트레이 아이콘 실행. 작업 표시줄에서 우클릭하면 정지·종료할 수 있습니다.")
        return True

    def _run_icon(self) -> None:
        # 아이콘 쪽에서 터지더라도 감시는 계속되어야 한다.
        try:
            self._icon.run()
        except Exception:
            log.warning("트레이 아이콘이 중단됐습니다. 감시는 계속됩니다.", exc_info=True)

    def _tooltip(self) -> str:
        state = "일시정지" if self._controls.paused else "감시 중"
        return f"MerAlarm — {state}\n키워드 {len(self._keywords)}개"

    def _status_text(self, _item=None) -> str:
        state = "⏸ 일시정지됨" if self._controls.paused else "🔍 감시 중"
        return f"{state} · 키워드 {len(self._keywords)}개"

    def _refresh(self) -> None:
        if self._icon is None:
            return
        self._icon.icon = _make_icon(PAUSED if self._controls.paused else RUNNING)
        self._icon.title = self._tooltip()
        self._icon.update_menu()

    def _on_toggle(self, _icon=None, _item=None) -> None:
        paused = self._controls.toggle_pause()
        log.info("트레이: %s", "일시정지" if paused else "감시 재개")
        self._refresh()

    def _on_open_log(self, _icon=None, _item=None) -> None:
        try:
            if sys.platform == "win32":
                os.startfile(self._log_path)  # noqa: S606
            elif sys.platform == "darwin":
                subprocess.Popen(["open", str(self._log_path)])
            else:
                subprocess.Popen(["xdg-open", str(self._log_path)])
        except Exception:
            log.warning("로그 파일을 열지 못했습니다: %s", self._log_path, exc_info=True)

    def _on_quit(self, _icon=None, _item=None) -> None:
        log.info("트레이: 종료 요청")
        self._controls.stop()
        if self._icon is not None:
            self._icon.stop()

    def stop(self) -> None:
        if self._icon is not None:
            self._icon.stop()
