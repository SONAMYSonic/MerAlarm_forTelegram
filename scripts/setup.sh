#!/usr/bin/env bash
# 새 컴퓨터(Linux / macOS / 라즈베리파이)에서 MerAlarm 을 실행할 수 있게 준비한다.
#
#   bash scripts/setup.sh

set -euo pipefail
cd "$(dirname "$0")/.."

echo "== 1. Python 확인 =="
PYTHON=""
for candidate in python3.12 python3.11 python3.10 python3; do
    if command -v "$candidate" >/dev/null 2>&1; then
        version=$("$candidate" -c 'import sys; print(sys.version_info[0]*100+sys.version_info[1])')
        if [ "$version" -ge 310 ]; then
            PYTHON="$candidate"
            echo "   $($candidate --version) ($candidate)"
            break
        fi
    fi
done
if [ -z "$PYTHON" ]; then
    echo "Python 3.10 이상을 찾지 못했습니다." >&2
    echo "  데비안/우분투/라즈베리파이:  sudo apt install python3 python3-venv" >&2
    exit 1
fi

echo "== 2. 가상환경 만들기 =="
if [ -d .venv ]; then
    echo "   이미 있습니다. 건너뜁니다."
else
    "$PYTHON" -m venv .venv
    echo "   .venv 생성 완료"
fi

echo "== 3. 의존성 설치 =="
./.venv/bin/python -m pip install --upgrade pip --quiet
./.venv/bin/python -m pip install -r requirements.txt --quiet
echo "   완료"

echo "== 4. 설정 파일 =="
if [ ! -f .env ]; then
    cp .env.example .env
    chmod 600 .env   # 토큰이 들어가므로 남이 못 읽게 한다
    echo "   .env 를 만들었습니다. 텔레그램 토큰을 채워야 합니다."
else
    echo "   .env 가 이미 있습니다."
fi

cat <<'EOF'

준비 완료. 다음 순서로 진행하세요.

  1) .env 에 TELEGRAM_BOT_TOKEN 을 채운다
     (@BotFather 에서 /newbot, 만든 봇에게 아무 메시지나 한 번 보낼 것)
  2) ./.venv/bin/python telegram_check.py
     - chat_id 를 찾아주고 테스트 알림을 보낸다
  3) .env 에 안내된 TELEGRAM_CHAT_ID 를 채운다
  4) config.yaml 에서 감시할 키워드를 확인한다
  5) ./.venv/bin/python -m meralarm

계속 켜두려면:  bash scripts/install_service.sh
EOF
