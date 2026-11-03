# DAPENG Alpha · 部署加固脚本（护城河 · 部署层）
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install-moat.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\install-moat.ps1 -Uninstall
#
# 优先注册三个计划任务（需要管理员权限）：
#   DAPENG-Alpha-Server / DAPENG-Alpha-Watchdog / DAPENG-Alpha-Backup
# 无管理员权限时自动降级为「当前用户启动文件夹」方案：
#   %APPDATA%\Microsoft\Windows\Start Menu\Programs\Startup\DAPENG-Alpha.vbs
#   登录时隐藏启动 watchdog 常驻循环（内含每日备份），无需管理员，同样生效。

param([switch]$Uninstall)

$ErrorActionPreference = 'Continue'
$root = Split-Path -Parent $PSScriptRoot
$py = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $py) { $py = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe' }
$ps = 'powershell.exe'
$startupDir = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Startup'
$vbsPath = Join-Path $startupDir 'DAPENG-Alpha.vbs'

$serverName = 'DAPENG-Alpha-Server'
$watchName = 'DAPENG-Alpha-Watchdog'
$backupName = 'DAPENG-Alpha-Backup'

function Remove-TaskSafe([string]$name) {
  $t = Get-ScheduledTask -TaskName $name -ErrorAction SilentlyContinue
  if ($t) {
    Unregister-ScheduledTask -TaskName $name -Confirm:$false -ErrorAction SilentlyContinue
    Write-Output "removed task: $name"
  }
}

if ($Uninstall) {
  Remove-TaskSafe $serverName
  Remove-TaskSafe $watchName
  Remove-TaskSafe $backupName
  if (Test-Path $vbsPath) { Remove-Item $vbsPath -Force; Write-Output "removed startup: $vbsPath" }
  exit 0
}

$installed = $false
try {
  $serverAction = New-ScheduledTaskAction -Execute $ps `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -Command `"Start-Process '$py' -ArgumentList '-m','uvicorn','server.main:app','--host','127.0.0.1','--port','8900' -WorkingDirectory '$root' -WindowStyle Hidden -RedirectStandardOutput '$root\logs\uvicorn.out.log' -RedirectStandardError '$root\logs\uvicorn.err.log'`""
  $serverTrigger = New-ScheduledTaskTrigger -AtLogOn
  $serverSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1)
  Register-ScheduledTask -TaskName $serverName -Action $serverAction -Trigger $serverTrigger -Settings $serverSettings -Description "DAPENG Alpha web service (uvicorn 127.0.0.1:8900)" -Force | Out-Null
  Write-Output "installed task: $serverName"

  $watchAction = New-ScheduledTaskAction -Execute $ps `
    -Argument "-NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$root\tools\watchdog.ps1`" -Once"
  $watchTrigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 1) -RepetitionDuration (New-TimeSpan -Days 3650)
  $watchSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew
  Register-ScheduledTask -TaskName $watchName -Action $watchAction -Trigger $watchTrigger -Settings $watchSettings -Description "DAPENG Alpha watchdog: health probe every minute, restart after 3 failures" -Force | Out-Null
  Write-Output "installed task: $watchName"

  $backupAction = New-ScheduledTaskAction -Execute $py -Argument "-m server.infra.backup" -WorkingDirectory $root
  $backupTrigger = New-ScheduledTaskTrigger -Daily -At '03:10'
  $backupSettings = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -StartWhenAvailable
  Register-ScheduledTask -TaskName $backupName -Action $backupAction -Trigger $backupTrigger -Settings $backupSettings -Description "DAPENG Alpha consistent backup (docs-graph + sqlite, keep 14)" -Force | Out-Null
  Write-Output "installed task: $backupName"
  $installed = $true
} catch {
  Write-Output "scheduled task install failed (likely no admin): $($_.Exception.Message)"
}

if (-not $installed) {
  New-Item -ItemType Directory -Path $startupDir -Force | Out-Null
  $cmd = "powershell.exe -NoProfile -WindowStyle Hidden -ExecutionPolicy Bypass -File ""$root\tools\watchdog.ps1"""
  $vbs = @"
' DAPENG Alpha auto-start (user-level, no admin required)
Set sh = CreateObject("WScript.Shell")
sh.Run "$cmd", 0, False
"@
  Set-Content -Path $vbsPath -Value $vbs -Encoding Default
  Write-Output "installed startup entry: $vbsPath"
  Write-Output "note: requires logon to start; watchdog loop also performs daily backup"
}

Write-Output ""
Write-Output "current state:"
if (Test-Path $vbsPath) { Write-Output "  startup: $vbsPath" }
Get-ScheduledTask -TaskName 'DAPENG-Alpha-*' -ErrorAction SilentlyContinue | Select-Object TaskName, State | Format-Table -AutoSize | Out-String -Width 60
Write-Output "verify now: powershell -NoProfile -ExecutionPolicy Bypass -File $root\tools\watchdog.ps1 -Once"
