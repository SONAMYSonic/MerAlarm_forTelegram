#!/usr/bin/env bash
# MerAlarm 을 systemd 서비스로 등록한다. (라즈베리파이 / 리눅스 서버)
#
#   bash scripts/install_service.sh
#
# 해제하려면:
#   sudo systemctl disable --now meralarm
#   sudo rm /etc/systemd/system/meralarm.service

set -euo pipefail
cd "$(dirname "$0")/.."
ROOT="$(pwd)"

if [ ! -x "$ROOT/.venv/bin/python" ]; then
    echo "가상환경이 없습니다. 먼저 bash scripts/setup.sh 를 실행하세요." >&2
    exit 1
fi
if [ ! -f "$ROOT/.env" ]; then
    echo ".env 가 없습니다. 먼저 설정을 마치세요." >&2
    exit 1
fi

UNIT=/etc/systemd/system/meralarm.service

sudo tee "$UNIT" >/dev/null <<EOF
[Unit]
Description=MerAlarm - 메루카리 키워드 신착 알리미
After=network-online.target
Wants=network-online.target

[Service]
Type=simple
User=$USER
WorkingDirectory=$ROOT
ExecStart=$ROOT/.venv/bin/python -m meralarm
# 죽으면 30초 뒤 되살린다. 무인 운영에서 조용히 멈춰 있는 것이 최악이다.
Restart=always
RestartSec=30
StandardOutput=null
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

sudo systemctl daemon-reload
sudo systemctl enable --now meralarm

echo
echo "등록 완료. 부팅할 때마다 자동으로 뜹니다."
echo
echo "  상태 확인:  systemctl status meralarm"
echo "  로그 보기:  tail -f $ROOT/logs/meralarm.log"
echo "  재시작:     sudo systemctl restart meralarm"
echo "  중지:       sudo systemctl stop meralarm"
