# DAPENG Alpha · 服务看门狗（护城河 · 运行时保障）
# 用法：
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1 -Once
#   powershell -NoProfile -ExecutionPolicy Bypass -File tools\watchdog.ps1        # 常驻循环 + 每日备份
# 规则：
#   - 连续 3 次 /healthz 失败才考虑重启，120 秒冷却；
#   - 若已存在 scripts/server_watchdog.py 守护进程，则只记录、不抢着重启（避免双拉起）；
#   - 常驻模式下每 24 小时触发一次 python -m server.infra.backup。

param([switch]$Once)

$ErrorActionPreference = 'SilentlyContinue'
$root = Split-Path -Parent $PSScriptRoot
$logs = Join-Path $root 'logs'
New-Item -ItemType Directory -Path $logs -Force | Out-Null
$statePath = Join-Path $logs 'watchdog-state.json'
$logPath = Join-Path $logs 'watchdog.log'
$port = 8900
$portFile = Join-Path $root '.current_port'
if (Test-Path $portFile) {
  $candidate = (Get-Content $portFile -Raw).Trim()
  if ($candidate -match '^\d+$') { $port = [int]$candidate }
}
$python = (Get-Command python -ErrorAction SilentlyContinue).Source
if (-not $python) { $python = Join-Path $env:LOCALAPPDATA 'Programs\Python\Python311\python.exe' }

function Write-Log([string]$message) {
  $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message
  Add-Content -Path $logPath -Value $line -Encoding utf8
  Write-Output $line
}

function Read-State {
  try {
    $raw = Get-Content $statePath -Raw -Encoding utf8
    if (-not $raw) { throw 'empty' }
    return $raw | ConvertFrom-Json
  } catch {
    return [pscustomobject]@{ fails = 0; lastRestart = 0; lastBackup = 0 }
  }
}

function Save-State($state) {
  $json = $state | ConvertTo-Json -Compress
  Set-Content -Path $statePath -Value $json -Encoding utf8
}

function Test-Health {
  try {
    $resp = Invoke-WebRequest ('http://127.0.0.1:{0}/healthz' -f $port) -UseBasicParsing -TimeoutSec 5
    return ($resp.StatusCode -eq 200 -and $resp.Content -match '"ok"\s*:\s*true')
  } catch {
    return $false
  }
}

function Test-Supervisor {
  $sup = Get-CimInstance Win32_Process -Filter "name='pythonw.exe' or name='python.exe'" |
    Where-Object { $_.CommandLine -match 'server_watchdog\.py' }
  return [bool]$sup
}

function Restart-Server {
  if (Test-Supervisor) {
    Write-Log 'supervisor present (server_watchdog.py) - defer restart'
    return
  }
  $now = [int][double]::Parse((Get-Date -UFormat %s))
  $state = Read-State
  if (($now - [int]$state.lastRestart) -lt 120) {
    Write-Log ('restart suppressed (cooldown): last={0} now={1}' -f $state.lastRestart, $now)
    return
  }
  Write-Log ('restarting server on port {0}' -f $port)
  Get-CimInstance Win32_Process -Filter "name='python.exe'" |
    Where-Object { $_.CommandLine -match 'uvicorn\s+server\.main:app' } |
    ForEach-Object { Stop-Process -Id $_.ProcessId -Force }
  Start-Sleep -Milliseconds 600
  $out = Join-Path $logs 'uvicorn.out.log'
  $err = Join-Path $logs 'uvicorn.err.log'
  Start-Process -FilePath $python -ArgumentList @('-m', 'uvicorn', 'server.main:app', '--host', '127.0.0.1', '--port', "$port") -WorkingDirectory $root -WindowStyle Hidden -RedirectStandardOutput $out -RedirectStandardError $err
  $state.lastRestart = $now
  $state.fails = 0
  Save-State $state
}

function Backup-IfDue {
  $state = Read-State
  $now = [int][double]::Parse((Get-Date -UFormat %s))
  if (($now - [int]$state.lastBackup) -lt 86400) { return }
  Write-Log 'daily backup starting'
  $raw = & $python -m server.infra.backup 2>&1 | Out-String
  if ($raw -match '"ok"\s*:\s*true') {
    $state.lastBackup = $now
    Save-State $state
    Write-Log 'daily backup ok'
  } else {
    $snippet = $raw.Substring(0, [Math]::Min(200, $raw.Length))
    Write-Log ('daily backup failed: ' + $snippet)
  }
}

# ── 单次探活 ───────────────────────────────────────────────
$state = Read-State
if (Test-Health) {
  if ([int]$state.fails -ne 0) {
    $state.fails = 0
    Save-State $state
  }
  Write-Log ('healthy (port {0})' -f $port)
  if ($Once) { exit 0 }
} else {
  $state.fails = [int]$state.fails + 1
  Save-State $state
  Write-Log ('unhealthy (consecutive={0})' -f $state.fails)
  if ([int]$state.fails -ge 3) {
    Restart-Server
  } else {
    Write-Log 'waiting for next probe before restart'
  }
  if ($Once) { exit 1 }
}

# ── 常驻循环 ───────────────────────────────────────────────
while ($true) {
  Start-Sleep -Seconds 30
  $cur = Read-State
  if (Test-Health) {
    if ([int]$cur.fails -ne 0) {
      $cur.fails = 0
      Save-State $cur
    }
  } else {
    $cur.fails = [int]$cur.fails + 1
    Save-State $cur
    Write-Log ('unhealthy (consecutive={0})' -f $cur.fails)
    if ([int]$cur.fails -ge 3) {
      Restart-Server
    }
  }
  Backup-IfDue
}
