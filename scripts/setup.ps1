# 새 컴퓨터(Windows)에서 MerAlarm 을 실행할 수 있게 준비한다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

Write-Output "== 1. Python 확인 =="
$python = $null
foreach ($candidate in @("py -3.12", "py -3", "python")) {
    $parts = $candidate.Split(" ")
    try {
        $version = & $parts[0] $parts[1..$parts.Length] --version 2>$null
        if ($version -match "Python 3\.(\d+)" -and [int]$Matches[1] -ge 10) {
            $python = $candidate
            Write-Output "   $version ($candidate)"
            break
        }
    } catch {}
}
if (-not $python) {
    throw "Python 3.10 이상을 찾지 못했습니다. https://www.python.org/downloads/ 에서 설치하세요."
}

Write-Output "== 2. 가상환경 만들기 =="
if (Test-Path ".venv") {
    Write-Output "   이미 있습니다. 건너뜁니다."
} else {
    $parts = $python.Split(" ")
    & $parts[0] $parts[1..$parts.Length] -m venv .venv
    Write-Output "   .venv 생성 완료"
}

Write-Output "== 3. 의존성 설치 =="
& ".\.venv\Scripts\python.exe" -m pip install --upgrade pip --quiet
& ".\.venv\Scripts\python.exe" -m pip install -r requirements.txt --quiet
Write-Output "   완료"

Write-Output "== 4. 설정 파일 =="
if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Output "   .env 를 만들었습니다. 텔레그램 토큰을 채워야 합니다."
} else {
    Write-Output "   .env 가 이미 있습니다."
}

# 단일 따옴표 here-string. 안에 든 @ 나 $ 를 PowerShell 이 해석하지 않는다.
Write-Output @'

준비 완료. 다음 순서로 진행하세요.

  1) .env 에 TELEGRAM_BOT_TOKEN 을 채운다
     (텔레그램에서 @BotFather 검색 -> /newbot,
      만든 봇에게 아무 메시지나 한 번 보낼 것)
  2) .venv\Scripts\python.exe telegram_check.py
     - chat_id 를 찾아주고 테스트 알림을 보낸다
  3) .env 에 안내된 TELEGRAM_CHAT_ID 를 채운다
  4) config.yaml 에서 감시할 키워드를 확인한다
  5) .venv\Scripts\python.exe -m meralarm

자동 시작 등록:
  powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
'@
