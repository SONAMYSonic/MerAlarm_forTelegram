# MerAlarm 을 Windows 작업 스케줄러에 등록해 로그인 시 자동으로 띄운다.
#
#   powershell -ExecutionPolicy Bypass -File scripts\register_task.ps1
#
# 해제하려면:
#
#   Unregister-ScheduledTask -TaskName MerAlarm -Confirm:$false

$ErrorActionPreference = "Stop"

$root = Split-Path -Parent $PSScriptRoot
$python = Join-Path $root ".venv\Scripts\pythonw.exe"

if (-not (Test-Path $python)) {
    throw "가상환경을 찾지 못했습니다: $python`n먼저 py -3.12 -m venv .venv 로 만드세요."
}

# pythonw.exe 는 콘솔 창을 띄우지 않는다. 로그는 logs\meralarm.log 에 남는다.
$action = New-ScheduledTaskAction -Execute $python -Argument "-m meralarm" -WorkingDirectory $root

$trigger = New-ScheduledTaskTrigger -AtLogOn

# 죽으면 1분 뒤 다시 띄우고, 실행 시간에 제한을 두지 않는다.
$settings = New-ScheduledTaskSettingsSet `
    -RestartInterval (New-TimeSpan -Minutes 1) `
    -RestartCount 999 `
    -ExecutionTimeLimit ([TimeSpan]::Zero) `
    -AllowStartIfOnBatteries `
    -DontStopIfGoingOnBatteries `
    -StartWhenAvailable

Register-ScheduledTask -TaskName "MerAlarm" -Action $action -Trigger $trigger `
    -Settings $settings -Description "메루카리 키워드 신착 알리미" -Force | Out-Null

Write-Output "등록 완료. 다음 로그인부터 자동 실행됩니다."
Write-Output ""
Write-Output "지금 바로 시작하려면:  Start-ScheduledTask -TaskName MerAlarm"
Write-Output "중지하려면:            Stop-ScheduledTask  -TaskName MerAlarm"
Write-Output "상태 확인:             Get-ScheduledTask   -TaskName MerAlarm"
