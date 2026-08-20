# Registers a Scheduled Task so the EasyWorship agent auto-starts at logon.
# Run this ONCE on the EasyWorship laptop (in a normal, non-admin PowerShell
# is fine since the task runs for the current user at logon).
#
# Uninstall with:
#   Unregister-ScheduledTask -TaskName "EasyWorshipAgent" -Confirm:$false

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentScript = Join-Path $repoRoot "backend\easyworship_agent\agent.py"

if (-not (Test-Path $agentScript)) {
    Write-Host "ERROR: agent.py not found at $agentScript" -ForegroundColor Red
    Write-Host "Copy backend\easyworship_agent\agent.py onto this laptop and re-run." -ForegroundColor Yellow
    exit 1
}

# Prefer pythonw.exe (no console window) if present alongside python.exe.
$pythonCmd = Get-Command python.exe -ErrorAction SilentlyContinue
if (-not $pythonCmd) {
    Write-Host "ERROR: python.exe not found on PATH." -ForegroundColor Red
    exit 1
}
$pythonwPath = Join-Path (Split-Path $pythonCmd.Source) "pythonw.exe"
$exe = if (Test-Path $pythonwPath) { $pythonwPath } else { $pythonCmd.Source }

$action = New-ScheduledTaskAction -Execute $exe -Argument "`"$agentScript`""
$trigger = New-ScheduledTaskTrigger -AtLogOn
$settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -ExecutionTimeLimit ([TimeSpan]::Zero)

Register-ScheduledTask -TaskName "EasyWorshipAgent" `
    -Action $action -Trigger $trigger -Settings $settings `
    -Description "Remote EasyWorship slide control agent (church-directory-ai)" `
    -Force

Write-Host "Task 'EasyWorshipAgent' registered - it will start at next logon." -ForegroundColor Green
Write-Host "To start it immediately: Start-ScheduledTask -TaskName 'EasyWorshipAgent'" -ForegroundColor Cyan
