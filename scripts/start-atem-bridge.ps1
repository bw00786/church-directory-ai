# Start ATEM bridge (C++)

Write-Host "Starting ATEM Bridge..." -ForegroundColor Green

# Check if bridge is built
if (-not (Test-Path "atem-bridge\bin\atem-bridge.exe")) {
    Write-Host "ERROR: ATEM bridge not built" -ForegroundColor Red
    Write-Host "Build with: cd atem-bridge && cmake . && cmake --build . --config Release" -ForegroundColor Yellow
    exit 1
}

Write-Host "Starting ATEM Bridge on http://127.0.0.1:8090" -ForegroundColor Cyan
Write-Host ""

.\atem-bridge\bin\atem-bridge.exe
