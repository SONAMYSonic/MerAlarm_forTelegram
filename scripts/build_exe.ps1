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

@"
MerAlarm - 메루카리 키워드 알리미
====================================

처음 쓰실 때
------------
1. MerAlarm.exe 를 두 번 눌러 실행합니다.
2. 창이 뜨면 안내에 따라 텔레그램 봇을 만들고 토큰을 붙여넣습니다.
3. 끝입니다. 이후로는 알림이 텔레그램으로 옵니다.

3분이면 끝나고, 파일을 직접 고칠 일은 없습니다.

쓰는 법
-------
설정은 전부 텔레그램에서 합니다. 봇에게 /help 를 보내보세요.

  /add 키워드          감시할 키워드 추가
  /add 키워드 -제외어   특정 단어가 든 상품은 빼기
  /list               지금 감시 중인 목록
  /del 번호           빼기
  /config             현재 설정 보기
  /set interval 60    감시 주기 바꾸기(초)
  /pause  /resume     잠시 멈추기 / 다시 시작
  /status             잘 돌고 있는지 확인

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
- 백신 프로그램이 경고할 수 있습니다. 이런 방식으로 만든 프로그램에서 흔한
  오탐입니다. 마음이 놓이지 않으시면 소스에서 직접 빌드하실 수 있습니다.

만든 파일들
-----------
config.yaml   설정 (텔레그램에서 바꾸는 게 더 편합니다)
.env          텔레그램 토큰. 남에게 보내지 마세요
data\         본 상품 기록
logs\         실행 기록. 문제가 생기면 여기를 보세요
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
