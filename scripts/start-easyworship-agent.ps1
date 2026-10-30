# Start the standalone EasyWorship remote-control agent.
# Run this on the Windows laptop that runs EasyWorship (not the backend machine).
#
# Security: the agent injects keystrokes into EasyWorship, so it is guarded by a
# shared secret. Set EW_AGENT_TOKEN here and EASYWORSHIP_AGENT_TOKEN (same value)
# in the backend .env. Without a token the agent only accepts loopback clients
# (plus EW_AGENT_ALLOWED_IPS, comma-separated, if set).

param(
    [string]$Token = $env:EW_AGENT_TOKEN,
    [string]$AllowedIps = $env:EW_AGENT_ALLOWED_IPS
)

Write-Host "Starting EasyWorship Agent..." -ForegroundColor Green

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentScript = Join-Path $repoRoot "backend\easyworship_agent\agent.py"

if (-not (Test-Path $agentScript)) {
    Write-Host "ERROR: agent.py not found at $agentScript" -ForegroundColor Red
    Write-Host "Copy backend\easyworship_agent\agent.py onto this laptop and re-run." -ForegroundColor Yellow
    exit 1
}

if (-not $Token) {
    Write-Host "EW_AGENT_TOKEN is not set." -ForegroundColor Yellow
    Write-Host "Generate one on the backend machine with:  python -c `"import secrets; print(secrets.token_urlsafe(32))`"" -ForegroundColor Gray
    $Token = Read-Host "Shared agent token (must match EASYWORSHIP_AGENT_TOKEN in the backend .env; blank = loopback/allow-listed clients only)"
}

if ($Token) {
    $env:EW_AGENT_TOKEN = $Token
} else {
    Write-Host "WARNING: running WITHOUT a token. Only loopback clients" -NoNewline -ForegroundColor Red
    if ($AllowedIps) { Write-Host " and EW_AGENT_ALLOWED_IPS=$AllowedIps" -NoNewline -ForegroundColor Red }
    Write-Host " can control slides." -ForegroundColor Red
}
if ($AllowedIps) { $env:EW_AGENT_ALLOWED_IPS = $AllowedIps }

$port = if ($env:EW_AGENT_PORT) { $env:EW_AGENT_PORT } else { "8091" }
$bind = if ($env:EW_AGENT_HOST) { $env:EW_AGENT_HOST } else { "0.0.0.0" }

Write-Host "Listening on http://${bind}:$port (health check: http://localhost:$port/health)" -ForegroundColor Cyan
Write-Host "Make sure EasyWorship is open, and Windows Firewall allows inbound TCP $port from the backend machine only." -ForegroundColor Yellow
Write-Host ""

python $agentScript
