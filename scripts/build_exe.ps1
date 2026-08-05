# Python 설치 없이 돌아가는 실행 파일을 만든다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\build_exe.ps1
#
# 결과물은 dist\MerAlarm\ 폴더에 만들어지고, 같은 이름의 zip 으로도 묶인다.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

$python = ".\.venv\Scripts\python.exe"
if (-not (Test-Path $python)) { throw "가상환경이 없습니다. 먼저 scripts\setup.ps1 을 실행하세요." }

& $python -m PyInstaller --version 2>$null | Out-Null
if ($LASTEXITCODE -ne 0) {
    Write-Output "PyInstaller 를 설치합니다..."
    & $python -m pip install pyinstaller --quiet
}

Write-Output "== 이전 결과물 정리 =="
foreach ($d in @("build", "dist")) {
    if (Test-Path $d) { Remove-Item $d -Recurse -Force }
}

Write-Output "== 실행 파일 만들기 (몇 분 걸립니다) =="
# 콘솔을 남긴다. 설정 마법사가 물어보려면 입력창이 있어야 하고,
# 로그가 흐르는 게 보이는 편이 처음 쓰는 사람에게 안심이 된다.
& $python -m PyInstaller `
    --onefile `
    --console `
    --name MerAlarm `
    --hidden-import "pystray._win32" `
    --collect-submodules mercapi `
    --collect-submodules meralarm `
    --collect-submodules discord `
    --noconfirm `
    --clean `
    main.py

if ($LASTEXITCODE -ne 0) { throw "빌드에 실패했습니다." }

Write-Output "== 함께 넣을 파일 챙기기 =="
$out = "dist\MerAlarm"
New-Item -ItemType Directory $out -Force | Out-Null
Move-Item "dist\MerAlarm.exe" "$out\MerAlarm.exe" -Force

# 사용자가 직접 고칠 수 있어야 하므로 exe 안에 넣지 않고 옆에 둔다.
Copy-Item "config.example.yaml" "$out\config.yaml"
Copy-Item "config.example.yaml" "$out\config.example.yaml"

# --setup 은 명령줄 옵션이라 더블클릭으로는 실행할 수 없다. 설정을 다시 하거나
# 디스코드를 연결하려는 사람이 명령 프롬프트를 열게 만들 수는 없으므로 배치 파일로 싼다.
#
# 배치 파일은 줄바꿈이 반드시 CRLF 여야 한다. LF 로 두면 cmd 가 줄을 제대로 나누지
# 못해 "MerAlarm.exe 를 찾을 수 없습니다" 로 끝난다. ps1 이나 sh 와 달리 여기만 그렇다.
$bat = @'
@echo off
cd /d "%~dp0"
"%~dp0MerAlarm.exe" --setup
echo.
pause
'@ -replace "`r?`n", "`r`n"
[System.IO.File]::WriteAllText(
    (Join-Path $out "설정 다시하기.bat"), $bat, [System.Text.Encoding]::ASCII)

@"
MerAlarm - 메루카리 키워드 알리미
====================================

처음 쓰실 때
------------
1. 압축을 풀고 MerAlarm.exe 를 두 번 누릅니다.
   (바탕화면이나 문서 폴더처럼 쓰기가 되는 곳에 풀어주세요)
2. 창이 뜨면 안내에 따라 텔레그램 봇을 만들고 토큰을 붙여넣습니다.
3. 끝입니다. 이후로는 알림이 텔레그램으로 옵니다.

3분이면 끝나고, 파일을 직접 고칠 일은 없습니다.

  [!] "Windows의 PC 보호" 창이 뜨면
      개발자 서명이 없는 프로그램이라 나오는 경고입니다.
      "추가 정보" 를 누른 뒤 "실행" 을 누르면 됩니다.

쓰는 법
-------
설정은 봇에게 명령을 보내서 합니다. /help 를 보내보세요.
텔레그램이든 디스코드든 명령은 똑같습니다.

  /add 키워드          감시할 키워드 추가
  /add 키워드 -제외어   특정 단어가 든 상품은 빼기
  /list               지금 감시 중인 목록
  /del 번호           빼기
  /config             현재 설정 전부 보기
  /set 항목 값         설정 바꾸기
  /pause  /resume     잠시 멈추기 / 다시 시작
  /status             잘 돌고 있는지 확인

바꿀 수 있는 설정 (/set 만 쳐도 목록이 나옵니다)

  /set interval 60        감시 주기(초)
  /set age 7              출품 7일 이내 상품만
  /set bump 0             끌어올린 상품은 새 매물로 안 침
  /set krw off            원화 환산 끄기
  /set 2 price_max 50000  2번 키워드만 가격 상한
  /set 2 price_max off    해제

age 와 bump 의 차이
  age  = 출품한 지 며칠 됐나
  bump = 출품하고 며칠 뒤에 갱신됐나

  메루카리는 판매자가 상품을 손대면 검색 위로 다시 떠오릅니다.
  그것을 새 매물이라고 알리지 않으려고 bump 를 씁니다.

  5일 전에 올려두고 그대로인 물건   -> age 5, bump 0 (늦게 발견한 새 매물)
  5일 전에 올리고 오늘 끌어올린 물건 -> age 5, bump 5 (이미 있던 물건)

  둘 다 "새 매물 알림"에만 적용됩니다.
  걸러진 상품도 값을 내리면 그때 알려드립니다.

디스코드로도 받고 싶다면
------------------------
"설정 다시하기.bat" 을 두 번 누르면 두 가지 중에 고르라고 물어봅니다.

  웹훅  URL 하나만 붙여넣으면 끝. 알림만 옵니다        (1분, 쉬움)
  봇    알림에 더해 디스코드에서도 명령을 씁니다        (3~5분)

디스코드는 무료입니다. 봇을 만들어도 돈이 들지 않습니다.

[웹훅으로 하려면]
1. 알림 받을 채널의 톱니(채널 편집)를 누릅니다
2. 왼쪽 메뉴에서 "연동" -> "웹후크" -> "새 웹후크"
3. "웹후크 URL 복사" 를 누릅니다
4. "설정 다시하기.bat" 에서 2번을 고르고 붙여넣습니다

[봇으로 하려면]
1. https://discord.com/developers/applications 를 엽니다
2. 오른쪽 위 "New Application" -> 이름을 짓고 만듭니다
3. 왼쪽 메뉴 "Bot" -> "Reset Token" -> Yes -> 나온 값을 복사합니다
   (토큰은 그때 한 번만 보입니다. 놓치면 다시 Reset 하세요)
4. "설정 다시하기.bat" 에서 3번을 고르고 붙여넣습니다
5. 화면에 뜨는 초대 주소를 브라우저에 붙여넣고 내 서버를 고릅니다
6. 알림 받을 채널을 고르면 끝입니다

  [!] 봇 토큰은 비밀번호와 같습니다. 남에게 보내지 마세요.
  [!] 명령은 서버를 만든 사람만 쓸 수 있습니다. 다른 사람이 쳐도
      "주인만 조작할 수 있습니다" 가 뜨고 설정은 바뀌지 않습니다.

봇으로 하면 텔레그램 없이 디스코드만으로도 쓸 수 있습니다.
둘 다 설정하면 알림은 양쪽으로 오고, 명령은 어느 쪽에서 쳐도 됩니다.
(웹훅과 봇을 둘 다 넣어도 알림이 두 번 오지는 않습니다. 봇만 씁니다)

끄는 법
-------
창을 닫거나, 작업 표시줄 오른쪽 아래 종 모양 아이콘을 우클릭해 종료를 누릅니다.
(아이콘이 안 보이면 ^ 를 눌러 숨겨진 아이콘을 펼치세요)

컴퓨터를 켤 때마다 자동으로 띄우려면
------------------------------------
MerAlarm.exe 바로가기를 만들어 아래 폴더에 넣으세요.
  Win+R -> shell:startup -> Enter

알아두면 좋은 것
----------------
- 처음 켠 직후 한 번은 알림이 오지 않습니다. 지금 올라와 있는 상품을 조용히
  기억해 두고, 그다음에 새로 올라오는 것부터 알려드립니다.
- 키워드를 새로 추가할 때도 마찬가지입니다. 알림이 쏟아지지 않습니다.
- 두 번 실행되지 않습니다. 창이 안 보인다고 다시 눌러도 "이미 실행 중"이라고
  알려주고 끝납니다. 트레이 아이콘을 확인하세요.
- 감시 주기를 무리하게 줄여도 얻는 게 없습니다. 한 번에 120건씩 확인하므로
  30~60초면 놓치지 않습니다.
- 백신 프로그램이 경고할 수 있습니다. 이런 방식으로 만든 프로그램에서 흔한
  오탐입니다. 마음이 놓이지 않으시면 소스에서 직접 빌드하실 수 있습니다.

문제가 생기면
-------------
logs\meralarm.log 를 열어보세요. 무엇을 언제 확인했는지 다 남아 있습니다.

  "이 폴더에 파일을 만들 수 없습니다"
      압축을 풀고 실행했는지, 바탕화면 같은 곳에 두었는지 확인하세요.

  "이미 실행 중입니다"
      트레이 아이콘을 확인하세요. 이미 돌고 있습니다.

  알림이 안 옵니다
      /status 로 살아있는지 확인하세요. 첫 회차는 원래 조용합니다.

  디스코드에서 슬래시 명령이 안 보입니다
      프로그램을 한 번 켜야 명령이 등록됩니다. 켠 뒤에도 안 보이면
      디스코드를 Ctrl+R 로 새로고침하세요.

  디스코드에서 "주인만 조작할 수 있습니다" 가 뜹니다
      서버를 만든 사람만 명령을 쓸 수 있습니다. 다른 계정으로 쓰시려면
      "설정 다시하기.bat" 에서 그 사람의 사용자 ID 를 넣어주세요.

  원하는 상품이 안 옵니다
      /config 로 조건을 확인하세요.
      출품이 오래됐으면 /set age, 끌어올린 것이면 /set bump 를 조정합니다.

  원하지 않는 상품이 옵니다
      끌어올린 것이면 /set bump 0
      특정 단어 때문이면 /del 로 지운 뒤 /add 키워드 -단어 로 다시 넣으세요.

들어있는 파일
-------------
MerAlarm.exe        본체. 이것을 두 번 누르면 시작합니다
설정 다시하기.bat    텔레그램 봇을 다시 잡거나 디스코드를 연결할 때
config.yaml         설정 (텔레그램에서 바꾸는 게 더 편합니다)
config.example.yaml 설정을 처음 상태로 되돌리고 싶을 때 참고용

실행하면 아래가 만들어집니다
  .env      봇 토큰. 남에게 보내지 마세요
  data\     본 상품과 가격 기록
  logs\     실행 기록
"@ | Out-File "$out\사용법.txt" -Encoding UTF8

Write-Output "== 압축 =="
# 실행해 보고 나서 다시 압축하는 경우가 있다. 그때 생긴 것들이 배포본에
# 섞이면 받는 사람이 남의 기록과 잠금 파일을 함께 받게 된다.
foreach ($junk in @(".env", ".meralarm.lock", "data", "logs")) {
    Remove-Item (Join-Path $out $junk) -Recurse -Force -ErrorAction SilentlyContinue
}

# 파일 이름에 버전을 넣어야 받은 사람이 무엇을 갖고 있는지 알 수 있다.
$version = (Select-String -Path "meralarm\__init__.py" -Pattern '__version__\s*=\s*"([^"]+)"').Matches[0].Groups[1].Value
$zip = "dist\MerAlarm-v$version.zip"
Compress-Archive -Path "$out\*" -DestinationPath $zip -Force

$exeMb = [math]::Round((Get-Item "$out\MerAlarm.exe").Length / 1MB, 1)
$zipMb = [math]::Round((Get-Item $zip).Length / 1MB, 1)

Write-Output ""
Write-Output "완성했습니다."
Write-Output "  실행 파일 : $root\$out\MerAlarm.exe  ($exeMb MB)"
Write-Output "  배포용 zip: $root\$zip  ($zipMb MB)"
Write-Output ""
Write-Output "zip 을 통째로 전달하면 받는 사람은 압축을 풀고 exe 를 두 번 누르기만 하면 됩니다."
