# 서버로 보낼 파일만 골라 하나로 묶는다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\pack.ps1
#
# .venv 와 logs 는 옮겨봐야 쓸 수 없고, .env 는 토큰이 들어 있어 따로 옮긴다.
#
# zip 이 아니라 tar.gz 를 쓴다. PowerShell 의 Compress-Archive 는 경로 구분자를
# 백슬래시로 넣어서, 리눅스에서 풀면 폴더가 만들어지지 않고 이름에 백슬래시가
# 박힌 파일이 생긴다. tar 는 양쪽 모두 기본 탑재이고 그런 문제가 없다.

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
Set-Location $root

if (-not (Get-Command tar -ErrorAction SilentlyContinue)) {
    throw "tar 를 찾지 못했습니다. Windows 10 1803 이상이 필요합니다."
}

$out = "MerAlarm-deploy.tar.gz"
if (Test-Path $out) { Remove-Item $out -Force }

# 서버에서 필요한 것만 담는다.
$include = @("meralarm", "scripts", "config.yaml", "requirements.txt", ".env.example", "README.md")
$missing = $include | Where-Object { -not (Test-Path $_) }
if ($missing) { throw "다음 파일이 없습니다: $($missing -join ', ')" }

tar --exclude="__pycache__" -czf $out $include
if ($LASTEXITCODE -ne 0) { throw "묶는 데 실패했습니다." }

$sizeKb = [math]::Round((Get-Item $out).Length / 1KB)
$full = Join-Path $root $out

Write-Output ""
Write-Output "만들었습니다: $full  ($sizeKb KB)"
Write-Output ""
Write-Output "담긴 내용:"
tar -tzf $out | Select-Object -First 8 | ForEach-Object { Write-Output "  $_" }
Write-Output "  ..."
Write-Output ""
Write-Output "이제 서버로 보내세요. <개인키>와 <공용IP>는 본인 값으로 바꾸세요."
Write-Output "  scp -i <개인키> `"$full`" ubuntu@<공용IP>:~/"
