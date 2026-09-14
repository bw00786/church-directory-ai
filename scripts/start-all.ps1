# Start all services

Write-Host "Church Production Director - Starting All Services" -ForegroundColor Green
Write-Host "=====================================================" -ForegroundColor Green
Write-Host ""
Write-Host "Ollama is managed separately; these launchers do not start it or download models." -ForegroundColor Yellow
Write-Host "Verify OLLAMA_BASE_URL and the exact configured tags (local default qwen3.8:latest)." -ForegroundColor Yellow
Write-Host ""

# Start ATEM bridge in new window
Write-Host "Starting ATEM Bridge..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD; .\scripts\start-atem-bridge.ps1"
Start-Sleep -Seconds 2

# Start backend in new window
Write-Host "Starting Backend..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD; .\scripts\start-backend.ps1"
Start-Sleep -Seconds 2

# Start frontend in new window
Write-Host "Starting Frontend..." -ForegroundColor Cyan
Start-Process powershell -ArgumentList "-NoExit", "-Command", "cd $PWD; .\scripts\start-frontend.ps1"

Write-Host ""
Write-Host "✅ All services starting..." -ForegroundColor Green
Write-Host ""
Write-Host "Services:" -ForegroundColor Cyan
Write-Host "  ATEM Bridge:  http://127.0.0.1:8090" -ForegroundColor Gray
Write-Host "  Backend API:  http://localhost:8000" -ForegroundColor Gray
Write-Host "  Frontend:     http://localhost:5173" -ForegroundColor Gray
Write-Host "  API Docs:     http://localhost:8000/docs" -ForegroundColor Gray
Write-Host "  Ollama check: http://localhost:8000/health/ollama (inference; allow up to 120s by default)" -ForegroundColor Gray
