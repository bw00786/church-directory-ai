# Start ATEM bridge (C++)

Write-Host "Starting ATEM Bridge..." -ForegroundColor Green

# Check if bridge is built
$repoRoot = Split-Path -Parent $PSScriptRoot
$bridgeExecutable = Join-Path $repoRoot "atem-bridge\build\bin\Release\atem-bridge.exe"

if (-not (Test-Path $bridgeExecutable)) {
    Write-Host "ERROR: ATEM bridge not built" -ForegroundColor Red
    Write-Host "Build with: cd atem-bridge; cmake --build build --config Release" -ForegroundColor Yellow
    exit 1
}

Write-Host "Starting ATEM Bridge on http://127.0.0.1:8090" -ForegroundColor Cyan
Write-Host ""

& $bridgeExecutable
