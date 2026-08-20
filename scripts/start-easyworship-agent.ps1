# Start the standalone EasyWorship remote-control agent.
# Run this on the Windows laptop that runs EasyWorship (not the backend machine).

Write-Host "Starting EasyWorship Agent..." -ForegroundColor Green

$repoRoot = Split-Path -Parent $PSScriptRoot
$agentScript = Join-Path $repoRoot "backend\easyworship_agent\agent.py"

if (-not (Test-Path $agentScript)) {
    Write-Host "ERROR: agent.py not found at $agentScript" -ForegroundColor Red
    Write-Host "Copy backend\easyworship_agent\agent.py onto this laptop and re-run." -ForegroundColor Yellow
    exit 1
}

$port = if ($env:EW_AGENT_PORT) { $env:EW_AGENT_PORT } else { "8091" }

Write-Host "Listening on http://0.0.0.0:$port (health check: http://localhost:$port/health)" -ForegroundColor Cyan
Write-Host "Make sure EasyWorship is open, and Windows Firewall allows inbound TCP $port." -ForegroundColor Yellow
Write-Host ""

python $agentScript
