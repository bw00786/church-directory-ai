# Registers a Scheduled Task so the EasyWorship agent auto-starts at logon.
# Run this ONCE on the EasyWorship laptop (in a normal, non-admin PowerShell
# is fine since the task runs for the current user at logon).
#
# The shared secret is stored as a per-user environment variable
# (EW_AGENT_TOKEN) so the logon task inherits it; nothing is written into the
# task definition. Put the same value in the backend .env as
# EASYWORSHIP_AGENT_TOKEN.
#
#   .\install-easyworship-agent-task.ps1                  # prompts for the token
#   .\install-easyworship-agent-task.ps1 -Token <secret>  # non-interactive
#   .\install-easyworship-agent-task.ps1 -AllowedIps 192.168.4.10
#
# Uninstall with:
#   Unregister-ScheduledTask -TaskName "EasyWorshipAgent" -Confirm:$false
#   [Environment]::SetEnvironmentVariable("EW_AGENT_TOKEN", $null, "User")

param(
    [string]$Token = $env:EW_AGENT_TOKEN,
    [string]$AllowedIps = $env:EW_AGENT_ALLOWED_IPS,
    [switch]$NoToken
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentScript = Join-Path $repoRoot "backend\easyworship_agent\agent.py"

if (-not (Test-Path $agentScript)) {
    Write-Host "ERROR: agent.py not found at $agentScript" -ForegroundColor Red
    Write-Host "Copy backend\easyworship_agent\agent.py onto this laptop and re-run." -ForegroundColor Yellow
    exit 1
}

if (-not $Token -and -not $NoToken) {
    Write-Host "Generate a token on the backend machine with:  python -c `"import secrets; print(secrets.token_urlsafe(32))`"" -ForegroundColor Gray
    $Token = Read-Host "Shared agent token (must match EASYWORSHIP_AGENT_TOKEN in the backend .env)"
    if (-not $Token) {
        Write-Host "ERROR: a token is required. Re-run with -NoToken to install an unauthenticated agent (loopback/EW_AGENT_ALLOWED_IPS only)." -ForegroundColor Red
        exit 1
    }
}

if ($Token) {
    [Environment]::SetEnvironmentVariable("EW_AGENT_TOKEN", $Token, "User")
    Write-Host "Stored EW_AGENT_TOKEN as a user environment variable." -ForegroundColor Green
} else {
    Write-Host "WARNING: installing WITHOUT a token; only loopback" -NoNewline -ForegroundColor Red
    if ($AllowedIps) { Write-Host " and EW_AGENT_ALLOWED_IPS=$AllowedIps" -NoNewline -ForegroundColor Red }
    Write-Host " clients can control slides." -ForegroundColor Red
}
if ($AllowedIps) {
    [Environment]::SetEnvironmentVariable("EW_AGENT_ALLOWED_IPS", $AllowedIps, "User")
    Write-Host "Stored EW_AGENT_ALLOWED_IPS=$AllowedIps as a user environment variable." -ForegroundColor Green
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
Write-Host "Backend .env: EASYWORSHIP_AGENT_URL=http://<this-pc-ip>:8091 and EASYWORSHIP_AGENT_TOKEN=<the token>" -ForegroundColor Cyan
